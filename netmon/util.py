"""通用工具：命令执行、时间、数值。"""

from __future__ import annotations

import statistics
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path


def run_cmd(cmd: list[str], timeout: float = 5.0) -> dict:
    """执行命令，永不抛异常，返回统一结构。"""
    t0 = time.monotonic()
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = p.stdout or ""
        err = p.stderr or ""
        return {
            "ok": p.returncode == 0,
            "rc": p.returncode,
            "stdout": out,
            "stderr": err,
            "text": (out + ("\n" + err if err else "")).strip(),
            "ms": round((time.monotonic() - t0) * 1000, 1),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "rc": -1,
            "stdout": "",
            "stderr": "timeout",
            "text": "",
            "ms": round((time.monotonic() - t0) * 1000, 1),
        }
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return {
            "ok": False,
            "rc": -2,
            "stdout": "",
            "stderr": type(exc).__name__,
            "text": "",
            "ms": round((time.monotonic() - t0) * 1000, 1),
        }


# ---------------------------------------------------------------- 时间

def now_dt() -> datetime:
    return datetime.now().astimezone()


def now_ts() -> float:
    return time.time()


def iso(ts: float, timespec: str = "seconds") -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec=timespec)


def hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%H:%M")


def hhmmss(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%H:%M:%S")


def day_str(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d")


def hour_of(ts: float) -> int:
    return datetime.fromtimestamp(ts).astimezone().hour


def day_start_ts(d: str) -> float:
    dt = datetime.strptime(d, "%Y-%m-%d").astimezone()
    return dt.timestamp()


def human_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m} 分 {s} 秒" if s else f"{m} 分钟"
    h, m = divmod(m, 60)
    return f"{h} 小时 {m} 分" if m else f"{h} 小时"


# ---------------------------------------------------------------- 数值

def pct(part: float, whole: float) -> float:
    if not whole:
        return 0.0
    return round(part * 100.0 / whole, 2)


def safe_avg(values: list[float]) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return None
    return round(statistics.fmean(vals), 2)


def safe_p95(values: list[float]) -> float | None:
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return None
    idx = min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))
    return round(vals[idx], 2)


def downsample(points: list[tuple[float, float | None]], max_points: int) -> list[tuple[float, float | None]]:
    """按时间等分桶取均值，避免图表点过密。None 值保留为断点。"""
    if len(points) <= max_points:
        return points
    bucket = len(points) / max_points
    out: list[tuple[float, float | None]] = []
    i = 0
    while i < len(points):
        chunk = points[i:int(i + bucket)] or [points[i]]
        t = chunk[0][0]
        vals = [v for _, v in chunk if v is not None]
        out.append((t, round(statistics.fmean(vals), 2) if vals else None))
        i += max(1, int(bucket))
    return out


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def tail_lines(text: str, n: int) -> list[str]:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return lines[-n:] if n > 0 else lines
