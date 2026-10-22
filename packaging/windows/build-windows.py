#!/usr/bin/env python3
"""Build the self-contained jt-live-whisper Windows x86-64 portable ZIP.

Run on Windows (the CI windows runner) with any Python 3; needs uv and git:

    python packaging\\windows\\build-windows.py

ZIP layout (jt-live-whisper\\ ...):
  jt-live-whisper.cmd, launcher.py  double-click entry + launcher logic
  python\\                           portable CPython 3.12.13 (uv /
                                    python-build-standalone) + app-local MSVC
                                    runtime; Lib\\site-packages = venv packages
                                    MINUS the on-demand pack exclusions
                                    (split_packs.py) + sitecustomize.py
  app\\                              program files (git ls-files minus docs/
                                    images/ packaging/) + packs\\ specs
Output: dist\\jt-live-whisper-<APP_VERSION>-<7-char hash>-windows-x86_64.zip
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PYVER = "3.12.13"
REQS = os.path.join(REPO, "packaging", "requirements", "windows-x86_64.txt")
LINUX_LOCK = os.path.join(REPO, "packaging", "requirements", "linux-x86_64.txt")
APPIMAGE_DIR = os.path.join(REPO, "packaging", "appimage")
CHECK_SITE = os.path.join(REPO, "packaging", "check_site.py")
# smoke test: core modules must import, pack-only ones must be absent
CORE_IMPORTS = ["ctranslate2", "faster_whisper", "numpy", "sounddevice",
                "opencc", "sentencepiece", "fastapi", "uvicorn", "noisereduce",
                "huggingface_hub", "onnxruntime"]
NOT_BUNDLED = ["torch", "PyQt6", "nvidia", "spacy", "moonshine_voice"]
# MSVC runtime that onnxruntime / ctranslate2 / PyQt6 need; a clean Windows
# may lack it, so ship it app-local next to python.exe
MSVC_DLLS = ["vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll",
             "msvcp140_1.dll", "msvcp140_2.dll", "concrt140.dll"]


def fail(msg):
    print(f"打包失敗：{msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def run(cmd, **kw):
    print("      $ " + subprocess.list2cmdline(cmd), flush=True)
    rc = subprocess.run(cmd, **kw).returncode
    if rc != 0:
        fail(f"指令結束碼 {rc}：{subprocess.list2cmdline(cmd)}")


def get_release_name():
    """<APP_VERSION>-<7-char commit hash>, same rule as build-appimage.sh"""
    with open(os.path.join(REPO, "translate_meeting.py"), encoding="utf-8") as f:
        m = re.search(r'^APP_VERSION = "([^"]+)"', f.read(), re.M)
    if not m:
        fail("讀不到 translate_meeting.py 的 APP_VERSION")
    try:
        # first 7 of the full sha (--short can grow when ambiguous)
        commit = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout
        commit = commit.strip()[:7]
    except OSError:
        commit = ""
    if not commit:
        fail(f"取不到 git commit hash（{REPO} 不是 git repo？）")
    return f"{m.group(1)}-{commit}"


def install_python(dst):
    """uv-managed CPython copied to dst, minus its own site-packages (the venv's
    pinned pip and packages replace it); returns the uv install dir"""
    run(["uv", "python", "install", PYVER])
    uv_dir = subprocess.run(["uv", "python", "dir"], capture_output=True,
                            text=True).stdout.strip()
    found = sorted(glob.glob(os.path.join(
        uv_dir, f"cpython-{PYVER}-windows-x86_64-*")))
    if not found or not os.path.isfile(os.path.join(found[0], "python.exe")):
        fail(f"uv 裝好後找不到 {PYVER} 安裝目錄（{uv_dir}）")
    shutil.copytree(found[0], dst)
    shutil.rmtree(os.path.join(dst, "Lib", "site-packages"))
    return found[0]


def copy_msvc_runtime(python_dir):
    """Copy MSVC_DLLS from System32 into python_dir when present there and
    not shipped yet; returns the copied names."""
    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                         "System32")
    copied = []
    for dll in MSVC_DLLS:
        src = os.path.join(sys32, dll)
        if os.path.isfile(src) and not os.path.exists(
                os.path.join(python_dir, dll)):
            shutil.copy2(src, python_dir)
            copied.append(dll)
    return copied


def create_venv(base, venv):
    """venv from the uv Python with the Windows package list; returns its python"""
    run([os.path.join(base, "python.exe"), "-m", "venv", venv])
    vpy = os.path.join(venv, "Scripts", "python.exe")
    run([vpy, "-m", "pip", "install", "--disable-pip-version-check",
         "-r", REQS])
    # resemblyzer --no-deps like install.ps1: its deps pin the original
    # webrtcvad (needs a C compiler) and the old typing backport
    with open(LINUX_LOCK, encoding="utf-8") as f:
        m = re.search(r"^resemblyzer==(\S+)", f.read(), re.M | re.I)
    if not m:
        fail(f"{LINUX_LOCK} 裡沒有 resemblyzer 版本")
    run([vpy, "-m", "pip", "install", "--disable-pip-version-check",
         "--no-deps", f"resemblyzer=={m.group(1)}"])
    r = subprocess.run([vpy, "-m", "pip", "check"], capture_output=True,
                       text=True)
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    for ln in lines:
        print(f"      pip check: {ln}")
    # known noise: resemblyzer's undeclared-on-purpose deps (webrtcvad, typing)
    unknown = [ln for ln in lines if not ln.lower().startswith("resemblyzer ")]
    if r.returncode != 0 and unknown:
        fail("pip check 有非預期的相依問題")
    return vpy


def copy_site_packages(src, dst, exclude_file):
    """Copy src into dst minus exclude.txt entries (site-packages relative,
    forward slashes) and __pycache__; returns the number of files copied."""
    with open(exclude_file, encoding="utf-8") as f:
        skip = {os.path.normcase(os.path.normpath(ln.strip()))
                for ln in f if ln.strip()}
    n = 0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, src)
            if os.path.normcase(rel) in skip:
                continue
            out = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(full, out)
            n += 1
    return n


def stage_app(app, packs_spec):
    """app\\ = git ls-files minus docs/ images/ packaging/ (files missing from
    the worktree are skipped) + packs\\packs.json and one <name>.txt per pack"""
    out = subprocess.run(["git", "-C", REPO, "ls-files", "-z"],
                         capture_output=True).stdout.decode("utf-8")
    for rel in out.split("\0"):
        if not rel or rel.startswith(("docs/", "images/", "packaging/")):
            continue
        src = os.path.join(REPO, rel)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(app, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    os.makedirs(os.path.join(app, "packs"), exist_ok=True)
    spec = os.path.join(packs_spec, "packs.json")
    shutil.copy2(spec, os.path.join(app, "packs"))
    with open(spec, encoding="utf-8") as f:
        for p in json.load(f)["order"]:
            shutil.copy2(os.path.join(packs_spec, f"{p}.txt"),
                         os.path.join(app, "packs"))
    for rel in ["translate_meeting.py", "webui.py", "subtitle_overlay.py",
                "jtlw_packs.py", "packs/packs.json"]:
        if not os.path.isfile(os.path.join(app, rel)):
            fail(f"app\\{rel} 缺失")


def copy_cmd_crlf(src, dst):
    """.cmd with CRLF line ends whatever the checkout's eol (cmd.exe needs
    CRLF); also enforces ASCII-only"""
    with open(src, encoding="ascii") as f:
        text = f.read()
    with open(dst, "w", encoding="ascii", newline="\r\n") as f:
        f.write(text)


def smoke_test(stage, env):
    py = os.path.join(stage, "python", "python.exe")
    r = subprocess.run([py, "translate_meeting.py", "--help"],
                       cwd=os.path.join(stage, "app"), env=env,
                       capture_output=True)
    text = (r.stdout + r.stderr).decode("utf-8", "replace")
    if r.returncode != 0:
        print(text)
        fail(f"translate_meeting.py --help 結束碼 {r.returncode}")
    print(f"      --help 結束碼 0（{len(text.splitlines())} 行）")
    r = subprocess.run([py, "-c", "import " + ", ".join(CORE_IMPORTS)],
                       cwd=stage, env=env, capture_output=True)
    if r.returncode != 0:
        print((r.stdout + r.stderr).decode("utf-8", "replace"))
        fail("核心模組匯入失敗")
    print(f"      匯入 {', '.join(CORE_IMPORTS)} ✓")
    sp = os.path.join(stage, "python", "Lib", "site-packages")
    bad = [m for m in NOT_BUNDLED if os.path.exists(os.path.join(sp, m))]
    if bad:
        fail(f"{', '.join(bad)} 不應出現在 ZIP 裡")
    print(f"      {'/'.join(NOT_BUNDLED)} 均已排除 ✓")


def write_zip(folder, out):
    """Zip folder as <folder name>\\... (deflate)."""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out):
        os.remove(out)
    base = os.path.dirname(folder)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames.sort()
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                zf.write(full, os.path.relpath(full, base))


def main():
    for stream in (sys.stdout, sys.stderr):  # CI pipes use the ANSI code page
        stream.reconfigure(encoding="utf-8", errors="replace")
    if sys.platform != "win32":
        fail("build-windows.py 要在 Windows 上執行（例如 CI 的 Windows runner）")
    if sys.maxsize <= 2 ** 32:  # 32-bit: System32 redirects to 32-bit DLLs
        fail("需要 64 位元 Python 執行 build-windows.py")
    for tool in ("uv", "git"):
        if not shutil.which(tool):
            fail(f"需要 {tool}")
    t0 = time.time()
    name = get_release_name()
    out = os.path.join(REPO, "dist",
                       f"jt-live-whisper-{name}-windows-x86_64.zip")
    os.makedirs(os.path.join(REPO, ".tmp"), exist_ok=True)
    build = tempfile.mkdtemp(prefix="win.", dir=os.path.join(REPO, ".tmp"))
    try:
        stage = os.path.join(build, "jt-live-whisper")
        sp = os.path.join(stage, "python", "Lib", "site-packages")
        packs_spec = os.path.join(build, "packs")
        no_packs = os.path.join(build, "no-packs")  # keep the runner's packs out
        os.makedirs(no_packs)
        env = {k: v for k, v in os.environ.items()
               if k.upper() not in ("PYTHONHOME", "PYTHONPATH")}
        env.update(PYTHONNOUSERSITE="1", JTLW_PORTABLE="1",
                   JTLW_PACKS_DIR=no_packs)

        print(f"[1/7] 可攜式 Python {PYVER}（uv）...", flush=True)
        base = install_python(os.path.join(stage, "python"))
        copied = copy_msvc_runtime(os.path.join(stage, "python"))
        print("      MSVC runtime："
              + ("、".join(copied) if copied else "不需複製（已附或建置機沒有）"))
        print("[2/7] venv 與 Windows 套件清單...", flush=True)
        vpy = create_venv(base, os.path.join(build, "venv"))
        print("[3/7] split_packs.py（分出選用套件包）...", flush=True)
        run([vpy, os.path.join(APPIMAGE_DIR, "split_packs.py"), packs_spec])
        print("[4/7] 複製 site-packages（扣除選用套件包）與 sitecustomize.py...",
              flush=True)
        n = copy_site_packages(os.path.join(build, "venv", "Lib",
                                            "site-packages"),
                               sp, os.path.join(packs_spec, "exclude.txt"))
        print(f"      {n} 個檔案")
        run([sys.executable, CHECK_SITE,
             os.path.join(build, "venv", "Lib", "site-packages"), sp,
             os.path.join(packs_spec, "exclude.txt")])
        shutil.copy2(os.path.join(APPIMAGE_DIR, "sitecustomize.py"), sp)
        spy = os.path.join(stage, "python", "python.exe")
        if subprocess.run([spy, "-c", "import pip"], env=env).returncode != 0:
            run([spy, "-m", "ensurepip"], env=env)
        run([spy, "-c", "import pip; print('      pip', pip.__version__, 'ready')"],
            env=env)
        print("[5/7] 程式檔、packs\\ 清單與啟動檔...", flush=True)
        stage_app(os.path.join(stage, "app"), packs_spec)
        copy_cmd_crlf(os.path.join(HERE, "jt-live-whisper.cmd"),
                      os.path.join(stage, "jt-live-whisper.cmd"))
        shutil.copy2(os.path.join(HERE, "launcher.py"), stage)
        print("[6/7] 煙霧測試（用打包好的 python）...", flush=True)
        smoke_test(stage, env)
        print("[7/7] 壓縮 ZIP（deflate）...", flush=True)
        write_zip(stage, out)
    finally:
        shutil.rmtree(build, ignore_errors=True)
    print("")
    print(f"已產生：{out}")
    print(f"大小：{os.path.getsize(out) / 1024 / 1024:.0f} MB")
    print(f"耗時：{time.time() - t0:.0f} 秒")


if __name__ == "__main__":
    main()
