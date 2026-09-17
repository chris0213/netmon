"""判定与事件归并：把探针快照翻译成人能看懂的故障结论。

分层判定优先级（从下往上，先命中先定性）：
    link_down  → 无线"未关联"／接口无 IP：本机到 AP 这一段断了
    lan_down   → 网关（ARP 表项）不可达：本机到路由器这一段断了
    wan_down   → 网关在、外网全灭：运营商／光猫／单位出口这一段断了
    dns_fail   → 外网通但解析失败：DNS 配置或 DNS 服务器问题
    degraded   → 通但延迟／丢包超阈值：无线干扰、负载高
    weak_signal→ 信号强度不足（可能尚未掉线）
    ok
"""

from __future__ import annotations

from collections import Counter, defaultdict

from . import util

STATUS_ORDER = [
    "link_down",
    "lan_down",
    "wan_down",
    "dns_fail",
    "degraded",
    "weak_signal",
    "ok",
]

STATUS_LABEL = {
    "ok": "正常",
    "weak_signal": "信号弱",
    "degraded": "延迟/丢包异常",
    "dns_fail": "DNS 解析失败",
    "wan_down": "外网中断",
    "lan_down": "网关不可达",
    "link_down": "无线链路断开",
}

STATUS_COLOR = {
    "ok": "#16a34a",
    "weak_signal": "#ca8a04",
    "degraded": "#ea580c",
    "dns_fail": "#0e7490",
    "wan_down": "#dc2626",
    "lan_down": "#b91c1c",
    "link_down": "#7f1d1d",
}

