"""launchd 常驻服务管理。不用 cron，因为 LaunchAgent 能跟随登录、支持 KeepAlive 自动拉起。"""

from __future__ import annotations

import plistlib
import subprocess
import sys
import time
from pathlib import Path

LABEL = "com.netmon.probe"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
ROOT = Path(__file__).resolve().parent.parent


def _python() -> str:
    exe = sys.executable or ""
    # WorkBuddy 托管解释器（~/.workbuddy/binaries）运行在沙箱内，launchd
    # 直接拉起会因无法连接沙箱 broker 而失败（bootstrap 报 error 5，实测）。
    # 服务一律退回系统解释器——纯标准库实现，对版本无要求。
    if ".workbuddy" in exe or not exe or not Path(exe).exists():
        return "/usr/bin/python3"
    return exe


def build_plist(interval: int, log_dir: Path) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            _python(), "-m", "netmon", "run", "--interval", str(interval),
        ],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {
            "PYTHONPATH": str(ROOT),
            "PYTHONUNBUFFERED": "1",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin",
        },
        "RunAtLoad": True,
        # 监控服务必须常在：无论退出码如何都拉起（ SIGTERM 干净退出后也要自愈）
        "KeepAlive": True,
        "ThrottleInterval": 20,
        "StandardOutPath": str(log_dir / "launchd.out.log"),
        "StandardErrorPath": str(log_dir / "launchd.err.log"),
        "ProcessType": "Background",
    }


def _bootout(uid: str) -> None:
    """卸载服务并等待 job 真正从 gui 域消失。

    bootout 是异步的：对 KeepAlive 常驻进程发 SIGTERM 后立即返回，但 job
    注销需要时间——立刻 bootstrap 会撞上报错 "Bootstrap failed: 5:
    Input/output error"（用户实测复现过）。这里轮询直到 print 失败为止。
    """
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
                   capture_output=True, text=True)
    for _ in range(30):  # 最多等约 3 秒
        r = subprocess.run(["launchctl", "print", f"gui/{uid}/{LABEL}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return
        time.sleep(0.1)


def install(interval: int, log_dir: Path) -> dict:
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    data = build_plist(interval, log_dir)
    with PLIST.open("wb") as fh:
        plistlib.dump(data, fh)
    uid = str(Path.home().stat().st_uid)
    _bootout(uid)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)],
                       capture_output=True, text=True)
    ok = r.returncode == 0
    if ok:
        subprocess.run(["launchctl", "enable", f"gui/{uid}/{LABEL}"],
                       capture_output=True, text=True)
    return {"ok": ok, "plist": str(PLIST), "stderr": r.stderr.strip(), "stdout": r.stdout.strip()}


def uninstall() -> dict:
    uid = str(Path.home().stat().st_uid)
    _bootout(uid)
    existed = PLIST.exists()
    if existed:
        PLIST.unlink()
    return {"ok": True, "removed": existed, "plist": str(PLIST)}


def status() -> dict:
    uid = str(Path.home().stat().st_uid)
    r = subprocess.run(["launchctl", "print", f"gui/{uid}/{LABEL}"],
                       capture_output=True, text=True)
    loaded = r.returncode == 0
    pid = None
    if loaded:
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("pid = "):
                pid = line.split("=", 1)[1].strip()
                break
    return {
        "loaded": loaded,
        "pid": pid,
        "plist_exists": PLIST.exists(),
        "plist": str(PLIST),
        "detail": (r.stdout[:400] if loaded else r.stderr.strip()[:400]),
    }
