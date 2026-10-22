"""jt-live-whisper Windows portable launcher, run by jt-live-whisper.cmd with
the bundled python\\python.exe (Python instead of cmd so Chinese text prints
reliably).

  jt-live-whisper.cmd [args]                  app\\translate_meeting.py [args]
  jt-live-whisper.cmd --webui [args]          app\\webui.py [args]
  jt-live-whisper.cmd --install-pack <name|all>
                                              app\\jtlw_packs.py install ...

Config and logs go into app\\ (the program's own folder); on-demand packs into
%LOCALAPPDATA%\\jt-live-whisper\\packs.
"""
import glob
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app")
START_CMD = os.path.join(HERE, "jt-live-whisper.cmd")
# flags that only print and exit -> never let the gpu prompt stand in front
NONRUN = {"-h", "--help", "--version", "--list-devices", "--install-pack"}


def get_data_root():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
        r"~\AppData\Local")
    return os.path.join(base, "jt-live-whisper")


def get_child_env(packs):
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ("PYTHONHOME", "PYTHONPATH")}
    env.update(PYTHONNOUSERSITE="1", JTLW_PORTABLE="1",
               JTLW_START_CMD=START_CMD, JTLW_PACKS_DIR=packs)
    return env


def detect_nvidia():
    """NVIDIA driver present: nvcuda.dll or a working nvidia-smi -L"""
    nvcuda = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "nvcuda.dll")
    if os.path.isfile(nvcuda):
        return True
    try:
        return subprocess.run(["nvidia-smi", "-L"], capture_output=True,
                              timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_child(script, args, env):
    """Run app\\<script> from app\\ and return its exit code."""
    proc = subprocess.Popen([sys.executable, os.path.join(APP, script)] + args,
                            cwd=APP, env=env)
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:
            pass  # Ctrl+C reaches the child too: let it clean up and exit


def offer_gpu_pack(declined, env):
    try:
        with open(os.path.join(APP, "packs", "packs.json"),
                  encoding="utf-8") as f:
            size = json.load(f)["packs"]["gpu"]["size_mb"]
        size_text = (f"，約 {size / 1024:.1f} GB" if size >= 1024
                     else f"，約 {size} MB")
    except (OSError, ValueError, KeyError, TypeError):
        size_text = ""
    print("")
    print(f"偵測到 NVIDIA 顯示卡。安裝 gpu 套件（NVIDIA cuBLAS／cuDNN{size_text}，"
          "只下載一次）")
    print("後，辨識與翻譯可用 GPU 加速；略過則用 CPU 執行，之後可手動安裝：")
    print(f'  "{START_CMD}" --install-pack gpu')
    if not sys.stdin.isatty():
        print(f'[提示] 非互動模式，未安裝 gpu 套件：可用 "{START_CMD}" '
              "--install-pack gpu 下載")
        return
    try:
        ans = input("現在下載安裝嗎？(Y/n) ").strip()
    except EOFError:
        print("")
        return  # no explicit answer: ask again next time
    if ans[:1] in ("n", "N"):
        open(declined, "w").close()
        print(f"已略過 gpu 套件（要改回來：刪除 {declined}，"
              "或直接執行 --install-pack gpu）")
    elif run_child("jtlw_packs.py", ["install", "gpu"], env) == 0:
        print("gpu 套件安裝完成，本次啟動即用 GPU 加速")


def main(argv):
    for stream in (sys.stdout, sys.stderr):  # pipes default to the ANSI code page
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    root = get_data_root()
    packs = os.path.join(root, "packs")
    os.makedirs(packs, exist_ok=True)
    env = get_child_env(packs)

    if argv[:1] == ["--install-pack"]:
        if len(argv) < 2:
            print(f'用法："{START_CMD}" --install-pack '
                  "<gpu|ml|overlay|argos|moonshine|all>", file=sys.stderr)
            return 2
        return run_child("jtlw_packs.py", ["install"] + argv[1:], env)

    print("── 環境檢查 ──────────────────────────────────────────────")
    if not shutil.which("ffmpeg"):
        print("[缺] ffmpeg（錄音檔格式轉換需要）：winget install Gyan.FFmpeg")
    has_nvidia = detect_nvidia()
    if has_nvidia:
        print("[資訊] 偵測到 NVIDIA 驅動（可用 GPU 加速；未安裝的套件會於首次使用時下載）")
    else:
        print("[資訊] 未偵測到 NVIDIA 驅動，辨識與翻譯將使用 CPU")

    declined = os.path.join(root, "gpu-declined")
    if (has_nvidia and not NONRUN.intersection(argv)
            and not glob.glob(os.path.join(packs, "gpu", ".jtlw-complete-*"))
            and not os.path.isfile(declined)):
        offer_gpu_pack(declined, env)

    if argv[:1] == ["--webui"]:
        return run_child("webui.py", argv[1:], env)
    return run_child("translate_meeting.py", argv, env)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
