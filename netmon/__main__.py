"""netmon 命令行入口。

  python3 -m netmon once                  采集一次并打印，用于快速自检
  python3 -m netmon run                   常驻采集（前台）
  python3 -m netmon run --duration 600    限时采集，用于试跑
  python3 -m netmon report --days 7       生成 HTML 报告
  python3 -m netmon status                查看服务与数据概况
  python3 -m netmon serve --port 8899     本地实时看板（仅监听 127.0.0.1）
  python3 -m netmon install --interval 30 装成开机自启的常驻服务
  python3 -m netmon uninstall             卸载服务
  python3 -m netmon export                导出 CSV
  python3 -m netmon purge                 清空所有采集数据
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

from . import __version__, analyze, forensics, launchd, probe, report, store
from .config import DATA_DIR, REPORT_DIR, load_config, write_default_config
from .util import human_duration, hhmmss, iso, now_ts

_STOP = threading.Event()


# ------------------------------------------------------------------ 输出格式

def fmt_line(tick: dict, status: str, reasons: list[str], metrics: dict) -> str:
    label = analyze.STATUS_LABEL.get(status, status)
    parts = [
        hhmmss(tick["ts"]),
        f"{label:<12}",
        f"外网 {metrics.get('wan_ratio') if metrics.get('wan_ratio') is not None else '-'}",
        f"RTT {metrics.get('rtt_avg') if metrics.get('rtt_avg') is not None else '-'}ms",
        f"丢包 {metrics.get('loss_pct') if metrics.get('loss_pct') is not None else '-'}%",
    ]
    if metrics.get("rssi_dbm") is not None:
        parts.append(f"信号 {metrics['rssi_dbm']}dBm")
    gw = tick.get("gateway") or {}
    if gw.get("host"):
        parts.append(f"网关 {gw.get('method')}")
    line = "  ".join(str(p) for p in parts)
    if reasons:
        line += "  |  " + "；".join(reasons)
    return line


def print_once(tick: dict, status: str, reasons: list[str], metrics: dict) -> None:
    print(fmt_line(tick, status, reasons, metrics))
    print()
    ips = "、".join(tick.get("iface_info", {}).get("ipv4") or []) or "-"
    print("  接口      ", tick.get("iface"), ips)
    gw = tick.get("gateway") or {}
    print("  网关      ", gw.get("host"),
          "| 存活信号", "+".join(gw.get("signals") or []) or "无（判定为不可达）",
          "| ARP", (gw.get("arp") or {}).get("mac"),
          "| ICMP", "回显" if (gw.get("icmp") or {}).get("ok") else "被屏蔽",
          "| TCP RST", "有" if (gw.get("tcp") or {}).get("alive") else "无")
    for t in tick.get("icmp", []):
        print(f"  ICMP {t['name']:<10}", f"{t['rtt_avg']}ms" if t.get("ok") else "无响应",
              f"丢包 {t['loss_pct']}%", "" if t.get("actor") else "(不参与判定)")
    for t in tick.get("tcp", []):
        print(f"  TCP  {t['host']}:{t['port']}", f"{t['ms']}ms" if t.get("ok") else f"失败 {t.get('error')}")
    for d in tick.get("dns", []):
        print(f"  DNS  系统解析器 -> {d['name']}", f"{d['ms']}ms" if d.get("ok") else f"失败 {d.get('error')}")
    for d in tick.get("dns_ext", []):
        print(f"  DNS  @{d['server']} -> {d['name']}", f"{d['ms']}ms" if d.get("ok") else f"失败 {d.get('error')}")
    for d in tick.get("dns_auto", []):
        print(f"  DNS  网卡自动@{d['server']} -> {d['name']}",
              f"{d['ms']}ms" if d.get("ok") else f"失败 {d.get('error')}")
    if (metrics or {}).get("dns_note"):
        print("  DNS  提示    ", metrics["dns_note"])
    for h in tick.get("http", []):
        print(f"  HTTP {h['url'][:52]}", f"{h['status']} {h['ms']}ms" if h.get("ok") else f"失败 {h.get('error')}")
    w = tick.get("wifi") or {}
    if w.get("device"):
        print(f"  无线      {w.get('band')} ch{w.get('channel')} {w.get('width')} "
              f"{w.get('phymode')} | RSSI {w.get('rssi_dbm')} dBm | 噪声 {w.get('noise_dbm')} dBm "
              f"| SNR {w.get('snr_db')} dB | 速率 {w.get('tx_rate_mbps')} Mbps")
    c = tick.get("counters") or {}
    if c:
        print(f"  接口计数  in {c.get('ipkts')} pkt / err {c.get('ierrs')} | out {c.get('opkts')} pkt / err {c.get('oerrs')}")
    print(f"  本轮耗时  {tick.get('elapsed_ms')} ms")
    if os.environ.get("NETMON_DEBUG") and tick.get("timings"):
        tms = tick["timings"]
        detail = "  ".join(f"{k}={v}ms" for k, v in sorted(tms.items(), key=lambda x: -x[1]))
        print(f"  耗时分解  {detail}（并行取最慢者，非累加）")


# ------------------------------------------------------------------ 常驻采集

class Watcher:
    """维护"当前是否处于事件中"的状态机，负责触发取证。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.incident: dict | None = None
        self.last_forensic_ts = 0.0
        self.last_prune_ts = 0.0
        self.runs: list[dict] = []
        self._pool: list[threading.Thread] = []

    def _spawn(self, fn, *args) -> None:
        t = threading.Thread(target=fn, args=args, daemon=True)
        t.start()
        self._pool = [x for x in self._pool if x.is_alive()] + [t]

    def observe(self, tick: dict, status: str, reasons: list[str]) -> None:
        fz = self.cfg.get("forensics", {})
        if status == "ok":
            if self.incident is not None:
                self.incident["end_ts"] = tick["ts"]
                store.log(f"事件结束 {self.incident['class']} 于 {iso(tick['ts'])}")
            self.incident = None
            return

        if self.incident is None:
            self.incident = {"start_ts": tick["ts"], "class": status, "reasons": list(reasons)}
            store.log(f"事件开始 {status}：{'；'.join(reasons)}", level="WARN")
            if fz.get("capture_snapshot", True):
                self._spawn(self._do_snapshot, tick)
            self._maybe_traceroute(tick)
            return

        # 事件持续中：类型升级时补一次取证
        if status != self.incident["class"]:
            self.incident["class"] = status
            self.incident["reasons"] = list(reasons)
            self._maybe_traceroute(tick)

    def _maybe_traceroute(self, tick: dict) -> None:
        fz = self.cfg.get("forensics", {})
        if not (fz.get("traceroute_on_incident") or fz.get("wifi_log_on_incident", True)):
            return
        now = now_ts()
        if now - self.last_forensic_ts < float(fz.get("cooldown_seconds", 600)):
            return
        self.last_forensic_ts = now
        if fz.get("traceroute_on_incident"):
            self._spawn(
                self._do_traceroute, tick,
                str(fz.get("traceroute_target", "223.5.5.5")),
                int(fz.get("traceroute_max_hops", 12)),
                float(fz.get("traceroute_timeout_seconds", 25)),
            )
        if fz.get("wifi_log_on_incident", True):
            self._spawn(self._do_wifi_log, tick)

    def _do_snapshot(self, tick: dict) -> None:
        try:
            rec = forensics.snapshot(tick["ts"], tick.get("iface"),
                                     (tick.get("gateway") or {}).get("host"))
            store.append_evidence(rec)
            store.log("已保存现场快照")
        except Exception as exc:  # noqa: BLE001
            store.log(f"现场快照失败：{type(exc).__name__}", level="ERROR")

    def _do_traceroute(self, tick: dict, target: str, hops: int, timeout_s: float) -> None:
        try:
            rec = forensics.traceroute(tick["ts"], target, hops, timeout_s)
            store.append_evidence(rec)
            store.log(f"已保存断点追踪（{target}）")
        except Exception as exc:  # noqa: BLE001
            store.log(f"断点追踪失败：{type(exc).__name__}", level="ERROR")

    def _do_wifi_log(self, tick: dict) -> None:
        """断网瞬间抓取无线子系统日志摘要 —— 判断"是不是 AP 把你踢了"最硬的证据。"""
        try:
            rec = forensics.wifi_log_excerpt()
            rec["incident_start"] = tick["ts"]
            store.append_evidence(rec)
            store.log("已保存无线子系统日志摘要")
        except Exception as exc:  # noqa: BLE001
            store.log(f"无线日志取证失败：{type(exc).__name__}", level="ERROR")

    def maybe_prune(self, tick: dict) -> None:
        if now_ts() - self.last_prune_ts < 3600:
            return
        self.last_prune_ts = now_ts()
        removed = store.prune(int(self.cfg.get("retention_days", 30)))
        if removed:
            store.log(f"清理过期数据 {len(removed)} 个文件")


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    interval = float(args.interval or cfg["interval_seconds"])
    cfg["interval_seconds"] = interval
    duration = float(args.duration) if args.duration else None

    store.log(f"启动采集：间隔 {interval}s，时长 {'不限' if not duration else human_duration(duration)}")
    store.write_pid()

    def _stop(signum, frame):  # noqa: ARG001
        _STOP.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    watcher = Watcher(cfg)
    tick_index = 0
    t_start = now_ts()
    print(f"netmon {__version__} 开始采集 · 间隔 {interval:.0f}s · 数据目录 {DATA_DIR}")
    print(f"配置：{cfg.get('_config_path')}")
    print("-" * 100)

    while not _STOP.is_set():
        cycle = now_ts()
        try:
            tick = probe.collect(cfg, tick_index)
            status, reasons, metrics = analyze.classify(tick, cfg)
            tick["status"] = status
            tick["reasons"] = reasons
            store.append_tick(tick)
            watcher.observe(tick, status, reasons)
            watcher.maybe_prune(tick)
            print(fmt_line(tick, status, reasons, metrics), flush=True)
        except Exception as exc:  # noqa: BLE001 - 采集循环绝不能因单次异常退出
            store.log(f"采集异常：{type(exc).__name__}: {exc}", level="ERROR")
            print(f"{hhmmss(now_ts())}  采集异常 {type(exc).__name__}: {exc}")
        tick_index += 1
        if duration and now_ts() - t_start >= duration:
            break
        sleep_for = interval - (now_ts() - cycle)
        _STOP.wait(max(0.5, sleep_for))

    store.log("采集已停止")
    try:
        store.pid_path().unlink()
    except OSError:
        pass
    print("-" * 100)
    print(f"采集结束，共 {tick_index} 个采样点。生成报告：python3 -m netmon report")
    return 0


