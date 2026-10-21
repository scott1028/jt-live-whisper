"""On-demand download packs for the AppImage build (packaging/appimage/).

Normal installs (repo venv etc.) import every module directly, so
ensure_pack() succeeds immediately and nothing is ever downloaded. In
AppImage mode (JTLW_APPIMAGE=1 and a packs/ spec next to this file) a
missing pack is pip-installed with --no-deps --target into the user packs
dir, dependency packs first, then added to sys.path.

CLI (used by AppRun --install-pack):
    python jtlw_packs.py install <pack|all>
"""
import glob
import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys

# import-test module names when no packs.json spec is present (normal installs)
DEFAULT_IMPORTS = {
    "gpu": ["triton"],
    "ml": ["torch", "transformers", "resemblyzer", "librosa"],
    "overlay": ["PyQt6"],
    "argos": ["argostranslate"],
    "moonshine": ["moonshine_voice"],
}
_FALLBACK_ORDER = ["gpu", "ml", "overlay", "argos", "moonshine"]

_spec_cache = {}


def is_appimage():
    return os.environ.get("JTLW_APPIMAGE") == "1"


def _spec_dir():
    # realpath: in the AppImage jtlw_packs.py is reached through a symlink in
    # the work home; packs/ sits next to the real file inside the mount
    return os.environ.get("JTLW_PACKS_SPEC") or os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "packs")


def _load_spec():
    """packs.json contents or None (no spec -> never download)."""
    if "spec" not in _spec_cache:
        path = os.path.join(_spec_dir(), "packs.json")
        try:
            with open(path, encoding="utf-8") as f:
                _spec_cache["spec"] = json.load(f)
        except (OSError, ValueError):
            _spec_cache["spec"] = None
    return _spec_cache["spec"]


def pack_names():
    spec = _load_spec()
    return list((spec or {}).get("order", _FALLBACK_ORDER))


def pack_size_mb(name):
    spec = _load_spec()
    return ((spec or {}).get("packs", {}).get(name) or {}).get("size_mb")


def packs_dir():
    root = os.environ.get("JTLW_PACKS_DIR")
    if not root:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
            "~/.local/share")
        root = os.path.join(base, "jt-live-whisper", "packs")
    return root


def _importable(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _pins_hash(pins_path):
    with open(pins_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def _add_to_path(target):
    if target not in sys.path:
        sys.path.append(target)
        importlib.invalidate_caches()


def _appimage_hint():
    path = (os.environ.get("APPIMAGE") or os.environ.get("JTLW_START_CMD")
            or sys.argv[0])
    return f"{path} --install-pack"


def ensure_pack(name, reason=""):
    """Make modules of pack `name` importable; download it in AppImage mode.

    Returns True when the pack's modules are available (normal installs take
    this path via the import test and never touch the network). Returns False
    outside AppImage mode (or on failure) so callers keep today's messages.
    """
    spec = _load_spec()
    if spec is None:
        # normal install: import test only, never download
        info = {"imports": DEFAULT_IMPORTS.get(name, [name]), "depends": []}
    else:
        info = spec.get("packs", {}).get(name)
        if info is None:
            print(f"[錯誤] 沒有 {name} 這個套件可下載"
                  f"（可選：{'、'.join(pack_names())}）", file=sys.stderr)
            return False
    imports = info.get("imports") or DEFAULT_IMPORTS.get(name, [name])
    if all(_importable(m) for m in imports):
        return True
    if not is_appimage():
        return False

    for dep in info.get("depends", []):
        if not ensure_pack(dep, reason):
            return False

    target = os.path.join(packs_dir(), name)
    pins = os.path.join(_spec_dir(), f"{name}.txt")
    if not os.path.isfile(pins):
        print(f"[錯誤] 缺少套件清單 {pins}", file=sys.stderr)
        return False

    want = _pins_hash(pins)
    if glob.glob(os.path.join(target, f".jtlw-complete-{want}")):
        _add_to_path(target)  # completed earlier but not on sys.path
        if all(_importable(m) for m in imports):
            return True

    size = info.get("size_mb")
    label = f"「{reason}」" if reason else name
    print(f"\n  首次使用{label}，正在下載 {name} 套件"
          f"{'（約 ' + str(size) + ' MB）' if size else ''}...", flush=True)
    cmd = [sys.executable, "-m", "pip", "install",
           "--disable-pip-version-check", "--no-deps",
           "--target", target, "-r", pins]
    if info.get("pip_index"):
        cmd += ["--index-url", info["pip_index"]]
    shutil.rmtree(target, ignore_errors=True)
    if subprocess.run(cmd).returncode != 0:
        print(f"[錯誤] {name} 套件下載失敗，請檢查網路後重試", file=sys.stderr)
        print(f"  亦可稍後手動安裝：{_appimage_hint()} {name}", file=sys.stderr)
        return False
    for old in glob.glob(os.path.join(target, ".jtlw-complete-*")):
        os.remove(old)
    with open(os.path.join(target, f".jtlw-complete-{want}"), "w") as f:
        f.write("")
    _add_to_path(target)
    if not all(_importable(m) for m in imports):
        print(f"[錯誤] {name} 套件安裝後仍無法匯入，"
              f"請重新執行或改用其他功能", file=sys.stderr)
        return False
    print(f"  {name} 套件安裝完成", flush=True)
    return True


def _cli(argv):
    if len(argv) < 2 or argv[0] != "install":
        print("用法：python jtlw_packs.py install <套件名|all>")
        print(f"可用套件：{'、'.join(pack_names())}")
        return 2
    targets = pack_names() if argv[1] == "all" else [argv[1]]
    return 0 if all(ensure_pack(n, "手動安裝") for n in targets) else 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