# 逐层排查知识库：结论 → 下一步动作
PLAYBOOK = {
    "link_down": {
        "meaning": "Mac 的 Wi-Fi 处于未关联状态，或接口没有拿到 IP。故障点在本机网卡与 AP 之间的无线关联环节。",
        "likely": ["AP 强制踢线/漫游切换失败", "Wi-Fi 省电导致网卡休眠", "DHCP 续租失败", "信号瞬间跌穿解调门限"],
        "checks": [
            ("查看无线子系统日志（关键证据）", "log show --last 30m --predicate 'subsystem == \"com.apple.wifi\"' --style compact | grep -Ei 'disassoc|deauth|roam|scan|reason'"),
            ("查看 DHCP 与接口事件", "log show --last 30m --predicate 'process == \"configd\" or process == \"dhcpd\" or eventMessage CONTAINS \"dhcp\"' --style compact | tail -50"),
            ("关闭 Wi-Fi 省电（临时验证）", "sudo /System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport prefs"),
            ("记录当前 AP 与信道", "system_profiler SPAirPortDataType | grep -A12 'Current Network'"),
            ("关闭「低数据模式」与私有地址轮换影响", "系统设置 > 无线局域网 > 详情 > 关闭「专用 Wi-Fi 地址」重测"),
        ],
    },
    "lan_down": {
        "meaning": "网关 ARP 表项失效，本机与路由器／AP 之间的一跳不通。",
        "likely": ["AP 与上行交换机链路抖动", "AP 过载或重启", "本机被 AP 踢下线但未重连", "ARP 缓存老化"],
        "checks": [
            ("观察 ARP 与邻居表", "arp -an | head -20"),
            ("连续性探测网关（同时开 3 个终端）", "ping -i 0.2 192.168.1.1"),
            ("查看 AP 侧关联日志（如有权限）", "登录无线控制器查看该 MAC 的关联/去关联记录"),
            ("检查接口错误计数是否增长", "netstat -ib | awk 'NR==1 || $1 ~ /^en/'"),
        ],
    },
    "wan_down": {
        "meaning": "网关可达但所有外网目标同时失联 —— 问题不在你的 Mac，在路由器到运营商这一段。",
        "likely": ["运营商线路闪断／割接", "光猫掉线或 PON 口失联", "路由器 WAN 口重播／PPPoE 掉线", "单位出口设备限流或会话耗尽"],
        "checks": [
            ("抓包确认是本机发不出去还是没人回", "sudo tcpdump -ni en0 -c 40 icmp and host 223.5.5.5"),
            ("追踪断点（看在哪一跳开始丢）", "traceroute -n -w 1 -q 1 -m 15 223.5.5.5"),
            ("查看路由器 WAN 状态与日志", "登录路由器后台 → 系统状态 → WAN 连接 / 系统日志"),
            ("向运营商报障时提供", "断网时刻 + 持续时长 + traceroute 结果（本工具报告可直接作为凭据）"),
        ],
    },
    "dns_fail": {
        "meaning": "外网连得通但域名解析不出来 —— 典型 DNS 配置或 DNS 服务器问题。",
        "likely": ["路由器下发的 DNS 不可用", "企业 DNS 服务器故障或策略拦截", "VPN 接管了 DNS 但在退出时没还原", "搜索域/后缀配置错误"],
        "checks": [
            ("查看当前生效的 DNS 服务器", "scutil --dns | grep nameserver | sort -u"),
            ("绕过本地 DNS 直查对比", "dig +short @223.5.5.5 www.baidu.com ; dig +short @119.29.29.29 www.baidu.com"),
            ("看系统解析器原始日志", "log show --last 20m --predicate 'process == \"mDNSResponder\"' --style compact | tail -50"),
            ("临时切到公共 DNS 验证", "networksetup -setdnsservers Wi-Fi 223.5.5.5 119.29.29.29"),
        ],
    },
    "degraded": {
        "meaning": "网络没断但延迟或丢包超过阈值，表现为卡顿、视频会议掉帧。",
        "likely": ["2.4GHz/5GHz 信道拥挤", "蓝牙/微波炉/USB3 干扰", "AP 负载均衡把你挂到了远端 AP", "本机或网关有大流量任务"],
        "checks": [
            ("看当前信道与周边 AP 密度", "system_profiler SPAirPortDataType | grep -E 'Channel|Signal|Noise|PHY Mode'"),
            ("对比不同信道的表现", "分别在 5GHz（149/44）与 2.4GHz（1/6/11）下观察延迟"),
            ("检查本机是否有异常大流量", "nettop -m route -n -l 1 | head -20"),
            ("查蓝牙是否与 Wi-Fi 抢频段", "system_profiler SPBluetoothDataType | head -20"),
        ],
    },
    "weak_signal": {
        "meaning": "信号强度偏弱，尚未断但已处于易掉线边缘。",
        "likely": ["离 AP 太远或有承重墙遮挡", "天线朝向不利", "AP 发射功率被调低"],
        "checks": [
            ("读取 RSSI / 噪声 / SNR 趋势", "本工具报告中的「无线信号」图表"),
            ("更换位置或改用 5GHz 小带宽重测", "system_profiler SPAirPortDataType | grep signal_noise"),
        ],
    },
}


# ------------------------------------------------------------------ 单点判定

def _wan_ratio(tick: dict) -> float | None:
    actors = [t for t in tick.get("icmp", []) if t.get("actor")]
    if not actors:
        return None
    total = sum(float(t.get("weight", 1.0)) for t in actors)
    good = sum(float(t.get("weight", 1.0)) for t in actors if t.get("ok"))
    return round(good / total, 3) if total else None


def _avg_rtt(tick: dict) -> float | None:
    vals = [t["rtt_avg"] for t in tick.get("icmp", [])
            if t.get("actor") and t.get("ok") and isinstance(t.get("rtt_avg"), (int, float))]
    return util.safe_avg(vals)


