#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve the newest *stable* Android SDK Build Tools ``37.x`` and its AOSP source tag.

Why this exists
---------------
The build tools version from the SDK repository (e.g. ``37.0.0``) is **not** an
AOSP tag, **not** an Android platform version and **not** an NDK version.  This
script derives the mapping from evidence only:

1. metadata  -> which ``build-tools;<version>`` packages exist and on which channel
2. channel   -> only ``channel-0`` (stable); any ``rc``/``beta``/``alpha``/``preview``
                marker is rejected, preview sources are never used
3. evidence  -> list the final release tags of ``platform/build``, then for each
                candidate tag read ``frameworks/base``'s ``Build.java`` and require
                its highest ``VERSION_CODES`` constant to equal the build tools
                major version
4. sources   -> require the tag to really contain the ``aapt2`` / ``zipalign`` sources

Any step that cannot be confirmed is a hard failure with diagnostics; there is no
silent fallback to an older version.

Usage
-----
    python3 scripts/resolve_build_tools.py --output /tmp/out.env
    python3 scripts/resolve_build_tools.py --requested 37.0.0 --metadata-file /tmp/repo.xml
    python3 scripts/resolve_build_tools.py --json /tmp/resolved.json

Exit codes: 0 ok, 2 resolution/mapping failure, 1 unexpected error.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import gzip
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_METADATA_URL = "https://dl.google.com/android/repository/repository2-3.xml"
GITILES = "https://android.googlesource.com"
UA = {"User-Agent": "android-sdk-tools-ci/1.0", "Accept-Encoding": "identity"}
# version strings that must never be treated as stable
UNSTABLE_RE = re.compile(r"(rc|beta|alpha|preview|dev|canary|exp)", re.I)
# final AOSP release tag, e.g. android-17.0.0_r1 (qpr/beta tags never match)
RELEASE_TAG_RE = re.compile(r"^android-(\d+)\.(\d+)\.(\d+)_r(\d+)$")
VERSION_CODE_RE = re.compile(r"\bint\s+([A-Z][A-Z0-9_]*)\s*=\s*(\d+)\s*;")


class ResolutionError(RuntimeError):
    """the mapping could not be confirmed; the caller must fail the build"""


