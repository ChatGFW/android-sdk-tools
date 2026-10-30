#!/usr/bin/env python3
"""Resolve the newest *stable* clang offered by apt.llvm.org for this Ubuntu release.

Nothing here is pinned by hand: the repository index at https://apt.llvm.org/ is
parsed at run time, the highest stable major is selected, and the exact package
version is read back from that repository's Packages index.  The result is then
cross-checked against the upstream LLVM release feed, because apt.llvm.org can
lag behind a brand new tag.  A lagging repository is a hard error: silently
building with an older major would defeat the point of asking for the newest
stable toolchain.

apt.llvm.org only publishes released branches under versioned suites
(``llvm-toolchain-<codename>-<major>``); trunk lives in the unversioned suite,
so the highest versioned major is by construction a release branch.

Usage:
    python3 scripts/resolve_clang_version.py
    python3 scripts/resolve_clang_version.py --github-output "$GITHUB_OUTPUT"
"""

import argparse
import gzip
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE_URL = 'https://apt.llvm.org'
ARCH = 'arm64'

# a released version looks like 1:23.1.4~++20261020...-1~exp1; pre-releases carry
# an explicit rc/beta/alpha marker in the upstream version, so they are rejected
PRERELEASE_RE = re.compile(r'(rc|beta|alpha)', re.IGNORECASE)
SUITE_RE = re.compile(r'llvm-toolchain-([a-z0-9]+)-(\d+)\b')


def log(msg):
    print(msg, file=sys.stderr)


def _request_headers(user_agent):
    """Headers for every outbound request.

    Only the cross-check against the LLVM release feed touches api.github.com, but
    that one call is enough to fail: anonymous callers get 60 requests per hour
    counted per IP, and Actions runners share their egress address with unrelated
    jobs.  A token raises the budget to 5000 per hour against the token itself.
    """
    headers = {
        'User-Agent': user_agent,
        'Accept-Encoding': 'identity',
    }
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    return headers


def fetch(url, timeout=45, binary=False):
    # apt.llvm.org sits behind a CDN that compresses whether or not the client
    # asked for it, so ask for identity and still cope with a gzip response.
    req = urllib.request.Request(url, headers=_request_headers('taixu-resolve-clang/1.0'))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        if (resp.headers.get('Content-Encoding') or '').lower() == 'gzip':
            body = gzip.decompress(body)
    return body if binary else body.decode('utf-8', 'replace')


def codename():
    """Codename of the distribution we are running on."""
    path = '/etc/os-release'
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                key, _, value = line.strip().partition('=')
                if key == 'VERSION_CODENAME' and value:
                    return value.strip('"\'')
    return 'noble'


def discover_suites(codename_value):
    """Major versions apt.llvm.org publishes for this codename, ascending."""
    page = fetch(BASE_URL + '/')
    majors = set()
    for dist, major in SUITE_RE.findall(page):
        if dist == codename_value:
            majors.add(int(major))
    if not majors:
        sys.exit('::error::apt.llvm.org lists no llvm-toolchain-%s-<major> suite'
                 % codename_value)
    return sorted(majors)


def package_version(codename_value, major):
    """Exact clang package version inside one suite's arm64 index."""
    url = '%s/%s/dists/llvm-toolchain-%s-%d/main/binary-%s/Packages.gz' % (
        BASE_URL, codename_value, codename_value, major, ARCH)
    raw = fetch(url, binary=True)
    text = gzip.GzipFile(fileobj=io.BytesIO(raw)).read().decode('utf-8', 'replace')

    current = None
    for line in text.splitlines():
        if line.startswith('Package:'):
            current = line.split(':', 1)[1].strip()
        elif line.startswith('Version:') and current in ('clang-%d' % major, 'clang'):
            return line.split(':', 1)[1].strip()
    sys.exit('::error::suite llvm-toolchain-%s-%d has no clang package in its %s index'
             % (codename_value, major, ARCH))


def upstream_latest():
    """Newest non-prerelease LLVM tag from the upstream release feed."""
    url = 'https://api.github.com/repos/llvm/llvm-project/releases/latest'
    try:
        data = json.loads(fetch(url), strict=False)
    except (urllib.error.URLError, ValueError) as exc:
        log('warning: cannot reach the LLVM release feed (%s)' % exc)
        return None
    tag = data.get('tag_name') or ''
    if data.get('prerelease') or data.get('draft'):
        return None
    match = re.match(r'llvmorg-(\d+)\.(\d+)\.(\d+)$', tag)
    if not match:
        return None
    return tag, tuple(int(part) for part in match.groups())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--github-output', default='',
                        help='append key=value pairs to this file (GitHub Actions $GITHUB_OUTPUT)')
    parser.add_argument('--json', default='', help='write a machine readable summary here')
    args = parser.parse_args()

    dist = codename()
    majors = discover_suites(dist)
    log('apt.llvm.org suites for %s: %s' % (dist, ', '.join(str(m) for m in majors)))

    major = majors[-1]
    version = package_version(dist, major)
    log('suite llvm-toolchain-%s-%d -> clang %s' % (dist, major, version))

    # strip the epoch, then require a plain X.Y.Z release version
    plain = version.split(':', 1)[-1]
    match = re.match(r'(\d+)\.(\d+)\.(\d+)', plain)
    if not match:
        sys.exit('::error::cannot parse a release version out of %r' % version)
    semver = tuple(int(part) for part in match.groups())
    if PRERELEASE_RE.search(plain):
        sys.exit('::error::refusing a pre-release clang: %s' % version)

    latest = upstream_latest()
    if latest:
        tag, upstream_semver = latest
        log('upstream latest stable: %s' % tag)
        if upstream_semver[0] > semver[0]:
            sys.exit(
                '::error::apt.llvm.org for %s only publishes clang %d.x while upstream is '
                'already at %s.\n'
                'Refusing to silently fall back to an older major. Either wait for '
                'apt.llvm.org to publish the %d.x suite, or decide explicitly to stay on %d.'
                % (dist, semver[0], tag, upstream_semver[0], semver[0]))
        if upstream_semver > semver:
            log('::warning::apt.llvm.org has not packaged %s yet, using %s'
                % (tag, '.'.join(str(p) for p in semver)))
    else:
        log('::warning::upstream release feed unavailable, trusting the apt.llvm.org index')

    summary = {
        'clang_major': major,
        'clang_version': '.'.join(str(p) for p in semver),
        'clang_package_version': version,
        'distro_codename': dist,
        'repo_suite': 'llvm-toolchain-%s-%d' % (dist, major),
        'upstream_tag': latest[0] if latest else '',
    }
    for key, value in summary.items():
        print('%s=%s' % (key.upper(), value))

    if args.github_output:
        with open(args.github_output, 'a', encoding='utf-8') as handle:
            for key, value in summary.items():
                handle.write('%s=%s\n' % (key, value))
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
            handle.write('\n')


if __name__ == '__main__':
    main()
