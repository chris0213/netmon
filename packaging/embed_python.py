#!/usr/bin/env python3
"""把 Python 运行时收集成可随 .app 分发的自包含目录（纯标准库）。

为什么需要它
------------
macOS 桌面应用若依赖宿主机的 python3，会遇到两个问题：
  1. 对方机器没装 python.org 版 Python —— 应用直接打不开；
  2. 系统自带 /usr/bin/python3 是 Tk 8.5 —— 打开是白屏窗口。
把解释器连同 Tcl/Tk 一起打进 .app，就消除了对外部环境的全部假设。

产物结构（对应 .app 内 Contents/Resources/python）
------------------------------------------------
  bin/python3.13                             解释器
  lib/libpython3.13.dylib
  lib/libtcl9.0.dylib / libtcl9tk9.0.dylib
  lib/python3.13/                            标准库 + lib-dynload
  lib/tcl9.0 / tk9.0 / tcl9                  Tcl/Tk 脚本库（tkinter 依赖）

为什么可以整体搬移
------------------
实测该运行时的非系统依赖只有 3 个，且全部以 @rpath 表示；解释器带
LC_RPATH = @executable_path/../lib。因此目录可原样搬移，无需
install_name_tool 改写。check_relocatable() 会重新校验这件事——不满足
就报错退出，而不是产出一个在别人机器上崩溃的包。

用法
----
  python3 packaging/embed_python.py --dest /tmp/py            # 收集并校验
  python3 packaging/embed_python.py --dest /tmp/py --keep-pip # 保留 pip
  python3 packaging/embed_python.py --list                    # 列出可用运行时
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Tk 8.5 在高分屏上渲染白屏，必须排除
MIN_TK = (8, 6)

# 判定"系统库"的前缀：这些由 macOS 保证存在，不必随包分发
SYSTEM_PREFIXES = ("/usr/lib/", "/System/")

# 运行时绝不需要的目录（相对运行时根）
PRUNE_DIRS = [
    "include",                    # C 头文件，只有编译扩展才需要
    "share/man",
    "lib/pkgconfig",
    "lib/python3.13/ensurepip",   # 不再需要 pip 引导
    "lib/python3.13/idlelib",     # IDLE 编辑器
    "lib/python3.13/turtledemo",
    "lib/python3.13/test",        # 标准库自测套件，体积最大的一块
    "lib/python3.13/venv",        # 应用内不建虚拟环境
]

# 运行时绝不需要的可执行入口
PRUNE_FILES = [
    "bin/idle3", "bin/idle3.13",
    "bin/pip", "bin/pip3", "bin/pip3.13",
    "bin/pydoc3", "bin/pydoc3.13",
    "bin/python3-config", "bin/python3.13-config",
]

# 仅在编译期使用的配置目录与自测扩展
PRUNE_GLOBS = [
    "lib/python3.13/config-*",
    "lib/python3.13/lib-dynload/_test*",
    "lib/python3.13/lib-dynload/_ctypes_test*",
    "lib/python3.13/lib-dynload/_xxtestfuzz*",
]

SITE_PACKAGES = "lib/python3.13/site-packages"

# 自检要导入的模块：覆盖 netmon 用到的全部非第三方依赖
VERIFY_CODE = (
    "import tkinter, sqlite3, ssl, http.client, socketserver, plistlib,"
    " statistics, struct, fcntl, csv, concurrent.futures, html, plistlib"
)


def _find_exe(root: Path) -> Path | None:
    """在运行时根目录里找到解释器可执行文件。"""
    for name in ("python3.13", "python3"):
        p = root / "bin" / name
        if p.is_file() and os.access(p, os.X_OK):
            return p
    for p in sorted((root / "bin").glob("python3.*")):
        if "-config" in p.name or not p.is_file():
            continue
        if os.access(p, os.X_OK):
            return p
    return None


def _run(argv: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _otool_deps(binary: Path) -> list[str]:
    """返回 Mach-O 的依赖清单，跳过 otool 首行文件名。"""
    r = _run(["otool", "-L", str(binary)])
    deps: list[str] = []
    for line in r.stdout.splitlines():
        s = line.strip()
        # 首行形如 "/path/to/file:"，不是依赖
        if not s or s.endswith(":"):
            continue
        deps.append(s.split(" (")[0])
    return deps


def _rpaths(binary: Path) -> list[str]:
    """提取 LC_RPATH 条目。"""
    r = _run(["otool", "-l", str(binary)])
    out: list[str] = []
    want = False
    for line in r.stdout.splitlines():
        s = line.strip()
        if s == "cmd LC_RPATH":
            want = True
        elif want and s.startswith("path "):
            out.append(s.split()[1])
            want = False
    return out


def _macho_files(root: Path) -> list[Path]:
    """运行时要参与搬移校验的 Mach-O 文件（解释器 + dylib + C 扩展）。"""
    files: list[Path] = []
    exe = _find_exe(root)
    if exe:
        files.append(exe)
    files += sorted((root / "lib").glob("*.dylib"))
    files += sorted((root / "lib").glob("python*/lib-dynload/*.so"))
    return files


def check_relocatable(root: Path) -> list[str]:
    """校验运行时能否原样搬走。返回问题清单，空列表代表安全。"""
    problems: list[str] = []
    exe = _find_exe(root)
    if not exe:
        return ["找不到解释器可执行文件"]

    rpaths = _rpaths(exe)
    if not any("@executable_path" in r or "@loader_path" in r for r in rpaths):
        problems.append(f"解释器缺少相对 LC_RPATH（现有：{rpaths or '无'}）")

    have = {p.name for p in (root / "lib").iterdir()} if (root / "lib").is_dir() else set()
    for binary in _macho_files(root):
        rel = binary.relative_to(root)
        for dep in _otool_deps(binary):
            if dep.startswith(SYSTEM_PREFIXES):
                continue
            if dep.startswith("@rpath/"):
                name = dep[len("@rpath/"):]
                if name not in have:
                    problems.append(f"{rel} 依赖 {dep}，但 lib/ 下没有该文件")
            elif dep.startswith(("@executable_path/", "@loader_path/")):
                continue
            else:
                problems.append(f"{rel} 依赖绝对路径 {dep}，搬移后会失效")
    return problems


def arch_info(root: Path) -> tuple[str, str]:
    """返回 (架构, 最低系统版本)。

    这决定产物能装在哪些机器上：从 python-build-standalone 拿到的运行时通常是
    纯 arm64，Intel Mac 直接跑不起来，Info.plist 的最低系统版本也必须跟着它走，
    不能写死。
    """
    exe = _find_exe(root)
    if not exe:
        return "unknown", ""
    archs = _run(["lipo", "-archs", str(exe)]).stdout.split()
    if {"arm64", "x86_64"} <= set(archs):
        arch = "universal2"
    elif archs:
        arch = archs[0]
    else:
        arch = "unknown"

    minos = ""
    want = False
    for line in _run(["otool", "-l", str(exe)]).stdout.splitlines():
        s = line.strip()
        if s == "cmd LC_BUILD_VERSION":
            want = True
        elif want and s.startswith("minos "):
            minos = s.split()[1]
            break
    return arch, minos


def _candidates() -> list[Path]:
    """按优先级列出候选运行时。"""
    out: list[Path] = []
    base = Path.home() / ".workbuddy" / "binaries" / "python" / "versions"
    if base.is_dir():
        out += [p for p in sorted(base.iterdir(), reverse=True)
                if p.is_dir() and (p / "bin").is_dir()]
    fw = Path("/Library/Frameworks/Python.framework/Versions")
    if fw.is_dir():
        out += [p for p in sorted(fw.iterdir(), reverse=True)
                if p.is_dir() and p.name[:1].isdigit()]
    for pat in ("opt/homebrew/opt/python@*/Frameworks/Python.framework/Versions/*",
                "usr/local/opt/python@*/Frameworks/Python.framework/Versions/*"):
        out += [p for p in sorted(Path("/").glob(pat), reverse=True) if p.is_dir()]
    return out


def probe(root: Path) -> tuple[bool, str]:
    """检查一个候选运行时是否"可搬移 + 带得动 tkinter"。"""
    exe = _find_exe(root)
    if not exe:
        return False, "没有 bin/python3.x"
    if not (root / "lib").is_dir():
        return False, "没有 lib/ 目录"
    try:
        r = _run([str(exe), "-c", "import sys,tkinter;print(sys.version.split()[0],tkinter.TkVersion)"])
    except subprocess.TimeoutExpired:
        return False, "解释器启动超时"
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ["未知错误"])[-1]
        return False, f"tkinter 不可用：{tail}"
    try:
        pyver, tkver = r.stdout.split()[:2]
    except ValueError:
        return False, f"输出异常：{r.stdout.strip()[:80]}"
    tk = tuple(int(x) for x in tkver.split(".")[:2])
    if tk < MIN_TK:
        return False, f"Tk {tkver} 过旧（会白屏），需要 Tk >= {MIN_TK[0]}.{MIN_TK[1]}"
    problems = check_relocatable(root)
    if problems:
        return False, "不可搬移：" + "；".join(problems[:2])
    return True, f"Python {pyver} / Tk {tkver}"


def find_runtime(explicit: str | None = None) -> Path:
    if explicit:
        root = Path(explicit).expanduser().resolve()
        ok, why = probe(root)
        if not ok:
            raise SystemExit(f"指定的 Python 运行时不可用：{root}\n原因：{why}")
        return root

    rejected: list[str] = []
    for root in _candidates():
        ok, why = probe(root)
        if ok:
            return root
        rejected.append(f"  {root}\n      {why}")
    msg = ["找不到可用于打包的 Python 运行时。", ""]
    if rejected:
        msg += ["已检查并排除："] + rejected + [""]
    msg += [
        "需要一个自带 Tk >= 8.6、且依赖全部以 @rpath 表示的 Python 3 安装，例如",
        "python.org 的 macOS 安装包，或 python-build-standalone 的 install_only 版本。",
        "也可以用 --python-home <路径> 显式指定。",
    ]
    raise SystemExit("\n".join(msg))


def _clone(src: Path, dest: Path) -> None:
    """优先用 APFS clone（秒级、不占额外空间），失败退回普通复制。"""
    r = _run(["cp", "-Rc", str(src), str(dest)], timeout=300)
    if r.returncode != 0:
        _run(["cp", "-R", str(src), str(dest)], timeout=300)
        if not dest.exists():
            raise SystemExit(f"复制运行时失败：{r.stderr.strip()}")


def prune(root: Path, keep_pip: bool = False) -> None:
    for rel in PRUNE_DIRS:
        if keep_pip and rel.endswith(("ensurepip",)):
            continue
        p = root / rel
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
    for rel in PRUNE_FILES:
        if keep_pip and "/pip" in rel:
            continue
        p = root / rel
        if p.exists() or p.is_symlink():
            p.unlink()
    for pat in PRUNE_GLOBS:
        for p in root.glob(pat):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()

    # netmon 零第三方依赖：site-packages 只需留一个空目录占位
    sp = root / SITE_PACKAGES
    if sp.is_dir():
        for child in sp.iterdir():
            if keep_pip and child.name in ("pip", "pip-*.dist-info"):
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
    elif sp.parent.is_dir():
        sp.mkdir()


def _clean_env() -> dict:
    env = {
        "HOME": os.environ.get("HOME", "/tmp"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "TK_SILENCE_DEPRECATION": "1",
    }
    if os.environ.get("TMPDIR"):
        env["TMPDIR"] = os.environ["TMPDIR"]
    return env


def verify(root: Path) -> str:
    """用收集后的解释器自检：自定位正确、关键模块可导入、Tk 版本达标。"""
    exe = _find_exe(root)
    if not exe:
        raise SystemExit(f"校验失败：{root} 下没有解释器")
    code = (
        "import sys;"
        f"assert sys.base_prefix == {str(root)!r}, '自定位失败: ' + sys.base_prefix;"
        f"{VERIFY_CODE};"
        "import tkinter;"
        "print('Tk', tkinter.TkVersion)"
    )
    r = subprocess.run([str(exe), "-I", "-c", code],
                       capture_output=True, text=True,
                       env=_clean_env(), timeout=120)
    if r.returncode != 0:
        tail = r.stderr.strip()[-1000:] or r.stdout.strip()[-400:]
        raise SystemExit(f"内嵌运行时自检失败：\n{tail}")
    return r.stdout.strip()


def _du(path: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            try:
                total += os.lstat(fp).st_size
            except OSError:
                pass
    return total


def _count(path: Path) -> int:
    return sum(len(f) for _r, _d, f in os.walk(path))


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}GB"


def collect(dest: Path, src: Path | None = None,
            keep_pip: bool = False, quiet: bool = False) -> dict:
    """收集并裁剪运行时到 dest。失败时抛 SystemExit，不留下半成品。"""
    source = src or find_runtime()
    dest = dest.resolve()

    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not quiet:
        print(f"  运行时来源：{source}")
    _clone(source, dest)
    if not _find_exe(dest):
        shutil.rmtree(dest, ignore_errors=True)
        raise SystemExit(f"复制后找不到解释器：{dest}")

    prune(dest, keep_pip=keep_pip)
    tk_info = verify(dest)
    arch, minos = arch_info(dest)

    info = {
        "path": str(dest),
        "src": str(source),
        "files": _count(dest),
        "size": _du(dest),
        "tk": tk_info,
        "arch": arch,
        "minos": minos,
    }
    if not quiet:
        print(f"  内嵌运行时：{human(info['size'])} / {info['files']} 个文件 / {tk_info}")
        note = ""
        if arch == "arm64":
            note = "（仅 Apple 芯片可用）"
        elif arch == "x86_64":
            note = "（仅 Intel 可用）"
        print(f"  架构       ：{arch}{note} / 最低 macOS {minos or '未知'}")
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description="收集自包含的 Python 运行时")
    ap.add_argument("--dest", help="输出目录")
    ap.add_argument("--python-home", help="显式指定源运行时根目录")
    ap.add_argument("--keep-pip", action="store_true", help="保留 pip 与 ensurepip")
    ap.add_argument("--list", action="store_true", help="只列出候选运行时并退出")
    args = ap.parse_args()

    if args.list:
        for root in _candidates():
            ok, why = probe(root)
            print(f"{'[可用]' if ok else '[跳过]'} {root}\n        {why}")
        return 0

    if not args.dest:
        ap.error("需要 --dest")

    src = find_runtime(args.python_home) if args.python_home else None
    info = collect(Path(args.dest), src=src, keep_pip=args.keep_pip)
    print(f"完成：{info['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