def log(msg: str) -> None:
    print("[resolve] " + msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- http
def http_get(url: str, *, tries: int = 5, timeout: int = 90) -> bytes:
    """GET with exponential backoff (gitiles rate limits aggressively)."""
    if url.count("://") != 1:
        # catches base+url concatenation bugs before they become a bare 404
        raise ResolutionError("refusing to fetch a malformed url: %s" % url)
    delay = 3.0
    last = None
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if (resp.headers.get('Content-Encoding') or '').lower() == 'gzip':
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as exc:
            last = "HTTP %s" % exc.code
            if exc.code in (408, 429, 500, 502, 503, 504) and attempt < tries:
                log("%s -> %s, retry in %.0fs" % (url, last, delay))
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise ResolutionError("%s: %s" % (url, last)) from exc
        except Exception as exc:  # noqa: BLE001 - network layer, retry everything
            last = str(exc)
            if attempt < tries:
                log("%s -> %s, retry in %.0fs" % (url, last, delay))
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise ResolutionError("%s: %s" % (url, last)) from exc
    raise ResolutionError("%s: %s" % (url, last))


def gitiles_rel(url_or_path: str) -> str:
    """repository url (repository.json) -> gitiles path below the base host.

    ``repository.json`` stores full clone urls such as
    ``https://android.googlesource.com/platform/frameworks/base`` while a gitiles
    request needs ``platform/frameworks/base`` relative to ``GITILES``.  Accepting
    both forms here keeps callers honest and makes a double prefix (the bug that
    broke the first CI run) impossible.
    """
    text = url_or_path.strip().rstrip("/")
    base = GITILES.rstrip("/") + "/"
    if text.startswith(base):
        return text[len(base):]
    if "://" in text:
        raise ResolutionError(
            "repository %s is not hosted on %s, refusing to guess a gitiles path"
            % (url_or_path, GITILES)
        )
    return text.lstrip("/")


def gitiles_text(rel_url: str, tag: str, path: str) -> str:
    url = "%s/%s/+/refs/tags/%s/%s?format=TEXT" % (GITILES, gitiles_rel(rel_url), tag, path)
    raw = http_get(url)
    try:
        return base64.b64decode(raw).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        raise ResolutionError("cannot decode %s: %s" % (url, exc)) from exc


# ----------------------------------------------------------------------- metadata
def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", text.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def load_metadata(args) -> tuple[bytes, list[dict]]:
    if args.metadata_file:
        payload = Path(args.metadata_file).read_bytes()
        log("metadata: local file %s" % args.metadata_file)
    else:
        log("metadata: %s" % args.metadata_url)
        payload = http_get(args.metadata_url)
    digest = hashlib.sha256(payload).hexdigest()
    log("metadata sha256: %s (%d bytes)" % (digest, len(payload)))

    root = ET.fromstring(payload)
    packages = []
    for node in root.iter():
        if local(node.tag) != "remotePackage":
            continue
        path = node.attrib.get("path", "")
        if not path.startswith("build-tools;"):
            continue
        version_text = path.split(";", 1)[1]
        version = parse_version(version_text)
        if version is None:
            continue

        # revision may carry preview/obsolete markers as children
        revision = next((c for c in node if local(c.tag) == "revision"), None)
        preview = False
        if revision is not None:
            preview = any(local(c.tag) == "preview" for c in revision)
            for name in ("major", "minor", "micro"):
                got = next((c for c in revision if local(c.tag) == name), None)
                if got is not None and (got.text or "").strip().isdigit():
                    version = tuple(  # keep version and revision consistent
                        int((got.text or "0").strip()) if name == idx else value
                        for idx, value in zip(("major", "minor", "micro"), version)
                    )
        channel = next((c.attrib.get("ref", "") for c in node if local(c.tag) == "channelRef"), "")
        obsolete = node.attrib.get("obsolete", "false").lower() == "true"

        archives = []
        for arch in node.iter():
            if local(arch.tag) != "archive":
                continue
            info = {}

            def absorb(element):
                for child in element:
                    key = local(child.tag)
                    if len(child):
                        # <complete><size/><checksum/><url/></complete> wraps the payload
                        absorb(child)
                        continue
                    if key == "checksum":
                        info["checksum_type"] = child.attrib.get("type", "")
                    info[key] = (child.text or "").strip()

            absorb(arch)
            if info.get("host-os") == "linux":
                archives.append(info)

        packages.append(
            {
                "version": version,
                "version_text": version_text,
                "channel": channel,
                "obsolete": obsolete,
                "preview": preview,
                "archives": archives,
                "display": next((c.text or "" for c in node if local(c.tag) == "display-name"), ""),
            }
        )
    log("metadata: %d build-tools packages" % len(packages))
    return payload, packages


def stability_problems(pkg: dict) -> list[str]:
    """Everything that disqualifies a package apart from its major series."""
    reasons = []
    if pkg["preview"]:
        reasons.append("preview revision")
    if pkg["channel"] and pkg["channel"] != "channel-0":
        reasons.append("channel %s" % pkg["channel"])
    if UNSTABLE_RE.search(pkg["version_text"]):
        reasons.append("unstable version string")
    if pkg["obsolete"]:
        reasons.append("obsolete")
    if not pkg["archives"]:
        reasons.append("no linux archive")
    return reasons


def stable_candidates(packages: list[dict], series: int | None = None) -> list[dict]:
    stable, rejected = [], []
    for pkg in packages:
        major, minor, micro = pkg["version"]
        reasons = stability_problems(pkg)
        if series is not None and major != series:
            reasons.append("series %d != %d" % (major, series))
        if reasons:
            rejected.append((pkg["version_text"], reasons))
        else:
            stable.append(pkg)
    stable.sort(key=lambda p: p["version"], reverse=True)
    return stable, rejected


# --------------------------------------------------------------------------- aosp
def repo_urls() -> dict[str, str]:
    data = json.loads((ROOT / "repository.json").read_text())
    return {Path(item["path"]).name: item["url"] for item in data}


def final_release_tags(url: str) -> list[tuple[tuple[int, int, int, int], str]]:
    out = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", url],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
    )
    if out.returncode != 0:
        raise ResolutionError(
            "cannot list tags of %s: %s" % (url, out.stderr.decode("utf-8", "replace").strip())
        )
    tags = []
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        name = line.rsplit("refs/tags/", 1)[-1].strip()
        if RELEASE_TAG_RE.search(name):
            key = tuple(int(x) for x in RELEASE_TAG_RE.search(name).groups())
            tags.append((key, name))
    tags.sort(reverse=True)
    if not tags:
        raise ResolutionError("no final release tag (android-X.Y.Z_rN) found in %s" % url)
    log("aosp: %d final release tags, newest %s" % (len(tags), tags[0][1]))
    return tags


