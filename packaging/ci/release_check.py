#!/usr/bin/env python3
"""Decide whether a release workflow run publishes, and name the release
(stdlib only; run from .github/workflows/release.yml, testable locally):

    release_check.py --event push --before <sha> --after <sha>
    release_check.py --event workflow_dispatch --after <sha>

push: release=true when APP_VERSION in translate_meeting.py differs between
<before> and <after>; a before of 40 zeros, or one not in this clone, counts
as changed. workflow_dispatch: always true.
Prints key=value lines and appends them to $GITHUB_OUTPUT when set:
  release, version (APP_VERSION at <after>), hash (first 7 of the full sha),
  tag (v<version>-<hash>), prefix (jt-live-whisper-<version>-<hash>)
"""
import argparse
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
ZERO_SHA = "0" * 40


def read_version(sha):
    """APP_VERSION at commit sha; None when the commit or the line is missing"""
    r = subprocess.run(["git", "-C", REPO, "show",
                        f"{sha}:translate_meeting.py"], capture_output=True)
    if r.returncode != 0:
        return None
    m = re.search(rb'^APP_VERSION = "([^"]+)"', r.stdout, re.M)
    return m.group(1).decode() if m else None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--event", required=True,
                    choices=["push", "workflow_dispatch"])
    ap.add_argument("--before", default="")
    ap.add_argument("--after", required=True)
    args = ap.parse_args(argv)
    if args.event == "push" and not args.before:
        ap.error("--event push 需要 --before")

    full = subprocess.run(["git", "-C", REPO, "rev-parse", "--verify",
                           "--quiet", f"{args.after}^{{commit}}"],
                          capture_output=True, text=True).stdout.strip()
    if not full:
        sys.exit(f"[錯誤] 找不到 commit {args.after}")
    version = read_version(full)
    if not version:
        sys.exit(f"[錯誤] 讀不到 {full[:7]} 的 translate_meeting.py APP_VERSION")

    if args.event == "workflow_dispatch":
        release, why = True, "手動執行（workflow_dispatch）一律發佈"
    elif args.before == ZERO_SHA:
        release, why = True, "before 全為 0（新分支或第一次 push），視為版號有變"
    else:
        old = read_version(args.before)
        release = old != version
        why = (f"APP_VERSION {old} -> {version}" if old is not None else
               f"before {args.before[:7]} 不在這個 clone 裡或讀不到 APP_VERSION，"
               "視為版號有變")
    print(why, file=sys.stderr)

    hash7 = full[:7]
    out = {"release": "true" if release else "false", "version": version,
           "hash": hash7, "tag": f"v{version}-{hash7}",
           "prefix": f"jt-live-whisper-{version}-{hash7}"}
    lines = "".join(f"{k}={v}\n" for k, v in out.items())
    sys.stdout.write(lines)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
