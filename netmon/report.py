"""HTML 报告生成：单文件、离线可看、零外部资源（无 CDN / 无字体外链 / 无 emoji）。

图表全部手写内联 SVG，断网时也能正常打开。
"""

from __future__ import annotations

import copy
import html
import json
import platform
import re
from collections import Counter, defaultdict
from urllib.parse import urlsplit

from . import analyze, i18n, util
from .config import REPORT_DIR
from .i18n import t as tr

E = html.escape


def _esc(v) -> str:
    return html.escape(str(v)) if v is not None else "-"


# ------------------------------------------------------------------ 隐私脱敏
# 报告是可分享的单文件 HTML：默认隐藏本机身份与内网拓扑。
# 只做展示层脱敏，不改动采集到的原始数据（磁盘 jsonl 保持原样，排障仍可用）。
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_MAC_RE = re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")


def _is_global(ip: str) -> bool:
    """公网 IP 保留（排障有价值）；私网/回环/链路本地一律隐藏。"""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if a in (0, 10, 127) or a >= 224:
        return False
    if a == 192 and b == 168:
        return False
    if a == 172 and 16 <= b <= 31:
        return False
    if a == 169 and b == 254:
        return False
    return True


def _redact_ip(ip: str) -> str:
    if _is_global(ip):
        return ip
    parts = ip.split(".")
    return f"{parts[0]}.{parts[1]}.*.*" if len(parts) == 4 else "*"


def _redact_mac(mac: str) -> str:
    """只留厂商前缀（OUI），隐藏设备标识。"""
    p = mac.split(":")
    return ":".join(p[:3] + ["**"] * 3) if len(p) == 6 else "**:**:**:**:**:**"


def _redact_text(text: str) -> str:
    text = _IPV4_RE.sub(lambda m: _redact_ip(m.group(0)), text)
    return _MAC_RE.sub(lambda m: _redact_mac(m.group(0)), text)


def _redact_config(cfg: dict) -> dict:
    """复制一份配置用于渲染，探测目标里的内网地址也脱敏。"""
    out = copy.deepcopy(cfg)
    for t in out.get("icmp_targets", []):
        if t.get("host"):
            t["host"] = _redact_text(str(t["host"]))
    dns = out.get("dns", {})
    dns["external_servers"] = [_redact_text(str(x)) for x in dns.get("external_servers", [])]
    out["http"] = dict(out.get("http", {}))
    out["http"]["urls"] = [_redact_text(str(x)) for x in out["http"].get("urls", [])]
    return out


def _redact_tick(t: dict) -> dict:
    """脱敏单个采样点里会被渲染到的身份/拓扑字段，数值与判定字段原样保留。"""
    r = copy.deepcopy(t)
    gw = r.get("gateway") or {}
    if gw.get("host"):
        gw["host"] = _redact_text(str(gw["host"]))
    arp = gw.get("arp") or {}
    if arp.get("mac"):
        arp["mac"] = _redact_mac(str(arp["mac"]))
    if (gw.get("icmp") or {}).get("host"):
        gw["icmp"]["host"] = _redact_text(str(gw["icmp"]["host"]))
    iface = r.get("iface_info") or {}
    iface["ipv4"] = [_redact_text(str(x)) for x in (iface.get("ipv4") or [])]
    if iface.get("ether"):
        iface["ether"] = _redact_mac(str(iface["ether"]))
    for key in ("dns", "dns_ext", "dns_auto"):
        for d in r.get(key) or []:
            if d.get("server"):
                d["server"] = _redact_text(str(d["server"]))
            if d.get("answers"):
                d["answers"] = [_redact_text(str(a)) for a in d["answers"]]
    for key in ("icmp", "tcp"):
        for x in r.get(key) or []:
            if x.get("host"):
                x["host"] = _redact_text(str(x["host"]))
    for x in r.get("http") or []:
        if x.get("url"):
            x["url"] = _redact_text(str(x["url"]))
    route = r.get("route") or {}
    if route.get("gateway"):
        route["gateway"] = _redact_text(str(route["gateway"]))
    return r


