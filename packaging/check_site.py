#!/usr/bin/env python3
"""Completeness guard for the portable builds (build-appimage.sh,
windows/build-windows.py) after copying site-packages:

    python check_site.py <venv site-packages> <staged site-packages> <exclude.txt>

Core distributions = every *.dist-info in the venv whose RECORD is not in
exclude.txt (split_packs.py moved those into packs). Every file their RECORD
lists inside site-packages (not __pycache__/*.pyc) must exist in the staged
copy; otherwise the missing paths are printed and the exit code is 1.
"""
import csv
import glob
import os
import sys

SHOW_MAX = 50


def find_missing(src, staged, exclude_file):
    """(missing paths, core distribution count, checked file count)"""
    with open(exclude_file, encoding="utf-8") as f:
        excluded = {ln.strip() for ln in f if ln.strip()}
    missing, n_dists, n_files = [], 0, 0
    for record in sorted(glob.glob(os.path.join(src, "*.dist-info", "RECORD"))):
        if os.path.relpath(record, src).replace(os.sep, "/") in excluded:
            continue  # pack distribution
        n_dists += 1
        with open(record, encoding="utf-8", newline="") as f:
            for row in csv.reader(f):
                path = row[0] if row else ""
                if (not path or path.startswith("../") or os.path.isabs(path)
                        or "__pycache__/" in path or path.endswith(".pyc")):
                    continue  # outside site-packages, or bytecode
                n_files += 1
                if not os.path.isfile(os.path.join(staged, path)):
                    missing.append(path)
    return missing, n_dists, n_files


def main(argv):
    for stream in (sys.stdout, sys.stderr):  # pipes use the ANSI code page on Windows
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    missing, n_dists, n_files = find_missing(*argv)
    if missing:
        print(f"打包失敗：site-packages 缺少 {len(missing)} 個核心檔案"
              f"（{n_dists} 個核心套件的 RECORD）：", file=sys.stderr)
        for path in missing[:SHOW_MAX]:
            print(f"  {path}", file=sys.stderr)
        if len(missing) > SHOW_MAX:
            print(f"  ...另外 {len(missing) - SHOW_MAX} 個", file=sys.stderr)
        return 1
    print(f"      site-packages 完整：{n_dists} 個核心套件、{n_files} 個檔案 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
