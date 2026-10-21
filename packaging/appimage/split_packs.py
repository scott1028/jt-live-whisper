#!/usr/bin/env python3
"""Split the venv's site-packages into AppImage core + on-demand packs.

Run at build time with the repo venv's python from the repo root:

    venv/bin/python packaging/appimage/split_packs.py [output_dir]

Writes next to this script (default):
  packs/<name>.txt   exact "name==version" pins for one pack
  packs/packs.json   dependencies, approx sizes, import-test module names
  packs/exclude.txt  site-packages-relative files to drop from the AppImage

A package stays in core whenever core code imports it at top level or any
core package depends on it; packs only get what is exclusive to them. The
script fails loudly when that invariant is broken.
"""
import ast
import importlib.metadata as md
import json
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", ".."))
OUT_DIR = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "packs")

# pack name -> roots (resolved names, normalized); deps = other packs needed
PACK_DEPS = {"ml": ["gpu"], "argos": ["ml"]}
PACK_IMPORTS = {
    "gpu": ["triton"],
    "ml": ["torch", "transformers", "resemblyzer", "librosa"],
    "overlay": ["PyQt6"],
    "argos": ["argostranslate"],
    "moonshine": ["moonshine_voice"],
}
PACK_ORDER = ["gpu", "ml", "overlay", "argos", "moonshine"]

# core features: live subtitles (faster-whisper), NLLB translate, denoise,
# WebUI; requests is used by the SSL-retry path of the model downloader
EXPLICIT_CORE_ROOTS = [
    "faster-whisper", "ctranslate2", "sentencepiece",
    "opencc-python-reimplemented", "noisereduce", "sounddevice", "soundfile",
    "requests", "fastapi", "uvicorn", "websockets", "huggingface-hub", "psutil",
]

# files whose top-level imports MUST stay inside core. subtitle_overlay.py is
# the overlay pack's entry (runs as its own process) and
# remote_whisper_server.py is the standalone GPU-server script (needs torch);
# neither is imported by the core app.
CORE_FILES = ["translate_meeting.py", "webui.py", "jtlw_tls.py"]
CORE_PKGS = ["jtlw_api", "jtlw_tts"]

# top-level imports that sit in try/except ImportError and degrade gracefully
# (verified in translate_meeting.py): they belong to their packs, not core
OPTIONAL_TOPLEVEL = {"moonshine_voice", "argostranslate"}

PIP_INDEX = {}  # pack -> extra pip index URL, or none when PyPI has the wheel
PIP_INDEX_NOTE = ("torch 2.14.1 comes from PyPI: its PyPI metadata already "
                  "depends on the CUDA 13 wheels (cuda-toolkit, nvidia-*-cu13,"
                  " triton), so no custom --index-url is needed.")


def norm(n):
    return re.sub(r"[-_.]+", "-", n).lower()


def load_dists():
    dists = {}
    for d in md.distributions():
        name = d.metadata["Name"]
        if not name:
            continue
        reqs = []
        for r in d.requires or []:
            if ";" in r:
                req, marker = r.split(";", 1)
                if "extra" in marker:
                    continue
            else:
                req = r
            reqs.append(norm(re.split(r"[\s<>=!;\[]", req)[0]))
        dists[norm(name)] = {"name": name, "version": d.version,
                             "requires": sorted(set(reqs)), "dist": d}
    return dists


def module_index(dists):
    """top-level importable module name -> normalized dist name"""
    mod2dist = {}
    for n, info in dists.items():
        d = info["dist"]
        names = set()
        tl = d.read_text("top_level.txt")
        if tl:
            names |= {x for x in tl.split() if x}
        rec = d.read_text("RECORD")
        if rec:
            for line in rec.splitlines():
                p = line.split(",")[0]
                if "/" in p and not p.startswith(".."):
                    top = p.split("/")[0]
                    if not top.endswith((".dist-info", ".data", ".py")) \
                            and not top.startswith("__"):
                        names.add(top)
        for m in names:
            mod2dist.setdefault(norm(m), n)
    return mod2dist


def closure(roots, dists, label=""):
    missing = [r for r in roots if r not in dists]
    if missing:
        print(f"WARNING: {label} roots not installed: {missing}",
              file=sys.stderr)
    seen, stack = set(), [r for r in roots if r in dists]
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        stack += dists.get(x, {}).get("requires", [])
    return seen & set(dists)


def toplevel_imports(path):
    """module names imported at file top level (incl. module-level try/if)"""
    out = set()
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except (SyntaxError, OSError) as e:
        sys.exit(f"SPLIT_FAIL: cannot parse {path}: {e}")

    def visit(body):
        for n in body:
            if isinstance(n, ast.Import):
                out.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                if n.module and n.level == 0:
                    out.add(n.module.split(".")[0])
            elif isinstance(n, ast.Try):
                visit(n.body)
                for h in n.handlers:
                    visit(h.body)
                visit(n.orelse)
                visit(n.finalbody)
            elif isinstance(n, ast.If):
                visit(n.body)
                visit(n.orelse)
    visit(tree.body)
    return out


