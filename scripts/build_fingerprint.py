#!/usr/bin/env python3
"""Compute a stable fingerprint of every input that can change the built tools.

The daily schedule must not rebuild for nothing.  Everything that can actually
make a different aapt2/zipalign is folded into one hash:

* the resolved build-tools version and the AOSP tag it maps to
* the clang and cmake versions, which are themselves resolved at run time
* a hash of this repository's own build inputs (patches, cmake glue, build.py)

The AOSP sources themselves are *not* hashed: they are fetched from an immutable
release tag, so their content cannot change without the version changing too.

The previous fingerprint is read back from the published BUILD-INFO.txt, which
keeps the state inside the artifact instead of adding another place to sync.

Exit codes: 0 always when the fingerprint could be computed, 1 on bad input.
"""

import argparse
import hashlib
import json
import os
import re
import sys

SCHEMA = 'fingerprint-v1'

FIELD_RE = re.compile(r'^\s*fingerprint\s*:\s*([0-9a-f]{64})\s*$', re.MULTILINE)


def log(message):
    print('[fingerprint] %s' % message, flush=True)


def build_fingerprint(values):
    """Serialise the inputs deterministically and hash them."""
    lines = [SCHEMA]
    for key in sorted(values):
        lines.append('%s=%s' % (key, values[key].strip()))
    payload = '\n'.join(lines) + '\n'
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    return digest, payload


def previous_fingerprint(path):
    if not path:
        return None
    if not os.path.isfile(path):
        log('no previous BUILD-INFO.txt at %s' % path)
        return None
    text = open(path, encoding='utf-8', errors='replace').read()
    match = FIELD_RE.search(text)
    if not match:
        log('the previous BUILD-INFO.txt carries no fingerprint field')
        return None
    return match.group(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--build-tools', required=True)
    parser.add_argument('--aosp-tag', required=True)
    parser.add_argument('--api-level', default='')
    parser.add_argument('--clang', required=True)
    parser.add_argument('--cmake', required=True)
    parser.add_argument('--inputs', required=True,
                        help='hash of this repository build inputs, e.g. from hashFiles')
    parser.add_argument('--previous', default='',
                        help='BUILD-INFO.txt of the published release, if any')
    parser.add_argument('--force', action='store_true',
                        help='ignore the previous fingerprint and rebuild')
    parser.add_argument('--github-output', default='')
    parser.add_argument('--json', default='')
    args = parser.parse_args()

    values = {
        'build-tools': args.build_tools,
        'aosp-tag': args.aosp_tag,
        'api-level': args.api_level or 'unknown',
        'clang': args.clang,
        'cmake': args.cmake,
        'inputs': args.inputs,
    }
    digest, payload = build_fingerprint(values)

    previous = previous_fingerprint(args.previous)
    if args.force:
        should_build = True
        reason = 'force rebuild requested'
    elif previous is None:
        should_build = True
        reason = 'no previous fingerprint to compare against'
    elif previous != digest:
        should_build = True
        reason = 'the inputs changed'
    else:
        should_build = False
        reason = 'every input still matches the published build'

    log('payload:\n%s' % payload)
    log('fingerprint: %s' % digest)
    log('previous   : %s' % (previous or '(none)'))
    log('decision   : %s (%s)' % ('build' if should_build else 'skip', reason))

    summary = {
        'fingerprint': digest,
        'previous': previous,
        'should_build': should_build,
        'reason': reason,
        'inputs': values,
    }

    print('FINGERPRINT=%s' % digest)
    print('PREVIOUS=%s' % (previous or ''))
    print('SHOULD_BUILD=%s' % ('true' if should_build else 'false'))

    if args.github_output:
        with open(args.github_output, 'a', encoding='utf-8') as handle:
            handle.write('fingerprint=%s\n' % digest)
            handle.write('previous=%s\n' % (previous or ''))
            handle.write('should_build=%s\n' % ('true' if should_build else 'false'))
            handle.write('reason=%s\n' % reason)
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
            handle.write('\n')

    return 0


if __name__ == '__main__':
    sys.exit(main())
