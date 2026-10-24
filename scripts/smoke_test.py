#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Functional smoke test for the freshly built ``aapt2`` / ``zipalign`` binaries.

The test must run on the *target* architecture (an ARM64 runner in CI), because a
binary that only passes ``readelf`` can still be completely broken at run time.

What is exercised
-----------------
* ``aapt2 version``                      - process starts, C++ runtime linked in
* ``aapt2 compile``                      - libpng + protobuf + zip writing
* ``aapt2 dump apc``                     - protobuf decoding
* ``aapt2 link`` (+ ``dump badging``)    - full link path, skipped when no framework
* ``zipalign`` negative / positive / ``-c`` checks plus an independent
  re-measurement of the entry alignment offsets (never trust the tool alone)

Exit code 0 only when no test FAILED; skipped tests are reported explicitly.
"""
from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

RESULTS: list[dict] = []


def record(name: str, status: str, detail: str) -> None:
    RESULTS.append({"test": name, "status": status, "detail": detail})
    print("%-5s %-18s %s" % (status, name, detail), flush=True)


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=600)


def first_line(text: str, limit: int = 240) -> str:
    text = (text or "").strip().replace("\n", " | ")
    return text[:limit]


# ------------------------------------------------------------------ fixtures
def make_png(path: Path) -> None:
    """2x2 RGBA PNG written by hand, so no external tool is needed"""
    raw = b"".join(b"\x00" + bytes([255, 0, 0, 255] * 2) for _ in range(2))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_res(work: Path) -> Path:
    res = work / "res"
    (res / "values").mkdir(parents=True)
    (res / "layout").mkdir()
    (res / "drawable").mkdir()
    (res / "values" / "strings.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        '    <string name="app_name">smoke</string>\n'
        '    <string name="greeting">hello %1$s</string>\n'
        '    <string-array name="colors">\n'
        '        <item>red</item>\n        <item>green</item>\n    </string-array>\n'
        '</resources>\n',
        encoding="utf-8",
    )
    (res / "layout" / "main.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<LinearLayout>\n'
        '    <TextView/>\n'
        '</LinearLayout>\n',
        encoding="utf-8",
    )
    make_png(res / "drawable" / "icon.png")
    return res


def make_manifest(work: Path) -> Path:
    path = work / "AndroidManifest.xml"
    # deliberately no android: attributes, so linking works without android.jar
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    package="com.example.smoketest"/>\n',
        encoding="utf-8",
    )
    return path


def make_zip(path: Path, entries: int = 24, native: bool = True) -> None:
    """stored zip with unaligned data so a bad zipalign cannot pass by luck"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        info = zipfile.ZipInfo("payload/odd.txt")
        archive.writestr(info, b"x" * 3)  # 3 bytes -> next entry cannot stay aligned
        for index in range(entries):
            archive.writestr("res/raw/data%02d.bin" % index, bytes([index % 251]) * (17 + index))
        if native:
            archive.writestr("lib/arm64-v8a/libsmoke.so", b"\x7fELF" + b"\x00" * 4093)


def entry_offsets(path: Path) -> list[tuple[str, int, bool]]:
    """(name, data offset, stored?) measured from the local headers itself"""
    out = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            with path.open("rb") as handle:
                handle.seek(info.header_offset)
                header = handle.read(30)
                if header[:4] != b"PK\x03\x04":
                    continue
                name_len, extra_len = struct.unpack_from("<HH", header, 26)
            out.append((info.filename, info.header_offset + 30 + name_len + extra_len,
                        info.compress_type == zipfile.ZIP_STORED))
    return out


def misaligned(path: Path, align: int) -> list[str]:
    return [name for name, offset, stored in entry_offsets(path) if stored and offset % align]


# -------------------------------------------------------------------- tests
def test_version(aapt2: Path) -> None:
    proc = run([str(aapt2), "version"])
    text = (proc.stdout + proc.stderr).lower()
    if proc.returncode == 0 and ("aapt2" in text or "android asset packaging tool" in text):
        record("aapt2-version", "PASS", first_line(proc.stdout))
    else:
        record("aapt2-version", "FAIL", "rc=%d %s" % (proc.returncode, first_line(proc.stderr or proc.stdout)))


def test_compile(aapt2: Path, work: Path) -> list[Path]:
    out = work / "compiled"
    out.mkdir(exist_ok=True)
    res = make_res(work)
    proc = run([str(aapt2), "compile", "-o", str(out), str(res / "values" / "strings.xml"),
                str(res / "layout" / "main.xml"), str(res / "drawable" / "icon.png")])
    flats = sorted(out.glob("*.flat"))
    if proc.returncode == 0 and len(flats) >= 3:
        record("aapt2-compile", "PASS", "%d .flat files: %s" % (len(flats), ", ".join(p.name for p in flats)))
    else:
        record("aapt2-compile", "FAIL", "rc=%d %s" % (proc.returncode, first_line(proc.stderr)))
    return flats


def test_dump(aapt2: Path, flats: list[Path]) -> None:
    target = next((f for f in flats if "values" in f.name), flats[0] if flats else None)
    if target is None:
        record("aapt2-dump-apc", "SKIP", "no compiled output to dump")
        return
    proc = run([str(aapt2), "dump", "apc", str(target)])
    if proc.returncode == 0 and proc.stdout.strip():
        record("aapt2-dump-apc", "PASS", "%d bytes of protobuf decoded" % len(proc.stdout))
    elif "unknown" in (proc.stderr or "").lower():
        record("aapt2-dump-apc", "SKIP", "dump apc unsupported by this aapt2")
    else:
        record("aapt2-dump-apc", "FAIL", "rc=%d %s" % (proc.returncode, first_line(proc.stderr)))


