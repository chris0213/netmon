"""HTML 报告生成：单文件、离线可看、零外部资源（无 CDN / 无字体外链 / 无 emoji）。

图表全部手写内联 SVG，断网时也能正常打开。
"""

from __future__ import annotations

import html
import json
import platform
from collections import Counter, defaultdict

from . import analyze, util
from .config import REPORT_DIR

E = html.escape


def _esc(v) -> str:
    return html.escape(str(v)) if v is not None else "-"


def _rel(path) -> str:
    """报告里一律显示相对路径，避免把本机绝对路径写进可分享的 HTML。"""
    from pathlib import Path
    root = REPORT_DIR.parent
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)


CSS = """
:root{
  --bg:#f4f5f7; --card:#ffffff; --line:#e4e7ec; --line-2:#cfd4dc;
  --fg:#101828; --fg2:#475467; --fg3:#98a2b3;
  --accent:#1d4ed8; --accent-soft:#eef2ff;
  --ok:#16a34a; --warn:#d97706; --bad:#dc2626;
  --radius:10px;
  --mono:"SF Mono",ui-monospace,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);
  font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  font-size:14px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:1160px;margin:0 auto;padding:28px 22px 64px}
header.top{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;flex-wrap:wrap;margin-bottom:22px}
h1{font-size:21px;margin:0 0 6px;letter-spacing:-.01em;font-weight:650}
h2{font-size:15px;margin:0 0 14px;font-weight:650;letter-spacing:.01em;display:flex;align-items:center;gap:8px}
h2 .idx{font-family:var(--mono);font-size:11px;color:var(--fg3);font-weight:500}
.sub{color:var(--fg2);font-size:13px;margin:0}
.meta{color:var(--fg3);font-size:12px;font-family:var(--mono);margin-top:8px;line-height:1.75}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:20px 22px;margin-bottom:16px}
.section{margin-top:26px}
.grid{display:grid;gap:12px}
.kpis{grid-template-columns:repeat(auto-fit,minmax(158px,1fr))}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px}
.kpi .k{font-size:12px;color:var(--fg2);margin-bottom:6px}
.kpi .v{font-size:23px;font-weight:640;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.kpi .n{font-size:11px;color:var(--fg3);margin-top:4px;font-family:var(--mono)}
.verdict{border-left:3px solid var(--accent)}
.verdict h3{margin:0 0 10px;font-size:17px;font-weight:660;line-height:1.5}
.verdict ul{margin:0;padding-left:0;list-style:none}
.verdict li{position:relative;padding-left:16px;margin-bottom:7px;color:var(--fg2)}
.verdict li:before{content:"";position:absolute;left:0;top:9px;width:5px;height:5px;border-radius:50%;background:var(--line-2)}
.actions{margin:14px 0 0;padding:0;list-style:none;border-top:1px dashed var(--line);padding-top:12px}
.actions li{margin-bottom:10px;font-size:13px;color:var(--fg2)}
.actions .t{display:block;color:var(--fg);font-weight:600;margin-bottom:3px}
code,.mono{font-family:var(--mono);font-size:12px}
.actions code{display:block;background:#f8f9fb;border:1px solid var(--line);border-radius:6px;
  padding:7px 9px;margin-top:4px;color:#0f172a;word-break:break-all;white-space:pre-wrap}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--fg2);font-weight:600;font-size:12px;padding:8px 10px;
  border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
td.num{font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}
.tag{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600;
  border:1px solid transparent;white-space:nowrap}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--fg2);margin-bottom:10px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:middle}
svg{display:block;max-width:100%;height:auto}
.dl{display:grid;grid-template-columns:190px 1fr;gap:2px 16px;font-size:13px}
.dl dt{color:var(--fg2)}
.dl dd{margin:0;font-family:var(--mono);font-size:12px;word-break:break-all}
details{border-top:1px solid var(--line);padding:10px 0}
details summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--accent);list-style:none}
details summary::-webkit-details-marker{display:none}
details summary:before{content:"+ ";font-family:var(--mono)}
details[open] summary:before{content:"- "}
pre{background:#0f172a;color:#d7dee9;padding:12px 14px;border-radius:8px;overflow:auto;
  font-family:var(--mono);font-size:11.5px;line-height:1.55;margin:10px 0 0;max-height:340px}
.bad{color:var(--bad)}.ok{color:var(--ok)}.warnc{color:var(--warn)}
footer{color:var(--fg3);font-size:11.5px;margin-top:34px;border-top:1px solid var(--line);padding-top:14px;
  font-family:var(--mono);line-height:1.8}
"""


