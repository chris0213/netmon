"""分层探针：链路层 / 网关层 / 外网层 / DNS 层 / 无线层。

设计要点（依据 macOS 实测行为）：
1. 路由器普遍屏蔽 ICMP，网关存活以 ARP 表项为主判据，ICMP 仅作参考。
2. ICMP 目标分权（weight）与投票（actor），避免单一目标被墙导致误判。
3. 每轮所有探针并行执行，保证是同一时刻的快照，且单轮耗时可控。
4. 只使用系统自带工具 + 标准库，不引入任何第三方依赖。
"""

from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import util

# 模块级缓存：Wi-Fi 型号等静态信息、上次 Wi-Fi 采样值
_CACHE: dict = {
    "wifi_device": None,
    "hardware_ports": None,
    "wifi_last": {},
    "env_snapshot": None,
}

_PING_TIME_RE = re.compile(r"time=([0-9.]+)\s*ms")
_PING_LOSS_RE = re.compile(r"([0-9.]+)%\s*packet loss")
_PING_RTT_RE = re.compile(
    r"round-trip\s+min/avg/max/stddev\s*=\s*([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+)"
)


# ------------------------------------------------------------------ 基础解析

def parse_ping(text: str) -> dict:
    loss_m = _PING_LOSS_RE.search(text or "")
    rtt_m = _PING_RTT_RE.search(text or "")
    times = [float(x) for x in _PING_TIME_RE.findall(text or "")]
    tx_m = re.search(r"(\d+) packets transmitted,\s*(\d+) packets received", text or "")
    sent = int(tx_m.group(1)) if tx_m else len(times)
    recv = int(tx_m.group(2)) if tx_m else len(times)
    return {
        "sent": sent,
        "recv": recv,
        "loss_pct": float(loss_m.group(1)) if loss_m else (100.0 if sent and not recv else 0.0),
        "rtt_min": round(float(rtt_m.group(1)), 2) if rtt_m else None,
        "rtt_avg": round(float(rtt_m.group(2)), 2) if rtt_m else None,
        "rtt_max": round(float(rtt_m.group(3)), 2) if rtt_m else None,
        "rtt_stddev": round(float(rtt_m.group(4)), 2) if rtt_m else None,
        "samples": times,
        "ok": recv > 0,
    }


def do_ping(host: str, count: int = 2, wait_ms: int = 800, total_s: int = 2) -> dict:
    r = util.run_cmd(
        ["ping", "-n", "-c", str(count), "-W", str(wait_ms), "-t", str(total_s), host],
        timeout=total_s + 2.5,
    )
    out = parse_ping(r["stdout"] + "\n" + r["stderr"])
    out["host"] = host
    out["cmd_ms"] = r["ms"]
    out["error"] = None if out["sent"] else (r["stderr"] or "no reply").strip()[:120]
    return out


def parse_route(text: str) -> dict:
    info: dict = {}
    for line in (text or "").splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            info[k.strip()] = v.strip()
    return {
        "gateway": info.get("gateway"),
        "interface": info.get("interface"),
        "destination": info.get("destination"),
        "flags": info.get("flags", ""),
    }


def parse_arp(text: str, host: str) -> dict:
    """返回目标 IP 的 ARP 表项。有 MAC 即证明二层可达。

    典型输出：? (192.168.1.1) at 00:11:22:33:44:55 on en0 ifscope [ethernet]
    注意：第一列在 -n 模式下是 "?"，目标 IP 固定落在第二列。
    另外 macOS 上 `arp -n <单个IP>` 对 ifscope 表项会误报 no entry，必须取全表再筛。
    """
    want = f"({host})"
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] == want and parts[2] == "at":
            mac = parts[3]
            if mac == "(incomplete)":
                return {"resolved": False, "mac": None, "raw": line.strip()}
            return {"resolved": True, "mac": mac, "raw": line.strip()}
    return {"resolved": False, "mac": None, "raw": ""}