def _redact_evidence(ev: dict) -> dict:
    """断网取证快照含 ifconfig/arp 全文，逐字段脱敏 IP 与 MAC。"""
    r = copy.deepcopy(ev)
    for key in ("ifconfig", "route_get", "scutil_nwi", "arp", "output", "wifi"):
        v = r.get(key)
        if isinstance(v, str):
            r[key] = _redact_text(v)
    return r


def _http_host(item: dict) -> str:
    """从 http 探针条目里安全提取主机名。

    历史数据中部分超时条目只写了 {ok, error} 而没有 url，
    早期采集也可能写入非标准 URL。此处统一兜底，避免 KeyError/IndexError。
    """
    url = item.get("url")
    if not isinstance(url, str) or not url:
        return item.get("host") or tr("未知目标")
    try:
        host = urlsplit(url).hostname
    except ValueError:
        host = None
    if host:
        return host
    # urlsplit 拿不到主机名时，退回按 '/' 切分（兼容无协议写法）
    parts = url.split("/")
    return parts[2] if len(parts) > 2 and parts[2] else (parts[0] or tr("未知目标"))


def _rel(path) -> str:
    """报告里一律显示相对路径，避免把本机绝对路径写进可分享的 HTML。"""
    from pathlib import Path
    root = REPORT_DIR.parent
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)


def _break_gaps(points: list[tuple], threshold: float) -> list[tuple]:
    """相邻采样间隔过大时插入空值，避免折线把未采集时段画成连续。"""
    out: list[tuple] = []
    prev: float | None = None
    for t, v in points:
        if prev is not None and t - prev > threshold:
            out.append((prev, None))
        out.append((t, v))
        prev = t
    return out


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
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:20px 22px;margin-bottom:16px;
  box-shadow:0 1px 2px rgba(16,24,40,.04)}
.section{margin-top:26px}
.grid{display:grid;gap:12px}
.kpis{grid-template-columns:repeat(auto-fit,minmax(158px,1fr))}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;
  position:relative;overflow:hidden}
.kpi:before{content:"";position:absolute;left:0;top:0;right:0;height:3px;background:var(--kpi-c,var(--line))}
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
.grade{display:inline-block;vertical-align:4px;margin-left:12px;padding:3px 13px;border-radius:20px;
  border:1px solid;font-size:12.5px;font-weight:700;letter-spacing:.05em}