# ------------------------------------------------------------------ SVG 基元

def line_chart(
    series: list[dict],
    x0: float,
    x1: float,
    bands: list[tuple[float, float]] | None = None,
    height: int = 230,
    width: int = 1080,
    pad_l: int = 52,
    pad_r: int = 16,
    pad_t: int = 16,
    pad_b: int = 30,
    unit: str = "ms",
    refline: float | None = None,
    refline_label: str = "",
) -> str:
    vals = [v for s in series for _, v in s["points"] if v is not None]
    if not vals:
        return '<p class="sub" style="padding:12px 0">该时段无有效数据。</p>'
    y_min = min(vals)
    y_max = max(vals)
    if refline is not None:
        y_min = min(y_min, refline)
    spread = y_max - y_min
    if spread < 1e-6:
        # 数据完全水平（例如全天零丢包）时人为撑开轴，否则曲线会糊在一条线上
        spread = max(1.0, abs(y_max) * 0.05)
        y_min -= spread
        y_max += spread
    pad = max(spread * 0.14, 0.5)
    y_min -= pad
    y_max += pad
    if unit == "%":
        y_min = max(0.0, y_min)
    if y_max - y_min < 1e-6:
        y_max = y_min + 1.0

    # 量程小的时候用一位小数，避免刻度出现 0 0 1 1 2 这种毫无信息量的重复标签
    decimals = 1 if (y_max - y_min) < 5 else 0

    def _fmt_v(v: float) -> str:
        s = f"{v:.{decimals}f}"
        return "0" + s[2:] if s.startswith("-0.") or s == "-0" else s

    w = width
    h = height
    inner_w = w - pad_l - pad_r
    inner_h = h - pad_t - pad_b
    span = max(x1 - x0, 1.0)
    short_span = span <= 900
    n_xticks = 5 if short_span else 7
    xtick_fmt = util.hhmmss if short_span else util.hhmm

    def X(t: float) -> float:
        return pad_l + (t - x0) / span * inner_w

    def Y(v: float) -> float:
        return pad_t + inner_h - (v - y_min) / (y_max - y_min) * inner_h

    out: list[str] = [
        f'<svg viewBox="0 0 {w} {h}" role="img" xmlns="http://www.w3.org/2000/svg">',
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff"/>',
    ]

    # 断网时段背景带
    for b0, b1 in (bands or []):
        xa, xb = max(X(b0), pad_l), min(X(b1), pad_l + inner_w)
        if xb > xa:
            out.append(
                f'<rect x="{xa:.1f}" y="{pad_t}" width="{max(xb - xa, 1.6):.1f}" '
                f'height="{inner_h}" fill="#dc2626" opacity="0.10"/>'
            )

    # Y 轴网格
    ticks = 4
    for i in range(ticks + 1):
        v = y_min + (y_max - y_min) * i / ticks
        y = Y(v)
        out.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + inner_w}" y2="{y:.1f}" '
            f'stroke="#eef0f3" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{pad_l - 8}" y="{y + 3.5:.1f}" text-anchor="end" font-size="10.5" '
            f'fill="#98a2b3" font-family="SF Mono,Menlo,monospace">{_fmt_v(v)}</text>'
        )
    # 阈值参考线
    if refline is not None and y_min <= refline <= y_max:
        yr = Y(refline)
        out.append(
            f'<line x1="{pad_l}" y1="{yr:.1f}" x2="{pad_l + inner_w}" y2="{yr:.1f}" '
            f'stroke="#d97706" stroke-width="1" stroke-dasharray="5 4"/>'
        )
        out.append(
            f'<text x="{pad_l + inner_w - 4}" y="{yr - 5:.1f}" text-anchor="end" font-size="10" '
            f'fill="#d97706">{_esc(refline_label)}</text>'
        )

    # X 轴刻度：短时间窗用秒级标签，否则会挤出一排重复的 HH:MM
    for i in range(n_xticks + 1):
        t = x0 + span * i / n_xticks
        x = X(t)
        out.append(
            f'<line x1="{x:.1f}" y1="{pad_t + inner_h}" x2="{x:.1f}" y2="{pad_t + inner_h + 4}" '
            f'stroke="#cfd4dc" stroke-width="1"/>'
        )
        anchor = "start" if i == 0 else ("end" if i == n_xticks else "middle")
        out.append(
            f'<text x="{x:.1f}" y="{h - 8}" text-anchor="{anchor}" font-size="10.5" '
            f'fill="#98a2b3" font-family="SF Mono,Menlo,monospace">{xtick_fmt(t)}</text>'
        )

    # 数据线
    for s in series:
        d: list[str] = []
        pen = False
        for t, v in s["points"]:
            if v is None:
                pen = False
                continue
            d.append(("M" if not pen else "L") + f"{X(t):.1f},{Y(v):.1f}")
            pen = True
        if d:
            out.append(
                f'<path d="{" ".join(d)}" fill="none" stroke="{s["color"]}" '
                f'stroke-width="{s.get("width", 1.4)}" stroke-linejoin="round" '
                f'stroke-linecap="round" opacity="{s.get("opacity", 1)}"/>'
            )

    out.append(
        f'<text x="6" y="{pad_t - 5}" font-size="10.5" fill="#98a2b3" '
        f'font-family="SF Mono,Menlo,monospace">{_esc(unit)}</text>'
    )
    out.append("</svg>")
    return "".join(out)