def parse_ifconfig(text: str) -> dict:
    out: dict = {"ipv4": [], "status": None, "media": None, "ether": None, "mtu": None}
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("inet "):
            out["ipv4"].append(s.split()[1])
        elif s.startswith("status:"):
            out["status"] = s.split(":", 1)[1].strip()
        elif s.startswith("media:"):
            out["media"] = s.split(":", 1)[1].strip()
        elif s.startswith("ether "):
            out["ether"] = s.split()[1]
        elif s.startswith("mtu "):
            out["mtu"] = s.split()[1]
    return out


def parse_netstat_iface(text: str, iface: str) -> dict:
    """取 netstat -ib 中该接口的链路层计数行（含 Ierrs/Oerrs/Coll）。"""
    for line in (text or "").splitlines():
        f = line.split()
        if len(f) >= 10 and f[0] == iface and f[2].startswith("<Link#"):
            def num(i: int) -> int | None:
                try:
                    return int(f[i])
                except (ValueError, IndexError):
                    return None
            return {
                "ipkts": num(4), "ierrs": num(5), "ibytes": num(6),
                "opkts": num(7), "oerrs": num(8), "obytes": num(9),
                "coll": num(10) if len(f) > 10 else None,
            }
    return {}


# ------------------------------------------------------------------ 无线层

def wifi_device() -> str | None:
    if _CACHE.get("wifi_device"):
        return _CACHE["wifi_device"]
    r = util.run_cmd(["networksetup", "-listallhardwareports"], timeout=4)
    dev = None
    cur_port = None
    ports = []
    for line in r["stdout"].splitlines():
        line = line.strip()
        if line.startswith("Hardware Port:"):
            cur_port = line.split(":", 1)[1].strip()
        elif line.startswith("Device:"):
            d = line.split(":", 1)[1].strip()
            ports.append({"port": cur_port, "device": d})
            if cur_port and cur_port.lower() in ("wi-fi", "airport"):
                dev = d
    _CACHE["hardware_ports"] = ports
    _CACHE["wifi_device"] = dev
    return dev


def sample_wifi() -> dict:
    """采样当前 Wi-Fi 状态。airport 工具在新版 macOS 已移除，改用 system_profiler。"""
    dev = wifi_device()
    res: dict = {
        "device": dev,
        "connected": None,
        "ssid_redacted": False,
        "rssi_dbm": None,
        "noise_dbm": None,
        "snr_db": None,
        "channel": None,
        "band": None,
        "width": None,
        "phymode": None,
        "tx_rate_mbps": None,
        "mcs": None,
        "security": None,
        "country": None,
        "neighbors": None,
        "error": None,
    }
    if not dev:
        res["error"] = "no_wifi_device"
        return res

    r = util.run_cmd(["system_profiler", "SPAirPortDataType", "-json"], timeout=20)
    if not r["ok"] or not r["stdout"].strip():
        res["error"] = "system_profiler_failed"
        return res
    try:
        data = json.loads(r["stdout"])
        ifaces = data["SPAirPortDataType"][0].get("spairport_airport_interfaces", [])
        body = next((i for i in ifaces if i.get("_name") == dev), ifaces[0] if ifaces else {})
    except (json.JSONDecodeError, KeyError, IndexError, StopIteration):
        res["error"] = "parse_failed"
        return res

    status = body.get("spairport_status_information", "")
    res["connected"] = status == "spairport_status_connected"
    cur = body.get("spairport_current_network_information") or {}
    if cur:
        name = cur.get("_name", "")
        res["ssid_redacted"] = name.startswith("<redacted>")
        res["ssid"] = None if res["ssid_redacted"] else name

        sn = cur.get("spairport_signal_noise", "")
        m = re.match(r"\s*(-?\d+)\s*dBm\s*/\s*(-?\d+)\s*dBm", sn)
        if m:
            res["rssi_dbm"] = int(m.group(1))
            res["noise_dbm"] = int(m.group(2))
            res["snr_db"] = res["rssi_dbm"] - res["noise_dbm"]

        ch = cur.get("spairport_network_channel", "")
        cm = re.match(r"(\d+)\s*\(([^,]+),\s*([^)]+)\)", ch)
        if cm:
            res["channel"] = int(cm.group(1))
            res["band"] = cm.group(2).strip()
            res["width"] = cm.group(3).strip()

        res["phymode"] = cur.get("spairport_network_phymode")
        res["tx_rate_mbps"] = cur.get("spairport_network_rate")
        res["mcs"] = cur.get("spairport_network_mcs")
        res["country"] = cur.get("spairport_network_country_code")
        res["security"] = (cur.get("spairport_security_mode") or "").replace(
            "spairport_security_mode_", ""
        )

    if _CACHE["hardware_ports"]:
        res["other_networks"] = len(body.get("spairport_airport_other_local_wireless_networks", []))
    res["phymodes_supported"] = body.get("spairport_supported_phymodes")
    return res