def _worst_rtt(tick: dict) -> tuple[float | None, str | None]:
    """取"最差的那条路径"。判定是否劣化要看最差路径，而不是所有目标的均值 ——
    单条路径劣化会被均值稀释掉，这是很容易踩的坑。"""
    cands = [(t["rtt_avg"], t.get("name") or t["host"]) for t in tick.get("icmp", [])
             if t.get("actor") and t.get("ok") and isinstance(t.get("rtt_avg"), (int, float))]
    if not cands:
        return None, None
    v, n = max(cands)
    return round(float(v), 2), n


def _worst_loss(tick: dict) -> tuple[float | None, str | None]:
    cands = [(t["loss_pct"], t.get("name") or t["host"]) for t in tick.get("icmp", [])
             if t.get("actor") and isinstance(t.get("loss_pct"), (int, float))]
    if not cands:
        return None, None
    v, n = max(cands)
    return round(float(v), 1), n


def _loss(tick: dict) -> float | None:
    vals = [t["loss_pct"] for t in tick.get("icmp", [])
            if t.get("actor") and isinstance(t.get("loss_pct"), (int, float))]
    return util.safe_avg(vals)


def _jitter(tick: dict) -> float | None:
    vals = [t["rtt_stddev"] for t in tick.get("icmp", [])
            if t.get("actor") and t.get("ok") and isinstance(t.get("rtt_stddev"), (int, float))]
    return util.safe_avg(vals)


def classify(tick: dict, cfg: dict) -> tuple[str, list[str], dict]:
    """返回 (状态, 原因列表, 关键指标)。"""
    th = cfg["thresholds"]
    reasons: list[str] = []

    iface_info = tick.get("iface_info") or {}
    has_ip = bool(iface_info.get("ipv4"))
    wifi = tick.get("wifi") or {}
    wifi_known = wifi.get("connected") is not None
    wifi_connected = wifi.get("connected")

    metrics = {
        "wan_ratio": _wan_ratio(tick),
        "rtt_avg": _avg_rtt(tick),
        "rtt_max": _worst_rtt(tick)[0],
        "rtt_max_host": _worst_rtt(tick)[1],
        "loss_pct": _loss(tick),
        "loss_max": _worst_loss(tick)[0],
        "loss_max_host": _worst_loss(tick)[1],
        "jitter_ms": _jitter(tick),
        "rssi_dbm": wifi.get("rssi_dbm"),
        "snr_db": wifi.get("snr_db"),
        "http_ok": any(h.get("ok") for h in tick.get("http", [])),
        "dns_sys_ok": any(d.get("ok") for d in tick.get("dns", [])),
        "dns_ext_ok": any(d.get("ok") for d in tick.get("dns_ext", [])),
        "gw_alive": (tick.get("gateway") or {}).get("alive"),
        "gw_method": (tick.get("gateway") or {}).get("method"),
    }

    # 1. 链路层
    if not has_ip:
        reasons.append("接口无 IPv4 地址")
    if wifi_known and not wifi_connected:
        reasons.append("Wi-Fi 未关联到 AP")
    if not has_ip or (wifi_known and not wifi_connected):
        return "link_down", reasons, metrics

    # 2. 网关层
    gw_alive = metrics["gw_alive"]
    if gw_alive is False:
        reasons.append("网关三路存活信号（ARP / ICMP / TCP）全部无响应")
        return "lan_down", reasons, metrics

    # 3. 外网层
    ratio = metrics["wan_ratio"]
    wan_ok = metrics["http_ok"] or (ratio is not None and ratio >= float(th.get("quorum_ratio", 0.5)))
    if not wan_ok:
        bad = [t["name"] for t in tick.get("icmp", []) if t.get("actor") and not t.get("ok")]
        reasons.append(f"外网目标全部失联（{ '、'.join(bad) or '无有效目标' }）")
        if not metrics["http_ok"]:
            reasons.append("HTTP 应用层探针同样失败")
        return "wan_down", reasons, metrics

    # 4. DNS 层
    sys_ok, ext_ok = metrics["dns_sys_ok"], metrics["dns_ext_ok"]
    if not sys_ok:
        if ext_ok:
            reasons.append("系统解析器失败，但直连 223.5.5.5 成功 → 本机 DNS 配置问题")
        else:
            reasons.append("系统解析器与外部 DNS 同时失败")
        return "dns_fail", reasons, metrics

    # 5. 质量层：用"最差路径"判定，避免被其他正常目标的均值稀释
    rtt = metrics["rtt_max"]
    rtt_host = metrics["rtt_max_host"]
    loss = metrics["loss_max"]
    loss_host = metrics["loss_max_host"]
    jitter = metrics["jitter_ms"]
    rssi = metrics["rssi_dbm"]
    rtt_tag = f"{rtt_host} " if rtt_host else ""

    bad_rtt = rtt is not None and rtt >= float(th.get("rtt_bad_ms", 400))
    warn_rtt = rtt is not None and rtt >= float(th.get("rtt_warn_ms", 150))
    bad_loss = loss is not None and loss >= float(th.get("loss_warn_pct", 20))
    bad_jitter = jitter is not None and jitter >= float(th.get("jitter_warn_ms", 60))
    bad_rssi = rssi is not None and rssi <= int(th.get("rssi_bad_dbm", -80))
    warn_rssi = rssi is not None and rssi <= int(th.get("rssi_warn_dbm", -70))

    if bad_rtt:
        reasons.append(f"{rtt_tag}平均延迟 {rtt:.0f} ms 超过严重阈值")
    if bad_loss and loss is not None:
        reasons.append(f"{loss_host or ''} 丢包率 {loss:.0f}% 偏高".strip())
    if bad_jitter:
        reasons.append(f"抖动 {jitter:.0f} ms 偏大")
    if bad_rssi:
        reasons.append(f"信号 {rssi} dBm 已低于可用门限")

    if bad_rtt or bad_loss or bad_jitter or bad_rssi:
        return "degraded", reasons, metrics

    if warn_rtt:
        reasons.append(f"{rtt_tag}平均延迟 {rtt:.0f} ms 偏高")
    if warn_rssi:
        reasons.append(f"信号 {rssi} dBm 偏弱")
    if warn_rtt or warn_rssi:
        return "weak_signal", reasons, metrics

    return "ok", reasons, metrics