def ribbon(ticks: list[dict], cfg: dict) -> str:
    """按天 × 小时的可用性时间带。格子颜色取该小时内最严重的状态。"""
    order = {s: i for i, s in enumerate(analyze.STATUS_ORDER)}
    grid: dict[str, dict[int, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for t in ticks:
        st, _, _ = analyze.classify(t, cfg)
        grid[util.day_str(t["ts"])][util.hour_of(t["ts"])][st] += 1

    days = sorted(grid.keys())
    cw, ch, gap = 26, 20, 3
    left = 74
    top = 22
    w = left + 24 * (cw + gap) + 8
    h = top + len(days) * (ch + gap) + 26

    out = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
           f'<rect width="{w}" height="{h}" fill="#ffffff"/>']
    for i in range(24):
        if i % 3 == 0:
            x = left + i * (cw + gap)
            out.append(f'<text x="{x}" y="14" font-size="10" fill="#98a2b3" '
                       f'font-family="SF Mono,Menlo,monospace">{i:02d}</text>')
    for r, day in enumerate(days):
        y = top + r * (ch + gap)
        out.append(f'<text x="{left - 10}" y="{y + 14}" text-anchor="end" font-size="10.5" '
                   f'fill="#475467" font-family="SF Mono,Menlo,monospace">{day[5:]}</text>')
        for hh in range(24):
            x = left + hh * (cw + gap)
            cnt = grid[day].get(hh)
            if not cnt:
                fill, tip = "#f2f4f7", f"{day} {hh:02d}:00 无数据"
            else:
                worst = max(cnt, key=lambda s: order.get(s, 0))
                total = sum(cnt.values())
                bad = total - cnt.get("ok", 0)
                fill = analyze.STATUS_COLOR.get(worst, "#16a34a")
                detail = "，".join(f"{analyze.STATUS_LABEL.get(k, k)} {v}" for k, v in cnt.most_common())
                tip = f"{day} {hh:02d}:00 采样 {total} 次，异常 {bad} 次（{detail}）"
            out.append(
                f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="2.5" fill="{fill}">'
                f'<title>{_esc(tip)}</title></rect>'
            )
    out.append("</svg>")
    return "".join(out)


def bar_row(label: str, ok: int, total: int, width: int = 1080) -> str:
    if total == 0:
        rate = 0.0
        color = "#cfd4dc"
    else:
        rate = ok * 100.0 / total
        color = "#16a34a" if rate >= 99 else ("#d97706" if rate >= 90 else "#dc2626")
    bw = int(rate / 100 * (width - 300))
    return (
        f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:9px">'
        f'<div style="width:150px;font-size:12.5px;color:#475467">{_esc(label)}</div>'
        f'<div style="flex:1;height:8px;background:#f2f4f7;border-radius:4px;overflow:hidden">'
        f'<div style="width:{bw}px;max-width:100%;height:100%;background:{color}"></div></div>'
        f'<div class="mono" style="width:112px;text-align:right;color:#475467">{ok}/{total} · '
        f'{rate:.1f}%</div></div>'
    )


# ------------------------------------------------------------------ 报告主体

def _kpi(k: str, v: str, n: str = "", color: str | None = None) -> str:
    style = f' style="color:{color}"' if color else ""
    return (f'<div class="kpi"><div class="k">{_esc(k)}</div>'
            f'<div class="v"{style}>{_esc(v)}</div>'
            f'<div class="n">{_esc(n)}</div></div>')


def _status_tag(status: str) -> str:
    c = analyze.STATUS_COLOR.get(status, "#98a2b3")
    lbl = analyze.STATUS_LABEL.get(status, status)
    return (f'<span class="tag" style="color:{c};background:{c}14;border-color:{c}40">{_esc(lbl)}</span>')


def _evidence_block(ev: dict) -> str:
    if ev.get("kind") == "traceroute":
        body = ev.get("output", "")
    elif ev.get("kind") == "snapshot":
        parts = []
        for key in ("ifconfig", "route_get", "scutil_nwi", "wifi", "arp"):
            if ev.get(key):
                parts.append(f"$ {key}\n{ev[key]}")
        body = "\n\n".join(parts)
    else:
        body = json.dumps(ev, ensure_ascii=False, indent=2)
    return f'<pre>{_esc(body)}</pre>'


def render(cfg: dict, ticks: list[dict], incidents: list[dict], evidence: list[dict],
           env: dict | None = None, title_suffix: str = "") -> str:
    if not ticks:
        incidents = []
        summary = {"empty": True}
    else:
        summary = analyze.summarize(ticks, incidents, cfg)
    vd = analyze.verdict(summary, incidents, cfg)
    env = env or {}

    span_start = ticks[0]["ts"] if ticks else util.now_ts()
    span_end = ticks[-1]["ts"] if ticks else util.now_ts()
    if len(ticks) > 1:
        span_end += float(cfg.get("interval_seconds", 30))

    bands = [(i["start_ts"], i["end_ts"]) for i in incidents
             if i["class"] in ("link_down", "lan_down", "wan_down", "dns_fail")]

    # ---------------- 图表数据
    max_pts = 420
    gw_pts, wan_pts, wan_max_pts = [], [], []
    loss_pts, loss_max_pts = [], []
    rssi_pts, noise_pts = [], []
    for t in ticks:
        gw = (t.get("gateway") or {}).get("icmp") or {}
        gw_pts.append((t["ts"], gw.get("rtt_avg") if gw.get("ok") else None))
        best = [x["rtt_avg"] for x in t.get("icmp", [])
                if x.get("actor") and x.get("ok") and isinstance(x.get("rtt_avg"), (int, float))]
        wan_pts.append((t["ts"], min(best) if best else None))
        wan_max_pts.append((t["ts"], max(best) if best else None))
        lv = [x["loss_pct"] for x in t.get("icmp", [])
              if x.get("actor") and isinstance(x.get("loss_pct"), (int, float))]
        loss_pts.append((t["ts"], util.safe_avg(lv) if lv else None))
        loss_max_pts.append((t["ts"], max(lv) if lv else None))
        w = t.get("wifi") or {}
        rssi_pts.append((t["ts"], w.get("rssi_dbm")))
        noise_pts.append((t["ts"], w.get("noise_dbm")))

    gw_pts = util.downsample(gw_pts, max_pts)
    wan_pts = util.downsample(wan_pts, max_pts)
    wan_max_pts = util.downsample(wan_max_pts, max_pts)
    loss_pts = util.downsample(loss_pts, max_pts)
    loss_max_pts = util.downsample(loss_max_pts, max_pts)
    rssi_pts = util.downsample(rssi_pts, max_pts)
    noise_pts = util.downsample(noise_pts, max_pts)

    th = cfg["thresholds"]
    latency_svg = line_chart(
        [
            {"name": "外网（最慢目标）", "color": "#0891b2", "points": wan_max_pts,
             "width": 1.2, "opacity": 0.9},
            {"name": "外网（最快目标）", "color": "#1d4ed8", "points": wan_pts, "width": 1.5},
            {"name": "网关", "color": "#64748b", "points": gw_pts, "width": 1.0, "opacity": 0.8},
        ],
        span_start, span_end, bands=bands, unit="ms",
        refline=float(th.get("rtt_warn_ms", 150)), refline_label="告警阈值 150ms",
    )
    loss_svg = line_chart(
        [
            {"name": "最高丢包", "color": "#dc2626", "points": loss_max_pts, "width": 1.2,
             "opacity": 0.85},
            {"name": "平均丢包", "color": "#ea580c", "points": loss_pts, "width": 1.4},
        ],
        span_start, span_end, bands=bands, unit="%", height=170,
        refline=float(th.get("loss_warn_pct", 20)), refline_label="告警阈值 20%",
    )

    wifi_svg = ""
    if any(v is not None for _, v in rssi_pts):
        wifi_svg = line_chart(
            [
                {"name": "信号强度 RSSI", "color": "#1d4ed8", "points": rssi_pts, "width": 1.5},
                {"name": "噪声底 Noise", "color": "#98a2b3", "points": noise_pts, "width": 1.1},
            ],
            span_start, span_end, bands=bands, unit="dBm", height=200,
            refline=float(th.get("rssi_warn_dbm", -70)), refline_label="弱信号门限 -70dBm",
        )

    # ---------------- 探针成功率
    probe_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for t in ticks:
        gw = t.get("gateway") or {}
        if gw.get("host"):
            probe_stats[f"网关 {gw['host']}"][1] += 1
            probe_stats[f"网关 {gw['host']}"][0] += 1 if gw.get("alive") else 0
        for x in t.get("icmp", []):
            k = f"ICMP {x['name']} {x['host']}"
            probe_stats[k][1] += 1
            probe_stats[k][0] += 1 if x.get("ok") else 0
        for x in t.get("dns", []):
            k = f"DNS 系统解析器"
            probe_stats[k][1] += 1
            probe_stats[k][0] += 1 if x.get("ok") else 0
        for x in t.get("dns_ext", []):
            k = f"DNS 直连 {x['server']}"
            probe_stats[k][1] += 1
            probe_stats[k][0] += 1 if x.get("ok") else 0
        for x in t.get("http", []):
            k = f"HTTP {x['url'].split('/')[2]}"
            probe_stats[k][1] += 1
            probe_stats[k][0] += 1 if x.get("ok") else 0
        for x in t.get("tcp", []):
            k = f"TCP {x['host']}:{x['port']}"
            probe_stats[k][1] += 1
            probe_stats[k][0] += 1 if x.get("ok") else 0
    probe_html = "".join(
        bar_row(k, v[0], v[1]) for k, v in sorted(probe_stats.items(), key=lambda kv: -kv[1][1])
    )

    # ---------------- 事件表
    if incidents:
        rows = []
        for i in incidents:
            ev_html = ""
            if i.get("evidence"):
                ev_html = "".join(
                    f'<details><summary>{_esc(e.get("label") or e.get("kind"))} · {util.hhmmss(e["ts"])}</summary>'
                    f'{_evidence_block(e)}</details>' for e in i["evidence"]
                )
            metrics = []
            if i.get("rssi_min") is not None:
                metrics.append(f"RSSI {i['rssi_min']}")
            if i.get("rtt_max") is not None:
                metrics.append(f"最差 RTT {i['rtt_max']:.0f}ms")
            if i.get("loss_max") is not None:
                metrics.append(f"最高丢包 {i['loss_max']:.0f}%")
            metrics.append(f"{i['tick_count']} 采样点")
            bleep = ' <span class="tag" style="color:#b45309;background:#fffbeb;border-color:#fcd34d">瞬断</span>' if i["is_bleep"] else ""
            rows.append(
                "<tr>"
                f'<td class="num">{_esc(i["day"][5:])} {_esc(i["start_hhmm"])}</td>'
                f'<td class="num">{_esc(i["duration_human"])}</td>'
                f'<td>{_status_tag(i["class"])}{bleep}</td>'
                f'<td class="num">{_esc(" / ".join(metrics) or "-")}</td>'
                f'<td>{_esc("；".join(i["reasons"]) or "-")}{ev_html}</td>'
                "</tr>"
            )
        incidents_html = (
            '<table><thead><tr><th>开始</th><th>持续</th><th>类型</th>'
            "<th>现场指标</th><th>判定依据 / 取证</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>"
        )
    else:
        incidents_html = '<p class="sub">观测期内没有登记到异常事件。</p>'

    # ---------------- 排查手册（按实际出现的类型排序）
    present = list(dict.fromkeys(
        [i["class"] for i in incidents] + list(analyze.PLAYBOOK.keys())
    ))
    manual = []
    for idx, cls in enumerate(present):
        pb = analyze.PLAYBOOK.get(cls)
        if not pb:
            continue
        checks = "".join(
            f'<li><span class="t">{_esc(t)}</span><code>{_esc(c)}</code></li>' for t, c in pb["checks"]
        )
        opened = " open" if idx == 0 else ""
        manual.append(
            f'<details{opened}><summary>{_status_tag(cls)} {_esc(analyze.STATUS_LABEL.get(cls, cls))}'
            f' — {_esc(pb["meaning"].split("。")[0])}</summary>'
            f'<ul class="actions">{checks}</ul></details>'
        )

    # ---------------- 结论
    verdict_html = (
        f'<div class="card verdict"><h3>{_esc(vd["headline"])}</h3><ul>'
        + "".join(f"<li>{_esc(b)}</li>" for b in vd["bullets"])
        + "</ul>"
        + ('<ul class="actions">' + "".join(
            f'<li><span class="t">{_esc(a.split("：", 1)[0])}</span>'
            f'<code>{_esc(a.split("：", 1)[1] if "：" in a else "")}</code></li>'
            for a in vd["actions"]
        ) + "</ul>" if vd.get("actions") else "")
        + "</div>"
    )

    # ---------------- KPI
    if summary.get("empty"):
        kpis = _kpi("采样点", "0", "尚未采集数据")
    else:
        up = summary["uptime_pct"]
        up_color = "#16a34a" if up >= 99.5 else ("#d97706" if up >= 98 else "#dc2626")
        kpis = (
            _kpi("整体可用率", f"{up}%", f"硬中断可用率 {summary['hard_uptime_pct']}%", up_color)
            + _kpi("异常事件", str(summary["incident_count"]),
                   f"其中瞬断 {summary['bleep_count']} 次",
                   "#dc2626" if summary["hard_incident_count"] else "#16a34a")
            + _kpi("累计异常时长", summary["down_total_human"],
                   f"硬中断 {summary['hard_down_total_human']}")
            + _kpi("平均延迟", f"{summary.get('rtt_avg') if summary.get('rtt_avg') is not None else '-'} ms",
                   f"P95 {summary.get('rtt_p95') if summary.get('rtt_p95') is not None else '-'} / 最差 {summary.get('rtt_worst') if summary.get('rtt_worst') is not None else '-'} ms")
            + _kpi("平均丢包", f"{summary.get('loss_avg') if summary.get('loss_avg') is not None else 0}%",
                   f"P95 {summary.get('loss_p95') if summary.get('loss_p95') is not None else '-'}%")
            + _kpi("无线信号", f"{summary.get('rssi_avg') if summary.get('rssi_avg') is not None else '-'} dBm",
                   f"最低 {summary.get('rssi_min') if summary.get('rssi_min') is not None else '-'} dBm / SNR {summary.get('snr_avg') if summary.get('snr_avg') is not None else '-'} dB")
            + _kpi("观测时长", summary["span_human"], f"{summary['tick_count']} 个采样点")
        )

    # ---------------- 环境
    env_rows = [
        ("主机", env.get("host") or f"{platform.node()} · macOS {platform.mac_ver()[0]}"),
        ("采集区间", f"{util.iso(span_start)} → {util.iso(span_end)}"),
        ("采样间隔", f"{cfg.get('interval_seconds')} 秒"),
        ("默认路由接口", _esc(env.get("interface") or (ticks[0].get("iface") if ticks else "-") or "-")),
        ("网关", _esc(env.get("gateway") or "-")),
        ("DNS 服务器", "、".join(env.get("nameservers") or []) or "-"),
        ("无线网卡", _esc(env.get("wifi_card") or "-")),
        ("ICMP 判定目标", "、".join(
            f"{t['name']}({t['host']})" for t in cfg.get("icmp_targets", []) if t.get("actor")) or "-"),
        ("HTTP 探针", "、".join(cfg.get("http", {}).get("urls", [])) or "-"),
        ("数据目录", _rel(REPORT_DIR.parent / "data")),
        ("报告目录", _rel(REPORT_DIR)),
    ]
    env_html = "".join(
        f'<dt>{_esc(k)}</dt><dd>{v if k == "主机" else _esc(v)}</dd>' for k, v in env_rows
    )

    legend = "".join(
        f'<span><i style="background:{c}"></i>{_esc(analyze.STATUS_LABEL[s])}</span>'
        for s, c in analyze.STATUS_COLOR.items()
    )

    n_days = len({util.day_str(t["ts"]) for t in ticks}) if ticks else 0
    title = f"网络断连排查报告 {util.day_str(span_start)}" + ("" if n_days <= 1 else f" ~ {util.day_str(span_end)}")

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}{_esc(title_suffix)}</title>
<style>{CSS}</style></head>
<body><div class="wrap">