def size_mb(names, dists):
    tot = 0
    for n in names:
        for f in dists[n]["dist"].files or []:
            try:
                p = f.locate()
                if p.is_file():
                    tot += p.stat().st_size
            except Exception:
                pass
    return round(tot / 1024 / 1024)


def main():
    os.chdir(REPO)
    dists = load_dists()
    mod2dist = module_index(dists)

    pack_roots = {
        "gpu": [n for n in dists if n.startswith("nvidia-") or n == "triton"],
        "ml": ["torch", "transformers", "resemblyzer", "librosa",
               "scikit-learn", "spectralcluster", "numba", "llvmlite"],
        "overlay": ["pyqt6"],
        "argos": ["argostranslate"],
        "moonshine": ["moonshine-voice"],
    }
    for p in PACK_ORDER:  # sanity: every declared root must be installed
        missing = [r for r in pack_roots[p] if r not in dists]
        if missing:
            sys.exit(f"SPLIT_FAIL: pack {p} roots missing from venv: {missing}")

    pack_closure = {p: closure(rs, dists, p) for p, rs in pack_roots.items()}
    pack_reachable = set().union(*pack_closure.values())

    # core = explicit roots + everything the core app imports at top level +
    # packages no pack reaches (dev leftovers simply stay in the AppImage)
    local = {"translate_meeting", "webui", "jtlw_tls", "jtlw_tts", "jtlw_api",
             "subtitle_overlay", "remote_whisper_server"}
    app_files = [f for f in CORE_FILES] + [
        f"{pkg}/{f}" for pkg in CORE_PKGS
        for f in sorted(os.listdir(pkg)) if f.endswith(".py")]
    app_mods = set()
    for f in app_files:
        app_mods |= toplevel_imports(f)
    app_mods -= set(sys.stdlib_module_names) | {"__future__"} | local
    app_mods -= OPTIONAL_TOPLEVEL
    app_dists, unmapped = {}, set()
    for m in sorted(app_mods):
        dn = mod2dist.get(norm(m))
        if dn:
            app_dists[m] = dn
        else:
            unmapped.add(m)
    if unmapped:
        print(f"NOTE: unmapped core imports (not installed / optional): "
              f"{sorted(unmapped)}", file=sys.stderr)

    core_roots = set(EXPLICIT_CORE_ROOTS) | set(app_dists.values()) \
        | (set(dists) - pack_reachable)
    core = closure(core_roots, dists, "core")

    pack_excl = {}
    for p in PACK_ORDER:
        e = pack_closure[p] - core
        for d in PACK_DEPS.get(p, []):
            e -= pack_closure[d] - core
        pack_excl[p] = e

    # fail loudly: core top-level imports and core dependencies must stay
    errors = []
    for m, dn in sorted(app_dists.items()):
        for p, e in pack_excl.items():
            if dn in e:
                errors.append(f"core app imports {m} -> {dn}, "
                              f"but it would be excluded into pack {p}")
    for c in sorted(core):
        for r in dists[c]["requires"]:
            for p, e in pack_excl.items():
                if r in e:
                    errors.append(f"core package {dists[c]['name']} requires "
                                  f"{dists[r]['name']}, but it would be "
                                  f"excluded into pack {p}")
    if errors:
        print("SPLIT_FAIL:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)

    # --- outputs ---
    os.makedirs(OUT_DIR, exist_ok=True)
    spec = {"order": PACK_ORDER, "note_pip_index": PIP_INDEX_NOTE, "packs": {}}
    print(f"core: {len(core)} packages stay in the AppImage "
          f"({size_mb(core, dists)} MB)")
    print(f"     {', '.join(sorted(dists[n]['name'] for n in core))}")
    for p in PACK_ORDER:
        names = sorted(pack_excl[p])
        pins = [f"{dists[n]['name']}=={dists[n]['version']}" for n in names]
        with open(os.path.join(OUT_DIR, f"{p}.txt"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(pins) + "\n")
        spec["packs"][p] = {
            "depends": PACK_DEPS.get(p, []),
            "imports": PACK_IMPORTS[p],
            "size_mb": size_mb(pack_excl[p], dists),
            "pip_index": PIP_INDEX.get(p),
            "packages": len(names),
        }
        print(f"pack {p}: {len(names)} packages, "
              f"{spec['packs'][p]['size_mb']} MB, "
              f"deps={spec['packs'][p]['depends']}")
        print(f"     {', '.join(pins)}")
    with open(os.path.join(OUT_DIR, "packs.json"), "w",
              encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
        f.write("\n")

    n_files = 0
    with open(os.path.join(OUT_DIR, "exclude.txt"), "w",
              encoding="utf-8") as f:
        for p in PACK_ORDER:
            for n in sorted(pack_excl[p]):
                for fl in dists[n]["dist"].files or []:
                    rel = str(fl.as_posix() if hasattr(fl, "as_posix")
                              else fl)
                    if rel.startswith(".."):
                        rel = rel.split("../")[-1]  # outside site-packages
                    f.write(rel + "\n")
                    n_files += 1
    print(f"exclude.txt: {n_files} file paths "
          f"({sum(spec['packs'][p]['size_mb'] for p in PACK_ORDER)} MB moved "
          f"out of the AppImage)")
    print(f"wrote {OUT_DIR}")


if __name__ == "__main__":
    main()