def api_level_of_tag(tag: str, frameworks_url_rel: str) -> tuple[int, str]:
    """highest VERSION_CODES constant of android.os.Build at ``tag``"""
    text = gitiles_text(frameworks_url_rel, tag, "core/java/android/os/Build.java")
    best, best_name = -1, ""
    for name, value in VERSION_CODE_RE.findall(text):
        if "CUR_DEVELOPMENT" in name or "PREVIEW" in name:
            continue
        number = int(value)
        if number >= 9000:  # CUR_DEVELOPMENT is 10000, never a released level
            continue
        if number > best:
            best, best_name = number, name
    if best < 0:
        raise ResolutionError("cannot find VERSION_CODES in Build.java at %s" % tag)
    return best, best_name


def verify_sources(rel: dict[str, str], tag: str) -> list[str]:
    """the tag must contain both tools and their build inputs"""
    checks = [
        ("build", "tools/zipalign/ZipAlign.cpp"),
        ("build", "tools/zipalign/ZipAlignMain.cpp"),
        ("base", "tools/aapt2/Main.cpp"),
        ("base", "tools/aapt2/Android.bp"),
    ]
    ok = []
    for repo, path in checks:
        url = "%s/%s/+/refs/tags/%s/%s?format=TEXT" % (GITILES, gitiles_rel(rel[repo]), tag, path)
        body = http_get(url)
        if len(body) < 16:
            raise ResolutionError("source check failed: %s is empty at %s" % (path, tag))
        ok.append("%s/%s" % (repo, path))
        log("source ok: %s/%s" % (repo, path))
    return ok


