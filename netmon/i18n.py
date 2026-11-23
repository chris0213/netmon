"""轻量国际化：中文原文作 key，英文查表。

设计约束：
  - 零第三方依赖，纯标准库。
  - 中文（默认）模式下 t() 原样返回入参，保证既有行为零回归。
  - 语言由 config.json 的 "language" 字段决定（zh / en），也可运行时切换。
  - 支持 {name} 占位符：t("采样间隔 {n} 秒", n=30)。
  - 未翻译的 key 回退原文（中文），绝不抛异常、绝不返回空串。

用法：
    from . import i18n
    i18n.t("启动监控")                       # 中→"启动监控" 英→"Start Monitoring"
    i18n.t("采样 {n} 点", n=37)              # 带占位符
    i18n.set_language("en")                   # 运行期切换（GUI 无需重启）
"""

from __future__ import annotations

import os

_LANG = "zh"          # 进程内当前语言；默认中文
_VALID = ("zh", "en")


def set_language(lang: str | None) -> None:
    """设置当前语言。无效值一律回落到中文，避免拼错导致整界面空白。"""
    global _LANG
    if lang and str(lang).lower().startswith("en"):
        _LANG = "en"
    else:
        _LANG = "zh"


def get_language() -> str:
    return _LANG


def is_en() -> bool:
    return _LANG == "en"


def is_zh() -> bool:
    return _LANG == "zh"


def init_from_config(cfg: dict | None = None) -> None:
    """用配置里的 language 字段初始化；支持环境变量 NETMON_LANG 覆盖。"""
    env = os.environ.get("NETMON_LANG")
    if env:
        set_language(env)
        return
    if cfg is not None:
        set_language(cfg.get("language"))


