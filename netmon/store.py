"""数据落盘：按天滚动的 JSONL + 取证附件 + 运行日志。

选 JSONL 而不是 SQLite 的理由：断网排查是"写入量极小、需要事后逐条人工核对"的场景，
纯文本行能直接用 grep/tail 查看、不易损坏、且不需要任何依赖。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import util
from .config import DATA_DIR, REPORT_DIR


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


def prune(retention_days: int) -> list[str]:
    """删除超过保留期的数据文件，返回删除列表。"""
    import time

    cutoff = time.time() - retention_days * 86400
    removed: list[str] = []
    for p in _d().glob("*.jsonl"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
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
