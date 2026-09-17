"""配置加载。默认值已按中国大陆网络环境与 macOS 实际情况标定。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"

DEFAULTS: dict = {
    "interval_seconds": 30,
    "retention_days": 30,

    # 网关：企业/家用路由器普遍屏蔽 ICMP，因此以 ARP 表项作为主判据。
    # gw_icmp: "auto" 表示先试 ICMP，失败则回落到 ARP，不把 ICMP 失败当故障。
    "gateway": {
        "enabled": True,
        "method": "arp_primary",
    },

    # 外网 ICMP 目标。1.1.1.1 / 8.8.8.8 在国内不稳定，默认不参与判定。
    "icmp_targets": [
        {"name": "阿里DNS", "host": "223.5.5.5", "weight": 1.0, "actor": True, "tcp_port": 443},
        {"name": "百度DNS", "host": "180.76.76.76", "weight": 1.0, "actor": True, "tcp_port": None},
        {"name": "腾讯DNS", "host": "119.29.29.29", "weight": 0.6, "actor": True, "tcp_port": None},
        {"name": "GoogleDNS", "host": "8.8.8.8", "weight": 0.4, "actor": False, "tcp_port": None},
    ],

    # DNS 解析探针
    "dns": {
        "names": ["www.baidu.com", "www.aliyun.com"],
        "external_servers": ["223.5.5.5"],
        "timeout_seconds": 3,
    },

    # 真实端到端 HTTP 探针（204 端点体积最小，不消耗流量）
    "http": {
        "urls": [
            "http://connect.rom.miui.com/generate_204",
            "http://wifi.vivo.com.cn/generate_204",
        ],
        "timeout_seconds": 4,
    },

    # 判定阈值
    "thresholds": {
        "rtt_warn_ms": 150,
        "rtt_bad_ms": 400,
        "loss_warn_pct": 20.0,
        "jitter_warn_ms": 60.0,
        "rssi_warn_dbm": -70,
        "rssi_bad_dbm": -80,
        "noise_bad_dbm": -85,
        "quorum_ratio": 0.5,          # 加权外网可达比例低于此值即判 WAN 故障
        "degraded_streak": 2,          # 连续 N 次异常才登记为事件
        "bleep_ticks": 1,              # 事件持续 <= N 个采样点标记为"瞬断抖动"
    },

    "wifi": {
        "enabled": True,
        "sample_every_ticks": 2,       # system_profiler 约 1.5s，不必每轮都采
        "scan_neighbors": False,       # 采集周边 AP 数量（更慢）
    },

    # 事件发生时自动取证
    "forensics": {
        "traceroute_on_incident": True,
        "traceroute_target": "223.5.5.5",
        "traceroute_max_hops": 12,
        "traceroute_timeout_seconds": 25,
        "cooldown_seconds": 600,
        "capture_snapshot": True,
    },

    "interface": "auto",               # auto = 跟随默认路由接口
    "timezone_note": "",
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Path | None = None) -> dict:
    p = Path(path) if path else CONFIG_PATH
    user: dict = {}
    if p.exists():
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            user = {}
    cfg = _deep_merge(DEFAULTS, user)
    cfg["_config_path"] = str(p)
    cfg["_root"] = str(ROOT)
    return cfg


def write_default_config(path: Path | None = None, force: bool = False) -> Path:
    p = Path(path) if path else CONFIG_PATH
    if p.exists() and not force:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(DEFAULTS, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return p
