"""launchd 常驻服务管理。不用 cron，因为 LaunchAgent 能跟随登录、支持 KeepAlive 自动拉起。"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

# 与 Info.plist 的 CFBundleIdentifier 保持一致；旧版用的是 .probe，
# 升级时必须把旧的 job 和 plist 一起迁走，否则会出现两个服务抢采集锁。
LABEL = "com.netmon.desktop"
LEGACY_LABEL = "com.netmon.probe"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LEGACY_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LEGACY_LABEL}.plist"
ROOT = Path(__file__).resolve().parent.parent


def _embedded_python() -> str:
    """若运行在打包后的 .app 内，返回随包分发的解释器路径。

    内嵌解释器位于 Contents/Resources/python，与源码目录 Contents/Resources/app
    同级。它自带 Tcl/Tk 9.x，不依赖宿主机是否安装过 Python。后台采集必须与 GUI
    用同一个解释器，否则会回落到系统 Tk 8.5（打开即白屏），或者在用户没装
    Python 的机器上直接起不来。
    """
    bin_dir = ROOT.parent / "python" / "bin"
    if not bin_dir.is_dir():
        return ""
    cand = bin_dir / "python3.13"
    if cand.is_file() and os.access(cand, os.X_OK):
        return str(cand)
    for p in sorted(bin_dir.glob("python3.*")):
        if "-config" in p.name:
            continue
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return ""


def _python() -> str:
    """为 launchd plist 选定一个"稳定"的 Python 解释器路径。

    安装时会把返回值写死进 plist 的 ProgramArguments，因此必须避开一切
    "可能被删除/迁移"的临时解释器，否则 KeepAlive 会在该解释器消失后静默崩溃：
      0) 打包后的 .app：优先用随包分发的内嵌解释器（自包含、Tk 9.x，最稳）。
      1) WorkBuddy 托管解释器（~/.workbuddy/binaries）运行在 seatbelt 沙箱内，
         launchd 直接拉起会因连不上沙箱 broker 而 bootstrap 报 error 5（实测）。
      2) venv / pyenv / conda 内的解释器：用户退出或删除环境后路径即失效，
         退回其"基座"解释器（sys.base_prefix/bin/python3）更稳。
      3) 其余情况用当前解释器，但需确实存在于磁盘。
    """
    embedded = _embedded_python()
    if embedded:
        return embedded
    exe = sys.executable or ""
    if ".workbuddy" in exe:
        return "/usr/bin/python3"
    if getattr(sys, "prefix", "") and sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        cand = os.path.join(sys.base_prefix, "bin", "python3")
        if os.path.exists(cand):
            return cand
    if exe and Path(exe).exists():
        return exe
    return "/usr/bin/python3"


def build_plist(interval: int, log_dir: Path) -> dict:
    env = {
        "PYTHONPATH": str(ROOT),
        "PYTHONUNBUFFERED": "1",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin",
        # 采集进程据此对自己的 stdout 做就地轮转：launchd 持有该文件的 fd，
        # 外部 rename/删除都不生效（详见 store.rotate_stdout_log）。
        "NETMON_STDOUT_LOG": str(log_dir / "launchd.out.log"),
    }
    # .app 场景：GUI 进程注入了 NETMON_HOME，必须原样透传给后台采集进程，
    # 否则 launchd 拉起的采集会把数据写到默认目录，导致 GUI 看不到数据。
    netmon_home = os.environ.get("NETMON_HOME")
    if netmon_home:
        env["NETMON_HOME"] = netmon_home
    return {
        "Label": LABEL,
        "ProgramArguments": [
            _python(), "-m", "netmon", "run", "--interval", str(interval),
        ],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        # 监控服务必须常在：无论退出码如何都拉起（ SIGTERM 干净退出后也要自愈）
        "KeepAlive": True,
        "ThrottleInterval": 20,
        "StandardOutPath": str(log_dir / "launchd.out.log"),
        # 错误流并入 netmon.log：store.py 已对 netmon.log 做 5MB 轮转，
        # 避免服务崩溃循环时 launchd.err.log 无上限增长撑爆磁盘。
        "StandardErrorPath": str(log_dir / "netmon.log"),
        "ProcessType": "Background",
    }


def _uid() -> str:
    return str(Path.home().stat().st_uid)


def _bootout(uid: str) -> None:
    """卸载新旧两个标签的 job 并等待真正注销。

    bootout 是异步的：对 KeepAlive 常驻进程发 SIGTERM 后立即返回，但 job
    注销需要时间——立刻 bootstrap 会撞上报错 "Bootstrap failed: 5:
    Input/output error"（用户实测复现过）。这里轮询直到 print 失败为止。
    """
    for label in (LABEL, LEGACY_LABEL):
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                       capture_output=True, text=True)
    for _ in range(30):  # 最多等约 3 秒
        pending = []
        for label in (LABEL, LEGACY_LABEL):
            r = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                pending.append(label)
        if not pending:
            return
        time.sleep(0.1)


def _drop_legacy_plist() -> bool:
    if not LEGACY_PLIST.exists():
        return False
    LEGACY_PLIST.unlink()
    return True


def install(interval: int, log_dir: Path) -> dict:
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    data = build_plist(interval, log_dir)
    with PLIST.open("wb") as fh:
        plistlib.dump(data, fh)
    legacy_removed = _drop_legacy_plist()
    uid = _uid()
    _bootout(uid)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)],
                       capture_output=True, text=True)
    ok = r.returncode == 0
    if ok:
        subprocess.run(["launchctl", "enable", f"gui/{uid}/{LABEL}"],
                       capture_output=True, text=True)
    return {"ok": ok, "plist": str(PLIST), "stderr": r.stderr.strip(),
            "stdout": r.stdout.strip(), "legacy_removed": legacy_removed}


def uninstall() -> dict:
    uid = _uid()
    _bootout(uid)
    existed = PLIST.exists()
    if existed:
        PLIST.unlink()
    legacy = _drop_legacy_plist()
    return {"ok": True, "removed": existed or legacy, "plist": str(PLIST),
            "legacy_removed": legacy}


def _default_home() -> Path:
    return Path.home() / "Library" / "Application Support" / "NetMon"


def _old_interval(data: dict) -> int | None:
    """从旧 plist 的 ProgramArguments 里取出采样间隔，重写时沿用。"""
    args = list(data.get("ProgramArguments") or [])
    for i, a in enumerate(args):
        if a == "--interval" and i + 1 < len(args):
            try:
                return int(args[i + 1])
            except ValueError:
                return None
        if a.startswith("--interval="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                return None
    return None


def _repair_reasons(data: dict) -> list[str]:
    """对比磁盘上的 plist 与「当前正在运行的这个 app」，返回需要修复的原因。

    KeepAlive 的 job 会每 20 秒重试一次，因此只要 ProgramArguments 里的解释器
    或源码目录失效（应用被移动/改名/删掉），launchd 就会无限次拉起失败进程。
    这类不一致必须由 app 自己发现并修好——用户不会去看 launchctl 的输出。
    """
    reasons: list[str] = []
    if not data:
        return ["plist 无法解析"]

    args = list(data.get("ProgramArguments") or [])
    exe = args[0] if args else ""
    env = data.get("EnvironmentVariables") or {}
    pypath = (env.get("PYTHONPATH") or "").strip()
    wd = (data.get("WorkingDirectory") or "").strip()
    want_exe = _python()
    want_root = str(ROOT)

    if exe != want_exe:
        reasons.append(f"解释器不一致：{exe or '（缺失）'} → {want_exe}")
    elif exe and not Path(exe).exists():
        reasons.append(f"解释器已不存在：{exe}")

    if pypath != want_root:
        reasons.append("源码目录不一致")
    elif pypath and not Path(pypath).is_dir():
        reasons.append(f"源码目录已不存在：{pypath}")

    if wd != want_root:
        reasons.append("工作目录不一致")

    # 旧版本 plist 没有这个变量，缺少它采集进程就无法轮转 stdout 日志。
    if not env.get("NETMON_STDOUT_LOG"):
        reasons.append("缺少日志轮转配置")

    # 数据目录被移动/清空时，日志父目录会消失，launchd 会拒开该 job。
    for key in ("StandardOutPath", "StandardErrorPath"):
        p = data.get(key)
        if p and not Path(p).parent.is_dir():
            reasons.append(f"日志目录不存在：{Path(p).parent}")
            break
    return reasons


def reconcile(interval: int, log_dir: Path) -> dict:
    """让已安装的 LaunchAgent 与当前运行的应用保持一致（自愈）。

    触发场景：应用被从 DMG/桌面拖到 /Applications、改名、或换目录运行。此时旧
    plist 里的绝对路径全部失效。本函数在 GUI 启动时调用一次：
      - 已加载（用户此前开着监控）→ 重写 plist 并重新 bootstrap，采集立即恢复；
      - 未加载（只是注册过）→ 只重写文件，下次登录/点启动时用新路径。

    自定义 NETMON_HOME（测试/多实例）时直接跳过，避免把临时路径写进用户常驻服务。
    """
    out = {"changed": False, "reasons": [], "reloaded": False, "detail": ""}

    home_env = os.environ.get("NETMON_HOME")
    if home_env and Path(home_env) != _default_home():
        out["detail"] = "自定义数据目录，跳过自愈"
        return out
    # 源码直跑（开发调试）时不要去改用户已安装的常驻服务，否则会把仓库路径
    # 和系统 python 写进 plist，反把服务弄坏。
    if ".app/Contents/" not in str(ROOT):
        out["detail"] = "源码运行模式，跳过自愈"
        return out
    legacy_exists = LEGACY_PLIST.exists()
    if not PLIST.exists() and not legacy_exists:
        out["detail"] = "未安装后台服务"
        return out

    source = LEGACY_PLIST if legacy_exists and not PLIST.exists() else PLIST
    try:
        with source.open("rb") as fh:
            old = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException):
        old = {}

    reasons = []
    if source == LEGACY_PLIST:
        reasons.append("旧版服务标签，需迁移")
    reasons += _repair_reasons(old)
    out["reasons"] = reasons
    if not reasons:
        out["detail"] = "后台服务路径正常"
        return out

    # 沿用旧间隔，避免自愈把用户设置的采样频率改回默认值。
    keep = _old_interval(old) or interval
    data = build_plist(keep, log_dir)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with PLIST.open("wb") as fh:
        plistlib.dump(data, fh)
    out["changed"] = True
    # 旧标签的 plist 必须删掉：否则登录时会同时注册两个服务抢采集锁。
    if _drop_legacy_plist():
        out["legacy_migrated"] = True

    uid = _uid()
    loaded = status()["loaded"]
    if loaded:
        _bootout(uid)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)],
                           capture_output=True, text=True)
        out["reloaded"] = r.returncode == 0
        if r.returncode == 0:
            subprocess.run(["launchctl", "enable", f"gui/{uid}/{LABEL}"],
                           capture_output=True, text=True)
        else:
            out["detail"] = (r.stderr or r.stdout).strip()
    out["interval"] = keep
    return out


def status() -> dict:
    uid = _uid()
    active = None
    for label in (LABEL, LEGACY_LABEL):
        r = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            active = (label, r)
            break

    loaded = active is not None
    label = active[0] if active else LABEL
    r = active[1] if active else None
    pid = None
    if loaded:
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("pid = "):
                pid = line.split("=", 1)[1].strip()
                break
    return {
        "loaded": loaded,
        "label": label,
        "legacy": label == LEGACY_LABEL,
        "pid": pid,
        "plist_exists": PLIST.exists() or LEGACY_PLIST.exists(),
        "plist": str(PLIST if PLIST.exists() else LEGACY_PLIST),
        "detail": (r.stdout[:400] if loaded else ""),
    }