# ------------------------------------------------------------------ 报告

def _load(days: int | None) -> tuple[list[str], list[dict], list[dict]]:
    all_days = store.available_days()
    if not all_days:
        return [], [], []
    use = all_days[-days:] if days else all_days
    ticks = store.load_ticks(use)
    evidence = store.load_evidence(use)
    return use, ticks, evidence


def cmd_report(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    use, ticks, evidence = _load(args.days)
    if not ticks:
        print("没有采集数据。先运行：python3 -m netmon run")
        return 1
    incidents = analyze.build_incidents(ticks, cfg)
    store.attach_evidence(incidents, evidence)
    env = probe.env_snapshot(cfg)
    paths = report.write(cfg, ticks, incidents, evidence, env)
    store.log(f"生成报告 {paths[0]}（{len(ticks)} 采样点，{len(incidents)} 事件）")
    print(f"已生成报告：{paths[0]}")
    for p in paths[1:]:
        print(f"归档副本：  {p}")
    for i in incidents:
        print(f"  [{i['day']} {i['start_hhmm']}] {i['label']} 持续 {i['duration_human']}"
              + ("  <瞬断>" if i["is_bleep"] else ""))
    if args.open:
        import subprocess
        subprocess.run(["open", paths[0]])
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    tick = probe.collect(cfg, 0)
    status, reasons, metrics = analyze.classify(tick, cfg)
    if args.save:
        store.append_tick(tick)
    print_once(tick, status, reasons, metrics)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    st = launchd.status()
    print(f"netmon {__version__}")
    print(f"配置文件     {cfg.get('_config_path')}")
    print(f"数据目录     {DATA_DIR}")
    print(f"报告目录     {REPORT_DIR}")
    days = store.available_days()
    print(f"已有数据     {len(days)} 天 {days[-3:] if days else ''}")
    ticks = store.load_ticks(days[-1:]) if days else []
    if ticks:
        incidents = analyze.build_incidents(ticks, cfg)
        s = analyze.summarize(ticks, incidents, cfg)
        print(f"最近一天     {s['tick_count']} 采样点 / 可用率 {s['uptime_pct']}% / 事件 {s['incident_count']} 次")
    pid = store.is_running()
    print(f"前台进程     {'运行中 pid=' + str(pid) if pid else '未运行'}")
    print(f"后台服务     {'已加载 pid=' + str(st['pid']) if st['loaded'] else '未加载'}"
          f"{'' if st['plist_exists'] else '（plist 不存在）'}")
    return 0


# ------------------------------------------------------------------ 实时看板

def cmd_serve(args: argparse.Namespace) -> int:
    import http.server
    import socketserver

    cfg = load_config(args.config)
    cache: dict = {"body": None, "ticks": None, "html_key": None, "ticks_key": None}

    def _fingerprint() -> tuple:
        """数据文件 mtime 指纹：数据没变化时直接复用缓存，避免每个请求全量读盘+重渲染。
        同时纳入 netmon 源码 mtime——代码更新后缓存自动失效，serve 不必手动重启。"""
        parts = []
        for p in sorted(DATA_DIR.glob("*.jsonl")):
            try:
                parts.append((p.name, p.stat().st_mtime_ns))
            except OSError:
                continue
        pkg_dir = Path(__file__).resolve().parent
        for src in sorted(pkg_dir.glob("*.py")):
            try:
                parts.append(("src:" + src.name, src.stat().st_mtime_ns))
            except OSError:
                continue
        return tuple(parts)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.startswith("/favicon"):
                self.send_response(204)
                self.end_headers()
                return
            if self.path.startswith("/api/ticks"):
                key = _fingerprint()
                if cache["ticks_key"] != key:
                    _, ticks, _ = _load(1)
                    cache["ticks"] = json.dumps(ticks[-args.tail:], ensure_ascii=False).encode()
                    cache["ticks_key"] = key
                body = cache["ticks"]
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            key = _fingerprint()
            if cache["html_key"] != key:
                _, ticks, evidence = _load(args.days)
                incidents = analyze.build_incidents(ticks, cfg)
                store.attach_evidence(incidents, evidence)
                cache["body"] = report.render(cfg, ticks, incidents, evidence,
                                              probe.env_snapshot(cfg)).encode("utf-8")
                cache["html_key"] = key
            body = cache["body"]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # noqa: D102
            return

    port = int(args.port)
    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
            print(f"实时看板：http://127.0.0.1:{port}/  （仅本机可访问，Ctrl+C 退出）")
            httpd.serve_forever()
    except OSError as exc:
        print(f"端口 {port} 不可用：{exc}")
        return 1
    return 0


# ------------------------------------------------------------------ 其它

def cmd_install(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    interval = int(args.interval or cfg["interval_seconds"])
    res = launchd.install(interval, DATA_DIR)
    if res["ok"]:
        print(f"已安装常驻服务 {launchd.LABEL}（间隔 {interval}s）")
        print(f"  plist: {res['plist']}")
        print(f"  日志 : {DATA_DIR / 'launchd.err.log'}")
        print("  查看 : launchctl print gui/$(id -u)/com.netmon.probe | head -20")
    else:
        print("安装失败：", res["stderr"] or res["stdout"])
        return 1
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    res = launchd.uninstall()
    print(f"已卸载常驻服务（plist 删除：{'是' if res['removed'] else '否'}）")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    use, ticks, _ = _load(args.days)
    if not ticks:
        print("没有数据可导出。")
        return 1
    out = Path(args.output) if args.output else (REPORT_DIR / f"netmon-export-{use[-1]}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["时间", "状态", "接口", "IP", "信道", "RSSI_dBm", "噪声_dBm", "SNR_dB",
                "网关可达", "网关方式", "外网加权可达", "RTT均值_ms", "丢包_%", "抖动_ms",
                "HTTP可达", "DNS系统可达", "DNS直连可达", "原因"])
    for t in ticks:
        st, rs, m = analyze.classify(t, cfg)
        w.writerow([
            iso(t["ts"]), analyze.STATUS_LABEL.get(st, st), t.get("iface"),
            ",".join((t.get("iface_info") or {}).get("ipv4") or []),
            (t.get("wifi") or {}).get("channel"), m.get("rssi_dbm"),
            (t.get("wifi") or {}).get("noise_dbm"), m.get("snr_db"),
            m.get("gw_alive"), m.get("gw_method"), m.get("wan_ratio"),
            m.get("rtt_avg"), m.get("loss_pct"), m.get("jitter_ms"),
            m.get("http_ok"), m.get("dns_sys_ok"), m.get("dns_ext_ok"),
            "；".join(rs),
        ])
    out.write_text("\ufeff" + buf.getvalue(), encoding="utf-8")
    print(f"已导出 {len(ticks)} 行到 {out}")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    if not args.yes:
        ans = input("确认清空 data/ 下所有采集数据？输入 yes 继续：").strip().lower()
        if ans != "yes":
            print("已取消。")
            return 1
    n = store.purge_all()
    print(f"已删除 {n} 个文件。")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    p = write_default_config(force=args.force)
    print(f"配置文件就绪：{p}")
    return 0


# ------------------------------------------------------------------ 主入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="netmon",
        description="macOS 网络断连定时监控与自动归因工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--version", action="version", version=f"netmon {__version__}")
    p.add_argument("--config", type=Path, default=None, help="指定配置文件路径")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("run", help="常驻采集")
    sp.add_argument("--interval", type=float, default=None, help="采样间隔（秒）")
    sp.add_argument("--duration", type=float, default=None, help="采集时长（秒），不填则一直跑")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("once", help="采集一次并打印")
    sp.add_argument("--save", action="store_true", help="同时落盘")
    sp.set_defaults(func=cmd_once)

    sp = sub.add_parser("report", help="生成 HTML 报告")
    sp.add_argument("--days", type=int, default=None, help="只统计最近 N 天")
    sp.add_argument("--open", action="store_true", help="生成后自动打开")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("status", help="查看服务与数据概况")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("serve", help="本地实时看板")
    sp.add_argument("--port", type=int, default=8899, help="监听端口，默认 8899，仅绑定 127.0.0.1")
    sp.add_argument("--days", type=int, default=1)
    sp.add_argument("--tail", type=int, default=200)
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("install", help="安装 launchd 常驻服务")
    sp.add_argument("--interval", type=int, default=None)
    sp.set_defaults(func=cmd_install)
    sp = sub.add_parser("uninstall", help="卸载 launchd 常驻服务")
    sp.set_defaults(func=cmd_uninstall)

    sp = sub.add_parser("export", help="导出 CSV")
    sp.add_argument("--days", type=int, default=None)
    sp.add_argument("--output", default=None)
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("purge", help="清空采集数据")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_purge)

    sp = sub.add_parser("init", help="生成默认配置文件")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        # serve/run 等 Ctrl+C 退出是正常操作，不该甩 traceback
        print("\n已停止。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
