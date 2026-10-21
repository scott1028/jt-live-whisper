#!/bin/bash
# Build the self-contained jt-live-whisper AppImage:
#   usr/python/                       portable CPython 3.12.13 (uv /
#                                     python-build-standalone, ships pip)
#   usr/python/lib/python3.12/site-   repo venv's packages MINUS the on-demand
#   packages                          pack exclusions (split_packs.py),
#                                     + sitecustomize.py
#   usr/lib/                          PortAudio and its deps that are not
#                                     present on every desktop
#   app/                              program files (git ls-files minus
#                                     docs/ images/ packaging/) + packs/ specs
# Requires: uv, rsync, the repo venv, appimagetool (found via $APPIMAGETOOL,
# PATH or .tmp/tools/).
#
#   JOBS=4 MEM=4G ./build-appimage.sh   (mksquashfs caps; zstd level 15 default)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PYVER="3.12.13"
JOBS="${JOBS:-4}"
MEM="${MEM:-4G}"
START_TS=$SECONDS

command -v uv >/dev/null 2>&1 || {
    echo "需要 uv（用来取得可攜式 Python）：https://docs.astral.sh/uv/" >&2; exit 1; }
command -v rsync >/dev/null 2>&1 || { echo "需要 rsync" >&2; exit 1; }
[ -x "$REPO/venv/bin/python" ] || { echo "找不到 $REPO/venv" >&2; exit 1; }

# find appimagetool: $APPIMAGETOOL -> PATH -> .tmp/tools/ (download location)
TOOL=""
if [ -n "${APPIMAGETOOL:-}" ] && [ -x "${APPIMAGETOOL:-}" ]; then
    TOOL="$APPIMAGETOOL"
elif command -v appimagetool >/dev/null 2>&1; then
    TOOL="$(command -v appimagetool)"
elif [ -x "$REPO/.tmp/tools/appimagetool-x86_64.AppImage" ]; then
    TOOL="$REPO/.tmp/tools/appimagetool-x86_64.AppImage"
else
    echo "找不到 appimagetool。請先下載並 chmod +x：" >&2
    echo "  https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" >&2
    echo "存到 .tmp/tools/appimagetool-x86_64.AppImage，或設 APPIMAGETOOL 指向它。" >&2
    exit 1
fi

APPDIR="$(mktemp -d "$REPO/.tmp/appdir.XXXXXX")"
trap 'rm -rf "$APPDIR"' EXIT

# ── 1. portable Python ─────────────────────────────────────────────────────
echo "[1/6] 可攜式 Python $PYVER（uv）..."
uv python install "$PYVER" >/dev/null
PYDIR="$(ls -d "$(uv python dir)"/cpython-"$PYVER"-linux-* 2>/dev/null | head -1)"
[ -n "$PYDIR" ] && [ -x "$PYDIR/bin/python3.12" ] || {
    echo "uv 裝好後找不到 $PYVER 安裝目錄" >&2; exit 1; }
mkdir -p "$APPDIR/usr/python"
cp -a "$PYDIR/." "$APPDIR/usr/python/"

# ── 2. site-packages = venv minus pack exclusions ──────────────────────────
echo "[2/6] 複製 site-packages（扣除選用套件包）..."
"$REPO/venv/bin/python" "$HERE/split_packs.py" >/dev/null
SP="$APPDIR/usr/python/lib/python3.12/site-packages"
mkdir -p "$SP"
rsync -a -m --exclude='__pycache__/' \
      --exclude-from="$HERE/packs/exclude.txt" \
      "$REPO/venv/lib/python3.12/site-packages/" "$SP/"
for m in torch PyQt6 nvidia triton spacy moonshine_voice; do
    if [ -e "$SP/$m" ]; then
        echo "打包失敗：$m 不應出現在 AppImage 裡" >&2; exit 1
    fi
done
echo "      torch/PyQt6/nvidia/triton/spacy/moonshine_voice 均已排除 ✓"

# ── 3. sitecustomize + pip sanity ──────────────────────────────────────────
echo "[3/6] sitecustomize.py 與 pip..."
cp "$HERE/sitecustomize.py" "$SP/"
"$APPDIR/usr/python/bin/python3.12" -c "import pip" 2>/dev/null ||
    "$APPDIR/usr/python/bin/python3.12" -m ensurepip >/dev/null
