"""网哨 NetMon — 桌面控制台（tkinter，纯标准库）。

设计约束：浅色主题、lucide 风格线性图标（Canvas 矢量绘制，无 emoji、无图片依赖）、
耗时操作异步执行避免卡界面。所有动作复用现有 netmon 后端接口，不重复造轮子。

样式约定：所有颜色/字号集中在文件顶部 token 区；macOS 上 tk.Button 的
背景色不生效，按钮一律用 Canvas 自绘；布局以 grid 为主，窗口可缩放。

启动：python3 -m netmon.gui
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from . import __version__, analyze, i18n, launchd, probe, report, store, util
from .i18n import t as tr
from .config import CONFIG_PATH, DATA_DIR, REPORT_DIR, load_config

# ================================================================== 样式 token
# 主色（蓝）
BLUE = "#2563EB"
BLUE_DARK = "#1D4ED8"
BLUE_SOFT = "#EFF4FF"
BLUE_PALE = "#93C5FD"
# 成功（绿）
GREEN = "#059669"
GREEN_SOFT = "#ECFDF5"
# 警示（橙）
AMBER = "#D97706"
AMBER_SOFT = "#FFF6E9"
AMBER_LINE = "#FBD9A8"
AMBER_TEXT = "#92400E"
# 危险（红）
RED = "#DC2626"
RED_DARK = "#B91C1C"
RED_SOFT = "#FEF0F0"
# 文字层级：INK 标题/正文 > BODY 正文 > MUTED 辅助 > FAINT 更浅
INK = "#111827"
BODY = "#374151"
MUTED = "#6B7280"
FAINT = "#9CA3AF"
# 背景与线条
BG = "#F4F6F9"
CARD = "#FFFFFF"
LINE = "#E5E7EB"
LINE_DARK = "#CBD2DC"          # 按钮分组分隔线（比常规边框更深）
HOVER = "#F3F6FC"
STOP_BG = "#EEF0F3"          # 已停止胶囊底色
WHITE = "#FFFFFF"

FONT = "PingFang SC"
FONT_MONO = "SF Mono"
# 字号层级：标题 > 卡片大数字 > 正文 > 辅助灰字
F_TITLE = 17
F_HERO = 22
F_BODY = 12
F_SMALL = 10

# 界面快照轮询间隔。数据只在 ticks 文件 mtime 变化时重算，轮询本身很轻。
REFRESH_MS = 5000
HARD_CLASSES = {"link_down", "lan_down", "wan_down", "dns_fail"}
UPTIME_TARGET = 99            # 可用率目标阈值（%）
MIN_SAMPLES = 10              # 可用率着色的最少样本数，低于此值仅作参考
WIN_W, WIN_H = 960, 740       # 初始窗口尺寸
MIN_W, MIN_H = 860, 620       # 最小窗口尺寸，缩放时布局不乱


def _f(size: int, bold: bool = False) -> tuple:
    return (FONT, size, "bold") if bold else (FONT, size)


# ------------------------------------------------------------------ 通用绘制工具
def _round_fill(c: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                r: float, color: str) -> None:
    """在 Canvas 上画一个纯填充圆角矩形（描边同色，避免 arc 径向杂线）。"""
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    c.create_arc(x1, y1, x1 + 2 * r, y1 + 2 * r, start=90, extent=90,
                 fill=color, outline=color)
    c.create_arc(x2 - 2 * r, y1, x2, y1 + 2 * r, start=0, extent=90,
                 fill=color, outline=color)
    c.create_arc(x1, y2 - 2 * r, x1 + 2 * r, y2, start=180, extent=90,
                 fill=color, outline=color)
    c.create_arc(x2 - 2 * r, y2 - 2 * r, x2, y2, start=270, extent=90,
                 fill=color, outline=color)
    c.create_rectangle(x1 + r, y1, x2 - r, y2, fill=color, outline=color)
    c.create_rectangle(x1, y1 + r, x2, y2 - r, fill=color, outline="")


def _ellipsize_middle(text: str, limit: int = 46) -> str:
    """长路径中间省略：保留头尾，中间用 … 代替；未超长则原样返回。"""
    if len(text) <= limit:
        return text
    head = (limit - 1) // 2
    tail = limit - 1 - head
    return f"{text[:head]}…{text[-tail:]}"


# ------------------------------------------------------------------ 线性图标绘制
# 统一 24×24 坐标系，ox/oy 为在目标 Canvas 上的平移量。
def icon_shield(c: tk.Canvas, color: str = BLUE, ox: int = 0, oy: int = 0) -> None:
    """盾牌 + 波纹，应用主标识。"""
    pts = [(20 + ox, 2 + oy), (33 + ox, 7 + oy), (33 + ox, 20 + oy),
           (20 + ox, 35 + oy), (7 + ox, 20 + oy), (7 + ox, 7 + oy)]
    c.create_polygon(pts, outline=color, width=2, fill="")
    c.create_arc(12 + ox, 11 + oy, 28 + ox, 23 + oy, start=200, extent=140,
                 style="arc", outline=color, width=1.8)
    c.create_arc(15 + ox, 14 + oy, 25 + ox, 20 + oy, start=200, extent=140,
                 style="arc", outline=color, width=1.8)
    c.create_oval(18.5 + ox, 16.5 + oy, 21.5 + ox, 19.5 + oy, fill=color)


def icon_play(c: tk.Canvas, color: str = GREEN, ox: int = 0, oy: int = 0) -> None:
    c.create_polygon([8 + ox, 5 + oy, 8 + ox, 19 + oy, 19 + ox, 12 + oy],
                     outline=color, fill=color)


def icon_stop(c: tk.Canvas, color: str = RED, ox: int = 0, oy: int = 0) -> None:
    c.create_rectangle(7 + ox, 7 + oy, 17 + ox, 17 + oy, outline=color, fill=color)


def icon_report(c: tk.Canvas, color: str = BLUE, ox: int = 0, oy: int = 0) -> None:
    c.create_polygon([5 + ox, 3 + oy, 15 + ox, 3 + oy, 19 + ox, 7 + oy,
                      19 + ox, 21 + oy, 5 + ox, 21 + oy], outline=color, width=1.8, fill="")
    c.create_line(15 + ox, 3 + oy, 15 + ox, 7 + oy, 19 + ox, 7 + oy, fill=color, width=1.8)
    for y, w in ((11, 8), (15, 10), (19, 6)):
        c.create_line(8 + ox, y + oy, 8 + ox + w, y + oy, fill=color, width=1.5)


def icon_folder(c: tk.Canvas, color: str = BLUE, ox: int = 0, oy: int = 0) -> None:
    c.create_polygon([3 + ox, 7 + oy, 10 + ox, 7 + oy, 12 + ox, 10 + oy,
                      21 + ox, 10 + oy, 21 + ox, 19 + oy, 3 + ox, 19 + oy],
                     outline=color, width=1.8, fill="")


def icon_pulse(c: tk.Canvas, color: str = BLUE, ox: int = 0, oy: int = 0) -> None:
    c.create_line(2 + ox, 12 + oy, 6 + ox, 12 + oy, 9 + ox, 6 + oy,
                  14 + ox, 18 + oy, 17 + ox, 10 + oy, 22 + ox, 12 + oy,
                  fill=color, width=1.8, joinstyle="round")


def icon_trash(c: tk.Canvas, color: str = RED, ox: int = 0, oy: int = 0) -> None:
    """垃圾桶（lucide 线性风格），用于清除历史数据。"""
    c.create_line(4 + ox, 6 + oy, 20 + ox, 6 + oy, fill=color, width=1.8, capstyle="round")
    c.create_line(9 + ox, 6 + oy, 9 + ox, 4 + oy, 15 + ox, 4 + oy, 15 + ox, 6 + oy,
                  fill=color, width=1.8, capstyle="round")
    c.create_polygon([6 + ox, 6 + oy, 7 + ox, 20 + oy, 17 + ox, 20 + oy, 18 + ox, 6 + oy],
                     outline=color, width=1.8, fill="")
    c.create_line(10 + ox, 10 + oy, 10 + ox, 16 + oy, fill=color, width=1.5, capstyle="round")
    c.create_line(14 + ox, 10 + oy, 14 + ox, 16 + oy, fill=color, width=1.5, capstyle="round")


def icon_check(c: tk.Canvas, color: str = GREEN, ox: int = 0, oy: int = 0) -> None:
    c.create_line(5 + ox, 12 + oy, 10 + ox, 17 + oy, 19 + ox, 7 + oy,
                  fill=color, width=2.2, capstyle="round")


def icon_warn(c: tk.Canvas, color: str = AMBER, ox: int = 0, oy: int = 0) -> None:
    c.create_polygon([12 + ox, 3 + oy, 22 + ox, 20 + oy, 2 + ox, 20 + oy],
                     outline=color, width=1.8, fill="")
    c.create_line(12 + ox, 9 + oy, 12 + ox, 14 + oy, fill=color, width=1.8)
    c.create_oval(11 + ox, 16 + oy, 13 + ox, 18 + oy, fill=color)


# ------------------------------------------------------------------ 自绘按钮
class ToolButton(tk.Canvas):
    """横向标准按钮：图标与文字并排，内边距 ≥14px(左右)/≥8px(上下)。

    macOS 上 tk.Button 的背景色不生效，故整颗按钮用 Canvas 自绘。
    variant: primary(主色蓝) / danger(危险红) / secondary(白底灰边框)。
    disabled 时重绘灰态，并在事件入口拦截点击。
    """

    PAD_X = 14
    PAD_Y = 8
    ICON = 24
    GAP = 8
    HEIGHT = 40

    def __init__(self, parent, icon_fn, text: str, command,
                 variant: str = "secondary"):
        # 估算文字宽度，给一个保守的初始宽度；Configure 时会按实际宽度重绘
        est = self.PAD_X * 2 + self.ICON + self.GAP + len(text) * 13
        super().__init__(parent, width=est, height=self.HEIGHT,
                         bg=CARD, highlightthickness=0)
        self._icon_fn = icon_fn
        self._text = text
        self._command = command
        self._variant = variant
        self._enabled = True
        self._hover = False
        # 用字体度量精确算出「图标 + 间距 + 文字」总宽，实现整体水平居中
        self._font = _f(F_BODY, variant != "secondary")
        self._text_w = tkfont.Font(font=self._font).measure(text)
        self.bind("<Button-1>", self._invoke)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Configure>", lambda e: self._paint())
        self._paint()

    def _palette(self) -> tuple[str, str, str, str]:
        """返回 (背景, 文字, 图标, 边框)。"""
        v = self._variant
        if not self._enabled:
            # 禁用态统一中性灰，避免看起来像可用
            return CARD, FAINT, FAINT, LINE
        if v == "primary":
            return (BLUE_DARK if self._hover else BLUE), WHITE, WHITE, BLUE
        if v == "danger":
            return (RED_DARK if self._hover else RED), WHITE, WHITE, RED
        if v == "warn":
            # 白底红字红图标：警示但不像 danger 那样满红，用于「清除数据」
            return (RED_SOFT if self._hover else CARD), RED, RED, (RED if self._hover else LINE)
        # secondary
        return (BLUE_SOFT if self._hover else CARD), INK, BLUE, (BLUE if self._hover else LINE)

    def _paint(self):
        bg, fg, icon, border = self._palette()
        w = self.winfo_width() or self.winfo_reqwidth()
        h = self.winfo_height() or self.HEIGHT
        self.delete("all")
        # 内缩 1px 画圆角，避免最右/最下边框与圆角被 Canvas 边缘裁切
        x0, y0, x1, y1 = 1, 1, w - 1, h - 1
        # 边框用「外层圆角 + 内层圆角」模拟，避免 arc 描边杂线
        _round_fill(self, x0, y0, x1, y1, 8, border)
        _round_fill(self, x0 + 1, y0 + 1, x1 - 1, y1 - 1, 7, bg)
        # 图标 + 文字作为整体居中：(图标宽 + 间距 + 文字宽) 水平居中
        group_w = self.ICON + self.GAP + self._text_w
        start_x = max(self.PAD_X, (w - group_w) / 2)
        iy = (h - self.ICON) / 2
        self._icon_fn(self, icon, start_x, iy)
        self.create_text(start_x + self.ICON + self.GAP, h / 2 + 1,
                         text=self._text, anchor="w", font=self._font, fill=fg)
        self.configure(cursor="hand2" if self._enabled else "arrow")

    def _invoke(self, _e=None):
        if self._enabled:
            self._command()

    def _on_enter(self, _e):
        if self._enabled:
            self._hover = True
            self._paint()

    def _on_leave(self, _e):
        self._hover = False
        self._paint()

    def set_enabled(self, enabled: bool):
        if self._enabled == enabled:
            return
        self._enabled = enabled
        self._hover = False
        self._paint()


# ------------------------------------------------------------------ 胶囊状态
class StatusPill(tk.Canvas):
    """右上角胶囊标签：浅色背景 + 圆点 + 文字。

    state: stopped(深灰) / running(绿) / alert(红)。文字用主题常量，清晰易读。
    """

    def __init__(self, parent):
        super().__init__(parent, width=126, height=28, bg=CARD, highlightthickness=0)
        self._state = "stopped"
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = 126, 28
        if self._state == "alert":
            bg, dot, fg, text = RED_SOFT, RED, RED, tr("监控异常")
        elif self._state == "running":
            bg, dot, fg, text = GREEN_SOFT, GREEN, GREEN, tr("监控运行中")
        else:
            bg, dot, fg, text = STOP_BG, MUTED, INK, tr("监控已停止")
        _round_fill(self, 0, 0, w, h, h / 2, bg)
        self.create_oval(12, 10, 20, 18, fill=dot)
        self.create_text(28, h / 2 + 1, text=text, anchor="w",
                         font=_f(F_BODY, True), fill=fg)

    def set(self, state: str):
        if state != self._state:
            self._state = state
            self._draw()


# ------------------------------------------------------------------ 悬停提示
class ToolTip:
    """给控件绑定悬停气泡，显示完整文本（如被省略的长路径）。"""

    def __init__(self, widget, text_fn, delay: int = 450):
        self.widget = widget
        self.text_fn = text_fn
        self.delay = delay
        self._tip = None
        self._job = None
        widget.bind("<Enter>", self._enter)
        widget.bind("<Leave>", self._leave)

    def _enter(self, _e):
        self._job = self.widget.after(self.delay, self._show)

    def _leave(self, _e):
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None
        self._hide()

    def _show(self):
        if self._tip:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tip, text=self.text_fn(), font=_f(F_SMALL),
                 bg="#1F2937", fg=WHITE, padx=8, pady=5).pack()

    def _hide(self):
        if self._tip:
            self._tip.destroy()
            self._tip = None


# ------------------------------------------------------------------ 指标卡片
class StatCard(tk.Frame):
    """三张等宽等高的指标卡：标题 / 大数字 / 一行可分段着色的说明。"""

    def __init__(self, parent, title: str):
        super().__init__(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        tk.Label(self, text=title, font=_f(F_SMALL), fg=MUTED, bg=CARD,
                 anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        self.value_lbl = tk.Label(self, text="—", font=_f(F_HERO, True), fg=INK,
                                  bg=CARD, anchor="w")
        self.value_lbl.pack(fill="x", padx=16, pady=(0, 4))
        self._note = tk.Frame(self, bg=CARD)
        self._note.pack(fill="x", padx=16, pady=(0, 4))
        # 第二行辅助说明（如「样本不足，仅供参考」），默认隐藏
        self._subnote = tk.Label(self, text="", font=_f(F_SMALL), fg=FAINT,
                                 bg=CARD, anchor="w")
        self._subnote.pack_forget()

    def set(self, value: str, value_color: str = INK,
            parts: list[tuple[str, str]] | None = None,
            subnote: str | None = None):
        """value 为大数字；parts 为说明段的 (文本, 颜色) 列表；subnote 为第二行灰字。"""
        self.value_lbl.configure(text=value, fg=value_color)
        for w in self._note.winfo_children():
            w.destroy()
        for text, color in (parts or []):
            tk.Label(self._note, text=text, font=_f(F_SMALL), fg=color,
                     bg=CARD, anchor="w").pack(side="left")
        if subnote:
            self._subnote.configure(text=subnote)
            self._subnote.pack(fill="x", padx=16, pady=(0, 12))
        else:
            self._subnote.pack_forget()


# ------------------------------------------------------------------ 自检详情弹窗
def _ok_text(ok, good: str, bad: str, unknown: str = tr("未采集")) -> tuple[str, str]:
    """把三态探针结果转成 (文案, 颜色)：True 绿 / False 红 / None 灰。"""
    if ok is None:
        return unknown, FAINT
    return (good, GREEN) if ok else (bad, RED)


class SelfCheckDialog(tk.Toplevel):
    """展示一次自检的分层实测数据。只读：不写数据、不改变监控状态。"""

    def __init__(self, master, status: str, label: str, reasons: list[str],
                 tick: dict, metrics: dict):
        super().__init__(master)
        self.title(tr("自检详情"))
        self.configure(bg=BG)
        self.transient(master)
        self.resizable(True, True)
        self.minsize(700, 520)
        self._center_on(master, 760, 640)
        self._build(status, label, reasons, tick, metrics)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        self.focus_set()

    def _center_on(self, master, w: int, h: int):
        self.update_idletasks()
        px = master.winfo_rootx() + master.winfo_width() // 2
        py = master.winfo_rooty() + master.winfo_height() // 2
        self.geometry(f"{w}x{h}+{px - w // 2}+{py - h // 2}")

    @staticmethod
    def _card(parent, title: str) -> tk.Frame:
        card = tk.Frame(parent, bg=CARD, highlightthickness=1,
                        highlightbackground=LINE)
        card.pack(fill="x", pady=(0, 10))
        tk.Label(card, text=title, font=_f(F_BODY, True), fg=INK, bg=CARD,
                 anchor="w").pack(fill="x", padx=16, pady=(12, 6))
        return card

    @staticmethod
    def _row(parent, label: str, value: str, color: str = INK, mono: bool = False):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", padx=16, pady=2)
        tk.Label(row, text=label, font=_f(F_SMALL), fg=MUTED, bg=CARD,
                 anchor="w", width=16).pack(side="left")
        font = (FONT_MONO, F_BODY) if mono else _f(F_BODY)
        tk.Label(row, text=value, font=font, fg=color, bg=CARD, anchor="w",
                 justify="left", wraplength=520).pack(side="left", fill="x", expand=True)

    def _build(self, status, label, reasons, tick, metrics):
        color = GREEN if status == "ok" else (RED if status in HARD_CLASSES else AMBER)
        icon_color = color

        # 顶部结果摘要
        head = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        head.pack(fill="x", padx=16, pady=(16, 8))
        head.grid_columnconfigure(1, weight=1)
        canvas = tk.Canvas(head, width=36, height=36, bg=CARD, highlightthickness=0)
        (icon_check if status == "ok" else icon_warn)(canvas, icon_color, 6, 6)
        canvas.grid(row=0, column=0, rowspan=2, padx=(16, 10), pady=(14, 10))
        tk.Label(head, text=tr("自检结果：{label}", label=label), font=_f(F_TITLE, True), fg=color,
                 bg=CARD, anchor="w").grid(row=0, column=1, sticky="w", pady=(14, 0))
        tk.Label(head, text=f"{tick.get('iso') or '—'}  ·  " + tr("本轮耗时 ")
                            + f"{tick.get('elapsed_ms')} ms",
                 font=_f(F_SMALL), fg=MUTED, bg=CARD,
                 anchor="w").grid(row=1, column=1, sticky="w", pady=(4, 14))

        # 可滚动详情区
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        bar = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        self.bind("<MouseWheel>", lambda e: canvas.yview_scroll(
            int(-1 * (e.delta / 120)), "units"))
        self.bind("<Button-4>", lambda _e: canvas.yview_scroll(-3, "units"))
        self.bind("<Button-5>", lambda _e: canvas.yview_scroll(3, "units"))

        # 判定概览
        sec = self._card(inner, tr("判定概览"))
        self._row(sec, tr("状态"), label, color)
        sep = "; " if i18n.is_en() else "；"
        self._row(sec, tr("原因"), sep.join(reasons) if reasons else tr("各项指标正常"),
                  AMBER_TEXT if reasons else GREEN)
        self._row(sec, tr("采样时间"), str(tick.get("iso") or "—"), mono=True)
        self._row(sec, tr("耗时"), f"{tick.get('elapsed_ms')} ms", mono=True)

        # 链路 / 网关
        iface_info = tick.get("iface_info") or {}
        gw = tick.get("gateway") or {}
        arp = gw.get("arp") or {}
        sec = self._card(inner, tr("链路 / 网关"))
        self._row(sec, tr("活动接口"), str(tick.get("iface") or "—"), mono=True)
        self._row(sec, "IPv4", "、".join(iface_info.get("ipv4") or []) or "—", mono=True)
        self._row(sec, tr("默认网关"), str(gw.get("host") or "—"), mono=True)
        signals = "+".join(gw.get("signals") or []) or tr("无（判定不可达）")
        self._row(sec, tr("存活信号"), signals, GREEN if gw.get("alive") else RED)
        self._row(sec, "ARP / MAC", str(arp.get("mac") or tr("无表项")), mono=True)
        text, fg = _ok_text((gw.get("icmp") or {}).get("ok"), tr("有回显"), tr("被屏蔽 / 无响应"))
        self._row(sec, tr("网关 ICMP"), text, fg)
        text, fg = _ok_text((gw.get("tcp") or {}).get("alive"), tr("有连接"), tr("无连接"))
        self._row(sec, tr("网关 TCP"), text, fg)

        # 外网 ICMP / TCP
        targets = tick.get("icmp") or []
        tcp_targets = tick.get("tcp") or []
        if targets or tcp_targets:
            sec = self._card(inner, tr("外网探测（{n} ICMP / {m} TCP）", n=len(targets), m=len(tcp_targets)))
            for t in targets:
                name = str(t.get("name") or t.get("host") or tr("目标"))
                if not t.get("actor"):
                    name += tr("（不参与判定）")
                if t.get("ok"):
                    text = tr("平均 {rtt} ms · 丢包 {loss}%", rtt=t.get('rtt_avg'), loss=t.get('loss_pct'))
                    fg = GREEN
                else:
                    text = tr("无响应 · 丢包 {loss}%", loss=t.get('loss_pct'))
                    if t.get("error"):
                        text += f" · {t['error']}"
                    fg = RED
                self._row(sec, name, text, fg)
            for t in tcp_targets:
                name = f"TCP {t.get('host')}:{t.get('port')}"
                if t.get("ok"):
                    text, fg = f"{t.get('ms')} ms", GREEN
                else:
                    text, fg = tr("失败 · {error}", error=t.get('error')), RED
                self._row(sec, name, text, fg)

        # DNS：系统解析器 / 指定服务器 / 网卡自动下发
        dns_groups = (
            (tr("系统 DNS"), tick.get("dns") or []),
            (tr("指定 DNS"), tick.get("dns_ext") or []),
            (tr("网卡 DNS"), tick.get("dns_auto") or []),
        )
        if any(group for _, group in dns_groups) or metrics.get("dns_note"):
            sec = self._card(inner, tr("DNS 解析"))
            for group_name, entries in dns_groups:
                for d in entries:
                    if d.get("ok") is None:
                        text, fg = tr("未采集 · {error}", error=d.get('error') or tr('不可用')), FAINT
                    elif d.get("ok"):
                        answers = "、".join(d.get("answers") or []) or tr("有应答")
                        text, fg = f"{answers} · {d.get('ms')} ms", GREEN
                    else:
                        text, fg = tr("失败 · {error}", error=d.get('error') or tr('无应答')), RED
                    label_text = f"{group_name} @{d.get('server') or 'system'}"
                    self._row(sec, label_text, text, fg)
            if metrics.get("dns_note"):
                self._row(sec, tr("提示"), str(metrics["dns_note"]), AMBER_TEXT)

        # HTTP 可达性
        http = tick.get("http") or []
        if http:
            sec = self._card(inner, tr("HTTP 可达（{n} 个目标）", n=len(http)))
            for h in http:
                url = _ellipsize_middle(str(h.get("url") or h.get("host") or tr("未知目标")), 40)
                if h.get("ok"):
                    text, fg = f"HTTP {h.get('status')} · {h.get('ms')} ms", GREEN
                else:
                    text, fg = tr("失败 · {error}", error=h.get('error') or tr('无响应')), RED
                self._row(sec, url, text, fg)

        # 无线与接口计数
        wifi = tick.get("wifi") or {}
        counters = tick.get("counters") or {}
        if wifi or counters:
            sec = self._card(inner, tr("无线 / 接口计数"))
            if wifi:
                head_text = " ".join(str(wifi.get(k)) for k in
                                     ("band", "channel", "width", "phymode") if wifi.get(k))
                if head_text:
                    self._row(sec, tr("无线"), head_text)
                for label_text, key, unit in (
                    (tr("信号强度"), "rssi_dbm", "dBm"),
                    (tr("噪声"), "noise_dbm", "dBm"),
                    (tr("信噪比"), "snr_db", "dB"),
                    (tr("发射速率"), "tx_rate_mbps", "Mbps"),
                ):
                    if wifi.get(key) is not None:
                        self._row(sec, label_text, f"{wifi[key]} {unit}")
            if counters:
                self._row(sec, tr("收包/错包"), f"{counters.get('ipkts')} / {counters.get('ierrs')}")
                self._row(sec, tr("发包/错包"), f"{counters.get('opkts')} / {counters.get('oerrs')}")

        # 底部关闭按钮
        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=16, pady=(0, 14))
        ToolButton(foot, icon_check, tr("关闭"), self.destroy, "primary").pack(side="right")


# ------------------------------------------------------------------ 卸载
def _app_bundle() -> Path | None:
    """返回当前运行代码所属的 .app 包路径；源码直跑时返回 None。"""
    for parent in Path(__file__).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def _trash_app_after_exit(bundle: Path) -> None:
    """等本进程退出后，再把 .app 移入废纸篓。

    运行中的包被移走会让后续 import 找不到文件，所以只能等退出。用 /bin/sh 的
    位置参数传路径，避免路径里的空格/中文引号问题。
    """
    script = (
        'while kill -0 "$1" 2>/dev/null; do sleep 0.2; done; '
        'd="$HOME/.Trash/$(basename "$2")"; '
        '[ -e "$d" ] && d="$d-$(date +%s)"; '
        'mv "$2" "$d"'
    )
    subprocess.Popen(
        ["/bin/sh", "-c", script, "netmon-uninstall", str(os.getpid()), str(bundle)],
        start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


class CheckRow(tk.Frame):
    """自绘复选框：macOS 上 tk.Checkbutton 不吃背景色，和浅色卡片拼在一起很脏。"""

    def __init__(self, parent, title: str, hint: str, value: bool = False,
                 accent: str = BLUE, locked: bool = False):
        super().__init__(parent, bg=CARD)
        self.value = value
        self._accent = accent
        self._locked = locked
        self.grid_columnconfigure(1, weight=1)
        self._box = tk.Canvas(self, width=18, height=18, bg=CARD, highlightthickness=0)
        self._box.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 10), pady=(2, 0))
        self._title = tk.Label(self, text=title, font=_f(F_BODY, True), fg=INK,
                               bg=CARD, anchor="w")
        self._title.grid(row=0, column=1, sticky="w")
        self._hint = tk.Label(self, text=hint, font=_f(F_SMALL), fg=FAINT, bg=CARD,
                              anchor="w", justify="left", wraplength=430)
        self._hint.grid(row=1, column=1, sticky="w", pady=(3, 0))
        if not locked:
            for w in (self, self._box, self._title, self._hint):
                w.bind("<Button-1>", self._toggle)
                w.configure(cursor="hand2")
        self._draw()

    def _draw(self):
        self._box.delete("all")
        if self.value:
            self._box.create_rectangle(1.5, 1.5, 16.5, 16.5, fill=self._accent,
                                       outline=self._accent, width=1)
            self._box.create_line(4.6, 9.2, 7.6, 12.4, 13.4, 5.6, fill=WHITE, width=2,
                                  capstyle="round", joinstyle="round")
        else:
            self._box.create_rectangle(1.5, 1.5, 16.5, 16.5, fill=CARD,
                                       outline=LINE_DARK, width=1)

    def _toggle(self, _e=None):
        if self._locked:
            return
        self.value = not self.value
        self._draw()

    def get(self) -> bool:
        return self.value


class UninstallDialog(tk.Toplevel):
    """卸载入口：把「删了 app 但后台服务和数据还在」这个坑一次说清并处理掉。"""

    def __init__(self, master, home: Path, bundle: Path | None, on_confirm):
        super().__init__(master)
        self.title(tr("卸载 NetMon"))
        self.configure(bg=BG)
        self.transient(master)
        self.resizable(False, False)
        self._home = home
        self._bundle = bundle
        self._on_confirm = on_confirm
        self._build()
        self.update_idletasks()
        px = master.winfo_rootx() + master.winfo_width() // 2
        py = master.winfo_rooty() + master.winfo_height() // 2
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f"{w}x{h}+{px - w // 2}+{py - h // 2}")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        self.focus_set()

    def _build(self):
        head = tk.Frame(self, bg=RED_SOFT, highlightthickness=1, highlightbackground=LINE)
        head.pack(fill="x", padx=16, pady=(16, 10))
        ic = tk.Canvas(head, width=24, height=24, bg=RED_SOFT, highlightthickness=0)
        icon_warn(ic, RED, 0, 0)
        ic.pack(side="left", padx=(16, 8), pady=14)
        txt = tk.Frame(head, bg=RED_SOFT)
        txt.pack(side="left", pady=12)
        tk.Label(txt, text=tr("卸载 NetMon"), font=_f(F_BODY, True), fg=RED_DARK,
                 bg=RED_SOFT, anchor="w").pack(anchor="w")
        tk.Label(txt, text=tr("勾选项会被移入废纸篓，可以随时拖回来；不会直接抹掉。"),
                 font=_f(F_SMALL), fg=AMBER_TEXT, bg=RED_SOFT,
                 anchor="w").pack(anchor="w", pady=(3, 0))

        card = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        card.pack(fill="x", padx=16)
        self.row_service = CheckRow(
            card, tr("停止并移除后台服务"),
            tr("{path}（必选，否则它会在每次登录时尝试拉起已删除的应用）", path=launchd.PLIST),
            value=True, locked=True)
        self.row_service.pack(fill="x", padx=16, pady=(14, 10))
        self.row_data = CheckRow(
            card, tr("删除采样数据与报告"),
            f"{self._home}", value=False, accent=RED)
        self.row_data.pack(fill="x", padx=16, pady=(0, 10))
        if self._bundle:
            self.row_app = CheckRow(
                card, tr("删除应用本体"),
                f"{self._bundle}", value=False, accent=RED)
            self.row_app.pack(fill="x", padx=16, pady=(0, 14))
        else:
            self.row_app = None
            tk.Label(card, text=tr("当前是以源码方式运行，不涉及删除应用本体。"),
                     font=_f(F_SMALL), fg=FAINT, bg=CARD, anchor="w").pack(
                fill="x", padx=16, pady=(0, 14))

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=16, pady=(12, 14))
        ToolButton(foot, icon_check, tr("卸载"), self._confirm, "danger").pack(side="right")
        ToolButton(foot, icon_stop, tr("取消"), self.destroy).pack(side="right", padx=(0, 10))

    def _confirm(self):
        data = self.row_data.get()
        app = bool(self.row_app and self.row_app.get())
        if (data or app) and not messagebox.askyesno(
                tr("确认卸载"),
                tr("将把所选内容移入废纸篓：\n\n")
                + (tr("· 全部采样数据与报告\n") if data else "")
                + (tr("· NetMon 应用本体\n") if app else "")
                + tr("\n文件仍可从废纸篓恢复。确定继续吗？"),
                parent=self):
            return
        self.destroy()
        self._on_confirm(data, app)


# ------------------------------------------------------------------ 主应用
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(tr("网哨 NetMon") + f"  ·  {__version__}")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.minsize(MIN_W, MIN_H)
        self._center(WIN_W, WIN_H)
        self._set_window_icon()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._q: queue.Queue = queue.Queue()
        self._busy = False
        self._refreshing = False
        self._running = False
        self._cache_key = None
        self._flash_token = 0     # 内联提示的序号，避免旧定时器清掉新提示
        self._cache_data = None
        self._home_full = str(DATA_DIR)
        self._build_ui()
        self._apply_button_states()
        self._poll_queue()
        self.root.after(50, self._refresh_async)
        self.root.after(REFRESH_MS, self._auto_refresh)
        # 启动后自愈后台服务路径（应用可能被移动过），稍等让界面先画出来
        self.root.after(800, self._reconcile_service)

    def _center(self, w: int, h: int):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2 - 20
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _set_window_icon(self):
        icon = Path(__file__).resolve().parent.parent / "Resources" / "netmon.png"
        if not icon.exists():
            icon = Path(__file__).resolve().parent.parent / "netmon.png"
        if icon.exists():
            try:
                img = tk.PhotoImage(file=str(icon))
                self.root.iconphoto(True, img)
                self._icon_ref = img
            except Exception:
                pass

    # ------------------------------------------------------------------ UI 构建
    def _build_ui(self):
        # ---------- 顶部 header：logo + 标题 + 右上角胶囊状态
        header = tk.Frame(self.root, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        header.pack(fill="x")
        logo = tk.Canvas(header, width=48, height=46, bg=CARD, highlightthickness=0)
        icon_shield(logo, BLUE, 4, 4)
        logo.pack(side="left", padx=(22, 8), pady=12)
        title_box = tk.Frame(header, bg=CARD)
        title_box.pack(side="left", pady=12)
        tk.Label(title_box, text=tr("网哨 NetMon"), font=_f(F_TITLE, True), fg=INK,
                 bg=CARD).pack(anchor="w")
        tk.Label(title_box, text=tr("网络断连监控与自动归因"), font=_f(F_BODY), fg=MUTED,
                 bg=CARD).pack(anchor="w")
        self._status_pill = StatusPill(header)
        self._status_pill.pack(side="right", padx=(0, 22), pady=14)

        # ---------- 主体（grid，行为主，最后一行可伸缩）
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=(16, 10))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(6, weight=1)

        # 运行信息条：进程 PID / 采样间隔 / 数据目录（去掉重复的「后台服务」）
        info = tk.Frame(body, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        info.grid(row=0, column=0, sticky="ew")
        info.grid_columnconfigure(0, weight=0, minsize=120)
        info.grid_columnconfigure(1, weight=0, minsize=120)
        info.grid_columnconfigure(2, weight=1)

        def info_col(index: int, label: str) -> tk.Frame:
            # 统一两行结构：row0 标签、row1 值，三列的标签与值各在同一基线
            col = tk.Frame(info, bg=CARD)
            col.grid(row=0, column=index, sticky="nw", padx=20, pady=13)
            tk.Label(col, text=label, font=_f(F_SMALL), fg=FAINT, bg=CARD,
                     anchor="w").grid(row=0, column=0, sticky="w")
            return col

        self._info_labels = {}
        info_row_h = 0  # 统一值行的网格行号
        col = info_col(0, tr("进程 PID"))
        self._info_labels["pid"] = tk.Label(col, text="—", font=_f(F_BODY, True),
                                             fg=INK, bg=CARD, anchor="w")
        self._info_labels["pid"].grid(row=info_row_h + 1, column=0, sticky="w", pady=(4, 0))
        tk.Frame(info, bg=LINE, width=1).grid(row=0, column=0, sticky="e")
        col = info_col(1, tr("采样间隔"))
        self._info_labels["interval"] = tk.Label(col, text="—", font=_f(F_BODY, True),
                                                   fg=INK, bg=CARD, anchor="w")
        self._info_labels["interval"].grid(row=info_row_h + 1, column=0, sticky="w", pady=(4, 0))
        tk.Frame(info, bg=LINE, width=1).grid(row=0, column=1, sticky="e")
        col = info_col(2, tr("数据目录"))
        home_row = tk.Frame(col, bg=CARD)
        home_row.grid(row=info_row_h + 1, column=0, sticky="w", pady=(4, 0))
        self._info_labels["home"] = tk.Label(home_row, text="—", font=_f(F_BODY, True),
                                              fg=INK, bg=CARD, anchor="w")
        self._info_labels["home"].pack(side="left")
        ToolTip(self._info_labels["home"], lambda: self._home_full)
        open_dir = tk.Label(home_row, text=tr("打开"), font=_f(F_SMALL), fg=BLUE,
                            bg=CARD, cursor="hand2")
        open_dir.pack(side="left", padx=(10, 0))
        open_dir.bind("<Button-1>", lambda _e: self._open_data_dir())

        # 历史数据提示条（浅橙，明显；实时数据时隐藏）
        self._banner = tk.Frame(body, bg=AMBER_SOFT, highlightthickness=1,
                                highlightbackground=AMBER_LINE)
        self._banner.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        bic = tk.Canvas(self._banner, width=24, height=24, bg=AMBER_SOFT,
                        highlightthickness=0)
        icon_warn(bic, AMBER, 0, 0)
        bic.pack(side="left", padx=(14, 6), pady=8)
        self._banner_text = tk.Label(self._banner, text="", font=_f(F_BODY),
                                     fg=AMBER_TEXT, bg=AMBER_SOFT, anchor="w")
        self._banner_text.pack(side="left", pady=8)
        self._banner_strong = tk.Label(self._banner, text=tr("今日暂无采样"),
                                       font=_f(F_BODY, True), fg=AMBER,
                                       bg=AMBER_SOFT, anchor="w")
        self._banner_strong.pack(side="left", pady=8)
        self._banner_tail = tk.Label(self._banner, text=tr("，非实时"), font=_f(F_BODY),
                                     fg=AMBER_TEXT, bg=AMBER_SOFT, anchor="w")
        self._banner_tail.pack(side="left", pady=8)

        # 数据日期窗口说明
        meta = tk.Frame(body, bg=BG)
        meta.grid(row=2, column=0, sticky="ew", pady=(12, 6))
        self._data_meta = tk.Label(meta, text=tr("正在读取数据…"), font=_f(F_SMALL),
                                   fg=MUTED, bg=BG, anchor="w")
        self._data_meta.pack(side="left")

        # 三张等宽等高的指标卡
        stats = tk.Frame(body, bg=BG)
        stats.grid(row=3, column=0, sticky="ew")
        for c in range(3):
            stats.grid_columnconfigure(c, weight=1, uniform="card")
        self.card_ticks = StatCard(stats, tr("采样点"))
        self.card_uptime = StatCard(stats, tr("硬断网可用率"))
        self.card_events = StatCard(stats, tr("异常事件"))
        self.card_ticks.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self.card_uptime.grid(row=0, column=1, sticky="nsew", padx=7)
        self.card_events.grid(row=0, column=2, sticky="nsew", padx=(7, 0))

        # 操作按钮：整行铺满，分三组，中间细竖线；相邻按钮留 10px 水平间距
        BTN_GAP = 10  # 相邻按钮间距（px）
        actions = tk.Frame(body, bg=BG)
        actions.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        for c in (0, 1, 3, 4, 6, 7):
            actions.grid_columnconfigure(c, weight=1, uniform="act")
        for c in (2, 5):
            actions.grid_columnconfigure(c, weight=0, minsize=1)

        def sep(col: int):
            # 分隔线加深（LINE_DARK），上下留白加大到 12px
            tk.Frame(actions, bg=LINE_DARK, width=1).grid(
                row=0, column=col, sticky="ns", pady=12, padx=4)

        self.btn_start = ToolButton(actions, icon_play, tr("启动监控"), self.on_start, "primary")
        self.btn_stop = ToolButton(actions, icon_stop, tr("停止监控"), self.on_stop, "danger")
        self.btn_report = ToolButton(actions, icon_report, tr("生成报告"), self.on_report)
        self.btn_open = ToolButton(actions, icon_folder, tr("报告文件夹"), self.on_open_report)
        self.btn_check = ToolButton(actions, icon_pulse, tr("立即自检"), self.on_check)
        self.btn_clear = ToolButton(actions, icon_trash, tr("清除数据"), self.on_clear, "warn")
        # 组内相邻按钮之间留 BTN_GAP；sep 列自身带 padx，组边也有间距
        self.btn_start.grid(row=0, column=0, sticky="nsew", padx=(0, BTN_GAP / 2))
        self.btn_stop.grid(row=0, column=1, sticky="nsew", padx=(BTN_GAP / 2, 0))
        sep(2)
        self.btn_report.grid(row=0, column=3, sticky="nsew", padx=(0, BTN_GAP / 2))
        self.btn_open.grid(row=0, column=4, sticky="nsew", padx=(BTN_GAP / 2, 0))
        sep(5)
        self.btn_check.grid(row=0, column=6, sticky="nsew", padx=(0, BTN_GAP / 2))
        # 最右按钮右侧留 1px，配合按钮自身内缩 1px，圆角完整且边距与卡片区一致
        self.btn_clear.grid(row=0, column=7, sticky="nsew", padx=(BTN_GAP / 2, 0))

        # 最近事件表头
        log_head = tk.Frame(body, bg=BG)
        log_head.grid(row=5, column=0, sticky="ew", pady=(18, 6))
        tk.Label(log_head, text=tr("最近事件"), font=_f(F_BODY, True), fg=INK,
                 bg=BG).pack(side="left")
        self._log_hint = tk.Label(log_head, text="", font=_f(F_SMALL), fg=BLUE, bg=BG)
        self._log_hint.pack(side="right")

        # 最近事件表格：Canvas + 内部 Frame + 滚动条，固定列对齐
        log_frame = tk.Frame(body, bg=CARD, highlightthickness=1,
                             highlightbackground=LINE)
        log_frame.grid(row=6, column=0, sticky="nsew", pady=(0, 12))
        self._rows_canvas = tk.Canvas(log_frame, bg=CARD, highlightthickness=0)
        self._vbar = ttk.Scrollbar(log_frame, orient="vertical",
                                   command=self._rows_canvas.yview)
        self._rows_canvas.configure(yscrollcommand=self._vbar.set)
        # 滚动条默认不显示，内容溢出时再 pack（见 _update_scrollbar）
        self._rows_canvas.pack(side="left", fill="both", expand=True)
        self._rows = tk.Frame(self._rows_canvas, bg=CARD)
        self._rows_win = self._rows_canvas.create_window((0, 0), window=self._rows,
                                                          anchor="nw")
        self._rows.bind("<Configure>", lambda e: (self._rows_canvas.configure(
            scrollregion=self._rows_canvas.bbox("all")), self._update_scrollbar()))
        self._rows_canvas.bind("<Configure>", lambda e: (self._rows_canvas.itemconfig(
            self._rows_win, width=e.width), self._update_scrollbar()))
        for widget in (self._rows_canvas, self._rows):
            widget.bind("<MouseWheel>", self._on_wheel)
        # 列宽：时间/类型/时长固定，原因 stretch 占满剩余；表头与内容共用同一 grid
        self._rows.grid_columnconfigure(0, minsize=150, weight=0)
        self._rows.grid_columnconfigure(1, minsize=96, weight=0)
        self._rows.grid_columnconfigure(2, minsize=96, weight=0)
        self._rows.grid_columnconfigure(3, weight=1, minsize=120)
        self._header = {}
        for col, text in enumerate((tr("时间"), tr("类型"), tr("时长"), tr("原因"))):
            h = tk.Label(self._rows, text=text, font=_f(F_SMALL, True), fg=MUTED,
                         bg=CARD, anchor="w")
            h.grid(row=0, column=col, sticky="ew", padx=(14, 6), pady=(10, 4))
            self._header[col] = h
        # 空状态：覆盖在表格区域中央，水平垂直居中、浅灰
        self._empty_lbl = tk.Label(self._rows_canvas, text=tr("暂无异常事件"),
                                   font=_f(F_BODY), fg=FAINT, bg=CARD)
        self._empty_win = None

        # 底栏
        foot = tk.Frame(self.root, bg=BG)
        foot.pack(fill="x", padx=22, pady=(0, 12))
        cfg_lbl = tk.Label(foot, text=tr("配置") + f"  {_ellipsize_middle(str(CONFIG_PATH))}",
                           font=_f(F_SMALL), fg=FAINT, bg=BG)
        cfg_lbl.pack(side="left")
        ToolTip(cfg_lbl, lambda: str(CONFIG_PATH))
        # 卸载入口放底栏右侧：低频操作，不占按钮区的视觉重量
        self._uninstall_lbl = tk.Label(foot, text=tr("卸载 NetMon…"), font=_f(F_SMALL),
                                       fg=FAINT, bg=BG, cursor="hand2")
        self._uninstall_lbl.pack(side="right")
        self._uninstall_lbl.bind("<Button-1>", lambda _e: self.on_uninstall())
        self._uninstall_lbl.bind("<Enter>",
                                 lambda _e: self._uninstall_lbl.configure(fg=RED))
        self._uninstall_lbl.bind("<Leave>",
                                 lambda _e: self._uninstall_lbl.configure(fg=FAINT))

    def _on_wheel(self, event):
        self._rows_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _update_scrollbar(self):
        """内容高度超出可视区时才显示滚动条，否则隐藏。"""
        try:
            bbox = self._rows_canvas.bbox("all")
            if not bbox:
                return
            content_h = bbox[3] - bbox[1]
            view_h = self._rows_canvas.winfo_height()
            if content_h > view_h + 2:
                if not self._vbar.winfo_ismapped():
                    self._vbar.pack(side="right", fill="y")
            else:
                if self._vbar.winfo_ismapped():
                    self._vbar.pack_forget()
        except (tk.TclError, TypeError):
            pass

    def _show_empty(self, text: str | None):
        """在表格可视区中央显示/隐藏空状态文字。"""
        if self._empty_win is not None:
            self._rows_canvas.delete(self._empty_win)
            self._empty_win = None
        if text:
            self._empty_lbl.configure(text=text)
            # 延迟到布局完成后按实际可视区尺寸居中
            def place():
                w = self._rows_canvas.winfo_width()
                h = self._rows_canvas.winfo_height()
                if self._empty_win is not None:
                    self._rows_canvas.delete(self._empty_win)
                self._empty_win = self._rows_canvas.create_window(
                    w / 2, h / 2, window=self._empty_lbl, anchor="center")
            self.root.after_idle(place)

    def _open_data_dir(self):
        try:
            subprocess.run(["open", str(DATA_DIR)], check=False)
        except Exception:
            pass

    # ------------------------------------------------------------------ 状态计算
    def _service_state(self) -> tuple[bool, str, str]:
        st = launchd.status()
        pid = store.is_running()
        if st["loaded"]:
            return True, str(st["pid"] or "—"), tr("launchd 常驻服务")
        if pid:
            return True, str(pid), tr("前台采集进程")
        return False, "—", tr("未运行")

    # ------------------------------------------------------------------ 后台快照
    def _snapshot(self) -> dict:
        """在后台线程读取服务状态与数据；主线程只消费结果做渲染。"""
        running, pid, service = self._service_state()
        cfg = load_config()
        snap = {
            "running": running,
            "pid": pid,
            "service": service,
            "interval": tr("{n} 秒", n=cfg.get('interval_seconds', 30)),
            "home": str(DATA_DIR),
            "data": None,
        }

        days = store.available_days()
        if not days:
            return snap

        day = days[-1]
        today = util.day_str(util.now_ts())
        path = store.tick_path(day)
        try:
            st = path.stat()
            cfg_mtime = CONFIG_PATH.stat().st_mtime_ns if CONFIG_PATH.exists() else 0
            key = (day, st.st_mtime_ns, st.st_size, cfg_mtime, today)
        except OSError:
            st = None
            key = None

        if key is not None and key == self._cache_key:
            snap["data"] = self._cache_data
            return snap

        ticks = store.load_ticks([day])
        summary = None
        recent: list[dict] = []
        if ticks:
            incidents = analyze.build_incidents(ticks, cfg)
            s = analyze.summarize(ticks, incidents, cfg)
            if not s.get("empty"):
                summary = s
            for inc in sorted(incidents, key=lambda i: i["start_ts"], reverse=True)[:20]:
                recent.append({
                    "day": inc["day"],
                    "start_hhmm": inc["start_hhmm"],
                    "end_hhmm": inc["end_hhmm"],
                    "label": inc["label"],
                    "class": inc["class"],
                    "duration_human": inc["duration_human"],
                    "reasons": inc.get("reasons") or [],
                })

        data = {
            "day": day,
            "today": today,
            "seeded": bool(st is not None and util.day_str(st.st_mtime) != day),
            "summary": summary,
            "recent": recent,
        }
        if key is not None:
            self._cache_key = key
            self._cache_data = data
        snap["data"] = data
        return snap

    def _refresh_async(self):
        if self._refreshing:
            return
        self._refreshing = True

        def worker():
            try:
                self._q.put(("refresh", self._snapshot()))
            except Exception as exc:  # noqa: BLE001
                self._q.put(("refresh_error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_snapshot(self, snap: dict):
        self._running = snap["running"]
        self._update_status_pill()
        self._info_labels["pid"].configure(text=snap["pid"])
        self._info_labels["interval"].configure(text=snap["interval"])
        self._home_full = snap["home"]
        self._info_labels["home"].configure(text=_ellipsize_middle(self._home_full))

        data = snap.get("data")
        s = data.get("summary") if data else None
        has_hard = any(i["class"] in HARD_CLASSES for i in data["recent"]) if data else False
        self._update_status_pill(has_hard=has_hard)

        if not data or not s:
            for card in (self.card_ticks, self.card_uptime, self.card_events):
                card.set("—", FAINT)
            if data:
                self._data_meta.configure(text=tr("数据日期 {day} · 无有效采样", day=data['day']))
            else:
                self._data_meta.configure(text=tr("暂无采集数据 · 启动监控后开始记录"))
            self._set_banner(data.get("day") if data else None,
                             bool(data and data.get("seeded")))
            self._render_events([], has_data=False)
            self._apply_button_states()
            return

        day = data["day"]
        start_hm = util.hhmm(s["start_ts"])
        end_hm = util.hhmm(s["end_ts"])
        self._data_meta.configure(text=tr("数据日期 {day} · {start}–{end}", day=day, start=start_hm, end=end_hm))
        self._set_banner(day, bool(data["seeded"]))

        coverage = s.get("coverage_pct", 100)
        # 主界面不展示覆盖率：采样连续性是排障/报告关心的指标，放在仪表盘上
        # 容易与旁边「可用率」混淆（用户会把 60% 误读成网络质量）。报告里仍完整呈现。
        self.card_ticks.set(str(s["tick_count"]), BLUE,
                            [(tr("采集窗口 {start}–{end}", start=start_hm, end=end_hm), MUTED)])

        hard = s.get("sample_hard_uptime_pct", s["hard_uptime_pct"])
        tick_count = s["tick_count"]
        if tick_count < MIN_SAMPLES or coverage < 80:
            # 样本不足：数字用中性深灰，附一行灰字提示，不做达标着色
            hard_color = INK
            subnote = tr("样本不足，仅供参考")
        else:
            ok = hard >= UPTIME_TARGET
            hard_color = GREEN if ok else AMBER
            subnote = None
        self.card_uptime.set(f"{hard}%", hard_color,
                             [(tr("目标 ≥ {target}%", target=UPTIME_TARGET), hard_color)],
                             subnote=subnote)

        quality = s["incident_count"] - s["hard_incident_count"]
        hard_n = s["hard_incident_count"]
        note_parts = [(tr("硬断网 "), MUTED), (str(hard_n), RED),
                      (tr("   ·   质量异常 "), MUTED), (str(quality), AMBER)]
        self.card_events.set(str(s["incident_count"]), INK, note_parts)

        self._render_events(data["recent"], has_data=True)
        self._apply_button_states()

    def _update_status_pill(self, has_hard: bool = False):
        if has_hard:
            state = "alert"
        elif self._running:
            state = "running"
        else:
            state = "stopped"
        self._status_pill.set(state)

    def _set_banner(self, day: str | None, seeded: bool):
        """历史/示例数据（今日无采样）时显示浅橙提示条，实时数据隐藏。"""
        stale = bool(day and day != util.day_str(util.now_ts()))
        if day and (stale or seeded):
            self._banner_text.configure(text=tr("当前显示的是历史数据（日期 {day}），", day=day))
            self._banner.grid()
        else:
            self._banner.grid_remove()

    def _render_events(self, recent: list[dict], has_data: bool):
        """渲染事件表格：仅「类型」列着色并带同色圆点，其余列为深灰。"""
        for w in self._rows.grid_slaves():
            if int(w.grid_info().get("row", 0)) >= 1:
                w.destroy()
        empty_msg = None
        if not has_data:
            empty_msg = tr("暂无数据，启动监控后事件会显示在这里。")
        elif not recent:
            empty_msg = tr("暂无异常事件")
        if empty_msg:
            # 空状态：隐藏表头，居中显示浅灰提示
            for h in self._header.values():
                h.grid_remove()
            self._show_empty(empty_msg)
            self._update_scrollbar()
            return

        for h in self._header.values():
            h.grid()
        self._show_empty(None)
        for r, inc in enumerate(recent, start=1):
            hard = inc["class"] in HARD_CLASSES
            kind = tr("硬断网") if hard else tr("质量异常")
            kind_color = RED if hard else AMBER
            reason = tr(next((x for x in inc["reasons"] if x), ""))
            cells = [
                (f"{inc['day'][5:]} {inc['start_hhmm']}", BODY, F_SMALL),
                (f"● {kind}", kind_color, F_BODY),
                (inc["duration_human"], BODY, F_SMALL),
                (reason[:56], BODY, F_SMALL),
            ]
            labels = []
            for col, (text, color, size) in enumerate(cells):
                lbl = tk.Label(self._rows, text=text, font=_f(size, col == 1),
                               fg=color, bg=CARD, anchor="w")
                lbl.grid(row=r, column=col, sticky="w", padx=(14, 6), pady=(7, 7))
                lbl.bind("<MouseWheel>", self._on_wheel)
                labels.append(lbl)
            self._bind_row_hover(labels)
        self._rows_canvas.yview_moveto(0)
        self.root.after_idle(self._update_scrollbar)

    @staticmethod
    def _bind_row_hover(labels: list[tk.Label]):
        """鼠标悬停时整行高亮，离开恢复。"""
        def enter(_e):
            for w in labels:
                w.configure(bg=HOVER)
        def leave(_e):
            for w in labels:
                w.configure(bg=CARD)
        for w in labels:
            w.bind("<Enter>", enter)
            w.bind("<Leave>", leave)

    # ------------------------------------------------------------------ 异步框架
    def _apply_button_states(self):
        idle = not self._busy
        # 运行中：启动置灰、停止高亮；未运行反之。启动用主色，其余次级。
        self.btn_start.set_enabled(idle and not self._running)
        self.btn_stop.set_enabled(idle and self._running)
        for b in (self.btn_report, self.btn_open, self.btn_check, self.btn_clear):
            b.set_enabled(idle)

    def _run_async(self, fn, hint: str):
        if self._busy:
            messagebox.showinfo(tr("请稍候"), tr("上一个操作还在进行中。"))
            return
        self._busy = True
        self._log_hint.configure(text=hint)
        self._apply_button_states()

        def worker():
            try:
                self._q.put(("action", fn()))
            except Exception as exc:  # noqa: BLE001
                self._q.put(("action_error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "refresh":
                    self._refreshing = False
                    self._apply_snapshot(payload)
                    continue
                if kind == "refresh_error":
                    self._refreshing = False
                    continue
                if kind == "quit":
                    self.root.destroy()
                    return
                if kind == "reconcile":
                    self._on_reconcile(payload)
                    continue

                self._busy = False
                self._log_hint.configure(text="")
                if kind == "action_error":
                    messagebox.showerror(tr("操作失败"), str(payload))
                else:
                    self._on_result(payload)
                self._apply_button_states()
                self._refresh_async()
        except queue.Empty:
            pass
        self.root.after(200, self._poll_queue)

    def _flash(self, text: str, color: str = GREEN, hold_ms: int = 5000):
        """在「最近事件」标题右侧显示短暂内联提示，替代模态弹窗。

        带 token 防止先前的定时器把后发的提示提前清掉。multiline 文案压成单行。
        """
        self._flash_token += 1
        token = self._flash_token
        one_line = " ".join(str(text).split())
        self._log_hint.configure(text=one_line, fg=color)

        def clear():
            if self._flash_token == token:
                self._log_hint.configure(text="")
        self.root.after(hold_ms, clear)

    def _on_result(self, res):
        kind, msg = res
        if kind in ("info", "warn"):
            # 结果反馈改为内联短暂提示，不再弹模态框遮挡界面。
            self._flash(msg, AMBER if kind == "warn" else GREEN)
        elif kind == "check":
            SelfCheckDialog(self.root, **msg)
        elif kind == "report":
            if messagebox.askyesno(tr("报告已生成"), f"{msg}\n\n" + tr("现在打开报告吗？")):
                threading.Thread(target=lambda: subprocess.run(["open", msg]),
                                 daemon=True).start()
        elif kind == "uninstall":
            text, exiting = msg
            messagebox.showinfo(tr("卸载完成"), text)
            if exiting:
                self.root.destroy()

    def _auto_refresh(self):
        self._refresh_async()
        self.root.after(REFRESH_MS, self._auto_refresh)

    # ------------------------------------------------------------------ 自愈
    def _reconcile_service(self):
        """应用被移动/改名后，LaunchAgent 里写死的绝对路径会失效。

        KeepAlive 的 job 会每 20 秒重试直到永远，所以必须由 app 自己发现并修正。
        """
        def worker():
            try:
                cfg = load_config()
                payload = launchd.reconcile(int(cfg.get("interval_seconds", 30)), DATA_DIR)
            except Exception as exc:  # noqa: BLE001 - 自愈失败不能影响界面
                payload = {"changed": False, "detail": f"{type(exc).__name__}: {exc}"}
            self._q.put(("reconcile", payload))

        threading.Thread(target=worker, daemon=True).start()

    def _on_reconcile(self, payload: dict):
        if not payload.get("changed"):
            return
        if payload.get("reloaded"):
            self._flash(tr("应用位置已变更，后台服务路径已自动修复，监控继续运行。"), GREEN, 9000)
        else:
            self._flash(tr("应用位置已变更，后台服务路径已自动修复，下次启动监控时生效。"), AMBER, 9000)
        self._refresh_async()

    # ------------------------------------------------------------------ 操作
    def on_start(self):
        def task():
            st = launchd.status()
            if st["loaded"] or not store.run_lock_available():
                return ("info", tr("监控已在运行，无需重复启动。"))
            cfg = load_config()
            interval = int(cfg.get("interval_seconds", 30))
            res = launchd.install(interval, DATA_DIR)
            if not res["ok"]:
                raise RuntimeError(res["stderr"] or res["stdout"] or tr("未知错误"))
            return ("info", tr("监控已启动，采样间隔 {interval} 秒。\n", interval=interval)
                            + tr("后台服务常驻运行，关闭本窗口不会中断监控。"))
        self._run_async(task, tr("正在启动监控…"))

    def on_stop(self):
        def task():
            launchd.uninstall()
            for _ in range(15):
                if not store.is_running():
                    break
                time.sleep(0.1)
            pid = store.is_running()
            if pid:
                return ("warn", tr("后台服务已卸载，但检测到前台采集进程（PID {pid}）仍在运行。\n", pid=pid)
                                + tr("请在启动它的终端按 Ctrl-C 停止。"))
            return ("info", tr("监控已停止，后台服务已卸载。"))
        self._run_async(task, tr("正在停止监控…"))

    def on_report(self):
        def task():
            cfg = load_config()
            days = store.available_days()
            if not days:
                return ("warn", tr("还没有采集数据。请先启动监控并运行一段时间。"))
            # 与仪表盘口径一致：桌面按钮只生成「最新采集日」的报告。
            # 历史数据按天独立成文件；跨天汇总请用命令行 report --days N。
            day = days[-1]
            ticks = store.load_ticks([day])
            if not ticks:
                return ("warn", tr("{day} 没有有效采样点，无法生成报告。", day=day))
            evidence = store.load_evidence([day])
            incidents = analyze.build_incidents(ticks, cfg)
            store.attach_evidence(incidents, evidence)
            env = probe.env_snapshot(cfg)
            paths = report.write(cfg, ticks, incidents, evidence, env)
            return ("report", str(paths[0]))
        self._run_async(task, tr("正在生成报告…"))

    def on_open_report(self):
        def task():
            # 空目录时打开 Finder 会让人以为按钮坏了，直接给一行内联提示。
            reports = sorted(REPORT_DIR.glob("*.html")) if REPORT_DIR.exists() else []
            if not reports:
                return ("warn", tr("还没有报告。请先启动监控，再点「生成报告」。"))
            # 按天生成后会有多份报告，直接打开目录让用户自己选。
            # 不弹确认框：Finder 窗口可见即是反馈，模态框会抢焦点并导致目录窗口被盖住/误以为关闭。
            util.ensure_dir(REPORT_DIR)
            subprocess.Popen(["open", str(REPORT_DIR)])
            return ("quiet", "")
        self._run_async(task, tr("正在打开报告文件夹…"))

    def on_clear(self):
        # 监控运行时禁止清除，避免边归档边写入产生不一致数据。
        if self._running or store.is_running() or launchd.status()["loaded"]:
            messagebox.showwarning(
                tr("无法清除"), tr("监控正在运行，请先点「停止监控」再清除历史数据。"))
            return
        days = store.available_days()
        reports = sorted(REPORT_DIR.glob("*.html")) if REPORT_DIR.exists() else []
        if not days and not reports:
            messagebox.showinfo(tr("无需清除"), tr("当前没有历史数据或报告。"))
            return
        detail = (tr("将归档 {days} 天采样/取证数据、{reports} 份报告。\n\n",
                    days=len(days), reports=len(reports))
                  + tr("文件会被移入 NetMon 数据目录下的时间戳备份文件夹，可随时找回；\n")
                  + tr("不会真正删除，日志也会保留。\n\n确定继续吗？"))
        if not messagebox.askyesno(tr("清除历史数据"), detail):
            return

        def task():
            backup, n_data, n_report = store.archive_all()
            return ("info", tr("已归档 {data} 个数据文件、{reports} 份报告。\n",
                              data=n_data, reports=n_report)
                            + tr("备份位置：{path}", path=backup))
        self._run_async(task, tr("正在清除历史数据…"))

    def on_uninstall(self):
        UninstallDialog(self.root, Path(self._home_full), _app_bundle(), self._do_uninstall)

    def _do_uninstall(self, remove_data: bool, remove_app: bool):
        def task():
            notes = []
            res = launchd.uninstall()
            notes.append(tr("后台服务已停止并移除") if res["removed"] else tr("后台服务未注册，无需移除"))

            if remove_data:
                home = Path(self._home_full)
                if home.exists():
                    try:
                        dest = util.move_to_trash(home)
                        notes.append(tr("数据与报告已移入废纸篓：{dest}", dest=dest))
                    except OSError as exc:
                        notes.append(tr("数据移动失败：{exc}", exc=exc))
                else:
                    notes.append(tr("数据目录不存在，已跳过"))

            if remove_app and _app_bundle():
                # 运行中的包被移走会让后续 import 找不到文件，交给退出后的守护脚本处理
                _trash_app_after_exit(_app_bundle())
                notes.append(tr("应用本体将在退出后移入废纸篓"))
            return ("uninstall", ("\n".join(notes), bool(remove_app)))
        self._run_async(task, tr("正在卸载…"))

    def on_check(self):
        def task():
            cfg = load_config()
            tick = probe.collect(cfg, 0)
            status, reasons, metrics = analyze.classify(tick, cfg)
            label = tr(analyze.STATUS_LABEL.get(status, status))
            # 详情交给自检弹窗渲染；采集与判定逻辑保持不变。仅翻译展示文案。
            reasons = [tr(r) for r in reasons]
            return ("check", {
                "status": status, "label": label, "reasons": reasons,
                "tick": tick, "metrics": metrics,
            })
        self._run_async(task, tr("正在采集自检数据…"))

    def on_close(self):
        if self._running and messagebox.askyesno(
                tr("退出网哨"),
                tr("监控正在后台运行。\n\n") +
                tr("「是」— 同时停止监控并退出\n") +
                tr("「否」— 仅关闭窗口，监控继续在后台运行")):
            def stop_then_exit():
                try:
                    launchd.uninstall()
                finally:
                    self._q.put(("quit", None))

            threading.Thread(target=stop_then_exit, daemon=False).start()
            return
        self.root.destroy()


def main() -> int:
    if sys.version_info < (3, 9):
        print(tr("需要 Python 3.9+"), file=sys.stderr)
        return 2
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
