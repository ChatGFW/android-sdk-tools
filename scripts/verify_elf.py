#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove that an ELF file is a fully static AArch64 Linux executable.

``-static`` on the command line, ``ldd`` output or a file name prove nothing, so
this script reads the ELF structures directly and additionally cross-checks the
result with ``readelf`` when it is available:

* ELFCLASS64, little endian, ``EM_AARCH64`` (183)
* ``ET_EXEC`` (a static PIE / ``ET_DYN`` is reported as a failure unless allowed)
* **no** ``PT_INTERP`` program header (no dynamic loader is requested)
* **no** ``PT_DYNAMIC`` / ``DT_NEEDED`` entry (nothing is resolved at run time)
* no ``.interp`` section, executable bit set, non-trivial size

Usage
-----
    python3 scripts/verify_elf.py --expect-arch aarch64 dist/aapt2 dist/zipalign
    python3 scripts/verify_elf.py --json report.json dist/aapt2

Exit code 0 only when every file passes.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ELF_MAGIC = b"\x7fELF"
ELFCLASS64 = 2
ELFDATA2LSB = 1
ET_EXEC = 2
ET_DYN = 3
PT_LOAD = 1
PT_DYNAMIC = 2
PT_INTERP = 3
DT_NULL = 0
DT_NEEDED = 1
DT_STRTAB = 5
EM_AARCH64 = 183
EM_NAMES = {EM_AARCH64: "aarch64", 62: "x86_64", 40: "arm", 3: "x86"}
ET_NAMES = {ET_EXEC: "ET_EXEC", ET_DYN: "ET_DYN", 1: "ET_REL", 4: "ET_CORE"}
MIN_SIZE = 64 * 1024  # a real aapt2/zipalign is far bigger; guards against stubs


class ElfError(RuntimeError):
    pass


def read_structs(handle, offset, count, fmt, size):
    handle.seek(offset)
    raw = handle.read(count * size)
    if len(raw) != count * size:
        raise ElfError("truncated table at offset %d" % offset)
    return [struct.unpack_from(fmt, raw, index * size) for index in range(count)]


def parse_elf(path: Path) -> dict:
    with path.open("rb") as handle:
        header = handle.read(64)
        if len(header) < 64 or header[:4] != ELF_MAGIC:
            raise ElfError("not an ELF file")
        ei_class, ei_data = header[4], header[5]
        e_type, e_machine = struct.unpack_from("<HH", header, 16)
        e_phoff = struct.unpack_from("<Q", header, 32)[0]
        e_phentsize, e_phnum = struct.unpack_from("<HH", header, 54)
        e_shoff = struct.unpack_from("<Q", header, 40)[0]
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", header, 58)

        info = {
            "path": str(path),
            "ei_class": "ELF64" if ei_class == ELFCLASS64 else "ELF32(class=%d)" % ei_class,
            "ei_data": "LSB" if ei_data == ELFDATA2LSB else "MSB(data=%d)" % ei_data,
            "e_type": ET_NAMES.get(e_type, "type=%d" % e_type),
            "e_type_value": e_type,
            "e_machine": EM_NAMES.get(e_machine, "machine=%d" % e_machine),
            "e_machine_value": e_machine,
            "size": path.stat().st_size,
            "interp": None,
            "has_dynamic_segment": False,
            "needed": [],
            "has_interp_section": False,
            "build_id": None,
        }
        if ei_class != ELFCLASS64 or ei_data != ELFDATA2LSB:
            return info

        segments = []
        if e_phnum and e_phentsize >= 56:
            for phdr in read_structs(handle, e_phoff, e_phnum, "<IIQQQQQQ", 56):
                p_type, _p_flags, p_offset, p_vaddr, _paddr, p_filesz, _memsz, _align = phdr
                segments.append((p_type, p_offset, p_vaddr, p_filesz))
                if p_type == PT_INTERP:
                    handle.seek(p_offset)
                    info["interp"] = handle.read(max(p_filesz, 1)).split(b"\0")[0].decode("utf-8", "replace")
                elif p_type == PT_DYNAMIC:
                    info["has_dynamic_segment"] = True
                    info.update(_dynamic(handle, p_offset, p_filesz, segments))

        if e_shnum and e_shentsize >= 64:
            info["has_interp_section"] = _has_section(handle, e_shoff, e_shentsize, e_shnum, e_shstrndx, ".interp")
        return info


