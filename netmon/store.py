"""数据落盘：按天滚动的 JSONL + 取证附件 + 运行日志。

选 JSONL 而不是 SQLite 的理由：断网排查是"写入量极小、需要事后逐条人工核对"的场景，
纯文本行能直接用 grep/tail 查看、不易损坏、且不需要任何依赖。
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

import shutil
import stat
import sys
import time

from . import util
from .config import DATA_DIR, REPORT_DIR, ROOT


def _d() -> Path:
    return util.ensure_dir(DATA_DIR)


def tick_path(day: str) -> Path:
    return _d() / f"ticks-{day}.jsonl"


def evidence_path(day: str) -> Path:
    return _d() / f"evidence-{day}.jsonl"


def log_path() -> Path:
    return _d() / "netmon.log"


def append_jsonl(path: Path, obj: dict) -> None:
    util.ensure_dir(path.parent)
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def append_tick(tick: dict) -> None:
    append_jsonl(tick_path(util.day_str(tick["ts"])), tick)


def append_evidence(rec: dict) -> None:
    append_jsonl(evidence_path(util.day_str(rec["ts"])), rec)


def log(msg: str, level: str = "INFO") -> None:
    line = f"{util.iso(util.now_ts())} [{level}] {msg}"
    try:
        p = log_path()
        if p.exists() and p.stat().st_size > 5 * 1024 * 1024:
            p.replace(p.with_suffix(".log.1"))
        with p.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


STDOUT_LOG_ENV = "NETMON_STDOUT_LOG"
STDOUT_LOG_LIMIT = 4 * 1024 * 1024
STDOUT_LOG_TAIL = 512 * 1024


def rotate_stdout_log(limit: int = STDOUT_LOG_LIMIT,
                      keep_tail: int = STDOUT_LOG_TAIL) -> bool:
    """就地轮转 launchd 重定向的 stdout 日志（launchd.out.log）。

    launchd 以固定的 fd 写 StandardOutPath，因此外部 rename 会让它继续往旧 inode
    写（新文件永远为空），直接删掉则日志凭空消失。正确做法是由写入方自己：
      1) 另开只读句柄取尾部若干字节，落到 <日志>.1 保留近期记录；
      2) 对自己的 stdout fd 执行 ftruncate——同一个 inode 归零，launchd 无需重开；
      3) lseek 到末尾，无论 launchd 是否用 O_APPEND 打开都能从 0 继续写。
    未由 launchd 拉起（没有该环境变量）时直接跳过。
    """
    path = os.environ.get(STDOUT_LOG_ENV)
    if not path:
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode) or st.st_size < limit:
        return False

    tail = b""
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, st.st_size - keep_tail))
            tail = fh.read()
    except OSError:
        pass

    try:
        if tail:
            Path(str(path) + ".1").write_bytes(tail)
        try:
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
        fd = sys.stdout.fileno()
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_END)
        return True
    except (OSError, AttributeError, ValueError):
        return False


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return out
    return out


def available_days() -> list[str]:
    days = []
    for p in sorted(_d().glob("ticks-*.jsonl")):
        d = p.stem.replace("ticks-", "")
        if len(d) == 10:
            days.append(d)
    return days


def load_ticks(days: list[str] | None = None) -> list[dict]:
    if days is None:
        days = available_days()
    ticks: list[dict] = []
    for d in days:
        ticks += _read_jsonl(tick_path(d))
    ticks.sort(key=lambda t: t.get("ts", 0))
    return ticks


def load_evidence(days: list[str] | None = None) -> list[dict]:
    if days is None:
        days = available_days()
    recs: list[dict] = []
    for d in days:
        recs += _read_jsonl(evidence_path(d))
    recs.sort(key=lambda r: r.get("ts", 0))
    return recs


def attach_evidence(incidents: list[dict], evidence: list[dict]) -> None:
    """把取证记录挂到时间窗覆盖它的事件上。"""
    for inc in incidents:
        inc["evidence"] = []
    for ev in evidence:
        ts = ev.get("ts", 0)
        for inc in incidents:
            if inc["start_ts"] - 60 <= ts <= inc["end_ts"] + 60:
                inc["evidence"].append(ev)
                break


def expired_dir() -> Path:
    return ROOT / "expired"


def prune(retention_days: int) -> list[str]:
    """把超过保留期的数据移入归档目录，返回文件名列表。

    早先是直接 unlink——采集器每小时跑一次，过期数据一删就再也没法追溯。
    改为移动到数据目录下的 expired/，既不参与 available_days()/load_ticks()
    （它不在 data/ 里），又能随时手工拷回来。
    """
    cutoff = time.time() - retention_days * 86400
    removed: list[str] = []
    for p in _d().glob("*.jsonl"):
        try:
            if p.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        try:
            dest_dir = util.ensure_dir(expired_dir())
            dest = dest_dir / p.name
            if dest.exists():
                dest = dest_dir / f"{p.stem}-{int(time.time())}{p.suffix}"
            shutil.move(str(p), str(dest))
            removed.append(p.name)
        except OSError:
            continue
    return removed


def purge_all() -> int:
    n = 0
    for p in sorted(_d().glob("*.jsonl")) + sorted(_d().glob("*.log*")):
        try:
            p.unlink()
            n += 1
        except OSError:
            continue
    return n


def archive_all() -> tuple[Path, int, int]:
    """把采样/取证数据与所有报告归档到带时间戳的备份目录（可恢复），不做真删除。

    返回 (备份目录, 移动的数据文件数, 移动的报告数)。日志保留在 data/ 原地不归档。
    """
    import datetime

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = ROOT / f"history-backup-{stamp}"
    (backup / "data").mkdir(parents=True, exist_ok=True)
    (backup / "reports").mkdir(parents=True, exist_ok=True)

    n_data = n_report = 0
    for p in sorted(_d().glob("*.jsonl")):
        try:
            shutil.move(str(p), str(backup / "data" / p.name))
            n_data += 1
        except OSError:
            continue
    if REPORT_DIR.exists():
        for p in sorted(REPORT_DIR.glob("*.html")):
            try:
                shutil.move(str(p), str(backup / "reports" / p.name))
                n_report += 1
            except OSError:
                continue
    return backup, n_data, n_report


def report_dir() -> Path:
    return util.ensure_dir(REPORT_DIR)


def write_report(name: str, html: str) -> Path:
    p = report_dir() / name
    p.write_text(html, encoding="utf-8")
    return p


def pid_path() -> Path:
    return _d() / "netmon.pid"


def is_running() -> int | None:
    p = pid_path()
    if not p.exists():
        return None
    try:
        pid = int(p.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, OSError):
        return None


def write_pid() -> None:
    pid_path().write_text(str(os.getpid()), encoding="utf-8")


def lock_path() -> Path:
    # 单实例锁按“当前用户”而非数据目录共享：即使用前台 CLI 和 .app
    # 使用了不同 NETMON_HOME，也不能让两个探针同时跑、叠加系统开销。
    root = Path.home() / "Library" / "Application Support" / "NetMon"
    return util.ensure_dir(root) / "netmon.lock"


def acquire_run_lock():
    """独占采集锁：成功返回持有中的文件对象，已有采集实例时返回 None。

    用 flock 而不是 pid 文件判重，避免进程崩溃后残留 pid 或 pid 复用导致误判。
    锁随进程退出自动释放，调用方只需在采集期间保持返回对象存活。
    """
    fh = lock_path().open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def run_lock_available() -> bool:
    """探测当前是否已有采集实例（不长期持有锁）。"""
    fh = acquire_run_lock()
    if fh is None:
        return False
    fh.close()
    return True