# ------------------------------------------------------------------ 事件归并

def build_incidents(ticks: list[dict], cfg: dict) -> list[dict]:
    """把逐点状态归并成断网事件。gap 小于 merge_gap 秒的同类事件会被合并。"""
    if not ticks:
        return []
    interval = float(cfg.get("interval_seconds", 30))
    merge_gap = max(interval * 3, 90.0)

    annotated: list[tuple[dict, str, list[str], dict]] = []
    for t in ticks:
        st, rs, mt = classify(t, cfg)
        annotated.append((t, st, rs, mt))

    runs: list[dict] = []
    cur: dict | None = None
    for t, st, rs, mt in annotated:
        if st == "ok":
            if cur is not None:
                cur["last_bad_ts"] = cur["_last_bad"]
                runs.append(cur)
                cur = None
            continue
        if cur is None:
            cur = {
                "start_ts": t["ts"], "_last_bad": t["ts"], "last_bad_ts": t["ts"],
                "statuses": Counter(), "reasons": [], "ticks": [], "metrics": [],
                "end_ts": None,
            }
        cur["statuses"][st] += 1
        cur["_last_bad"] = t["ts"]
        for r in rs:
            if r not in cur["reasons"]:
                cur["reasons"].append(r)
        cur["ticks"].append({
            "ts": t["ts"], "iso": t["iso"], "status": st, "reasons": rs, "metrics": mt,
        })
        cur["metrics"].append(mt)
    if cur is not None:
        runs.append(cur)

    # 合并相邻的同类事件（间隔小于 merge_gap）
    merged: list[dict] = []
    for r in runs:
        if merged:
            prev = merged[-1]
            gap = r["start_ts"] - prev["last_bad_ts"]
            same_family = (
                prev["statuses"].most_common(1)[0][0] == r["statuses"].most_common(1)[0][0]
                or (prev["statuses"].most_common(1)[0][0] in ("link_down", "lan_down")
                    and r["statuses"].most_common(1)[0][0] in ("link_down", "lan_down"))
            )
            if gap <= merge_gap and same_family:
                prev["statuses"].update(r["statuses"])
                for x in r["reasons"]:
                    if x not in prev["reasons"]:
                        prev["reasons"].append(x)
                prev["ticks"] += r["ticks"]
                prev["metrics"] += r["metrics"]
                prev["last_bad_ts"] = r["last_bad_ts"]
                continue
        merged.append(r)

    out: list[dict] = []
    span_end = max(t["ts"] for t in ticks) + interval
    for idx, r in enumerate(merged):
        start = r["start_ts"]
        end = min(r["last_bad_ts"] + interval, span_end)
        dominant = r["statuses"].most_common(1)[0][0]
        duration = max(end - start, interval)
        rssis = [m.get("rssi_dbm") for m in r["metrics"] if isinstance(m.get("rssi_dbm"), (int, float))]
        rtts = [m.get("rtt_avg") for m in r["metrics"] if isinstance(m.get("rtt_avg"), (int, float))]
        rtt_maxs = [m.get("rtt_max") for m in r["metrics"] if isinstance(m.get("rtt_max"), (int, float))]
        losses = [m.get("loss_pct") for m in r["metrics"] if isinstance(m.get("loss_pct"), (int, float))]
        loss_maxs = [m.get("loss_max") for m in r["metrics"] if isinstance(m.get("loss_max"), (int, float))]
        out.append({
            "id": f"inc-{idx + 1:03d}",
            "start_ts": start,
            "end_ts": end,
            "start": util.iso(start),
            "end": util.iso(end),
            "start_hhmm": util.hhmm(start),
            "end_hhmm": util.hhmm(end),
            "day": util.day_str(start),
            "duration_s": round(duration, 1),
            "duration_human": util.human_duration(duration),
            "class": dominant,
            "classes": dict(r["statuses"]),
            "label": STATUS_LABEL.get(dominant, dominant),
            "reasons": r["reasons"],
            "tick_count": len(r["ticks"]),
            # 单一采样点异常 → 单点瞬断。两次以上就该当作持续性故障看待，不要归为抖动。
            "is_bleep": len(r["ticks"]) <= 1 or duration <= interval * 1.6,
            "rssi_min": min(rssis) if rssis else None,
            "rssi_avg": util.safe_avg(rssis),
            "rtt_avg": util.safe_avg(rtts),
            "rtt_max": max(rtt_maxs) if rtt_maxs else None,
            "loss_max": max(loss_maxs) if loss_maxs else (max(losses) if losses else None),
            "ticks": r["ticks"],
        })
    return out


