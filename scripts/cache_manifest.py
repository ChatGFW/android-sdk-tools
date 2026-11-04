#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SHA-256 manifest for the GitHub Actions cache.

A cache restore is only an optimisation: if the restored tree does not match the
manifest byte for byte the workflow must throw it away and rebuild instead of
compiling against a corrupted source tree.

    python3 scripts/cache_manifest.py create --root .cache/src --manifest src.sha256
    python3 scripts/cache_manifest.py verify --root .cache/src --manifest src.sha256

Manifest format (stable, diffable)::

    # cache-manifest v1 files=<n> bytes=<n>
    <sha256>  <relative/path>

``verify`` exits 0 on a perfect match, 1 when files are missing or modified and
prints at most ``--max-report`` differences (extra files are only reported, a
re-clone can legitimately leave stray artefacts behind).
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_EXCLUDES = (
    "**/.git/**",
    ".git/**",
    "**/CMakeFiles/**",
    "**/CMakeCache.txt",
    "**/*.o",
    "**/*.a",
    "**/*.so",
    "**/__pycache__/**",
    "**/build/**",
)
CHUNK = 1 << 20


def excluded(rel: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern.lstrip("*"))
               for pattern in patterns)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            sha.update(block)
    return sha.hexdigest()


def collect(root: Path, patterns: tuple[str, ...]) -> list[str]:
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            if excluded(rel, patterns) or full.is_symlink():
                continue
            files.append(rel)
    files.sort()
    return files


def create(args) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        print("cache_manifest: %s is not a directory" % root, file=sys.stderr)
        return 2
    patterns = tuple(args.exclude) + DEFAULT_EXCLUDES
    files = collect(root, patterns)
    workers = max(1, min(args.jobs or os.cpu_count() or 4, 32))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        hashes = list(pool.map(lambda rel: digest(root / rel), files))
    total = sum((root / rel).stat().st_size for rel in files)
    target = Path(args.manifest)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        handle.write("# cache-manifest v1 files=%d bytes=%d\n" % (len(files), total))
        for rel, sha in zip(files, hashes):
            handle.write("%s  %s\n" % (sha, rel))
    print("cache_manifest: wrote %s (%d files, %.1f MiB)" % (target, len(files), total / 1048576))
    return 0


def read_manifest(path: Path) -> dict[str, str]:
    entries = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("  ", 1)
        if len(parts) == 2:
            entries[parts[1]] = parts[0]
    return entries


def verify(args) -> int:
    root = Path(args.root).resolve()
    manifest = Path(args.manifest)
    if not manifest.is_file():
        print("cache_manifest: manifest %s is missing" % manifest, file=sys.stderr)
        return 1
    if not root.is_dir():
        print("cache_manifest: %s is missing -> cache is invalid" % root, file=sys.stderr)
        return 1
    expected = read_manifest(manifest)
    if not expected:
        print("cache_manifest: manifest is empty -> cache is invalid", file=sys.stderr)
        return 1

    workers = max(1, min(args.jobs or os.cpu_count() or 4, 32))
    missing = [rel for rel in expected if not (root / rel).is_file()]
    present = [rel for rel in expected if (root / rel).is_file()]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        actual = dict(zip(present, pool.map(lambda rel: digest(root / rel), present)))
    modified = [rel for rel in present if actual[rel] != expected[rel]]

    if not missing and not modified:
        print("cache_manifest: OK, %d files match %s" % (len(expected), manifest))
        extra = set(collect(root, tuple(args.exclude) + DEFAULT_EXCLUDES)) - set(expected)
        if extra:
            print("cache_manifest: note, %d untracked files (ignored)" % len(extra))
        return 0

    print("cache_manifest: INVALID (%d missing, %d modified of %d)"
          % (len(missing), len(modified), len(expected)), file=sys.stderr)
    for rel in (missing + modified)[: args.max_report]:
        state = "missing" if rel in missing else "modified"
        print("  %s: %s" % (state, rel), file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "verify"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--root", required=True, help="directory the manifest describes")
        cmd.add_argument("--manifest", required=True, help="manifest file path")
        cmd.add_argument("--exclude", action="append", default=[], help="extra glob to skip (repeatable)")
        cmd.add_argument("--jobs", type=int, default=0, help="hash workers (default: cpu count)")
        if name == "verify":
            cmd.add_argument("--max-report", type=int, default=20)
    args = parser.parse_args()
    return create(args) if args.command == "create" else verify(args)


if __name__ == "__main__":
    sys.exit(main())
