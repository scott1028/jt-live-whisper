#!/usr/bin/env python3
"""GitHub release notes (Markdown, to stdout) for one version:

    release_notes.py <version> <sha> [CHANGELOG path]

= the CHANGELOG section that starts with "### v<version>" (up to the next
"### v"), then the commit, which file is for which OS and the on-demand packs
note. A short fallback text replaces the section when it is missing.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_section(changelog, version):
    """Lines of the "### v<version>" section (heading included), or []"""
    with open(changelog, encoding="utf-8") as f:
        lines = f.read().splitlines()
    head = re.compile(rf"^### v{re.escape(version)}(\s|$)")
    start = next((i for i, ln in enumerate(lines) if head.match(ln)), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("### v")), len(lines))
    section = lines[start:end]
    while section and not section[-1].strip():
        section.pop()
    return section


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    version, sha = argv[0], argv[1]
    changelog = argv[2] if len(argv) == 3 else os.path.join(REPO, "CHANGELOG.md")
    prefix = f"jt-live-whisper-{version}-{sha[:7]}"
    section = get_section(changelog, version)
    if not section:
        section = [f"### v{version}", "",
                   f"這一版的更新內容請看 CHANGELOG.md（找不到 v{version} 這一節）。"]
    notes = section + [
        "",
        "---",
        "",
        f"**下載**（commit `{sha}`）",
        f"- Linux x86-64：`{prefix}-linux-x86_64.AppImage`，"
        "下載後 `chmod +x` 再直接執行",
        f"- Windows x86-64：`{prefix}-windows-x86_64.zip`，"
        "解壓縮後雙擊 `jt-live-whisper.cmd`",
        "- 兩者都自帶 Python 與核心套件；選用功能（GPU 加速、講者辨識、懸浮字幕、"
        "Argos 翻譯、Moonshine）的套件包第一次用到時才自動下載",
    ]
    sys.stdout.reconfigure(encoding="utf-8")
    print("\n".join(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