# --------------------------------------------------------------------- 词库
# key = 中文原文；value = 英文。占位符名与调用处保持一致。
# 专有名词（NetMon / DNS / Wi-Fi / ARP / ICMP / TCP / RSSI / SNR / RTT 等）不翻译。
_EN: dict[str, str] = {
    # ---- 通用状态 / 胶囊 ----
    "监控异常": "Monitoring Issue",
    "监控运行中": "Monitoring",
    "监控已停止": "Monitoring Stopped",
    "未采集": "Not sampled",
    "暂无数据": "No data",
    "未知目标": "Unknown target",
    "未知错误": "Unknown error",
    "无": "None",
    "无响应": "No reply",
    "打开": "Open",
    "关闭": "Close",
    "取消": "Cancel",
    "卸载": "Uninstall",
    "确定": "OK",
    "是": "Yes",
    "否": "No",

    # ---- 自检详情 ----
    "自检详情": "Self-Check Details",
    "自检结果：{label}": "Self-check result: {label}",
    "本轮耗时 ": "Elapsed this round ",
    "判定概览": "Verdict Overview",
    "状态": "Status",
    "原因": "Reason",
    "各项指标正常": "All indicators normal",
    "采样时间": "Sampled at",
    "耗时": "Elapsed",
    "链路 / 网关": "Link / Gateway",
    "活动接口": "Active interface",
    "默认网关": "Default gateway",
    "无（判定不可达）": "None (unreachable)",
    "存活信号": "Alive signals",
    "无表项": "No ARP entry",
    "有回显": "Echo reply",
    "被屏蔽 / 无响应": "Blocked / no reply",
    "网关 ICMP": "Gateway ICMP",
    "有连接": "Connected",
    "无连接": "No connection",
    "网关 TCP": "Gateway TCP",
    "目标": "Target",
    "（不参与判定）": " (not scored)",
    "平均 {rtt} ms · 丢包 {loss}%": "Avg {rtt} ms · loss {loss}%",
    "无响应 · 丢包 {loss}%": "No reply · loss {loss}%",
    "失败 · {error}": "Failed · {error}",
    "系统 DNS": "System DNS",
    "指定 DNS": "Specified DNS",
    "网卡 DNS": "Interface DNS",
    "DNS 解析": "DNS Resolution",
    "未采集 · {error}": "Not sampled · {error}",
    "不可用": "Unavailable",
    "有应答": "Responded",
    "无应答": "No answer",
    "提示": "Note",
    "HTTP 可达（{n} 个目标）": "HTTP Reachability ({n} targets)",
    "外网探测（{n} ICMP / {m} TCP）": "External Probes ({n} ICMP / {m} TCP)",
    "无线 / 接口计数": "Wi-Fi / Interface Counters",
    "无线": "Wi-Fi",
    "信号强度": "Signal strength",
    "噪声": "Noise",
    "信噪比": "SNR",
    "发射速率": "TX rate",
    "收包/错包": "RX / errors",
    "发包/错包": "TX / errors",

    # ---- 卸载 ----
    "卸载 NetMon": "Uninstall NetMon",
    "勾选项会被移入废纸篓，可以随时拖回来；不会直接抹掉。": "Selected items will be moved to Trash — you can restore them anytime; nothing is permanently deleted.",
    "停止并移除后台服务": "Stop and remove background service",
    "{path}（必选，否则它会在每次登录时尝试拉起已删除的应用）": "{path} (required — otherwise it will try to launch the deleted app on every login)",
    "删除采样数据与报告": "Remove sampled data and reports",
    "删除应用本体": "Remove the app itself",
    "当前是以源码方式运行，不涉及删除应用本体。": "Running from source — the app itself will not be removed.",
    "确认卸载": "Confirm Uninstall",
    "将把所选内容移入废纸篓：\n\n": "The following will be moved to Trash:\n\n",
    "· 全部采样数据与报告\n": "· All sampled data and reports\n",
    "· NetMon 应用本体\n": "· The NetMon application\n",
    "\n文件仍可从废纸篓恢复。确定继续吗？": "\nItems can still be restored from Trash. Continue?",
    "卸载完成": "Uninstall Complete",

    # ---- 主界面 ----
    "网哨 NetMon": "NetMon",
    "网络断连监控与自动归因": "Network outage monitoring & auto-diagnosis",
    "进程 PID": "Process PID",
    "采样间隔": "Sampling Interval",
    "数据目录": "Data Directory",
    "今日暂无采样": "No samples today",
    "，非实时": " — not live",
    "正在读取数据…": "Loading data…",
    "采样点": "Samples",
    "硬断网可用率": "Hard-Outage Uptime",
    "异常事件": "Incidents",
    "启动监控": "Start Monitoring",
    "停止监控": "Stop Monitoring",
    "生成报告": "Generate Report",
    "报告文件夹": "Report Folder",
    "立即自检": "Run Self-Check",
    "清除数据": "Clear Data",
    "最近事件": "Recent Incidents",
    "时间": "Time",
    "类型": "Type",
    "时长": "Duration",
    "暂无异常事件": "No incidents",
    "卸载 NetMon…": "Uninstall NetMon…",
    "launchd 常驻服务": "launchd background service",
    "前台采集进程": "Foreground collector",
    "未运行": "Not running",
    "暂无采集数据 · 启动监控后开始记录": "No data yet · start monitoring to begin recording",
    "数据日期 {day} · 无有效采样": "Data date {day} · no valid samples",
    "数据日期 {day} · {start}–{end}": "Data date {day} · {start}–{end}",
    "采集窗口 {start}–{end}": "Collection window {start}–{end}",
    "样本不足，仅供参考": "Insufficient samples — for reference only",
    "目标 ≥ {target}%": "Target ≥ {target}%",
    "硬断网 ": "Hard outage ",
    "   ·   质量异常 ": "   ·   Quality issues ",
    "暂无数据，启动监控后事件会显示在这里。": "No data — incidents will appear here after monitoring starts.",
    "当前显示的是历史数据（日期 {day}），": "Showing historical data (date {day}),",
    "硬断网": "Hard outage",
    "质量异常": "Quality issue",
    "配置": "Config",

    # ---- 交互反馈 ----
    "请稍候": "Please Wait",
    "上一个操作还在进行中。": "Another operation is still in progress.",
    "操作失败": "Operation Failed",
    "报告已生成": "Report Generated",
    "现在打开报告吗？": "Open the report now?",
    "应用位置已变更，后台服务路径已自动修复，监控继续运行。": "App location changed — background service path repaired automatically; monitoring continues.",
    "应用位置已变更，后台服务路径已自动修复，下次启动监控时生效。": "App location changed — background service path repaired automatically; takes effect on next start.",
    "监控已在运行，无需重复启动。": "Monitoring is already running.",
    "监控已启动，采样间隔 {interval} 秒。\n": "Monitoring started, sampling every {interval} seconds.\n",
    "后台服务常驻运行，关闭本窗口不会中断监控。": "The background service keeps running — closing this window will not stop monitoring.",
    "正在启动监控…": "Starting monitoring…",
    "后台服务已卸载，但检测到前台采集进程（PID {pid}）仍在运行。\n": "Background service removed, but a foreground collector (PID {pid}) is still running.\n",
    "请在启动它的终端按 Ctrl-C 停止。": "Press Ctrl-C in the terminal that started it to stop.",
    "监控已停止，后台服务已卸载。": "Monitoring stopped and background service removed.",
    "正在停止监控…": "Stopping monitoring…",
    "还没有采集数据。请先启动监控并运行一段时间。": "No data collected yet — start monitoring and let it run for a while.",
    "{day} 没有有效采样点，无法生成报告。": "No valid samples for {day} — cannot generate a report.",
    "正在生成报告…": "Generating report…",
    "还没有报告。请先启动监控，再点「生成报告」。": "No reports yet — start monitoring, then click \"Generate Report\".",
    "正在打开报告文件夹…": "Opening report folder…",
    "无法清除": "Cannot Clear",
    "监控正在运行，请先点「停止监控」再清除历史数据。": "Monitoring is running — click \"Stop Monitoring\" before clearing data.",
    "无需清除": "Nothing to Clear",
    "当前没有历史数据或报告。": "No history data or reports found.",
    "将归档 {days} 天采样/取证数据、{reports} 份报告。\n\n": "This will archive {days} days of sample/forensic data and {reports} reports.\n\n",
    "文件会被移入 NetMon 数据目录下的时间戳备份文件夹，可随时找回；\n": "Files will be moved to a timestamped backup folder under NetMon's data directory and can be restored anytime;\n",
    "不会真正删除，日志也会保留。\n\n确定继续吗？": "nothing is permanently deleted, and logs are kept.\n\nContinue?",
    "清除历史数据": "Clear History Data",
    "已归档 {data} 个数据文件、{reports} 份报告。\n": "Archived {data} data files and {reports} reports.\n",
    "备份位置：{path}": "Backup location: {path}",
    "正在清除历史数据…": "Clearing history data…",
    "后台服务已停止并移除": "Background service stopped and removed",
    "后台服务未注册，无需移除": "Background service not registered — nothing to remove",
    "数据与报告已移入废纸篓：{dest}": "Data and reports moved to Trash: {dest}",
    "数据移动失败：{exc}": "Failed to move data: {exc}",
    "数据目录不存在，已跳过": "Data directory does not exist — skipped",
    "应用本体将在退出后移入废纸篓": "The app itself will be moved to Trash after exit",
    "正在卸载…": "Uninstalling…",
    "正在采集自检数据…": "Collecting self-check data…",
    "退出网哨": "Quit NetMon",
    "监控正在后台运行。\n\n": "Monitoring is running in the background.\n\n",
    "「是」— 同时停止监控并退出\n": "\"Yes\" — stop monitoring and quit\n",
    "「否」— 仅关闭窗口，监控继续在后台运行": "\"No\" — just close the window; monitoring keeps running",
    "需要 Python 3.9+": "Python 3.9+ required",

    # ---- 判定结论（analyze）----
    "正常": "Normal",
    "信号弱": "Weak signal",
    "延迟/丢包异常": "Latency/loss anomaly",
    "DNS 解析失败": "DNS resolution failed",
    "外网中断": "WAN outage",
    "网关不可达": "Gateway unreachable",
    "无线链路断开": "Wi-Fi link down",

    # ---- 排查手册 ----
    "Mac 的 Wi-Fi 处于未关联状态，或接口没有拿到 IP。故障点在本机网卡与 AP 之间的无线关联环节。": "The Mac's Wi-Fi is disassociated, or the interface has no IP. The fault lies in the wireless association between the NIC and the AP.",
    "AP 强制踢线/漫游切换失败": "AP forced deauth / failed roaming",
    "Wi-Fi 省电导致网卡休眠": "Wi-Fi power saving put the NIC to sleep",
    "DHCP 续租失败": "DHCP lease renewal failed",
    "信号瞬间跌穿解调门限": "Signal momentarily dropped below demodulation threshold",
    "查看无线子系统日志（关键证据）": "Check wireless subsystem logs (key evidence)",
    "查看 DHCP 与接口事件": "Check DHCP and interface events",
    "关闭 Wi-Fi 省电（临时验证）": "Disable Wi-Fi power saving (temporary test)",
    "记录当前 AP 与信道": "Record current AP and channel",
    "关闭「低数据模式」与私有地址轮换影响": "Rule out Low Data Mode and private address rotation",
    "系统设置 > 无线局域网 > 详情 > 关闭「专用 Wi-Fi 地址」重测": "System Settings > Wi-Fi > Details > turn off Private Wi-Fi Address and retest",
    "网关 ARP 表项失效，本机与路由器／AP 之间的一跳不通。": "The gateway's ARP entry is stale — the hop between this Mac and the router/AP is down.",
    "AP 与上行交换机链路抖动": "AP-to-uplink-switch link flapping",
    "AP 过载或重启": "AP overloaded or rebooting",
    "本机被 AP 踢下线但未重连": "This Mac was deauthed by the AP but did not reconnect",
    "ARP 缓存老化": "Stale ARP cache",
    "观察 ARP 与邻居表": "Inspect ARP and neighbor tables",
    "连续性探测网关（同时开 3 个终端）": "Continuously probe the gateway (open 3 terminals)",
    "查看 AP 侧关联日志（如有权限）": "Check AP-side association logs (if accessible)",
    "登录无线控制器查看该 MAC 的关联/去关联记录": "Log in to the wireless controller to view association/disassociation records for this MAC",
    "检查接口错误计数是否增长": "Check whether interface error counters are increasing",
    "网关可达但所有外网目标同时失联 —— 问题不在你的 Mac，在路由器到运营商这一段。": "The gateway responds but every external target is unreachable — the fault is not on your Mac; it is between the router and the ISP.",
    "运营商线路闪断／割接": "ISP line flap / maintenance",
    "光猫掉线或 PON 口失联": "ONT dropped or PON port lost",
    "路由器 WAN 口重播／PPPoE 掉线": "Router WAN renegotiation / PPPoE drop",
    "单位出口设备限流或会话耗尽": "Corporate egress throttling or session exhaustion",
    "抓包确认是本机发不出去还是没人回": "Capture packets to see whether this Mac cannot send or no one replies",
    "追踪断点（看在哪一跳开始丢）": "Trace the break (find which hop starts dropping)",
    "查看路由器 WAN 状态与日志": "Check router WAN status and logs",
    "登录路由器后台 → 系统状态 → WAN 连接 / 系统日志": "Log in to the router admin page → Status → WAN connection / system log",
    "向运营商报障时提供": "Provide to the ISP when reporting",
    "断网时刻 + 持续时长 + traceroute 结果（本工具报告可直接作为凭据）": "Outage time + duration + traceroute results (this tool's report can be used directly as evidence)",
    "外网连得通但域名解析不出来 —— 典型 DNS 配置或 DNS 服务器问题。": "External hosts are reachable but names do not resolve — a classic DNS configuration or server problem.",
    "路由器下发的 DNS 不可用": "DNS served by the router is unavailable",
    "企业 DNS 服务器故障或策略拦截": "Corporate DNS server failure or policy block",
    "VPN 接管了 DNS 但在退出时没还原": "VPN took over DNS but did not restore it on exit",
    "搜索域/后缀配置错误": "Misconfigured search domain / suffix",
    "查看当前生效的 DNS 服务器": "Check the currently active DNS servers",
    "绕过本地 DNS 直查对比": "Query public DNS directly for comparison",
    "看系统解析器原始日志": "Check raw system resolver logs",
    "临时切到公共 DNS 验证": "Temporarily switch to public DNS to verify",
    "网络没断但延迟或丢包超过阈值，表现为卡顿、视频会议掉帧。": "The network is up, but latency or loss exceeds thresholds — seen as lag and video-call frame drops.",
    "2.4GHz/5GHz 信道拥挤": "2.4GHz/5GHz channel congestion",
    "蓝牙/微波炉/USB3 干扰": "Bluetooth / microwave / USB3 interference",
    "AP 负载均衡把你挂到了远端 AP": "AP load balancing placed you on a distant AP",
    "本机或网关有大流量任务": "Heavy traffic on this Mac or the gateway",
    "看当前信道与周边 AP 密度": "Check current channel and nearby AP density",
    "对比不同信道的表现": "Compare behavior across channels",
    "分别在 5GHz（149/44）与 2.4GHz（1/6/11）下观察延迟": "Observe latency on 5GHz (149/44) and 2.4GHz (1/6/11) separately",
    "检查本机是否有异常大流量": "Check this Mac for abnormal traffic",
    "查蓝牙是否与 Wi-Fi 抢频段": "Check whether Bluetooth is competing with Wi-Fi for spectrum",
    "信号强度偏弱，尚未断但已处于易掉线边缘。": "Signal strength is weak — not disconnected yet, but on the edge of dropping.",
    "离 AP 太远或有承重墙遮挡": "Too far from the AP or blocked by a load-bearing wall",
    "天线朝向不利": "Unfavorable antenna orientation",
    "AP 发射功率被调低": "AP transmit power reduced",
    "读取 RSSI / 噪声 / SNR 趋势": "Read RSSI / noise / SNR trends",
    "本工具报告中的「无线信号」图表": "The \"Wi-Fi Signal\" chart in this tool's report",
    "更换位置或改用 5GHz 小带宽重测": "Move closer or retest on 5GHz with a narrower channel",

    # ---- 判定原因（analyze 动态）----
    "→ 疑似 DNS 劫持或强制门户": "→ possible DNS hijack or captive portal",
    "接口无 IPv4 地址": "Interface has no IPv4 address",
    "Wi-Fi 未关联到 AP": "Wi-Fi not associated with an AP",
    "网关三路存活信号（ARP / ICMP / TCP）全部无响应": "All three gateway liveness signals (ARP / ICMP / TCP) unresponsive",
    "无有效目标": "No valid targets",
    "HTTP 应用层探针同样失败": "HTTP application-layer probe also failed",
    "但直连公共 DNS 成功 → 路由器/DHCP 下发的 DNS 配置问题，": "But direct public DNS query succeeded → router/DHCP-provided DNS misconfiguration,",
    "可临时改用公共 DNS 验证": "you can temporarily switch to public DNS to verify",
    "系统解析器失败，但直连 223.5.5.5 成功 → 本机 DNS 配置问题": "System resolver failed but direct 223.5.5.5 succeeded → local DNS configuration issue",
    "系统解析器与外部 DNS 同时失败": "System resolver and external DNS both failed",
    "系统解析器解析失败（本机缺少 dig，外部 DNS 直连未采集）": "System resolver failed (dig unavailable locally; external DNS not sampled)",
    "劫持": "hijack",

    # ---- 结论 / verdict ----
    "暂无采集数据。": "No data collected yet.",
    "样本不足，暂不能判定网络健康 —— 需延长有效观测时间。": "Insufficient samples to assess network health — extend the observation window.",
    "未复现故障 —— 说明断连是间歇性的，需要延长观测窗口。": "No incident reproduced — the outage is intermittent; extend the observation window.",
    "把采样间隔调到 10-15 秒（config.json → interval_seconds），提高捕获瞬断的概率。": "Set the sampling interval to 10–15 seconds (config.json → interval_seconds) to better capture brief outages.",
    "在疑似断网的时段保持工具常驻运行，至少覆盖 24 小时（含早晚高峰）。": "Keep the tool running during suspected outage periods for at least 24 hours (including peak times).",
    "如果只在特定应用（如 VPN、会议软件）下断，问题更可能在应用层而非链路层。": "If outages happen only in specific apps (VPN, conferencing), the issue is more likely at the application layer than the link layer.",
    "按累计时长排序，各类异常的处置方向如下：": "Sorted by cumulative duration, here is how to handle each issue:",
    "事件构成：": "Incident breakdown:",
    "这 1 次异常为单点采样即恢复的瞬断 —— 最影响体感、也最难抓，": "This single incident recovered within one sample — a transient blip, the most disruptive and hardest to catch,",
    "说明链路存在亚秒到数十秒级的短暂失联。": "indicating sub-second to tens-of-seconds brief losses on the link.",
    "这类最影响体感、也最难抓，说明链路存在亚秒到数十秒级的短暂失联。": "These are the most disruptive and hardest to catch, indicating brief sub-second to tens-of-seconds link losses.",
    "最可能的原因：": "Most likely causes:",
    "无有效数据": "No valid data",

    # ---- 取证 ----
    "现场快照": "On-site Snapshot",
    "(无输出)": "(no output)",

    # ---- 报告：评级 / 章节 / 图例 ----
    "样本不足": "Insufficient data",
    "优": "Excellent",
    "良": "Good",
    "中": "Fair",
    "差": "Poor",
    "无异常": "No issues",
    "异常 {bad} 次": "{bad} issues",
    "网关": "Gateway",
    "外网（最慢目标）": "WAN (slowest target)",
    "外网（最快目标）": "WAN (fastest target)",
    "告警阈值 150ms": "Alert threshold 150ms",
    "告警阈值 20%": "Alert threshold 20%",
    "最高丢包": "Highest loss",
    "信号强度 RSSI": "Signal strength (RSSI)",
    "噪声底 Noise": "Noise floor",
    "弱信号门限 -70dBm": "Weak-signal threshold -70dBm",
    "未解析到": "not resolved",
    "硬中断时段": "Hard-outage period",
    "未采集空档": "Sampling gap",
    "分层探针采集 · 自动归因 · 离线可读": "Layered probes · auto-diagnosis · works offline",
    "分层健康总览": "Layered Health Overview",
    "可用性时间带": "Availability Timeline",
    "延迟与丢包": "Latency & Packet Loss",
    "无线信号质量": "Wi-Fi Signal Quality",
    "各层探针成功率": "Probe Success Rates by Layer",
    "异常事件明细": "Incident Details",
    "逐层排查手册": "Layer-by-Layer Troubleshooting",
    "采集环境快照": "Collection Environment",
    "每个采样点按最高优先级归入一个异常层（无线链路 → 网关 → 外网 → DNS → 传输质量 → 信号强度），条形为该层健康采样占比。哪一层条形不满格，故障就在哪一层。": "Each sample is assigned to one anomaly layer by priority (Wi-Fi link → gateway → WAN → DNS → latency/loss → signal strength); each bar shows the healthy-sample share for that layer. Any bar that is not full pinpoints the faulty layer.",
    "生成时间 ": "Generated ",
    "采样点 ": "Samples ",
    "有效采样 ": "effective ",
    "时间跨度 ": "time span ",
    "netmon · 数据目录 ": "netmon · data dir ",
    "报告目录 ": "report dir ",
    "跨天汇总：python3 -m netmon report --days N &nbsp;|&nbsp; 导出 CSV：python3 -m netmon export": "Multi-day summary: python3 -m netmon report --days N &nbsp;|&nbsp; Export CSV: python3 -m netmon export",

    # ---- 报告：tooltip / KPI / 环境 ----
    "{day} {hh}:00 无数据": "{day} {hh}:00 no data",
    "{day} {hh}:00 采样 {total} 次，异常 {bad} 次（{detail}）": "{day} {hh}:00 {total} samples, {bad} issues ({detail})",
    "网络健康报告 {day}": "Network Health Report {day}",
    "评级 {g}": "Rating {g}",
    "尚未采集数据": "No data collected yet",
    "整体可用率": "Overall Uptime",
    "基于 {n} 个采样点 · 硬中断 {pct}%": "Based on {n} samples · hard-outage {pct}%",
    "其中瞬断 {n} 次": "including {n} transient blips",
    "累计异常时长": "Total Issue Duration",
    "硬中断 {v}": "hard outage {v}",
    "平均延迟": "Average Latency",
    "P95 {p95} / 最差 {worst} ms": "P95 {p95} / worst {worst} ms",
    "平均丢包": "Average Loss",
    "P95 {pct}%": "P95 {pct}%",
    "无线信号": "Wi-Fi Signal",
    "最低 {min} dBm / SNR {snr} dB": "min {min} dBm / SNR {snr} dB",
    "采样覆盖率": "Sampling Coverage",
    "有效 {eff} / 跨度 {span}": "effective {eff} / span {span}",
    "最差 RTT {v:.0f}ms": "Worst RTT {v:.0f}ms",
    "最高丢包 {v:.0f}%": "Highest loss {v:.0f}%",
    "{n} 采样点": "{n} samples",
    "{gw}（观测期存活信号：{signals} · 最近 ARP MAC {mac}，共 {n} 个采样点）": "{gw} (liveness during observation: {signals} · last ARP MAC {mac}, {n} samples)",
    "主机": "Host",
    "采集区间": "Collection Period",
    "采样完整性": "Sampling Completeness",
    "{got}/{expected} 点 · 有效 {eff} / 跨度 {span} · 覆盖率 {cov}%": "{got}/{expected} points · effective {eff} / span {span} · coverage {cov}%",
    "未采集时段": "Unsampled Periods",
    "{gaps}（共 {n} 段）": "{gaps} ({n} segments)",
    "{n} 秒": "{n} s",
    "默认路由接口": "Default Route Interface",
    "DNS 服务器": "DNS Servers",
    "无线网卡": "Wi-Fi Adapter",
    "ICMP 判定目标": "ICMP Scoring Targets",
    "HTTP 探针": "HTTP Probes",
    "报告目录": "Report Directory",

    # ---- 自动结论 verdict（analyze）----
    "暂无采集数据。": "No data collected yet.",
    "采样覆盖 {cov}%（有效 {eff} / 跨度 {span}），存在 {n} 段未采集时段，结论仅供参考。": "Coverage {cov}% (effective {eff} / span {span}), with {n} unsampled segment(s) — conclusion is indicative only.",
    "整段观测期内 {n} 次采样全部正常，未捕获到断网事件。": "All {n} samples in the observation period were normal; no outage was captured.",
    "平均延迟 {avg} ms，P95 {p95} ms。": "Average latency {avg} ms, P95 {p95} ms.",
    "无线信号最低 {min} dBm，均值 {avg} dBm。": "Wi-Fi signal min {min} dBm, average {avg} dBm.",
    "观测到 {n} 类不同形态的异常（共 {inc} 次、累计 {dur}）—— 这不是单一原因，需要分别处置。": "Observed {n} distinct kinds of anomalies ({inc} incidents, total {dur}) — not a single root cause; address them separately.",
    "按累计时长排序，各类异常的处置方向如下：": "Sorted by cumulative duration, the handling direction for each issue is:",
    "{label}：{cnt} 次 / {dur} —— {h}。": "{label}: {cnt} times / {dur} — {h}.",
    "共捕获 {n} 次异常，累计 {dur}；主因指向「{label}」。": "Captured {n} incidents, total {dur}; primary cause points to \"{label}\".",
    "{label} {n} 次": "{label} ×{n}",
    "事件构成：{parts}。": "Incident breakdown: {parts}.",
    "这 1 次异常为单点采样即恢复的瞬断 —— 最影响体感、也最难抓，说明链路存在亚秒到数十秒级的短暂失联。": "This single incident recovered within one sample — a transient blip, the most disruptive and hardest to catch, indicating sub-second to tens-of-seconds brief link losses.",
    "其中 {n} 次为单点采样即恢复的瞬断 —— 这类最影响体感、也最难抓，说明链路存在亚秒到数十秒级的短暂失联。": "{n} of them recovered within one sample — transient blips, the most disruptive and hardest to catch, indicating sub-second to tens-of-seconds brief link losses.",
    "{hour}:00-{next}:00（异常占比 {rate}%）": "{hour}:00-{next}:00 (issue share {rate}%)",
    "高发时段：{span}，可用于对照单位网络的分时段策略或邻居 AP 的负载规律。": "Peak period: {span} — useful for comparing corporate network time-based policies or neighboring AP load patterns.",
    "发生时段：{span}，事件样本尚少，暂不足以归纳时段规律。": "Occurred at: {span}; too few incident samples to infer a time pattern yet.",
    "延迟：均值 {avg} ms / P95 {p95} ms / 最差路径 {worst} ms。": "Latency: avg {avg} ms / P95 {p95} ms / worst path {worst} ms.",
    "无线信号：最低 {min} dBm / 均值 {avg} dBm / SNR 均值 {snr} dB。": "Wi-Fi signal: min {min} dBm / avg {avg} dBm / avg SNR {snr} dB.",
    "无线信号：最低 {min} dBm，SNR 均值 {snr} dB。": "Wi-Fi signal: min {min} dBm, avg SNR {snr} dB.",
    "最可能的原因：{reasons}。": "Most likely causes: {reasons}.",
    "[{label}] {task}：{cmd}": "[{label}] {task}: {cmd}",
    "{task}：{cmd}": "{task}: {cmd}",
}

