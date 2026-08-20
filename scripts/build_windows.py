"""Windows 打包入口：PyInstaller 单文件 exe，可选再打 Inno 安装包。

由 build_exe.bat / build_installer.bat 调用，避免 cmd 解析中文路径和 UTF-8 批处理。
每次打包默认把 app/__init__.py 的 APP_VERSION 按十进制进位 +1（1.3.1 → 1.3.2，1.3.9 → 1.4.0）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_APP_INIT = ROOT / "app" / "__init__.py"
_APP_VERSION_RE = re.compile(r'(APP_VERSION\s*=\s*")([^"]+)(")')


def _die(msg: str, code: int = 1) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(code)


def _python() -> Path:
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        _die(f"未找到 {py}，请先创建 .venv 并安装 pyinstaller")
    return py


def _spec() -> Path:
    specs = sorted(ROOT.glob("*.spec"))
    if not specs:
        _die("项目根目录没有 .spec 文件")
    return specs[0]


def _iscc() -> Path:
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        Path(local) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ]
    for path in candidates:
        if path.is_file():
            return path
    _die("未找到 Inno Setup 6，请先安装：winget install JRSoftware.InnoSetup")


def _run(args: list[str], cwd: Path | None = None) -> None:
    print("[RUN]", " ".join(args), flush=True)
    proc = subprocess.run(args, cwd=str(cwd or ROOT))
    if proc.returncode != 0:
        _die(f"命令失败（退出码 {proc.returncode}）：{args[0]}")


def _ensure_ico(py: Path) -> None:
    _run([str(py), str(ROOT / "scripts" / "ensure_ico.py")])


def _pyinstaller(py: Path, spec: Path) -> None:
    _run([str(py), "-m", "PyInstaller", "--noconfirm", "--clean", str(spec)])


def next_version(current: str) -> str:
    """十进制进位：1.3.1 → 1.3.2，1.3.9 → 1.4.0，1.9.9 → 2.0.0。每段 0–9。"""
    text = (current or "").strip()
    parts = text.split(".")
    if not parts or any(not p.isdigit() for p in parts):
        raise ValueError(f"无法递增版本号：{current!r}")
    nums = [int(p) for p in parts]
    if any(n < 0 for n in nums):
        raise ValueError(f"无法递增版本号：{current!r}")
    i = len(nums) - 1
    while i >= 0:
        nums[i] += 1
        # 最高位可以超过 9：9.9.9 → 10.0.0
        if nums[i] < 10 or i == 0:
            break
        nums[i] = 0
        i -= 1
    return ".".join(str(n) for n in nums)


def bump_app_version(path: Path | None = None) -> tuple[str, str]:
    target = path or _APP_INIT
    text = target.read_text(encoding="utf-8")
    match = _APP_VERSION_RE.search(text)
    if not match:
        _die(f"{target} 里找不到 APP_VERSION")
    old = match.group(2)
    try:
        new = next_version(old)
    except ValueError as exc:
        _die(str(exc))
    target.write_text(text[:match.start(2)] + new + text[match.end(2):], encoding="utf-8")
    return old, new


def _inno(iscc: Path, version: str) -> Path:
    _run([str(iscc), f"/DAppVersion={version}", str(ROOT / "installer.iss")])
    out = ROOT / "dist" / f"奶龙投稿助手-{version}-windows-setup.exe"
    if not out.is_file():
        # Inno 默认输出名；若编码导致匹配失败，取最新 setup
        matches = sorted((ROOT / "dist").glob("*-windows-setup.exe"), key=lambda p: p.stat().st_mtime)
        if not matches:
            _die("Inno Setup 已结束，但 dist 下没有 *-windows-setup.exe")
        out = matches[-1]
    return out


def main(argv: list[str]) -> int:
    os.chdir(ROOT)
    args = [a for a in argv[1:] if a]
    no_bump = "--no-bump" in args
    args = [a for a in args if a != "--no-bump"]
    mode = (args[0] if args else "installer").strip().lower()
    if mode not in ("exe", "installer"):
        _die("用法：build_windows.py [exe|installer] [--no-bump]")

    sys.path.insert(0, str(ROOT))
    from app.announcements import validate_release_announcement
    if no_bump:
        from app import APP_VERSION
        print(f"[INFO] version {APP_VERSION} (no bump)")
        announcement_version = APP_VERSION
    else:
        current_text = _APP_INIT.read_text(encoding="utf-8")
        current_match = _APP_VERSION_RE.search(current_text)
        if not current_match:
            _die(f"{_APP_INIT} 里找不到 APP_VERSION")
        announcement_version = next_version(current_match.group(2))
        try:
            validate_release_announcement(
                announcement_version, ROOT / "app" / "data" / "announcements.json")
        except ValueError as exc:
            _die(f"发版公告校验失败：{exc}（版本文件尚未修改）")
        old, APP_VERSION = bump_app_version()
        print(f"[INFO] version {old} -> {APP_VERSION}")
        # 已写入文件；清掉缓存以免后续 import 仍是旧号
        sys.modules.pop("app", None)
        sys.modules.pop("app.__init__", None)

    try:
        validate_release_announcement(APP_VERSION, ROOT / "app" / "data" / "announcements.json")
    except ValueError as exc:
        _die(f"发版公告校验失败：{exc}")
    print(f"[INFO] announcement v{APP_VERSION} OK")

    py = _python()
    spec = _spec()
    print(f"[INFO] spec {spec.name}")

    _ensure_ico(py)
    _pyinstaller(py, spec)
    exe = ROOT / "dist" / "奶龙投稿助手.exe"
    if exe.is_file():
        print(f"[OK] {exe}")
    else:
        found = list((ROOT / "dist").glob("*.exe"))
        print(f"[WARN] 未找到预期 exe，dist 现有：{[p.name for p in found]}")

    if mode == "installer":
        setup = _inno(_iscc(), APP_VERSION)
        print(f"[OK] {setup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
