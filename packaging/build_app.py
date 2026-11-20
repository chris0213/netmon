#!/usr/bin/env python3
"""把 netmon 打包成自包含的 macOS .app 应用（纯标准库）。

产物：NetMon.app —— 双击即可打开图形控制台。
  Contents/MacOS/launcher        启动脚本（注入 NETMON_HOME，拉起 GUI）
  Contents/Resources/python/     内嵌 Python 运行时（含 Tcl/Tk，约 42MB）
  Contents/Resources/app/        内嵌 netmon 源码包 + config.json
  Contents/Resources/NetMon.icns 应用图标
  Contents/Info.plist            应用元数据

设计要点：
  - 自包含：解释器与 Tcl/Tk 随包分发，对方机器无需安装 Python。系统自带的
    Tk 8.5 在高分屏上会白屏，内嵌的 Tk 9.x 不会。
  - 代码与数据分离：数据落在 ~/Library/Application Support/NetMon，
    通过 NETMON_HOME 环境变量注入，GUI 与 launchd 后台采集共用。
  - 零第三方依赖：内嵌运行时是精简过的标准库，site-packages 留空。
  - 不附带任何历史数据：安装后从零开始采集，报告只反映真实运行。

用法：
  python3 packaging/build_app.py                    # 生成 dist/NetMon.app
  python3 packaging/build_app.py --out <dir>        # 指定输出目录
  python3 packaging/build_app.py --no-embed         # 不内嵌运行时（依赖宿主机 python3）
  python3 packaging/build_app.py --python-home <p>  # 指定用于内嵌的运行时
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP_NAME = "NetMon.app"
VERSION = "1.0.0"
IDENTIFIER = "com.netmon.desktop"

# 同目录的运行时收集器（构建期工具，不进 .app）
sys.path.insert(0, str(HERE))
import embed_python  # noqa: E402

LAUNCHER = r'''#!/bin/sh
# NetMon 桌面版启动器：优先使用随包分发的 Python，注入数据目录，拉起图形控制台。
set -e

RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
APPDIR="$RES/app"
HOME_DATA="${NETMON_HOME:-$HOME/Library/Application Support/NetMon}"

# 随包分发的运行时（Contents/Resources/python）自带 Tcl/Tk 9.x：与本机是否
# 安装过 Python 无关，也不会出现系统 Tk 8.5 的白屏。
EMBED_PY="$RES/python/bin/python3.13"
if [ -x "$EMBED_PY" ]; then
  PY="$EMBED_PY"
  BEST_SCORE=999
  PY_SOURCE="embedded"
else
  PY_SOURCE="system"
  # 回退路径（--no-embed 构建专用）。macOS 自带的 /usr/bin/python3 是 Tk 8.5，
  # 在 Apple Silicon + 高分屏上会渲染出「白屏窗口」，因此按 Tk 版本打分择优。
  CANDIDATES="
/usr/bin/python3
/opt/homebrew/bin/python3
/usr/local/bin/python3
$(command -v python3 2>/dev/null || true)
$(ls -d /Library/Frameworks/Python.framework/Versions/*/bin/python3 2>/dev/null | sort -r)
$(ls -d "$HOME"/.workbuddy/binaries/python/versions/*/bin/python3 2>/dev/null | sort -r)
"
  PY=""
  BEST_SCORE=-1
  for c in $CANDIDATES; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    TKVER="$("$c" -c 'import tkinter; print(tkinter.TkVersion)' 2>/dev/null || true)"
    [ -n "$TKVER" ] || continue
    MAJOR="${TKVER%%.*}"
    MINOR="${TKVER#*.}"; MINOR="${MINOR%%.*}"
    # Tk 9.x -> 900+, 8.6 -> 860, 8.5 -> 850
    SCORE=$(( MAJOR * 100 + MINOR ))
    if [ "$SCORE" -gt "$BEST_SCORE" ]; then
      BEST_SCORE="$SCORE"; PY="$c"
    fi
  done

  if [ -z "$PY" ]; then
    osascript -e 'display dialog "未找到可用的 Python 运行时。\n\n请改用自包含版本（无需本机安装 Python），或安装 python.org 的 Python 3：https://www.python.org/downloads/macos/" buttons {"好"} default button 1 with icon stop' >/dev/null 2>&1 || true
    exit 1
  fi
  # Tk 8.5 会白屏，若只找到它则明确告警并给出解决办法（仍尝试运行，交由用户判断）
  if [ "$BEST_SCORE" -lt 860 ]; then
    osascript -e 'display dialog "当前系统 Python 的图形组件版本过旧（Tk 8.5），界面可能显示为白屏。\n\n请改用自包含版本的 NetMon，或安装 python.org 的 Python 3 后重新打开：https://www.python.org/downloads/macos/" buttons {"知道了"} default button 1 with icon caution' >/dev/null 2>&1 || true
  fi
fi

# 首次运行：准备数据目录与默认配置（从内嵌副本拷贝，不覆盖已有配置）
mkdir -p "$HOME_DATA/data" "$HOME_DATA/reports"
if [ ! -f "$HOME_DATA/config.json" ] && [ -f "$APPDIR/config.json" ]; then
  cp "$APPDIR/config.json" "$HOME_DATA/config.json"
fi

export NETMON_HOME="$HOME_DATA"
# 只保留内嵌源码目录，剔除宿主环境（如 WorkBuddy 沙箱 shim）可能注入的 PYTHONPATH
export PYTHONPATH="$APPDIR"
export PYTHONUNBUFFERED=1
# 屏蔽 Tk 的弃用告警，保持控制台干净
export TK_SILENCE_DEPRECATION=1
# 宿主 shell 若设了 PYTHONHOME，会覆盖内嵌运行时的自定位，必须清掉
unset PYTHONHOME
# 不加载用户级 site-packages，避免与本机已装的包互相干扰
export PYTHONNOUSERSITE=1
# 不在 .app 内写 __pycache__：bundle 签名后任何新增文件都会让代码封条失效，
# 下次 codesign --verify 就会报 sealed resource 无效。
export PYTHONDONTWRITEBYTECODE=1

cd "$APPDIR"
# 捕获启动期异常到日志，便于排查图形界面启动失败（Finder 双击会丢弃 stderr）
LOG="$HOME_DATA/gui-launch.log"
exec >"$LOG" 2>&1
echo "=== launch $(date) ==="
echo "PY_SOURCE=$PY_SOURCE"
echo "PY=$PY"
echo "PYTHONPATH=$PYTHONPATH"
echo "NETMON_HOME=$NETMON_HOME"
"$PY" -u -m netmon.gui
RC=$?
echo "=== exit $RC $(date) ==="
exit $RC
'''


def _copy_source(dest: Path) -> None:
    """只拷贝运行时需要的 .py 源码 + 默认配置，排除缓存与测试产物。"""
    dest.mkdir(parents=True, exist_ok=True)
    src_pkg = ROOT / "netmon"
    dst_pkg = dest / "netmon"
    if dst_pkg.exists():
        shutil.rmtree(dst_pkg)
    shutil.copytree(src_pkg, dst_pkg,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "config.json", dest / "config.json")


def ad_hoc_sign(bundle: Path) -> bool:
    """对 .app 做 ad-hoc 签名（无需开发者账号）。

    作用：消除「code object is not signed at all」，让 Gatekeeper 把它识别为
    已签名应用。注意 ad-hoc 签名没有 Developer ID、无法公证，首次打开仍可能
    需要「右键 → 打开」；这一步让分发体验尽可能干净，但不等于正式签名。
    签名必须最后做——之后任何对 bundle 内文件的改动都会使签名失效。
    """
    import subprocess
    r = subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(bundle)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"警告：ad-hoc 签名失败（{r.stderr.strip()}），应用仍可使用但会被 Gatekeeper 拦截更明显")
        return False
    v = subprocess.run(["codesign", "--verify", "--verbose=1", str(bundle)],
                       capture_output=True, text=True)
    return v.returncode == 0


def _write_notices(resources: Path) -> Path:
    """汇总自带软件的许可声明，放在 .app/Contents/Resources 便于合规审查。

    Python 的 LICENSE.txt 虽然在包里，但散落在运行时目录深处；Tcl/Tk 的
    license.terms 在这份运行时的构建里只放进了 demos/。合成一个入口文件，
    审查时不必满世界找。
    """
    out = resources / "THIRD-PARTY-NOTICES.txt"
    parts: list[str] = [
        "NetMon 第三方软件许可声明",
        "=" * 60,
        "本应用包含下列第三方组件。以下为各自许可原文，随包分发。",
        "",
    ]

    def add(title: str, path: Path):
        if not path.is_file():
            print(f"  警告：许可文件缺失 {path}")
            return
        try:
            body = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return
        parts.extend(["-" * 60, title, "-" * 60, body, ""])

    add("NetMon 本身（MIT License，著作权见下）", ROOT / "LICENSE")

    py_root = resources / "python" / "lib"
    for lic in sorted(py_root.glob("python3.*/LICENSE.txt")):
        add(f"Python {lic.parent.name}", lic)

    for lic in sorted(py_root.glob("tk*/demos/license.terms")):
        add(f"Tcl / Tk（{lic.parent.parent.name}）", lic)

    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return out


def build(out_dir: Path, sign: bool = True, embed: bool = True,
          keep_pip: bool = False,
          python_home: str | None = None) -> tuple[Path, dict | None]:
    bundle = out_dir / APP_NAME
    if bundle.exists():
        shutil.rmtree(bundle)
    macos = bundle / "Contents" / "MacOS"
    resources = bundle / "Contents" / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    # 启动脚本
    launcher = macos / "launcher"
    launcher.write_text(LAUNCHER, encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # 内嵌源码 + 配置
    _copy_source(resources / "app")

    # 内嵌 Python 运行时：消除对宿主机 Python 的依赖（同时消除 Tk 8.5 白屏）。
    # 收集或自检失败会直接中断构建——宁可构建失败，也不产出一个在别人机器上
    # 打不开的包。
    runtime_info = None
    if embed:
        runtime_info = embed_python.collect(resources / "python",
                                            src=embed_python.find_runtime(python_home),
                                            keep_pip=keep_pip)

    # 图标
    icns = HERE / "NetMon.icns"
    if icns.exists():
        shutil.copy2(icns, resources / "NetMon.icns")
    png = HERE / "netmon.png"
    if png.exists():
        shutil.copy2(png, resources / "netmon.png")

    # 不随包附带任何历史数据：安装后数据目录为空，只记录真实运行产生的数据。
    # 历史数据播种会让新用户误把示例当成自己的监控结果（报告统计会被污染）。

    notices = _write_notices(resources)

    # Info.plist
    info = {
        "CFBundleName": "网哨",
        "CFBundleDisplayName": "网哨 NetMon",
        "CFBundleIdentifier": IDENTIFIER,
        "CFBundleVersion": VERSION,
        "CFBundleShortVersionString": VERSION,
        "CFBundleExecutable": "launcher",
        "CFBundleIconFile": "NetMon.icns",
        "CFBundlePackageType": "APPL",
        "CFBundleSignature": "????",
        # 跟着内嵌运行时的实际要求走：3.13 的构建通常要求 macOS 11+，
        # 写死 10.14 会让旧系统"装得上但起不来"。
        "LSMinimumSystemVersion": (runtime_info or {}).get("minos") or "10.14",
        "NSHighResolutionCapable": True,
        "LSUIElement": False,
        "CFBundleDevelopmentRegion": "zh_CN",
        "NSHumanReadableCopyright": "MIT © 2026 Chris Pan",
    }
    with (bundle / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump(info, fh)

    # 签名必须是构建的最后一步：之后不再改动 bundle 内任何文件。
    if sign:
        ad_hoc_sign(bundle)

    return bundle, runtime_info


def _tree_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description="打包 netmon 为 macOS .app")
    ap.add_argument("--out", default=str(ROOT / "dist"), help="输出目录，默认 dist/")
    ap.add_argument("--no-sign", action="store_true", help="跳过 ad-hoc 签名（默认签名）")
    ap.add_argument("--no-embed", action="store_true",
                    help="不内嵌 Python 运行时（产物约 200KB，但依赖宿主机 python3）")
    ap.add_argument("--keep-pip", action="store_true", help="内嵌运行时保留 pip")
    ap.add_argument("--python-home", help="指定用于内嵌的 Python 运行时根目录")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    bundle, runtime = build(out, sign=not args.no_sign, embed=not args.no_embed,
                            keep_pip=args.keep_pip, python_home=args.python_home)
    print(f"已生成 {bundle}")
    print(f"  体积       : {embed_python.human(_tree_size(bundle))}")
    if runtime:
        note = {"arm64": "仅 Apple 芯片", "x86_64": "仅 Intel 芯片"}.get(
            runtime["arch"], "intel + Apple 芯片通用")
        print(f"  运行时     : 内嵌（{note}，最低 macOS {runtime['minos']}）")
    else:
        print("  运行时     : 依赖宿主机 python3（体积小，但可能白屏或打不开）")
    print(f"  Info.plist : {bundle / 'Contents' / 'Info.plist'}")
    print(f"  源码       : {bundle / 'Contents' / 'Resources' / 'app'}")
    print(f"  许可声明   : {bundle / 'Contents' / 'Resources' / 'THIRD-PARTY-NOTICES.txt'}")
    print(f"  数据目录   : ~/Library/Application Support/NetMon（首次运行自动创建）")


if __name__ == "__main__":
    main()
