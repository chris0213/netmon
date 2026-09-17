"""事件取证：在断网发生的那一刻，把现场信息钉下来。

事后复盘时最有价值的不是"断了几次"，而是"断的那一刻，链路上到底发生了什么"。
"""

from __future__ import annotations

from . import probe, util


def snapshot(incident_start_ts: float, iface: str | None, gateway: str | None) -> dict:
    iface = iface or "en0"
    rec = {
        "ts": util.now_ts(),
        "kind": "snapshot",
        "label": "现场快照",
        "incident_start": incident_start_ts,
        "iface": iface,
    }
    rec["ifconfig"] = util.run_cmd(["ifconfig", iface], timeout=4)["stdout"].strip()
    rec["route_get"] = util.run_cmd(["route", "-n", "get", "default"], timeout=4)["stdout"].strip()
    rec["scutil_nwi"] = util.run_cmd(["scutil", "--nwi"], timeout=4)["stdout"].strip()
    arp = util.run_cmd(["arp", "-an"], timeout=4)["stdout"].strip().splitlines()
    rec["arp"] = "\n".join(arp[:25])
    if gateway:
        rec["route_get"] += "\n\n$ arp -n " + gateway + "\n" + \
            util.run_cmd(["arp", "-n", gateway], timeout=3)["stdout"].strip()
    wifi = probe.sample_wifi()
    rec["wifi"] = "\n".join(f"{k}: {v}" for k, v in wifi.items()
                            if k not in ("_tick", "cached", "phymodes_supported", "other_networks"))
    return rec


def traceroute(incident_start_ts: float, target: str, max_hops: int = 12,
               timeout_s: float = 25.0) -> dict:
    r = util.run_cmd(
        ["traceroute", "-n", "-w", "1", "-q", "1", "-m", str(max_hops), target],
        timeout=timeout_s,
    )
    return {
        "ts": util.now_ts(),
        "kind": "traceroute",
        "label": f"断点追踪 → {target}",
        "incident_start": incident_start_ts,
        "target": target,
        "output": r["stdout"].strip() or r["stderr"].strip() or "(无输出)",
    }


def wifi_log_excerpt(minutes: int = 5) -> dict:
    """抓取最近的无线子系统日志摘要（只取计数与最后若干行，避免体量失控）。"""
    r = util.run_cmd(
        ["log", "show", "--last", f"{minutes}m", "--style", "compact",
         "--predicate", 'subsystem == "com.apple.wifi"'],
        timeout=30,
    )
    lines = [ln for ln in r["stdout"].splitlines() if ln.strip()]
    keywords = ("disassoc", "deauth", "roam", "Assoc", "scan", "reason", "link down", "BSSID")
    hit = [ln for ln in lines if any(k.lower() in ln.lower() for k in keywords)]
    return {
        "ts": util.now_ts(),
        "kind": "wifilog",
        "label": f"无线子系统日志摘要（近 {minutes} 分钟）",
        "incident_start": None,
        "output": f"总行数 {len(lines)}，命中关键字 {len(hit)} 行。末尾 40 行：\n"
                  + "\n".join(hit[-40:] or lines[-40:]),
    }