tbody tr:hover td{background:#fafbfd}
.layer-note{font-size:12px;color:var(--fg3);margin:12px 2px 0;line-height:1.7}
footer{color:var(--fg3);font-size:11.5px;margin-top:34px;border-top:1px solid var(--line);padding-top:14px;
  font-family:var(--mono);line-height:1.8}
"""


# ------------------------------------------------------------------ SVG 基元

def line_chart(
    series: list[dict],
    x0: float,
    x1: float,
    bands: list[tuple[float, float]] | None = None,
    gap_bands: list[tuple[float, float]] | None = None,
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
        return tr('<p class="sub" style="padding:12px 0">该时段无有效数据。</p>')
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

    # 未采集时段背景带（浅灰），先画，避免盖住红色断网带
    for b0, b1 in (gap_bands or []):
        xa, xb = max(X(b0), pad_l), min(X(b1), pad_l + inner_w)
        if xb > xa:
            out.append(
                f'<rect x="{xa:.1f}" y="{pad_t}" width="{max(xb - xa, 1.6):.1f}" '
                f'height="{inner_h}" fill="#f2f4f7" opacity="0.85"/>'
            )

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
                fill, tip = "#f2f4f7", tr("{day} {hh}:00 无数据", day=day, hh=f"{hh:02d}")
            else:
                worst = max(cnt, key=lambda s: order.get(s, 0))
                total = sum(cnt.values())
                bad = total - cnt.get("ok", 0)
                fill = analyze.STATUS_COLOR.get(worst, "#16a34a")
                sep = ", " if i18n.is_en() else "，"
                detail = sep.join(f"{tr(analyze.STATUS_LABEL.get(k, k))} {v}" for k, v in cnt.most_common())
                tip = tr("{day} {hh}:00 采样 {total} 次，异常 {bad} 次（{detail}）",
                        day=day, hh=f"{hh:02d}", total=total, bad=bad, detail=detail)
            out.append(
                f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="2.5" fill="{fill}">'
                f'<title>{_esc(tip)}</title></rect>'
            )
    out.append("</svg>")
    return "".join(out)


def _localize_probe_key(key: str) -> str:
    """探针统计 key 仅用于聚合（保持中文原样），这里只在渲染时翻译前缀。"""
    if not i18n.is_en():
        return key
    replacements = (
        ("DNS 系统解析器", "DNS system resolver"),
        ("DNS 直连 ", "DNS direct "),
        ("DNS 网卡下发 ", "DNS interface "),
        ("网关 ", "Gateway "),
    )
    for old, new in replacements:
        if key.startswith(old):
            return new + key[len(old):]
    return key


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
    style = f' style="color:{color};--kpi-c:{color}"' if color else ""
    return (f'<div class="kpi"><div class="k">{_esc(k)}</div>'
            f'<div class="v"{style}>{_esc(v)}</div>'
            f'<div class="n">{_esc(n)}</div></div>')


def _grade(summary: dict) -> tuple[str, str]:
    """综合评级：采样覆盖不足时先降级为「样本不足」，避免稀疏采样误判为健康。"""
    coverage = summary.get("coverage_pct", 100)
    if summary.get("tick_count", 0) < 10 or coverage < 80:
        return tr("样本不足"), "#d97706"
    up = summary.get("sample_uptime_pct", summary.get("uptime_pct", 0))
    hard = summary.get("hard_incident_count") or 0
    hard_up = summary.get("sample_hard_uptime_pct", summary.get("hard_uptime_pct"))
    hard_up = 100 if hard_up is None else hard_up
    if up >= 99.9 and hard == 0:
        return tr("优"), "#16a34a"
    if up >= 99 and hard_up >= 99.5:
        return tr("良"), "#65a30d"
    if up >= 98:
        return tr("中"), "#d97706"
    return tr("差"), "#dc2626"


def _layer_row(label: str, bad: int, total: int, width: int = 1080) -> str:
    ok = total - bad
    rate = ok * 100.0 / total if total else 100.0
    if bad == 0:
        color = "#16a34a"
    elif rate >= 99:
        color = "#d97706"
    else:
        color = "#dc2626"
    bw = int(rate / 100 * (width - 340))
    note = tr("无异常") if bad == 0 else tr("异常 {bad} 次", bad=bad)
    return (
        f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">'
        f'<div style="width:150px;font-size:12.5px;color:#475467">{_esc(label)}</div>'
        f'<div style="flex:1;height:10px;background:#f2f4f7;border-radius:5px;overflow:hidden">'
        f'<div style="width:{bw}px;max-width:100%;height:100%;background:{color}"></div></div>'
        f'<div class="mono" style="width:150px;text-align:right;color:#475467">{rate:.2f}% · {_esc(note)}</div></div>'
    )


def _layer_bars(ticks: list[dict], cfg: dict) -> str:
    """分层健康条：每个采样点按 classify 的最高优先级归入一个异常层，
    条形为该层健康采样占比——哪一层出故障一眼可见。"""
    if not ticks:
        return ""
    cnt: Counter = Counter()
    for t in ticks:
        st, _, _ = analyze.classify(t, cfg)
        cnt[st] += 1
    total = len(ticks)
    rows = []
    for st in analyze.STATUS_ORDER:
        if st == "ok":
            continue
        rows.append(_layer_row(tr(analyze.STATUS_LABEL.get(st, st)), cnt.get(st, 0), total))
    return "".join(rows)


def _status_tag(status: str) -> str:
    c = analyze.STATUS_COLOR.get(status, "#98a2b3")
    lbl = tr(analyze.STATUS_LABEL.get(status, status))
    return (f'<span class="tag" style="color:{c};background:{c}14;border-color:{c}40">{_esc(lbl)}</span>')


def _evidence_block(ev: dict) -> str:
    if ev.get("kind") == "snapshot":
        parts = []
        for key in ("ifconfig", "route_get", "scutil_nwi", "wifi", "arp"):
            if ev.get(key):
                parts.append(f"$ {key}\n{ev[key]}")
        body = "\n\n".join(parts)
    else:
        # traceroute / wifilog 等均带 output 字段；未知类型兜底为 JSON
        body = ev.get("output") or json.dumps(ev, ensure_ascii=False, indent=2)
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

    # 隐私脱敏：默认开启。判定（上面）已用原始数据完成，口径不受影响；
    # 此处把后续渲染要用的四个变量整体替换为脱敏副本，绝不回写磁盘。
    if cfg.get("privacy", {}).get("redact_in_report", True):
        cfg = _redact_config(cfg)
        for k in ("host", "nameservers", "gateway", "wifi_card"):
            v = env.get(k)
            if isinstance(v, str):
                env[k] = _redact_text(v)
            elif isinstance(v, list):
                env[k] = [_redact_text(str(x)) for x in v]
        evidence = [_redact_evidence(e) for e in evidence]
        ticks = [_redact_tick(t) for t in ticks]

    interval = float(cfg.get("interval_seconds", 30))
    span_start = ticks[0]["ts"] if ticks else util.now_ts()
    span_end = ticks[-1]["ts"] if ticks else util.now_ts()
    if len(ticks) > 1:
        span_end += interval
    gap_threshold = max(interval * 2, 90)
    all_ts = sorted(t["ts"] for t in ticks)
    gaps = [(a, b) for a, b in zip(all_ts, all_ts[1:]) if b - a > gap_threshold]
    gap_sep = "; " if i18n.is_en() else "；"
    gap_text = gap_sep.join(f"{util.iso(a)} → {util.iso(b)}" for a, b in gaps[:3]) or tr("无")

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

    # 空档两侧断线，避免把没采到的时间画成连续曲线
    gw_pts = _break_gaps(gw_pts, gap_threshold)
    wan_pts = _break_gaps(wan_pts, gap_threshold)
    wan_max_pts = _break_gaps(wan_max_pts, gap_threshold)
    loss_pts = _break_gaps(loss_pts, gap_threshold)
    loss_max_pts = _break_gaps(loss_max_pts, gap_threshold)
    rssi_pts = _break_gaps(rssi_pts, gap_threshold)
    noise_pts = _break_gaps(noise_pts, gap_threshold)

    th = cfg["thresholds"]
    latency_svg = line_chart(
        [
            {"name": tr("外网（最慢目标）"), "color": "#0891b2", "points": wan_max_pts,
             "width": 1.2, "opacity": 0.9},
            {"name": tr("外网（最快目标）"), "color": "#1d4ed8", "points": wan_pts, "width": 1.5},
            {"name": tr("网关"), "color": "#64748b", "points": gw_pts, "width": 1.0, "opacity": 0.8},
        ],
        span_start, span_end, bands=bands, gap_bands=gaps, unit="ms",
        refline=float(th.get("rtt_warn_ms", 150)), refline_label=tr("告警阈值 150ms"),
    )
    loss_svg = line_chart(
        [
            {"name": tr("最高丢包"), "color": "#dc2626", "points": loss_max_pts, "width": 1.2,
             "opacity": 0.85},
            {"name": tr("平均丢包"), "color": "#ea580c", "points": loss_pts, "width": 1.4},
        ],
        span_start, span_end, bands=bands, gap_bands=gaps, unit="%", height=170,
        refline=float(th.get("loss_warn_pct", 20)), refline_label=tr("告警阈值 20%"),
    )

    wifi_svg = ""
    if any(v is not None for _, v in rssi_pts):
        wifi_svg = line_chart(
            [
                {"name": tr("信号强度 RSSI"), "color": "#1d4ed8", "points": rssi_pts, "width": 1.5},
                {"name": tr("噪声底 Noise"), "color": "#98a2b3", "points": noise_pts, "width": 1.1},
            ],
            span_start, span_end, bands=bands, gap_bands=gaps, unit="dBm", height=200,
            refline=float(th.get("rssi_warn_dbm", -70)), refline_label=tr("弱信号门限 -70dBm"),
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
            if x.get("ok") is None:
                continue  # dig 不可用导致未采集，不计入成功率
            k = f"DNS 直连 {x['server']}"
            probe_stats[k][1] += 1
            probe_stats[k][0] += 1 if x.get("ok") else 0
        for x in t.get("dns_auto", []):
            if x.get("ok") is None:
                continue  # dig 不可用导致未采集，不计入成功率
            k = f"DNS 网卡下发 {x['server']}"
            probe_stats[k][1] += 1
            probe_stats[k][0] += 1 if x.get("ok") else 0
        for x in t.get("http", []):
            k = f"HTTP {_http_host(x)}"
            probe_stats[k][1] += 1
            probe_stats[k][0] += 1 if x.get("ok") else 0
        for x in t.get("tcp", []):
            k = f"TCP {x['host']}:{x['port']}"
            probe_stats[k][1] += 1
            probe_stats[k][0] += 1 if x.get("ok") else 0
    probe_html = "".join(
        bar_row(_localize_probe_key(k), v[0], v[1])
        for k, v in sorted(probe_stats.items(), key=lambda kv: -kv[1][1])
    )

    # ---------------- 事件表
    if incidents:
        rows = []
        for i in incidents:
            ev_html = ""
            if i.get("evidence"):
                ev_html = "".join(
                    f'<details><summary>{_esc(tr(e.get("label") or e.get("kind")))} · {util.hhmmss(e["ts"])}</summary>'
                    f'{_evidence_block(e)}</details>' for e in i["evidence"]
                )
            metrics = []
            if i.get("rssi_min") is not None:
                metrics.append(f"RSSI {i['rssi_min']}")
            if i.get("rtt_max") is not None:
                metrics.append(tr("最差 RTT {v:.0f}ms", v=i['rtt_max']))
            if i.get("loss_max") is not None:
                metrics.append(tr("最高丢包 {v:.0f}%", v=i['loss_max']))
            metrics.append(tr("{n} 采样点", n=i['tick_count']))
            bleep = tr(' <span class="tag" style="color:#b45309;background:#fffbeb;border-color:#fcd34d">瞬断</span>') if i["is_bleep"] else ""
            reasons_sep = "; " if i18n.is_en() else "；"
            reasons_txt = reasons_sep.join(tr(r) for r in i["reasons"] if r)
            rows.append(
                "<tr>"
                f'<td class="num">{_esc(i["day"][5:])} {_esc(i["start_hhmm"])}</td>'
                f'<td class="num">{_esc(i["duration_human"])}</td>'
                f'<td>{_status_tag(i["class"])}{bleep}</td>'
                f'<td class="num">{_esc(" / ".join(metrics) or "-")}</td>'
                f'<td>{_esc(reasons_txt or "-")}{ev_html}</td>'
                "</tr>"
            )
        incidents_html = (
            tr('<table><thead><tr><th>开始</th><th>持续</th><th>类型</th>')
            + tr("<th>现场指标</th><th>判定依据 / 取证</th></tr></thead><tbody>")
            + "".join(rows) + "</tbody></table>"
        )
    else:
        incidents_html = tr('<p class="sub">观测期内没有登记到异常事件。</p>')

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
            f'<li><span class="t">{_esc(tr(task))}</span><code>{_esc(tr(cmd))}</code></li>'
            for task, cmd in pb["checks"]
        )
        opened = " open" if idx == 0 else ""
        meaning = tr(pb["meaning"])
        # 中文按句号截首句做摘要；英文按第一个句点截
        first_sentence = meaning.split("。")[0] if i18n.is_zh() else meaning.split(". ")[0]
        manual.append(
            f'<details{opened}><summary>{_status_tag(cls)} {_esc(tr(analyze.STATUS_LABEL.get(cls, cls)))}'
            f' — {_esc(first_sentence)}</summary>'
            f'<ul class="actions">{checks}</ul></details>'
        )

    manual_html = ("".join(manual) if incidents
                   else tr('<p class="sub">本次未触发异常事件，无需排查。</p>'))

    # ---------------- 结论（按健康度着色）+ 综合评级徽章
    hard_cnt = 0 if summary.get("empty") else (summary.get("hard_incident_count") or 0)
    v_color = "#dc2626" if hard_cnt else ("#d97706" if incidents else "#16a34a")
    verdict_html = (
        f'<div class="card verdict" style="border-left-color:{v_color}"><h3>{_esc(vd["headline"])}</h3><ul>'
        + "".join(f"<li>{_esc(b)}</li>" for b in vd["bullets"])
        + "</ul>"
        + ('<ul class="actions">' + "".join(
            f'<li><span class="t">{_esc(a.split("：", 1)[0])}</span>'
            f'<code>{_esc(a.split("：", 1)[1] if "：" in a else "")}</code></li>'
            for a in vd["actions"]
        ) + "</ul>" if vd.get("actions") else "")
        + "</div>"
    )
    if summary.get("empty"):
        grade_badge = ""
    else:
        g, gc = _grade(summary)
        grade_badge = (f'<span class="grade" style="color:{gc};background:{gc}14;'
                       f'border-color:{gc}55">{tr("评级 {g}", g=g)}</span>')

    # ---------------- 分层健康条
    layer_html = _layer_bars(ticks, cfg) if ticks else ""

    # ---------------- KPI
    if summary.get("empty"):
        kpis = _kpi(tr("采样点"), "0", tr("尚未采集数据"))
    else:
        up = summary.get("sample_uptime_pct", summary["uptime_pct"])
        up_color = "#16a34a" if up >= 99.5 else ("#d97706" if up >= 98 else "#dc2626")
        coverage = summary.get("coverage_pct", 100)
        coverage_color = "#16a34a" if coverage >= 80 else "#d97706"
        kpis = (
            _kpi(tr("整体可用率"), f"{up}%",
                 tr("基于 {n} 个采样点 · 硬中断 {pct}%",
                   n=summary['tick_count'], pct=summary.get('sample_hard_uptime_pct', summary['hard_uptime_pct'])),
                 up_color)
            + _kpi(tr("异常事件"), str(summary["incident_count"]),
                   tr("其中瞬断 {n} 次", n=summary['bleep_count']),
                   "#dc2626" if summary["hard_incident_count"] else "#16a34a")
            + _kpi(tr("累计异常时长"), summary["down_total_human"],
                   tr("硬中断 {v}", v=summary['hard_down_total_human']))
            + _kpi(tr("平均延迟"), f"{summary.get('rtt_avg') if summary.get('rtt_avg') is not None else '-'} ms",
                   tr("P95 {p95} / 最差 {worst} ms",
                     p95=summary.get('rtt_p95') if summary.get('rtt_p95') is not None else '-',
                     worst=summary.get('rtt_worst') if summary.get('rtt_worst') is not None else '-'))
            + _kpi(tr("平均丢包"), f"{summary.get('loss_avg') if summary.get('loss_avg') is not None else 0}%",
                   tr("P95 {pct}%", pct=summary.get('loss_p95') if summary.get('loss_p95') is not None else '-'))
            + _kpi(tr("无线信号"), f"{summary.get('rssi_avg') if summary.get('rssi_avg') is not None else '-'} dBm",
                   tr("最低 {min} dBm / SNR {snr} dB",
                     min=summary.get('rssi_min') if summary.get('rssi_min') is not None else '-',
                     snr=summary.get('snr_avg') if summary.get('snr_avg') is not None else '-'))
            + _kpi(tr("采样覆盖率"), f"{coverage}%",
                   tr("有效 {eff} / 跨度 {span}",
                     eff=summary.get('effective_span_human', '-'), span=summary['span_human']),
                   coverage_color)
        )

    # ---------------- 网关存活信号统计（观测期内所有含网关的采样点）
    gw_total = 0
    gw_sig_count: dict[str, int] = {}
    gw_mac_last = None
    for t in ticks:
        gw = t.get("gateway") or {}
        if not gw.get("host"):
            continue
        gw_total += 1
        for s in gw.get("signals") or []:
            gw_sig_count[s] = gw_sig_count.get(s, 0) + 1
        mac = (gw.get("arp") or {}).get("mac")
        if mac:
            gw_mac_last = mac
    if gw_total:
        sig_pct = " · ".join(
            f"{s} {round(gw_sig_count.get(s, 0) / gw_total * 100)}%"
            for s in ("arp", "icmp", "tcp_rst")
        )
        gw_row = tr("{gw}（观测期存活信号：{signals} · 最近 ARP MAC {mac}，共 {n} 个采样点）",
                   gw=env.get('gateway') or '-', signals=sig_pct,
                   mac=gw_mac_last or tr('未解析到'), n=gw_total)
    else:
        gw_row = env.get("gateway") or "-"

    # ---------------- 环境
    env_rows = [
        (tr("主机"), env.get("host") or f"{platform.node()} · macOS {platform.mac_ver()[0]}"),
        (tr("采集区间"), f"{util.iso(span_start)} → {util.iso(span_end)}"),
        (tr("采样完整性"),
         tr("{got}/{expected} 点 · 有效 {eff} / 跨度 {span} · 覆盖率 {cov}%",
           got=summary.get('tick_count', 0), expected=summary.get('expected_ticks', '-'),
           eff=summary.get('effective_span_human', '-'), span=summary.get('span_human', '-'),
           cov=summary.get('coverage_pct', '-'))),
        (tr("未采集时段"), tr("{gaps}（共 {n} 段）",
                        gaps=gap_text, n=summary.get('gap_count', len(gaps)))),
        (tr("采样间隔"), tr("{n} 秒", n=cfg.get('interval_seconds'))),
        (tr("默认路由接口"), _esc(env.get("interface") or (ticks[0].get("iface") if ticks else "-") or "-")),
        (tr("网关"), gw_row),
        (tr("DNS 服务器"), "、".join(env.get("nameservers") or []) or "-"),
        (tr("无线网卡"), _esc(env.get("wifi_card") or "-")),
        (tr("ICMP 判定目标"), "、".join(
            f"{t['name']}({t['host']})" for t in cfg.get("icmp_targets", []) if t.get("actor")) or "-"),
        (tr("HTTP 探针"), "、".join(cfg.get("http", {}).get("urls", [])) or "-"),
        (tr("数据目录"), str(REPORT_DIR.parent / "data")),
        (tr("报告目录"), str(REPORT_DIR)),
    ]
    env_html = "".join(
        f'<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>' for k, v in env_rows
    )

    legend = (tr('<span><i style="background:#f2f4f7;border:1px solid #cfd4dc"></i>无数据</span>')
              + "".join(
                  f'<span><i style="background:{c}"></i>{_esc(tr(analyze.STATUS_LABEL[s]))}</span>'
                  for s, c in analyze.STATUS_COLOR.items()
              ))

    n_days = len({util.day_str(t["ts"]) for t in ticks}) if ticks else 0
    title = tr("网络健康报告 {day}", day=util.day_str(span_start)) + (
        "" if n_days <= 1 else f" ~ {util.day_str(span_end)}")
    html_lang = "en" if i18n.is_en() else "zh-CN"

    return f"""<!DOCTYPE html>
<html lang="{html_lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}{_esc(title_suffix)}</title>
<style>{CSS}</style></head>
<body><div class="wrap">

<header class="top">
  <div>
    <h1>{_esc(title)}{grade_badge}</h1>
    <p class="sub">{tr("分层探针采集 · 自动归因 · 离线可读")}</p>
  </div>
  <div class="meta">
    {tr("生成时间 ")}{_esc(util.iso(util.now_ts()))}<br>
    {tr("采样点 ")}{summary.get("tick_count", 0)} · {tr("有效采样 ")}{summary.get("effective_span_human", "-")} · {tr("时间跨度 ")}{summary.get("span_human", "-")}
  </div>
</header>

{verdict_html}

<div class="grid kpis">{kpis}</div>

<div class="section">
  <h2><span class="idx">01</span>{tr("分层健康总览")}</h2>
  <div class="card">{layer_html or tr('<p class="sub">暂无数据</p>')}
    <p class="layer-note">{tr("每个采样点按最高优先级归入一个异常层（无线链路 → 网关 → 外网 → DNS → 传输质量 → 信号强度），条形为该层健康采样占比。哪一层条形不满格，故障就在哪一层。")}</p>
  </div>
</div>

<div class="section">
  <h2><span class="idx">02</span>{tr("可用性时间带")}</h2>
  <div class="legend">{legend}</div>
  <div class="card" style="padding:16px 18px">{ribbon(ticks, cfg) if ticks else tr('<p class="sub">暂无数据</p>')}</div>
</div>

<div class="section">
  <h2><span class="idx">03</span>{tr("延迟与丢包")}</h2>
  <div class="legend">
    <span><i style="background:#1d4ed8"></i>{tr("外网（最快目标）")}</span>
    <span><i style="background:#0891b2"></i>{tr("外网（最慢目标）")}</span>
    <span><i style="background:#64748b"></i>{tr("网关")}</span>
    <span><i style="background:#dc2626;opacity:.35"></i>{tr("硬中断时段")}</span>
    <span><i style="background:#f2f4f7;border:1px solid #cfd4dc"></i>{tr("未采集空档")}</span>
  </div>
  <div class="card" style="padding:14px 10px 6px">{latency_svg}
  <div style="margin-top:10px">{loss_svg}</div></div>
</div>

<div class="section">
  <h2><span class="idx">04</span>{tr("无线信号质量")}</h2>
  <div class="card" style="padding:14px 10px 6px">{wifi_svg or tr('<p class="sub" style="padding:8px">本轮未采集到无线数据（有线接口或未开启无线采集）。</p>')}</div>
</div>

<div class="section">
  <h2><span class="idx">05</span>{tr("各层探针成功率")}</h2>
  <div class="card">{probe_html or tr('<p class="sub">暂无数据</p>')}</div>
</div>

<div class="section">
  <h2><span class="idx">06</span>{tr("异常事件明细")}</h2>
  <div class="card">{incidents_html}</div>
</div>

<div class="section">
  <h2><span class="idx">07</span>{tr("逐层排查手册")}</h2>
  <div class="card">{manual_html}</div>
</div>

<div class="section">
  <h2><span class="idx">08</span>{tr("采集环境快照")}</h2>
  <div class="card"><dl class="dl">{env_html}</dl></div>
</div>

<footer>
  {tr("netmon · 数据目录 ")}{_esc(str(REPORT_DIR.parent / "data"))} · {tr("报告目录 ")}{_esc(str(REPORT_DIR))}<br>
  {tr("跨天汇总：python3 -m netmon report --days N &nbsp;|&nbsp; 导出 CSV：python3 -m netmon export")}
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