def resolve(args) -> dict:
    payload, packages = load_metadata(args)

    series = args.series
    if series is None:
        # Nothing pinned: a requested exact version carries its own major,
        # otherwise follow the newest major that actually has a stable release,
        # so a new build-tools series needs no workflow edit.
        if args.requested:
            parsed = parse_version(args.requested.strip())
            if not parsed:
                raise ResolutionError(
                    "build_tools_version '%s' is not MAJOR.MINOR.MICRO" % args.requested.strip()
                )
            series = parsed[0]
        else:
            all_stable, _ = stable_candidates(packages, None)
            if not all_stable:
                raise ResolutionError("the SDK metadata carries no stable build-tools release at all")
            series = max(p["version"][0] for p in all_stable)
            log("no series pinned, newest stable major in the metadata: %d" % series)

    stable, rejected = stable_candidates(packages, series)

    if not stable:
        detail = "\n".join("  %s: %s" % (v, ", ".join(r)) for v, r in sorted(rejected)[-15:])
        raise ResolutionError(
            "no stable build-tools %d.x in the SDK metadata.\nrejected candidates:\n%s" % (series, detail)
        )
    log("stable %d.x candidates: %s" % (series, ", ".join(p["version_text"] for p in stable)))

    if args.requested:
        wanted = args.requested.strip()
        if not parse_version(wanted):
            raise ResolutionError("build_tools_version '%s' is not MAJOR.MINOR.MICRO" % wanted)
        chosen = next((p for p in stable if p["version_text"] == wanted), None)
        if chosen is None:
            raise ResolutionError(
                "requested build-tools %s is not a stable %d.x release. available: %s"
                % (wanted, series, ", ".join(p["version_text"] for p in stable))
            )
    else:
        chosen = stable[0]

    version = chosen["version"]
    log("selected build-tools %s (channel %s)" % (chosen["version_text"], chosen["channel"] or "implicit"))

    rel = repo_urls()
    if "build" not in rel or "base" not in rel:
        raise ResolutionError("repository.json must contain the 'build' and 'base' repositories")

    tags = final_release_tags(rel["build"])
    api_major = version[0]

    if args.aosp_tag:
        candidates = [(None, args.aosp_tag)]
        log("aosp tag forced by --aosp-tag (still verified): %s" % args.aosp_tag)
    else:
        candidates = tags[: max(args.max_tag_probes, 1)]

    selected = None
    probed = []
    for _, tag in candidates:
        api, constant = api_level_of_tag(tag, rel["base"])
        probed.append((tag, api, constant))
        log("probe %s -> max VERSION_CODES %d (%s)" % (tag, api, constant))
        if api == api_major:
            selected = (tag, api, constant)
            break

    if selected is None:
        detail = "\n".join("  %s -> API %s (%s)" % (t, a, c) for t, a, c in probed)
        raise ResolutionError(
            "no AOSP release tag matches build-tools %s (API %d); probes:\n%s\n"
            "refusing to fall back to an older or preview source tree"
            % (chosen["version_text"], api_major, detail)
        )

    tag, api, constant = selected
    sources = verify_sources(rel, tag)

    linux = chosen["archives"][0]
    result = {
        "build_tools_version": chosen["version_text"],
        "build_tools_series": str(series),
        "build_tools_channel": chosen["channel"] or "channel-0",
        "aosp_tag": tag,
        "aosp_api_level": str(api),
        "aosp_api_constant": constant,
        "aosp_repositories": {"build": rel["build"], "base": rel["base"]},
        "verified_sources": sources,
        "metadata_url": args.metadata_file or args.metadata_url,
        "metadata_sha256": hashlib.sha256(payload).hexdigest(),
        "official_linux_archive": {
            "url": linux.get("url", ""),
            "size": linux.get("size", ""),
            "checksum": linux.get("checksum", ""),
            "checksum_type": linux.get("checksum_type", ""),
        },
        "rejected_candidates": [v for v, _ in rejected],
        "probed_tags": [t for t, _, _ in probed],
        "source_key": "bt%s-%s" % (chosen["version_text"], tag),
    }
    log("resolved: build-tools %s -> %s (API %d)" % (result["build_tools_version"], tag, api))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--series", type=int, default=None,
                        help="build-tools major series (default: the newest stable one)")
    parser.add_argument("--requested", default="", help="exact version from the workflow input; must be stable")
    parser.add_argument("--metadata-url", default=DEFAULT_METADATA_URL)
    parser.add_argument("--metadata-file", default="", help="use a local repository2-3.xml (offline)")
    parser.add_argument("--aosp-tag", default="", help="force a tag; still fully verified")
    parser.add_argument("--max-tag-probes", type=int, default=6, help="how many release tags to probe")
    parser.add_argument("--output", default="", help="write KEY=VALUE lines (GITHUB_OUTPUT compatible)")
    parser.add_argument("--json", dest="json_path", default="", help="write the full result as JSON")
    args = parser.parse_args()

    try:
        result = resolve(args)
    except ResolutionError as exc:
        print("\n[resolve] FATAL: %s" % exc, file=sys.stderr)
        print("[resolve] build aborted: the version/source mapping could not be confirmed.", file=sys.stderr)
        return 2

    if args.json_path:
        Path(args.json_path).write_text(json.dumps(result, indent=2) + "\n")
        log("json: %s" % args.json_path)

    lines = [
        ("BUILD_TOOLS_VERSION", result["build_tools_version"]),
        ("BUILD_TOOLS_SERIES", result["build_tools_series"]),
        ("AOSP_TAG", result["aosp_tag"]),
        ("AOSP_API_LEVEL", result["aosp_api_level"]),
        ("AOSP_API_CONSTANT", result["aosp_api_constant"]),
        ("METADATA_SHA256", result["metadata_sha256"]),
        ("SOURCE_KEY", result["source_key"]),
    ]
    for key, value in lines:
        print("%s=%s" % (key, value))
    if args.output:
        with open(args.output, "a", encoding="utf-8") as handle:
            for key, value in lines:
                handle.write("%s=%s\n" % (key, value))
        log("output: %s" % args.output)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ResolutionError as exc:  # pragma: no cover - defensive
        print("[resolve] FATAL: %s" % exc, file=sys.stderr)
        sys.exit(2)
