#!/usr/bin/env python3
"""Install the newest *stable* CMake from the official Kitware release feed.

The version is discovered at run time from the upstream release feed, never
pinned here.  Only non-prerelease, non-draft tags are considered, the official
``cmake-<ver>-linux-aarch64.tar.gz`` is downloaded together with Kitware's
``cmake-<ver>-SHA-256.txt`` and the digest is verified before the archive is
unpacked, so a truncated or substituted download cannot silently become the
toolchain.

Usage:
    python3 scripts/resolve_cmake_version.py --dest /opt
    python3 scripts/resolve_cmake_version.py --github-output "$GITHUB_OUTPUT"
"""

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request

API_LATEST = 'https://api.github.com/repos/Kitware/CMake/releases/latest'
ARCH_SUFFIX = 'linux-aarch64.tar.gz'
TAG_RE = re.compile(r'v(\d+)\.(\d+)\.(\d+)$')
PRERELEASE_RE = re.compile(r'(rc|beta|alpha)', re.IGNORECASE)


def log(msg):
    print(msg, file=sys.stderr)


def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'taixu-resolve-cmake/1.0',
        'Accept-Encoding': 'identity',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        if (resp.headers.get('Content-Encoding') or '').lower() == 'gzip':
            body = gzip.decompress(body)
        return body


def download(url, path, timeout=900):
    # the payload is already a tar.gz; transport-level compression would only
    # corrupt it, so ask for identity and never decompress here
    req = urllib.request.Request(url, headers={
        'User-Agent': 'taixu-resolve-cmake/1.0',
        'Accept-Encoding': 'identity',
    })
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(path, 'wb') as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            out.write(chunk)
    return digest.hexdigest(), size


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dest', default='/opt', help='where the toolchain is unpacked')
    parser.add_argument('--github-output', default='')
    parser.add_argument('--json', default='')
    parser.add_argument('--check-only', action='store_true',
                        help='report the resolved version without downloading it')
    args = parser.parse_args()

    release = json.loads(fetch(API_LATEST).decode('utf-8', 'replace'), strict=False)
    if release.get('prerelease') or release.get('draft'):
        sys.exit('::error::upstream latest release is a pre-release: %s' % release.get('tag_name'))

    tag = release.get('tag_name') or ''
    match = TAG_RE.match(tag)
    if not match:
        sys.exit('::error::cannot parse a stable version out of tag %r' % tag)
    version = '.'.join(match.groups())
    if PRERELEASE_RE.search(version):
        sys.exit('::error::refusing a pre-release cmake: %s' % version)
    log('upstream latest stable cmake: %s (published %s)'
        % (version, release.get('published_at', '?')))

    wanted = 'cmake-%s-%s' % (version, ARCH_SUFFIX)
    sums_name = 'cmake-%s-SHA-256.txt' % version
    assets = {asset['name']: asset['browser_download_url'] for asset in release.get('assets', [])}
    for name in (wanted, sums_name):
        if name not in assets:
            sys.exit('::error::release %s has no asset named %s' % (tag, name))

    if args.check_only:
        # the version gate only needs to know what upstream offers, so skip the
        # 52 MB download and the unpack entirely
        log('check-only: %s would be downloaded, stopping here' % wanted)
        print('CMAKE_VERSION=%s' % version)
        if args.github_output:
            with open(args.github_output, 'a', encoding='utf-8') as handle:
                handle.write('cmake_version=%s\n' % version)
        if args.json:
            with open(args.json, 'w', encoding='utf-8') as handle:
                json.dump({'version': version, 'home': None}, handle, indent=2)
                handle.write('\n')
        return

    with tempfile.TemporaryDirectory() as work_dir:
        archive = os.path.join(work_dir, wanted)
        digest, size = download(assets[wanted], archive)
        log('downloaded %s (%d bytes)' % (wanted, size))

        sums_text = fetch(assets[sums_name]).decode('utf-8', 'replace')
        expected = None
        for line in sums_text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip('*') == wanted:
                expected = parts[0].lower()
                break
        if not expected:
            sys.exit('::error::%s does not list %s' % (sums_name, wanted))
        if expected != digest:
            sys.exit('::error::SHA-256 mismatch for %s: expected %s, got %s'
                     % (wanted, expected, digest))
        log('SHA-256 verified: %s' % digest)

        os.makedirs(args.dest, exist_ok=True)
        with tarfile.open(archive, 'r:gz') as tar:
            members = tar.getnames()
            top = members[0].split('/')[0]
            # the digest was verified above, so trust the archive but keep the
            # mode bits (the data filter would drop some of them on new Pythons)
            try:
                tar.extractall(args.dest, filter='fully_trusted')
            except TypeError:  # Python < 3.11.4 has no filter parameter
                tar.extractall(args.dest)
        installed = os.path.join(args.dest, top)
        if not os.path.isfile(os.path.join(installed, 'bin', 'cmake')):
            sys.exit('::error::the archive did not unpack a bin/cmake into %s' % installed)

    reported = installed
    log('installed cmake at %s' % reported)
    print('CMAKE_VERSION=%s' % version)
    print('CMAKE_HOME=%s' % reported)
    if args.github_output:
        with open(args.github_output, 'a', encoding='utf-8') as handle:
            handle.write('cmake_version=%s\n' % version)
            handle.write('cmake_home=%s\n' % reported)
            handle.write('cmake_bin=%s\n' % os.path.join(reported, 'bin'))
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as handle:
            json.dump({'version': version, 'home': reported}, handle, indent=2)
            handle.write('\n')


if __name__ == '__main__':
    main()