"$APPDIR/usr/python/bin/python3.12" -c "import pip; print('      pip', pip.__version__, 'ready')"

# ── 4. PortAudio + deps not on every desktop ───────────────────────────────
echo "[4/6] PortAudio 函式庫..."
PA="$(ls /usr/lib/x86_64-linux-gnu/libportaudio.so.2 /usr/lib/*/libportaudio.so.2 \
       2>/dev/null | head -1)"
[ -n "$PA" ] || {
    echo "找不到系統 libportaudio.so.2（sudo apt install libportaudio2）" >&2; exit 1; }
mkdir -p "$APPDIR/usr/lib"
cp -L "$PA" "$APPDIR/usr/lib/libportaudio.so.2"
chmod 755 "$APPDIR/usr/lib/libportaudio.so.2"
echo "      libportaudio.so.2"
# allowlist: present on essentially every Linux desktop -> do not bundle
ALLOW='^(ld-linux|libc\.|libm\.|libmvec|libdl\.|libpthread\.|librt\.|libresolv|libutil|libnsl|libstdc\+\+|libgcc_s|libasound|libpulse|libX11|libxcb|libXau|libXdmcp|libdbus|libsystemd|libapparmor|libasyncns|libsndfile|libFLAC|libvorbis|libopus|libogg|libmpg123|libmp3lame)'
ldd "$(readlink -f "$PA")" | awk '$2=="=>" && $3 ~ /^\// {print $1, $3}' |
while read -r so path; do
    echo "$so" | grep -Eq "$ALLOW" && continue
    cp -aL "$path" "$APPDIR/usr/lib/"
    echo "      $so"
done

# ── 5. app/ (program files + pack specs) ───────────────────────────────────
echo "[5/6] 程式檔與 packs/ 清單..."
mkdir -p "$APPDIR/app"
# skip files tracked but missing from the worktree (e.g. pending deletions)
( cd "$REPO" && git ls-files -z | grep -zv '^docs/' | grep -zv '^images/' \
      | grep -zv '^packaging/' \
      | while IFS= read -r -d '' f; do
            if [ -f "$f" ]; then printf '%s\0' "$f"; fi
        done \
      | tar --null -cf - -T - ) \
    | tar -C "$APPDIR/app" -xf -
# new modules may still be untracked - include explicitly
cp "$REPO/jtlw_packs.py" "$APPDIR/app/"
mkdir -p "$APPDIR/app/packs"
cp "$HERE"/packs/*.txt "$HERE"/packs/packs.json "$APPDIR/app/packs/"
for f in translate_meeting.py webui.py subtitle_overlay.py jtlw_packs.py \
         packs/packs.json; do
    [ -f "$APPDIR/app/$f" ] || { echo "打包失敗：app/$f 缺失" >&2; exit 1; }
done

# ── 6. pack ────────────────────────────────────────────────────────────────
echo "[6/6] appimagetool（zstd 15 預設, -processors $JOBS, -mem $MEM）..."
cp "$HERE/AppRun" "$APPDIR/AppRun"
cp "$HERE/jt-live-whisper.desktop" "$APPDIR/jt-live-whisper.desktop"
cp "$REPO/icons/jt-live-whisper.png" "$APPDIR/jt-live-whisper.png"
cp "$REPO/icons/jt-live-whisper.png" "$APPDIR/.DirIcon"
chmod +x "$APPDIR/AppRun" "$APPDIR/jt-live-whisper.desktop"

mkdir -p "$REPO/dist"
OUT="$REPO/dist/jt-live-whisper-x86_64.AppImage"
rm -f "$OUT"
# -n: no update information (local build); -processors/-mem cap memory use
# (mksquashfs' zstd default compression level 15 is used as-is)
ARCH=x86_64 "$TOOL" -n --comp zstd \
    --mksquashfs-opt=-processors --mksquashfs-opt="$JOBS" \
    --mksquashfs-opt=-mem --mksquashfs-opt="$MEM" "$APPDIR" "$OUT"

echo ""
echo "已產生：$OUT"
du -h "$OUT"
echo "耗時：$((SECONDS - START_TS)) 秒"