<header class="top">
  <div>
    <h1>{_esc(title)}</h1>
    <p class="sub">分层探针采集 · 自动归因 · 离线可读</p>
  </div>
  <div class="meta">
    生成时间 {_esc(util.iso(util.now_ts()))}<br>
    采样点 {summary.get("tick_count", 0)} · 覆盖 {summary.get("span_human", "-")}
  </div>
</header>

{verdict_html}

<div class="grid kpis">{kpis}</div>

<div class="section">
  <h2><span class="idx">01</span>可用性时间带</h2>
  <div class="legend">{legend}</div>
  <div class="card" style="padding:16px 18px">{ribbon(ticks, cfg) if ticks else '<p class="sub">暂无数据</p>'}</div>
</div>

<div class="section">
  <h2><span class="idx">02</span>延迟与丢包</h2>
  <div class="legend">
    <span><i style="background:#1d4ed8"></i>外网（最快目标）</span>
    <span><i style="background:#0891b2"></i>外网（最慢目标）</span>
    <span><i style="background:#64748b"></i>网关</span>
    <span><i style="background:#dc2626;opacity:.35"></i>硬中断时段</span>
  </div>
  <div class="card" style="padding:14px 10px 6px">{latency_svg}
  <div style="margin-top:10px">{loss_svg}</div></div>