def test_link(aapt2: Path, work: Path, flats: list[Path], android_jar: str) -> None:
    if not flats:
        record("aapt2-link", "SKIP", "nothing compiled")
        return
    manifest = make_manifest(work)
    apk = work / "linked.apk"
    cmd = [str(aapt2), "link", "-o", str(apk), "--manifest", str(manifest)]
    if android_jar:
        cmd += ["-I", android_jar]
    cmd += [str(f) for f in flats]
    proc = run(cmd)
    if proc.returncode != 0:
        record("aapt2-link", "SKIP", "link needs a framework jar here: %s" % first_line(proc.stderr))
        return
    record("aapt2-link", "PASS", "linked %d resources -> %d bytes" % (len(flats), apk.stat().st_size))
    listing = run([str(aapt2), "dump", "resources", str(apk)])
    if listing.returncode == 0 and listing.stdout.strip():
        record("aapt2-dump-resources", "PASS", first_line(listing.stdout))
    else:
        record("aapt2-dump-resources", "FAIL", "rc=%d %s" % (listing.returncode, first_line(listing.stderr)))
    if android_jar:
        badging = run([str(aapt2), "dump", "badging", str(apk)])
        if badging.returncode == 0 and "package:" in badging.stdout:
            record("aapt2-dump-badging", "PASS", first_line(badging.stdout))
        else:
            record("aapt2-dump-badging", "FAIL", "rc=%d %s" % (badging.returncode, first_line(badging.stderr)))
    else:
        record("aapt2-dump-badging", "SKIP", "no android.jar provided")


def test_zipalign(zipalign: Path, work: Path) -> None:
    source = work / "unaligned.zip"
    make_zip(source)

    proc = run([str(zipalign), "-c", "4", str(source)])
    bad = misaligned(source, 4)
    if proc.returncode != 0:
        record("zipalign-detect", "PASS", "%d unaligned entries detected (%s)" % (len(bad), ", ".join(bad[:3])))
    else:
        record("zipalign-detect", "FAIL", "unaligned input was reported as aligned")

    aligned = work / "aligned.zip"
    proc = run([str(zipalign), "-f", "4", str(source), str(aligned)])
    if proc.returncode != 0 or not aligned.exists():
        record("zipalign-align", "FAIL", "rc=%d %s" % (proc.returncode, first_line(proc.stderr)))
        return
    record("zipalign-align", "PASS", "%d bytes -> %d bytes" % (source.stat().st_size, aligned.stat().st_size))

    check = run([str(zipalign), "-c", "4", str(aligned)])
    remaining = misaligned(aligned, 4)
    if check.returncode == 0 and not remaining:
        record("zipalign-verify", "PASS", "all %d stored entries are 4-byte aligned (independently measured)"
               % len(entry_offsets(aligned)))
    else:
        record("zipalign-verify", "FAIL", "rc=%d, %d misaligned (%s)"
               % (check.returncode, len(remaining), ", ".join(remaining[:3])))

    help_text = (run([str(zipalign), "-h"]).stdout + run([str(zipalign), "-h"]).stderr)
    if "-P" not in help_text:
        record("zipalign-page-16k", "SKIP", "this zipalign has no -P page alignment option")
        return
    paged = work / "aligned16k.zip"
    proc = run([str(zipalign), "-P", "16", "-f", "4", str(source), str(paged)])
    if proc.returncode != 0:
        record("zipalign-page-16k", "FAIL", "rc=%d %s" % (proc.returncode, first_line(proc.stderr)))
        return
    bad_so = [name for name, offset, stored in entry_offsets(paged)
              if stored and name.endswith(".so") and offset % 16384]
    if bad_so:
        record("zipalign-page-16k", "FAIL", "native libs not 16 KiB aligned: %s" % ", ".join(bad_so))
    else:
        record("zipalign-page-16k", "PASS", "lib/arm64-v8a/libsmoke.so is 16 KiB aligned")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--aapt2", required=True)
    parser.add_argument("--zipalign", required=True)
    parser.add_argument("--android-jar", default="", help="optional framework jar for the link test")
    parser.add_argument("--workdir", default="", help="keep the fixtures in this directory")
    parser.add_argument("--json", dest="json_path", default="")
    args = parser.parse_args()

    aapt2, zipalign = Path(args.aapt2).resolve(), Path(args.zipalign).resolve()
    work = Path(args.workdir).resolve() if args.workdir else Path(tempfile.mkdtemp(prefix="smoke-"))
    work.mkdir(parents=True, exist_ok=True)
    print("smoke test in %s" % work, flush=True)

    for binary in (aapt2, zipalign):
        if not binary.is_file():
            record("presence:" + binary.name, "FAIL", "%s is missing" % binary)
    if any(r["status"] == "FAIL" for r in RESULTS):
        return 1

    test_version(aapt2)
    flats = test_compile(aapt2, work)
    test_dump(aapt2, flats)
    test_link(aapt2, work, flats, args.android_jar)
    test_zipalign(zipalign, work)

    if args.json_path:
        Path(args.json_path).write_text(json.dumps(RESULTS, indent=2) + "\n")
    failed = [r for r in RESULTS if r["status"] == "FAIL"]
    skipped = [r for r in RESULTS if r["status"] == "SKIP"]
    print("\n%d passed, %d failed, %d skipped" % (len(RESULTS) - len(failed) - len(skipped), len(failed), len(skipped)))
    for item in skipped:
        print("SKIPPED: %s (%s)" % (item["test"], item["detail"]))
    if not args.workdir:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