# ------------------------------------------------------------------ 汇总与结论

def summarize(ticks: list[dict], incidents: list[dict], cfg: dict) -> dict:
    interval = float(cfg.get("interval_seconds", 30))
    if not ticks:
        return {"empty": True}
    span = max(t["ts"] for t in ticks) - min(t["ts"] for t in ticks) + interval
    downtime = sum(i["duration_s"] for i in incidents)
    by_class = Counter(i["class"] for i in incidents)
    hard_classes = {"link_down", "lan_down", "wan_down", "dns_fail"}
    hard_incs = [i for i in incidents if i["class"] in hard_classes]
    hard_downtime = sum(i["duration_s"] for i in hard_incs)

    rtts, losses, rssis, snrs, rtt_worsts = [], [], [], [], []
    for t in ticks:
        _, _, m = classify(t, cfg)
        if m["rtt_avg"] is not None:
            rtts.append(m["rtt_avg"])
        if m.get("rtt_max") is not None:
            rtt_worsts.append(m["rtt_max"])
        if m["loss_pct"] is not None:
            losses.append(m["loss_pct"])
        if m["rssi_dbm"] is not None:
            rssis.append(m["rssi_dbm"])
        if m["snr_db"] is not None:
            snrs.append(m["snr_db"])

    # 分小时统计最差时段
    hour_bad: dict[int, int] = defaultdict(int)
    hour_total: dict[int, int] = defaultdict(int)
    for t in ticks:
        h = util.hour_of(t["ts"])
        st, _, _ = classify(t, cfg)
        hour_total[h] += 1
        if st != "ok":
            hour_bad[h] += 1

    worst_hour = None
    if hour_bad:
        hh = max(hour_bad.items(), key=lambda kv: kv[1])[0]
        worst_hour = {"hour": hh, "bad": hour_bad[hh], "total": hour_total.get(hh, 0),
                      "rate": util.pct(hour_bad[hh], hour_total.get(hh, 1))}

    return {
        "empty": False,
        "tick_count": len(ticks),
        "start_ts": min(t["ts"] for t in ticks),
        "end_ts": max(t["ts"] for t in ticks),
        "start": util.iso(min(t["ts"] for t in ticks)),
        "end": util.iso(max(t["ts"] for t in ticks)),
        "span_s": round(span, 1),
        "span_human": util.human_duration(span),
        "uptime_pct": util.pct(span - downtime, span),
        "hard_uptime_pct": util.pct(span - hard_downtime, span),
        "incident_count": len(incidents),
        "hard_incident_count": len(hard_incs),
        "bleep_count": sum(1 for i in incidents if i["is_bleep"]),
        "down_total_s": round(downtime, 1),
        "down_total_human": util.human_duration(downtime),
        "hard_down_total_human": util.human_duration(hard_downtime),
        "by_class": dict(by_class),
        "by_class_label": {STATUS_LABEL.get(k, k): v for k, v in by_class.items()},
        "rtt_avg": util.safe_avg(rtts),
        "rtt_p95": util.safe_p95(rtts),
        "rtt_worst": max(rtt_worsts) if rtt_worsts else None,
        "loss_avg": util.safe_avg(losses),
        "loss_p95": util.safe_p95(losses),
        "rssi_avg": util.safe_avg(rssis),
        "rssi_min": min(rssis) if rssis else None,
        "snr_avg": util.safe_avg(snrs),
        "worst_hour": worst_hour,
    }


