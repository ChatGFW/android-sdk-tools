#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check that the hand written source lists in ``build-tools/*.cmake`` still cover
the AOSP tree we are compiling.

``aapt2.cmake`` / ``zipalign.cmake`` enumerate every translation unit explicitly
because the project does not use ``file(GLOB)``.  When Google adds a new
production ``.cpp`` to a build-tools ``Android.bp`` our list silently becomes
incomplete and the linker either fails with a wall of undefined symbols or, worse,
the tool ships without the new code path.

The authoritative source list is therefore the *upstream* ``Android.bp``: every
``.cpp``/``.cc`` that appears outside a ``cc_test*`` module must be compiled.  Test
modules, ``tests/`` helpers and generated files are ignored - they are not part of
the shipped tools.

Exit codes: 0 = lists are complete, 1 = a production source is missing,
3 = the upstream build files could not be inspected.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TARGETS = (
    # (label, source root relative to src/, cmake file, cmake prefix)
    ("aapt2", "base/tools/aapt2", "build-tools/aapt2.cmake", "base/tools/aapt2"),
    ("zipalign", "build/tools/zipalign", "build-tools/zipalign.cmake", "build/tools/zipalign"),
)

QUOTED = re.compile(r'"([^"]+\.(?:cpp|cc))"')
BLOCK_START = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{")
TEST_TYPES = ("cc_test",)
CMakeEntry = re.compile(r"\$\{SRC\}/(\S+?\.(?:cpp|cc))")


def strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        idx = line.find("//")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


def parse_android_bp(path: Path) -> set[str]:
    """every .cpp/.cc of the file that is not part of a cc_test* module"""
    text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    found: set[str] = set()
    block_type = ""
    depth = 0
    for line in text.splitlines():
        m = BLOCK_START.match(line)
        if depth == 0 and m:
            block_type = m.group(1)
        in_test = block_type.startswith(TEST_TYPES)
        if not in_test:
            found.update(QUOTED.findall(line))
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            depth = 0
            if line.strip() == "}":
                block_type = ""
    return found


def walk_sources(root: Path) -> set[str]:
    """fallback for directories without an Android.bp: skip test directories"""
    found = set()
    if not root.is_dir():
        return found
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in (".cpp", ".cc"):
            continue
        rel = path.relative_to(root)
        if "test" in rel.parts[:-1] or "tests" in rel.parts[:-1]:
            continue
        if rel.stem.endswith("_test"):
            continue
        found.add(str(rel))
    return found


def parse_cmake(path: Path, prefix: str) -> set[str]:
    if not path.is_file():
        raise SystemExit("missing cmake file: %s" % path)
    found = set()
    for entry in CMakeEntry.findall(strip_comments(path.read_text(encoding="utf-8"))):
        if entry.startswith(prefix + "/"):
            found.add(entry[len(prefix) + 1:])
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", default="src", help="the AOSP source directory created by get_source.py")
    parser.add_argument("--repo", default=".", help="repository root that contains build-tools/")
    args = parser.parse_args()

    src = Path(args.src)
    repo = Path(args.repo)
    if not src.is_dir():
        print("ERROR: source tree %s does not exist, run get_source.py first" % src, file=sys.stderr)
        return 3

    failed = False
    for label, rel_root, cmake_file, prefix in TARGETS:
        root = src / rel_root
        if not root.is_dir():
            print("ERROR: %s is missing from the source tree (%s)" % (label, root), file=sys.stderr)
            return 3
        bp = root / "Android.bp"
        if bp.is_file():
            upstream = parse_android_bp(bp)
            origin = "Android.bp"
        else:
            upstream = walk_sources(root)
            origin = "source tree (no Android.bp)"
        local = parse_cmake(repo / cmake_file, prefix)
        missing = sorted(upstream - local)
        extra = sorted(local - upstream)
        print("%-9s upstream=%-3d (%s)  cmake=%-3d  missing=%d  extra=%d"
              % (label, len(upstream), origin, len(local), len(missing), len(extra)))
        if missing:
            failed = True
            for name in missing:
                print("  MISSING from %s: %s" % (cmake_file, rel_root + "/" + name))
            print("  add them, for example:")
            for name in missing:
                print("    ${SRC}/%s/%s" % (rel_root, name))
        for name in extra:
            print("  note: %s lists %s which upstream does not build" % (cmake_file, name))

    if failed:
        print("\nBuild tools source layout changed: refusing to build a partial tool.")
        print("Update the explicit source lists in build-tools/*.cmake for this AOSP tag.")
        return 1
    print("\nall source lists match the upstream build definitions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
