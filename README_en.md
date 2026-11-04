# android-sdk-tools

[简体中文](README.md) | **English**

![platform](https://img.shields.io/badge/platform-linux%2Farm64-lightgrey)
![linkage](https://img.shields.io/badge/linkage-fully%20static-brightgreen)
![license](https://img.shields.io/badge/license-Apache--2.0-blue)

Android build tools for AArch64 Linux. Compiled from AOSP sources, producing
exactly two host tools, both **fully static** executables:

* no `PT_INTERP` — executes directly on any AArch64 kernel, no dynamic loader required
* no `DT_NEEDED` — no runtime dependencies on system libraries, glibc is linked in statically

Containers, minimal images and cross-compilation environments therefore need no
matching glibc installed beforehand.

## Quick start

```bash
base=https://github.com/ChatGFW/android-sdk-tools/releases/latest/download
curl -LO "$base/aapt2" -LO "$base/zipalign" -LO "$base/SHA256SUMS"

sha256sum -c SHA256SUMS          # verify integrity
chmod +x aapt2 zipalign
./aapt2 version
```

## Compatibility

| item | requirement |
| --- | --- |
| architecture | `linux/arm64` (AArch64) |
| kernel | any kernel able to execute AArch64 ELF, no minimum version |
| libc | not required, statically linked in |
| other dependencies | none |

x86_64 hosts can run these through qemu-user, but that is untested and out of scope.

## Release assets

| file | content |
| --- | --- |
| `aapt2` | static AArch64 executable |
| `zipalign` | static AArch64 executable |
| `SHA256SUMS` | for `sha256sum -c` verification |
| `BUILD-INFO.txt` | build-tools version, AOSP tag, API level, toolchain versions, input fingerprint |

## Continuous integration

Workflow: `.github/workflows/build-tools-static.yml`

* `schedule` — aimed at 19:55 Asia/Shanghai daily. The cron says `27 6 * * *` UTC, 5h28m ahead of the target, because GitHub's dispatcher has been measured firing 4h43m to 5h57m late (four runs between 2026-10-29 and 2026-11-01, none of them queued on the runner side). Real starts therefore land between 19:10 and 20:24 Asia/Shanghai, centred near 19:47
* `workflow_dispatch` — inputs `build_tools_version` (empty selects the newest stable),
  `force_rebuild`, and `gc_report` (prints the sections the linker discards, for
  auditing where the size goes; it does not change the artifacts)
* job dependency chain — `resolve` → `detect` → `build` → `verify` → `release`

`detect` acts as the build gate. It reduces every input that can affect the output
to a single fingerprint — the resolved build-tools version, the AOSP tag it maps
to, the resolved `clang` and `cmake` versions, and this repository's own build
inputs — then compares it against the fingerprint recorded in the published
`BUILD-INFO.txt`. On a match the build is skipped and the release is left
untouched, so the publish timestamp always reflects the last actual build.

Binaries reach the release only after both checks pass on a native AArch64 runner:

* `readelf` static-linkage verification — ELF class/machine, absence of `PT_INTERP`,
  absence of `DT_NEEDED`, static section layout
* functional smoke tests — `aapt2 version/compile/link/dump` and `zipalign`
  alignment checks including 16 KiB page alignment

A second workflow, `.github/workflows/cleanup-runs.yml`, keeps the Actions history
from growing without bound. Every third day (cron `34 20 */3 * *` UTC) it deletes
finished runs older than three days and Actions caches that have not been restored
for three days; runs that are queued or still executing are never touched, and
releases and tags are left alone. `workflow_dispatch` accepts `keep_days` (default 3) and `dry_run`,
which lists what would go without deleting anything. Retention is measured the way
each API measures it — runs by creation time, caches by last access — so a cache
written long ago but still being restored is kept.

## Nothing is pinned

Every version is resolved at run time rather than hard-coded in the workflow:

| what | where it comes from |
| --- | --- |
| build-tools version | official SDK metadata, newest stable release only |
| AOSP tag | probed: the release tag whose `Build.java` declares the matching API level |
| `clang` | apt.llvm.org, newest stable major (signing key pinned by fingerprint) |
| `cmake` | Kitware release feed, digest verified against the published SHA-256 |

A new build-tools series, a new clang patch release or a new cmake minor version
therefore requires no workflow change.

## Building from source

The build runs on GitHub Actions only. This repository provides no local build
path; artifacts are obtained from the releases.

The pipeline clones AOSP sources and applies patches via `get_source.py`, then
drives `build.py` to configure and compile, with `scripts/verify_elf.py` and
`scripts/smoke_test.py` performing verification. Version discovery is split across
three scripts: `scripts/resolve_build_tools.py` (official SDK metadata and the AOSP
tag), `scripts/resolve_clang_version.py` (the apt.llvm.org index) and
`scripts/resolve_cmake_version.py` (Kitware's release feed). All three send a
`GH_TOKEN`, because anonymous GitHub API quota is counted per egress IP and runners
share theirs with everyone else on the same address. The source cache is validated
against a SHA-256 manifest (`scripts/cache_manifest.py`), and the gate fingerprint
is computed by `scripts/build_fingerprint.py`.
Two preflight checks run before the build: `scripts/check_aapt2_sources.py`
confirms the hand-written source lists in `build-tools/*.cmake` still cover this
AOSP tag's `Android.bp`, and `scripts/gen_content_res_flags.py` derives the aconfig
flag header `libandroidfw` needs for that tag.

Jobs pass the binaries to each other through a single artifact
(`build-tools-<version>-aarch64-linux-static.zip`). `actions/upload-artifact` does
not preserve file permission bits, so downstream jobs run `chmod 0755` after
unpacking. The release publishes the files individually and never ships this
archive.

## Layout

| path | purpose |
| --- | --- |
| `.github/workflows/` | build pipeline and run cleanup |
| `build-tools/` | CMake modules for `aapt2` and `zipalign` |
| `lib/` | AOSP library modules both tools depend on |
| `cmake/` | host shims and platform helpers |
| `patches/` | AOSP source patches and generated headers |
| `scripts/` | version resolution, build verification, cache management |
| `build.py` | configures and compiles; the CI build entry point |
| `get_source.py` | clones AOSP sources and applies patches |
| `CMakeLists.txt` | top-level build entry |
| `repository.json` | AOSP repository list |

## License

Apache License 2.0, see [LICENSE.txt](LICENSE.txt).