def verdict(summary: dict, incidents: list[dict], cfg: dict) -> dict:
    """自动结论：先给判断，再给下一步。"""
    if summary.get("empty"):
        return {"headline": "暂无采集数据。", "bullets": [], "focus": "ok", "actions": []}

    if summary["incident_count"] == 0:
        bullets = [
            f"整段观测期内 {summary['tick_count']} 次采样全部正常，未捕获到断网事件。",
        ]
        if summary.get("rtt_avg") is not None:
            bullets.append(f"平均延迟 {summary['rtt_avg']} ms，P95 {summary.get('rtt_p95')} ms。")
        if summary.get("rssi_min") is not None:
            bullets.append(
                f"无线信号最低 {summary['rssi_min']} dBm，均值 {summary.get('rssi_avg')} dBm。"
            )
        return {
            "headline": "未复现故障 —— 说明断连是间歇性的，需要延长观测窗口。",
            "bullets": bullets,
            "focus": "ok",
            "actions": [
                "把采样间隔调到 10-15 秒（config.json → interval_seconds），提高捕获瞬断的概率。",
                "在疑似断网的时段保持工具常驻运行，至少覆盖 24 小时（含早晚高峰）。",
                "如果只在特定应用（如 VPN、会议软件）下断，问题更可能在应用层而非链路层。",
            ],
        }

    # 主因按"累计时长"而非"次数"判定：一次 5 分钟的断网比 10 次 30 秒抖动更值得先解决
    dur_by_class: dict[str, float] = defaultdict(float)
    cnt_by_class: dict[str, int] = defaultdict(int)
    for i in incidents:
        dur_by_class[i["class"]] += i["duration_s"]
        cnt_by_class[i["class"]] += 1
    dominant = max(dur_by_class.items(), key=lambda kv: kv[1])[0]
    distinct = len(dur_by_class)
    pb = PLAYBOOK.get(dominant, PLAYBOOK["degraded"])

    bullets: list[str] = []
    if distinct >= 3:
        headline = (f"观测到 {distinct} 类不同形态的异常（共 {len(incidents)} 次、"
                    f"累计 {summary['down_total_human']}）—— 这不是单一原因，需要分别处置。")
        bullets.append("按累计时长排序，各类异常的处置方向如下：")
        for cls, dur in sorted(dur_by_class.items(), key=lambda kv: -kv[1]):
            h = PLAYBOOK.get(cls, PLAYBOOK["degraded"])["meaning"].split("。")[0]
            bullets.append(
                f"{STATUS_LABEL.get(cls, cls)}：{cnt_by_class[cls]} 次 / "
                f"{util.human_duration(dur)} —— {h}。"
            )
    else:
        headline = (f"共捕获 {summary['incident_count']} 次异常，累计 {summary['down_total_human']}；"
                    f"主因指向「{STATUS_LABEL.get(dominant, dominant)}」。")
        bullets.append(pb["meaning"])
        parts = [f"{STATUS_LABEL.get(k, k)} {v} 次" for k, v in
                 sorted(cnt_by_class.items(), key=lambda kv: -kv[1])]
        bullets.append("事件构成：" + "、".join(parts) + "。")

    if summary.get("bleep_count"):
        bullets.append(
            f"其中 {summary['bleep_count']} 次为单点采样即恢复的瞬断 —— "
            "这类最影响体感、也最难抓，说明链路存在亚秒到数十秒级的短暂失联。"
        )
    if summary.get("worst_hour"):
        w = summary["worst_hour"]
        bullets.append(
            f"高发时段：{w['hour']:02d}:00-{w['hour'] + 1:02d}:00（异常占比 {w['rate']}%），"
            "可用于对照单位网络的分时段策略或邻居 AP 的负载规律。"
        )
    if summary.get("rtt_avg") is not None:
        def _r(v) -> str:
            return f"{v:.1f}" if isinstance(v, (int, float)) else "-"
        bullets.append(
            f"延迟：均值 {_r(summary.get('rtt_avg'))} ms / P95 {_r(summary.get('rtt_p95'))} ms / "
            f"最差路径 {_r(summary.get('rtt_worst'))} ms。"
        )
    if summary.get("rssi_min") is not None:
        r = summary.get("rssi_avg")
        bullets.append(
            f"无线信号：最低 {summary['rssi_min']} dBm / 均值 {r:.1f} dBm / SNR 均值 "
            f"{summary.get('snr_avg')} dB。" if isinstance(r, (int, float))
            else f"无线信号：最低 {summary['rssi_min']} dBm，SNR 均值 {summary.get('snr_avg')} dB。"
        )
    if distinct < 3:
        bullets.append("最可能的原因：" + "；".join(pb["likely"]) + "。")

    if distinct >= 3:
        actions: list[str] = []
        for cls, _ in sorted(dur_by_class.items(), key=lambda kv: -kv[1])[:2]:
            for t, c in PLAYBOOK.get(cls, PLAYBOOK["degraded"])["checks"][:3]:
                actions.append(f"[{STATUS_LABEL.get(cls, cls)}] {t}：{c}")
    else:
        actions = [f"{t}：{c}" for t, c in pb["checks"]]
    return {"headline": headline, "bullets": bullets, "focus": dominant, "actions": actions}