def collect_wifi(cfg: dict, tick_index: int) -> dict:
    every = max(1, int(cfg["wifi"].get("sample_every_ticks", 2)))
    if not cfg["wifi"].get("enabled", True):
        return {}
    stale = _CACHE.get("wifi_last") or {}
    fresh_enough = stale.get("_tick") is not None and (tick_index - stale["_tick"]) < every
    if fresh_enough and stale.get("connected") is not None:
        out = dict(stale)
        out["cached"] = True
        return out
    w = sample_wifi()
    w["_tick"] = tick_index
    w["cached"] = False
    _CACHE["wifi_last"] = w
    return w


# ------------------------------------------------------------------ 各层探针

def probe_tcp_liveness(host: str, ports: list[int], timeout: float = 1.0) -> dict:
    """用 TCP 判断主机存活。

    关键点：连接被 RST 拒绝（ConnectionRefused）恰恰证明对方 IP 栈是活的 ——
    路由器关掉了 80/443 却仍会回 RST。这比 ping 可靠得多，因为 ICMP 常被策略屏蔽。
    """
    detail: dict[int, dict] = {}

    def one(p: int) -> tuple[int, dict]:
        return p, probe_tcp(host, p, timeout)

    with ThreadPoolExecutor(max_workers=max(1, len(ports))) as ex:
        for p, r in ex.map(one, ports):
            detail[p] = r

    alive_ports = [p for p, r in detail.items()
                   if r["ok"] or r["error"] == "ConnectionRefusedError"]
    return {
        "alive": bool(alive_ports),
        "alive_ports": alive_ports,
        "detail": detail,
    }


def probe_gateway(cfg: dict, gw: str | None) -> dict:
    """网关存活判定：ARP 表项 OR ICMP 回显 OR TCP RST，三取一即为活。

    这里刻意做冗余，因为不同路由器屏蔽的东西不一样：
      - 大部分路由器屏蔽 ICMP → 只能靠 ARP 或 TCP RST
      - 部分环境不允许读 ARP 表（沙箱/权限）→ 退回 ICMP + TCP
    三种信号全灭，才判定网关真的不可达。
    """
    if not gw:
        return {"host": None, "arp": None, "icmp": None, "tcp": None,
                "alive": None, "method": None, "signals": []}

    # 先触发一次二层解析，让 ARP 表项尽快出现
    util.run_cmd(["ping", "-n", "-c", "1", "-W", "300", "-t", "1", gw], timeout=2.5)

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_arp = ex.submit(lambda: parse_arp(util.run_cmd(["arp", "-an"], timeout=3)["stdout"], gw))
        f_icmp = ex.submit(do_ping, gw, 2, 600, 2)
        f_tcp = ex.submit(probe_tcp_liveness, gw, [80, 443, 22, 53], 1.0)
        arp_entry = f_arp.result()
        icmp = f_icmp.result()
        tcp = f_tcp.result()

    signals = []
    if arp_entry["resolved"]:
        signals.append("arp")
    if icmp["ok"]:
        signals.append("icmp")
    if tcp["alive"]:
        signals.append("tcp_rst")
    method = signals[0] if signals else "none"

    return {
        "host": gw,
        "arp": arp_entry,
        "icmp": icmp,
        "tcp": tcp,
        "alive": bool(signals),
        "method": method,
        "signals": signals,
        "icmp_answered": bool(icmp["ok"]),
    }


def probe_icmp_target(spec: dict) -> dict:
    p = do_ping(spec["host"], count=2, wait_ms=800, total_s=2)
    return {
        "name": spec.get("name") or spec["host"],
        "host": spec["host"],
        "weight": float(spec.get("weight", 1.0)),
        "actor": bool(spec.get("actor", True)),
        **{k: v for k, v in p.items() if k != "samples"},
    }


