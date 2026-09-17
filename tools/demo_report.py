"""用合成数据生成一份"含故障"的样例报告，用于校验归因与图表渲染。

用途：
  1. 开发期回归：不依赖真实断网就能验证 4 类故障的判定与渲染路径。
  2. 展示用途：让使用者事先知道"报告长什么样、断网时能看到什么"。

运行：python3 -m tools.demo_report
产出：reports/demo-report.html（不会写入 data/）
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netmon import analyze, report, util  # noqa: E402
from netmon.config import REPORT_DIR, load_config  # noqa: E402


def _base_tick(ts: float, idx: int) -> dict:
    jitter = random.uniform(0.7, 1.35)
    rtt = round(8.4 * jitter, 2)
    gw_rtt = round(2.1 * jitter, 2)
    rssi = -39 + random.randint(-4, 4)
    return {
        "ts": ts,
        "iso": util.iso(ts),
        "tick": idx,
        "iface": "en0",
        "route_iface": "en0",
        "route": {"gateway": "192.168.1.1", "interface": "en0"},
        "iface_info": {"name": "en0", "ipv4": ["192.168.1.20"], "status": "active", "up": True},
        "gateway": {
            "host": "192.168.1.1",
            "arp": {"resolved": True, "mac": "00:11:22:33:44:55"},
            "icmp": {"ok": True, "rtt_avg": gw_rtt, "loss_pct": 0.0, "rtt_stddev": 0.3,
                     "sent": 2, "recv": 2},
            "tcp": {"alive": True},
            "alive": True,
            "method": "arp",
            "signals": ["arp"],
        },
        "icmp": [
            {"name": "阿里DNS", "host": "223.5.5.5", "weight": 1.0, "actor": True,
             "ok": True, "rtt_avg": rtt, "loss_pct": 0.0, "rtt_stddev": 0.6, "sent": 2, "recv": 2},
            {"name": "百度DNS", "host": "180.76.76.76", "weight": 1.0, "actor": True,
             "ok": True, "rtt_avg": round(rtt + 0.6, 2), "loss_pct": 0.0, "rtt_stddev": 0.5,
             "sent": 2, "recv": 2},
            {"name": "腾讯DNS", "host": "119.29.29.29", "weight": 0.6, "actor": True,
             "ok": True, "rtt_avg": 84.2, "loss_pct": 0.0, "rtt_stddev": 0.2, "sent": 2, "recv": 2},
        ],
        "tcp": [{"host": "223.5.5.5", "port": 443, "ok": True, "ms": 18.4, "error": None}],
        "dns": [{"name": "www.baidu.com", "server": "system", "ok": True, "answers": ["110.242.69.21"],
                 "status": "NOERROR", "ms": 28.0, "error": None}],
        "dns_ext": [{"name": "www.baidu.com", "server": "223.5.5.5", "ok": True,
                     "answers": ["110.242.69.21"], "status": "NOERROR", "ms": 25.0, "error": None}],
        "http": [{"url": "http://connect.rom.miui.com/generate_204", "ok": True, "status": 204,
                  "ms": 52.0, "error": None}],
        "wifi": {"device": "en0", "connected": True, "rssi_dbm": rssi, "noise_dbm": -96,
                 "snr_db": rssi + 96, "channel": 149, "band": "5GHz", "width": "20MHz",
                 "phymode": "802.11ax", "tx_rate_mbps": 286, "security": "wpa2_enterprise"},
        "counters": {"ipkts": 100000 + idx * 120, "ierrs": 0, "ibytes": 0, "opkts": 0,
                     "oerrs": 0, "obytes": 0, "coll": 0},
        "nwi": {"primary_ipv4": True, "ipv6_only": False},
        "elapsed_ms": 2600.0,
    }


def _wan_down(t: dict) -> None:
    for x in t["icmp"]:
        x.update({"ok": False, "rtt_avg": None, "loss_pct": 100.0, "recv": 0})
    t["http"][0].update({"ok": False, "status": None, "error": "URLError"})
    t["dns"][0].update({"ok": False, "error": "timeout", "answers": []})
    t["dns_ext"][0].update({"ok": False, "error": "timeout", "answers": []})
    t["tcp"][0].update({"ok": False, "error": "timeout"})


def _dns_down(t: dict) -> None:
    t["dns"][0].update({"ok": False, "error": "SERVFAIL", "answers": []})


def _lan_down(t: dict) -> None:
    _wan_down(t)
    t["gateway"].update({"alive": False, "method": "none", "signals": [],
                         "arp": {"resolved": False, "mac": None}})
    t["gateway"]["icmp"].update({"ok": False, "rtt_avg": None, "loss_pct": 100.0})
    t["gateway"]["tcp"]["alive"] = False


def _link_down(t: dict) -> None:
    _lan_down(t)
    t["iface_info"].update({"ipv4": [], "status": "inactive"})
    t["wifi"].update({"connected": False, "rssi_dbm": None, "snr_db": None, "tx_rate_mbps": None})


def _degraded(t: dict) -> None:
    for x in t["icmp"]:
        if x["host"] == "223.5.5.5":
            x.update({"rtt_avg": 320.0, "loss_pct": 33.3, "rtt_stddev": 95.0})
    t["wifi"].update({"rssi_dbm": -74, "snr_db": 22})


def build() -> list[dict]:
    random.seed(20260917)
    ticks: list[dict] = []
    interval = 30.0
    t0 = util.now_ts() - 6 * 3600
    n = int(6 * 3600 / interval)
    # 在时间轴上放 5 个故障窗口：(起始比例, 持续点数, 类型)
    plan = [
        (0.14, 4, _wan_down),        # 2 分钟外网中断
        (0.31, 1, _dns_down),        # 单点 DNS 失败（瞬断）
        (0.42, 3, _degraded),        # 1.5 分钟高延迟
        (0.63, 5, _lan_down),        # 2.5 分钟网关不可达
        (0.86, 2, _link_down),       # 1 分钟无线掉线
    ]
    events: dict[int, callable] = {}
    for ratio, dur, fn in plan:
        start = int(n * ratio)
        for k in range(dur):
            events[start + k] = fn
    for i in range(n):
        t = _base_tick(t0 + i * interval, i)
        fn = events.get(i)
        if fn:
            fn(t)
        ticks.append(t)
    return ticks


def main() -> int:
    cfg = load_config()
    cfg["interval_seconds"] = 30.0
    ticks = build()
    incidents = analyze.build_incidents(ticks, cfg)
    env = {
        "host": "MacBook Pro · macOS 15（示例环境）",
        "interface": "en0", "gateway": "192.168.1.1",
        "nameservers": ["223.5.5.5", "119.29.29.29"],
        "wifi_card": "Apple 无线网卡（5GHz 802.11ax）", "captured_at": util.iso(util.now_ts()),
    }
    html = report.render(cfg, ticks, incidents, [], env, title_suffix="（样例数据）")
    out = util.ensure_dir(REPORT_DIR) / "demo-report.html"
    out.write_text(html, encoding="utf-8")
    s = analyze.summarize(ticks, incidents, cfg)
    print(f"合成采样点 {len(ticks)}，事件 {len(incidents)} 次，可用率 {s['uptime_pct']}%")
    for i in incidents:
        print(f"  {i['start_hhmm']}  {i['label']:<10} {i['duration_human']:<9}"
              f"{'瞬断' if i['is_bleep'] else ''}  {'；'.join(i['reasons'])[:70]}")
    vd = analyze.verdict(s, incidents, cfg)
    print("\n结论：", vd["headline"])
    for b in vd["bullets"]:
        print("  -", b)
    print(f"\n样例报告：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