# 报告 HTML 里带标签的小片段（key 用中文原文）
_EN_HTML: dict[str, str] = {
    '<p class="sub" style="padding:12px 0">该时段无有效数据。</p>': '<p class="sub" style="padding:12px 0">No valid data for this period.</p>',
    ' <span class="tag" style="color:#b45309;background:#fffbeb;border-color:#fcd34d">瞬断</span>': ' <span class="tag" style="color:#b45309;background:#fffbeb;border-color:#fcd34d">Transient</span>',
    '<table><thead><tr><th>开始</th><th>持续</th><th>类型</th>': '<table><thead><tr><th>Start</th><th>Duration</th><th>Type</th>',
    "<th>现场指标</th><th>判定依据 / 取证</th></tr></thead><tbody>": "<th>On-site Metrics</th><th>Verdict / Evidence</th></tr></thead><tbody>",
    '<p class="sub">观测期内没有登记到异常事件。</p>': '<p class="sub">No incidents recorded during the observation period.</p>',
    '<p class="sub">本次未触发异常事件，无需排查。</p>': '<p class="sub">No incidents triggered — no troubleshooting needed.</p>',
    '<span><i style="background:#f2f4f7;border:1px solid #cfd4dc"></i>无数据</span>': '<span><i style="background:#f2f4f7;border:1px solid #cfd4dc"></i>No data</span>',
    '<p class="sub">暂无数据</p>': '<p class="sub">No data</p>',
    '<p class="sub" style="padding:8px">本轮未采集到无线数据（有线接口或未开启无线采集）。</p>': '<p class="sub" style="padding:8px">No Wi-Fi data sampled (wired interface or Wi-Fi sampling disabled).</p>',
}
_EN.update(_EN_HTML)


def t(text: str, **kwargs) -> str:
    """翻译一段用户可见文本。中文模式以原文为模板；英文模式查表后格式化。"""
    if not isinstance(text, str):
        return text
    out = text if _LANG == "zh" else _EN.get(text, text)
    if kwargs and "{" in out:
        try:
            return out.format(**kwargs)
        except (KeyError, ValueError, IndexError):
            return out
    return out