def probe_tcp(host: str, port: int, timeout: float = 2.0) -> dict:
    t0 = time.monotonic()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return {"host": host, "port": port, "ok": True,
                "ms": round((time.monotonic() - t0) * 1000, 1), "error": None}
    except (socket.timeout, TimeoutError):
        return {"host": host, "port": port, "ok": False,
                "ms": round((time.monotonic() - t0) * 1000, 1), "error": "timeout"}
    except OSError as exc:
        return {"host": host, "port": port, "ok": False,
                "ms": round((time.monotonic() - t0) * 1000, 1), "error": type(exc).__name__}
    finally:
        s.close()


def probe_dns(name: str, server: str | None, timeout_s: int = 3) -> dict:
    cmd = ["dig", f"+time={timeout_s}", "+tries=1", "+short"]
    if server:
        cmd += [f"@{server}"]
    cmd.append(name)
    r = util.run_cmd(cmd, timeout=timeout_s + 3)
    answers = [ln for ln in r["stdout"].splitlines() if ln.strip()]
    status = None
    for line in (r["stdout"] + r["stderr"]).splitlines():
        m = re.search(r"status:\s*(\w+)", line)
        if m:
            status = m.group(1)
    return {
        "name": name,
        "server": server or "system",
        "ok": bool(answers),
        "answers": answers[:4],
        "status": status,
        "ms": r["ms"],
        "error": None if answers else (status or r["stderr"].strip()[:80] or "no_answer"),
    }


def probe_http(url: str, timeout_s: float = 4.0) -> dict:
    t0 = time.monotonic()
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "netmon/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            resp.read(64)
            return {"url": url, "ok": True, "status": resp.status,
                    "ms": round((time.monotonic() - t0) * 1000, 1), "error": None}
    except urllib.error.HTTPError as exc:
        # 有 HTTP 响应码本身就说明链路是通的
        return {"url": url, "ok": exc.code < 500, "status": exc.code,
                "ms": round((time.monotonic() - t0) * 1000, 1), "error": f"http_{exc.code}"}
    except Exception as exc:  # noqa: BLE001 - 探针必须永不抛出
        return {"url": url, "ok": False, "status": None,
                "ms": round((time.monotonic() - t0) * 1000, 1), "error": type(exc).__name__}


def probe_route_interface(iface: str | None) -> dict:
    if not iface:
        return {}
    r = util.run_cmd(["ifconfig", iface], timeout=4)
    info = parse_ifconfig(r["stdout"])
    info["name"] = iface
    info["up"] = "UP" in (r["stdout"].splitlines()[0] if r["stdout"] else "")
    info["running"] = "RUNNING" in (r["stdout"].splitlines()[0] if r["stdout"] else "")
    return info


def probe_counters(iface: str) -> dict:
    r = util.run_cmd(["netstat", "-ib"], timeout=5)
    return parse_netstat_iface(r["stdout"], iface)


def probe_reachability_flags() -> dict:
    """scutil --nwi：系统自身对网络可达性的判断。"""
    r = util.run_cmd(["scutil", "--nwi"], timeout=4)
    txt = r["stdout"]
    return {
        "primary_ipv4": "IPv4 network interface information" in txt,
        "ipv6_only": "IPv6 network interface information" in txt and "IPv4" not in txt,
        "raw_len": len(txt),
    }


# ------------------------------------------------------------------ 环境快照

