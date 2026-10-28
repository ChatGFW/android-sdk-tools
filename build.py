#!/usr/bin/env python3
#
# Copyright © 2022 Github Lzhiyong
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an 'AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# pylint: disable=not-callable, line-too-long, no-else-return

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# One architecture only, built with the compiler of the build machine.
TARGET_ARCH = 'aarch64'

# The tools this tree builds. The workflow passes the same list through --target.
ANDROID_TOOLS = {
    'build-tools': ['aapt2', 'zipalign'],
}


def format_time(seconds):
    minute, sec = divmod(seconds, 60)
    hour, minute = divmod(minute, 60)

    hour = int(hour)
    minute = int(minute)
    if minute < 1:
        sec = float('%.2f' % sec)
    else:
        sec = int(sec)

    if hour != 0:
        return '{}h{}m{}s'.format(hour, minute, sec)
    elif minute != 0:
        return '{}m{}s'.format(minute, sec)
    else:
        return '{}s'.format(sec)


def info(message):
    print('\033[1;34m{}\033[0m'.format(message), flush=True)


def run(command, **kwargs):
    command = [str(c) for c in command]
    print('+ ' + ' '.join(command), flush=True)
    result = subprocess.run(command, **kwargs)
    if result.returncode != 0:
        sys.exit(result.returncode)
    return result


def tools_version():
    text = (ROOT / 'patches/misc/platform_tools_version.h').read_text()
    match = re.search(r'^\s*#define\s+PLATFORM_TOOLS_VERSION\s+"([^"]+)"', text, re.M)
    return match.group(1) if match else 'unknown'


# =============================== host protoc ===============================
def find_protoc(directory):
    if (directory / 'protoc').is_file():
        return directory / 'protoc'
    for candidate in sorted(directory.glob('protoc-*')):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def build_host_protoc(args):
    """build protoc for the build machine, it generates the *.pb.cc files of aapt2"""
    source = ROOT / 'src/protobuf'
    build_dir = (ROOT / args.host_build).resolve()
    protoc = find_protoc(build_dir)
    if protoc is not None:
        return protoc
    if not (source / 'CMakeLists.txt').exists():
        sys.exit('no protobuf source in {}, please run get_source.py first'.format(source))

    info('building host protoc ...')
    # always use the compilers of the build machine, never the cross ones
    env = {k: v for k, v in os.environ.items() if k not in ('CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'LDFLAGS')}
    run(['cmake', '-GNinja', '-S', source, '-B', build_dir,
         '-DCMAKE_BUILD_TYPE=Release',
         '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
         '-Dprotobuf_BUILD_TESTS=OFF',
         '-Dprotobuf_WITH_ZLIB=OFF'], env=env)
    run(['cmake', '--build', build_dir, '--target', 'protoc', '-j', args.jobs], env=env)
    protoc = find_protoc(build_dir)
    if protoc is None:
        sys.exit('failed to find the host protoc in {}'.format(build_dir))
    return protoc


# =============================== toolchains ===============================
def toolchain_args(args):
    """cmake arguments selecting the host compiler.

    The build machine compiles for itself: every release runs on a native
    AArch64 runner, so no cross toolchain is involved.
    """
    command = []
    cc = args.cc or os.environ.get('CC') or shutil.which('clang')
    cxx = args.cxx or os.environ.get('CXX') or shutil.which('clang++')
    if cc:
        command.append('-DCMAKE_C_COMPILER={}'.format(cc))
    if cxx:
        command.append('-DCMAKE_CXX_COMPILER={}'.format(cxx))
    return command, 'host ({} / {})'.format(cc or 'default', cxx or 'default')


# =============================== build ===============================
def verify_outputs(args, build_dir):
    """fail loudly when a requested target produced no executable"""
    binary_dir = build_dir / 'bin'
    if not binary_dir.exists():
        sys.exit('nothing was built in {}'.format(binary_dir))

    requested = [t for t in args.target if t != 'all']
    if not requested:
        requested = [tool for tools in ANDROID_TOOLS.values() for tool in tools]
    missing = [
        '{}/{}'.format(tooldir, tool)
        for tooldir, tools in ANDROID_TOOLS.items() for tool in tools
        if tool in requested and not (binary_dir / tooldir / tool).exists()
    ]
    if missing:
        sys.exit('\033[1;31mmissing tools: {}\033[0m'.format(', '.join(missing)))
    info('built: {}'.format(', '.join(
        '{}/{}'.format(tooldir, tool) for tooldir, tools in ANDROID_TOOLS.items()
        for tool in tools if tool in requested)))


def build(args):
    build_dir = Path(args.build or 'build/static-{}'.format(TARGET_ARCH))
    if not build_dir.is_absolute():
        build_dir = (Path.cwd() / build_dir).resolve()

    protoc = build_host_protoc(args)
    toolchain, description = toolchain_args(args)
    info('build-tools {}, arch {}, static link, toolchain {}'.format(
        tools_version(), TARGET_ARCH, description))

    command = ['cmake', '-GNinja', '-S', ROOT, '-B', build_dir,
               '-DCMAKE_BUILD_TYPE=Release',
               '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
               '-DSDK_STATIC=ON',
               '-DSDK_STRIP=ON',
               '-DPROTOC_PATH={}'.format(protoc)] + toolchain

    start_time = time.time()
    run(command)
    build_command = ['cmake', '--build', build_dir, '-j', args.jobs]
    if args.target != ['all']:
        build_command += ['--target'] + args.target
    if args.keep_going:
        # surface every remaining diagnostic in one run instead of stopping at the first
        # failed object; CI polls this workflow, so one slow run beats many fast ones.
        # NOTE: everything after '--' goes to the native tool, so --target must be added
        # first or cmake would forward it to ninja (which rejects --target).
        build_command += ['--', '-k', '0']
    run(build_command)

    verify_outputs(args, build_dir)
    info('build success cost time: {}'.format(format_time(time.time() - start_time)))


def main():
    parser = argparse.ArgumentParser(
        description='build the static aapt2 and zipalign host tools',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    jobs = os.cpu_count() or 1

    host = parser.add_argument_group('host toolchain')
    host.add_argument('--cc', help='C compiler, default is $CC or clang')
    host.add_argument('--cxx', help='C++ compiler, default is $CXX or clang++')

    parser.add_argument('--build', help='the build directory, default is build/static-<arch>')
    parser.add_argument('--host-build', default='build/host-protoc', help='build directory of the host protoc')
    parser.add_argument('--job', '--jobs', dest='jobs', default=jobs, help='run N jobs in parallel')
    parser.add_argument('--target', nargs='+', default=['all'],
                        help='build specified targets, e.g. aapt2 zipalign')
    parser.add_argument('--keep-going', action='store_true',
                        help='pass -k 0 to ninja so every failure is reported in one run')

    args = parser.parse_args()
    build(args)


if __name__ == '__main__':
    main()