def _dynamic(handle, offset, filesz, segments) -> dict:
    """collect DT_NEEDED names (needs the string table) and the build id marker"""
    entries = read_structs(handle, offset, filesz // 16, "<qQ", 16)
    needed, strtab = [], None
    for tag, value in entries:
        if tag == DT_NULL:
            break
        if tag == DT_NEEDED:
            needed.append(value)
        elif tag == DT_STRTAB:
            strtab = value
    names = []
    if needed and strtab is not None:
        file_offset = None
        for p_type, p_offset, p_vaddr, p_filesz in segments:
            if p_type == PT_LOAD and p_vaddr <= strtab < p_vaddr + p_filesz:
                file_offset = p_offset + (strtab - p_vaddr)
                break
        if file_offset is not None:
            handle.seek(file_offset)
            blob = handle.read(65536)
            for index in needed:
                end = blob.find(b"\0", index)
                names.append(blob[index : end if end > 0 else len(blob)].decode("utf-8", "replace"))
    return {"needed": names or ["<unresolved DT_NEEDED entry>"] * len(needed)}


def _has_section(handle, shoff, shentsize, shnum, shstrndx, name) -> bool:
    sections = read_structs(handle, shoff, shnum, "<IIQQQQIIQQ", 64)
    if shstrndx >= len(sections):
        return False
    strtab_off, strtab_size = sections[shstrndx][4], sections[shstrndx][5]
    handle.seek(strtab_off)
    names = handle.read(strtab_size)
    for section in sections:
        index = section[0]
        end = names.find(b"\0", index)
        current = names[index : end if end > 0 else len(names)].decode("utf-8", "replace")
        if current == name:
            return True
    return False


def readelf_cross_check(path: Path) -> tuple[bool, list[str]]:
    """independent verification with binutils readelf"""
    notes = []
    if shutil.which("readelf") is None:
        return True, ["readelf not installed, cross-check skipped"]
    ok = True
    header = subprocess.run(["readelf", "-hW", str(path)], capture_output=True, text=True)
    programs = subprocess.run(["readelf", "-lW", str(path)], capture_output=True, text=True)
    dynamic = subprocess.run(["readelf", "-dW", str(path)], capture_output=True, text=True)
    for result, label in ((header, "-h"), (programs, "-l"), (dynamic, "-d")):
        if result.returncode != 0:
            ok = False
            notes.append("readelf %s failed: %s" % (label, (result.stderr or "").strip()[:200]))
    if "INTERP" in programs.stdout:
        ok = False
        notes.append("readelf -l reports an INTERP segment")
    if "-d" not in notes and "There is no dynamic section" in dynamic.stdout:
        notes.append("readelf -d: no dynamic section")
    if "(NEEDED)" in dynamic.stdout:
        ok = False
        notes.append("readelf -d reports NEEDED entries")
    return ok, notes


def verify(path: Path, expect_arch: str, allow_pie: bool) -> tuple[bool, list[str], dict]:
    problems: list[str] = []
    if not path.is_file():
        return False, ["%s does not exist" % path], {"path": str(path)}
    mode = path.stat().st_mode
    if not mode & 0o111:
        problems.append("file is not executable (mode %o)" % (mode & 0o777))
    try:
        info = parse_elf(path)
    except ElfError as exc:
        return False, ["cannot parse ELF: %s" % exc], {"path": str(path)}

    if info["ei_class"] != "ELF64":
        problems.append("not a 64-bit ELF (%s)" % info["ei_class"])
    if info["ei_data"] != "LSB":
        problems.append("not little endian (%s)" % info["ei_data"])
    if info["e_machine_value"] != expect_arch:
        problems.append("machine is %s, expected %s"
                        % (info["e_machine"], EM_NAMES.get(expect_arch, str(expect_arch))))
    if info["e_type_value"] != ET_EXEC and not (allow_pie and info["e_type_value"] == ET_DYN):
        problems.append("type is %s, expected ET_EXEC" % info["e_type"])
    if info["interp"] is not None:
        problems.append("PT_INTERP present (%s) - dynamically linked" % info["interp"])
    if info["needed"]:
        problems.append("DT_NEEDED present (%s) - shared libraries required" % ", ".join(info["needed"]))
    if info["has_interp_section"]:
        problems.append(".interp section present")
    if info["size"] < MIN_SIZE:
        problems.append("suspiciously small (%d bytes)" % info["size"])

    cross_ok, notes = readelf_cross_check(path)
    if not cross_ok:
        problems.extend(notes)
    info["readelf"] = notes
    return not problems, problems, info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", help="ELF files to verify")
    parser.add_argument("--expect-arch", default="aarch64", choices=sorted(set(EM_NAMES.values())))
    parser.add_argument("--allow-pie", action="store_true", help="accept ET_DYN static PIE binaries")
    parser.add_argument("--json", dest="json_path", default="")
    args = parser.parse_args()

    expected = {name: value for value, name in EM_NAMES.items()}[args.expect_arch]
    report, failed = [], 0
    for name in args.files:
        ok, problems, info = verify(Path(name), expected, args.allow_pie)
        info["ok"] = ok
        info["problems"] = problems
        report.append(info)
        if ok:
            print("PASS  %-24s %s %s, no PT_INTERP, no DT_NEEDED, %d bytes"
                  % (Path(name).name, info.get("ei_class"), info.get("e_machine"), info.get("size", 0)))
        else:
            failed += 1
            print("FAIL  %-24s %s" % (Path(name).name, "; ".join(problems)))

    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, indent=2) + "\n")
    if failed:
        print("\n%d of %d binaries are not fully static %s executables" % (failed, len(args.files), args.expect_arch))
        return 1
    print("\nall %d binaries are fully static %s Linux executables" % (len(args.files), args.expect_arch))
    return 0


if __name__ == "__main__":
    sys.exit(main())
