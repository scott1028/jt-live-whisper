# PortAudio fallback for the jt-live-whisper AppImage: sounddevice on Linux
# only locates libportaudio via ctypes.util.find_library, which fails when
# the host has no system PortAudio. Only active inside the AppImage (APPDIR
# set) and when the bundled library exists; a complete no-op otherwise.
import ctypes.util
import glob
import os
import sys

_BUNDLED = os.path.join(os.environ.get("APPDIR", ""), "usr", "lib",
                        "libportaudio.so.2")

if os.environ.get("APPDIR") and os.path.isfile(_BUNDLED):
    _find_library_orig = ctypes.util.find_library

    def _find_library(name):  # portaudio only; everything else unchanged
        if name == "portaudio":
            return _find_library_orig(name) or _BUNDLED
        return _find_library_orig(name)

    ctypes.util.find_library = _find_library

# Completed on-demand packs (jtlw_packs.py) go on sys.path so subprocesses
# (overlay, engine) see them too; no-op when the packs dir does not exist.
_PACKS_ROOT = os.environ.get("JTLW_PACKS_DIR") or os.path.join(
    os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
    "jt-live-whisper", "packs")
if os.path.isdir(_PACKS_ROOT):
    for _d in sorted(os.listdir(_PACKS_ROOT)):
        _p = os.path.join(_PACKS_ROOT, _d)
        if os.path.isdir(_p) and glob.glob(
                os.path.join(_p, ".jtlw-complete-*")) and _p not in sys.path:
            sys.path.append(_p)
