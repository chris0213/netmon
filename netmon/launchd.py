"""launchd 常驻服务管理。不用 cron，因为 LaunchAgent 能跟随登录、支持 KeepAlive 自动拉起。"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.netmon.probe"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
ROOT = Path(__file__).resolve().parent.parent


def _python() -> str:
    exe = sys.executable
    return exe if exe and Path(exe).exists() else "/usr/bin/python3"


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


def install(interval: int, log_dir: Path) -> dict:
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    data = build_plist(interval, log_dir)
    with PLIST.open("wb") as fh:
        plistlib.dump(data, fh)
    uid = str(Path.home().stat().st_uid)
    # 先卸载旧的，避免重复加载报错
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
                   capture_output=True, text=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)],
                       capture_output=True, text=True)
    ok = r.returncode == 0
    if ok:
        subprocess.run(["launchctl", "enable", f"gui/{uid}/{LABEL}"],
                       capture_output=True, text=True)
    return {"ok": ok, "plist": str(PLIST), "stderr": r.stderr.strip(), "stdout": r.stdout.strip()}


def uninstall() -> dict:
    uid = str(Path.home().stat().st_uid)
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
                   capture_output=True, text=True)
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