</div>

<div class="section">
  <h2><span class="idx">03</span>无线信号质量</h2>
  <div class="card" style="padding:14px 10px 6px">{wifi_svg or '<p class="sub" style="padding:8px">本轮未采集到无线数据（有线接口或未开启无线采集）。</p>'}</div>
</div>

<div class="section">
  <h2><span class="idx">04</span>各层探针成功率</h2>
  <div class="card">{probe_html or '<p class="sub">暂无数据</p>'}</div>
</div>

<div class="section">
  <h2><span class="idx">05</span>异常事件明细</h2>
  <div class="card">{incidents_html}</div>
</div>

<div class="section">
  <h2><span class="idx">06</span>逐层排查手册</h2>
  <div class="card">{''.join(manual)}</div>
</div>

<div class="section">
  <h2><span class="idx">07</span>采集环境快照</h2>
  <div class="card"><dl class="dl">{env_html}</dl></div>
</div>

<footer>
  netmon · 数据目录 {_esc(_rel(REPORT_DIR.parent / "data"))} · 报告目录 {_esc(_rel(REPORT_DIR))}<br>
  重新生成：python3 -m netmon report --days 7 &nbsp;|&nbsp; 导出 CSV：python3 -m netmon export
</footer>

</div></body></html>
"""


def write(cfg: dict, ticks: list[dict], incidents: list[dict], evidence: list[dict],
          env: dict | None = None, name: str | None = None) -> list[str]:
    html_text = render(cfg, ticks, incidents, evidence, env)
    name = name or "index.html"
    paths = [util.ensure_dir(REPORT_DIR) / name]
    if ticks:
        d0 = util.day_str(ticks[0]["ts"])
        d1 = util.day_str(ticks[-1]["ts"])
        if name == "index.html":
            paths.append(REPORT_DIR / f"report-{d0}_{d1}.html")
    written = []
    for p in paths:
        p.write_text(html_text, encoding="utf-8")
        written.append(str(p))
    return written
