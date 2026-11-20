#!/usr/bin/env python3
"""组装可转发的分发包：签名 app → DMG → 与安装说明一起打成 zip。

产物：dist/NetMon-1.0.0-release.zip
  ├─ NetMon-1.0.0.dmg         已 ad-hoc 签名的安装包（含内嵌 Python 运行时）
  └─ INSTALL-GUIDE.zh-CN.md   安装说明 / 隐私说明

app 自带 Python 与 Tcl/Tk，接收方无需安装任何运行时；代价是 DMG 从早期的
178KB 增长到十几 MB——这是换掉"对方没装 Python 就打不开"的必要支出。

先决条件：dist/NetMon.app 已由 build_app.py 构建并签名（本脚本不重复签名）。
用法：python3 packaging/make_release.py
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIST = ROOT / "dist"
STAGE = DIST / "release-stage"
DMG = DIST / "NetMon-1.0.0.dmg"
OUT_ZIP = DIST / "NetMon-1.0.0-release.zip"
GUIDE = ROOT / "docs" / "安装说明-给同事.md"


def _human(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024:.0f} KB"


def main() -> None:
    app = DIST / "NetMon.app"
    if not app.exists():
        raise SystemExit("找不到 dist/NetMon.app，请先运行 packaging/build_app.py")

    # 确认签名有效（ad-hoc 也算），没签则拒绝打包成发行物
    v = subprocess.run(["codesign", "--verify", "--verbose=1", str(app)],
                       capture_output=True, text=True)
    if v.returncode != 0:
        raise SystemExit("NetMon.app 签名无效，请重新运行 build_app.py（默认会签名）")
    print("签名校验通过：", v.stdout.strip() or "valid on disk")

    # 1) 生成 DMG（含 Applications 软链）
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    # 用 cp -Rc 而不是 copytree：APFS clone 秒级完成，且完整保留符号链接、
    # 扩展属性与硬链接——copytree 默认会解引用 bin/python3 这类链接。
    r = subprocess.run(["cp", "-Rc", str(app), str(STAGE / "NetMon.app")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        shutil.copytree(app, STAGE / "NetMon.app", symlinks=True)
    (STAGE / "Applications").symlink_to("/Applications")
    subprocess.run([
        "hdiutil", "create", "-volname", "网哨 NetMon",
        "-srcfolder", str(STAGE), "-ov", "-format", "UDZO", str(DMG),
    ], check=True, capture_output=True, text=True)
    subprocess.run(["hdiutil", "verify", str(DMG)],
                   check=True, capture_output=True, text=True)
    print("DMG 已生成并校验：", DMG.name, f"（{_human(DMG.stat().st_size)}）")

    # 2) 组装 zip（DMG + 安装说明）
    if OUT_ZIP.exists():
        OUT_ZIP.unlink()
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(DMG, arcname=DMG.name)
        if GUIDE.exists():
            # 用 ASCII 文件名，避免不同系统/压缩工具下中文名乱码
            z.write(GUIDE, arcname="INSTALL-GUIDE.zh-CN.md")
    shutil.rmtree(STAGE, ignore_errors=True)
    print("分发包已生成：", OUT_ZIP)
    print("  大小：", _human(OUT_ZIP.stat().st_size))


if __name__ == "__main__":
    main()