def env_snapshot(cfg: dict) -> dict:
    if _CACHE.get("env_snapshot"):
        return _CACHE["env_snapshot"]
    route = parse_route(util.run_cmd(["route", "-n", "get", "default"], timeout=4)["stdout"])
    dns_r = util.run_cmd(["scutil", "--dns"], timeout=5)
    nameservers: list[str] = []
    for line in dns_r["stdout"].splitlines():
        m = re.search(r"nameserver\[\d+\]\s*:\s*(\S+)", line)
        if m and m.group(1) not in nameservers:
            nameservers.append(m.group(1))
    dev = wifi_device()
    ports = _CACHE.get("hardware_ports") or []
    wifi_card = None
    r = util.run_cmd(["system_profiler", "SPAirPortDataType", "-json"], timeout=20)
    try:
        d = json.loads(r["stdout"])["SPAirPortDataType"][0]["spairport_airport_interfaces"][0]
        wifi_card = d.get("spairport_wireless_card_type")
        wifi_fw = d.get("spairport_wireless_firmware_version")
    except Exception:  # noqa: BLE001
        wifi_fw = None
    snap = {
        "route": route,
        "interface": route.get("interface"),
        "gateway": route.get("gateway"),
        "nameservers": nameservers,
        "wifi_device": dev,
        "wifi_card": wifi_card,
        "wifi_firmware": wifi_fw,
        "hardware_ports": ports,
        "captured_at": util.iso(util.now_ts()),
        "config_path": cfg.get("_config_path"),
    }
    _CACHE["env_snapshot"] = snap
    return snap


# ------------------------------------------------------------------ 单轮采集

def collect(cfg: dict, tick_index: int = 0) -> dict:
    """采集一轮完整快照。所有探针并行，耗时 ≈ 最慢的那个（约 2-4 秒）。"""
    t_start = util.now_ts()

    route = parse_route(util.run_cmd(["route", "-n", "get", "default"], timeout=4)["stdout"])
    iface = cfg.get("interface")
    if not iface or iface == "auto":
        iface = route.get("interface")
    gw = route.get("gateway")

    icmp_specs = list(cfg.get("icmp_targets", []))
    dns_cfg = cfg.get("dns", {})
    http_cfg = cfg.get("http", {})

    tasks: list[tuple[str, object, tuple]] = []
    tasks.append(("gateway", probe_gateway, (cfg, gw)))
    for spec in icmp_specs:
        tasks.append(("icmp", probe_icmp_target, (spec,)))
        if spec.get("tcp_port"):
            tasks.append(("tcp", probe_tcp, (spec["host"], int(spec["tcp_port"]), 2.0)))
    for name in dns_cfg.get("names", []):
        tasks.append(("dns", probe_dns, (name, None, int(dns_cfg.get("timeout_seconds", 3)))))
    for srv in dns_cfg.get("external_servers", []):
        first_name = (dns_cfg.get("names") or ["www.baidu.com"])[0]
        tasks.append(("dns_ext", probe_dns, (first_name, srv, int(dns_cfg.get("timeout_seconds", 3)))))
    for url in http_cfg.get("urls", []):
        tasks.append(("http", probe_http, (url, float(http_cfg.get("timeout_seconds", 4)))))

    results: dict[str, list] = {"gateway": [], "icmp": [], "tcp": [], "dns": [], "dns_ext": [], "http": []}
    with ThreadPoolExecutor(max_workers=min(16, len(tasks) + 2)) as ex:
        futures = [(kind, ex.submit(fn, *args)) for kind, fn, args in tasks]
        for kind, fut in futures:
            try:
                results[kind].append(fut.result(timeout=30))
            except Exception as exc:  # noqa: BLE001
                results[kind].append({"ok": False, "error": type(exc).__name__})

    # 串行部分：接口状态 / 计数器 / 无线
    iface_info = probe_route_interface(iface)
    physical = iface
    if iface and re.match(r"^(utun|ppp|ipsec|tun)", iface):
        for cand in ("en0", "en1", "en2", "en3", "en4", "en5"):
            info = probe_route_interface(cand)
            if info.get("ipv4"):
                physical = cand
                iface_info = info
                break
    counters = probe_counters(physical) if physical else {}
    wifi = collect_wifi(cfg, tick_index) if re.match(r"^en\d", physical or "") else {}
    nwi = probe_reachability_flags()

    return {
        "ts": t_start,
        "iso": util.iso(t_start),
        "tick": tick_index,
        "route": route,
        "iface": physical,
        "route_iface": iface,
        "gateway": results["gateway"][0] if results["gateway"] else {},
        "icmp": results["icmp"],
        "tcp": results["tcp"],
        "dns": results["dns"],
        "dns_ext": results["dns_ext"],
        "http": results["http"],
        "iface_info": iface_info,
        "counters": counters,
        "wifi": wifi,
        "nwi": nwi,
        "elapsed_ms": round((util.now_ts() - t_start) * 1000, 1),
    }
