# android-sdk-tools — static `aapt2` + `zipalign` for linux/arm64

[简体中文](README.md) | **English**

Builds exactly two host tools from AOSP sources as **fully static AArch64 Linux
executables**:

* no `PT_INTERP` → runs on any AArch64 kernel, no dynamic loader required
* no `DT_NEEDED` → links against nothing at runtime, glibc included

## Release assets

| file | content |
| --- | --- |
| `aapt2` | static AArch64 executable |
| `zipalign` | static AArch64 executable |
| `SHA256SUMS` | `sha256sum -c SHA256SUMS` |
| `BUILD-INFO.txt` | build-tools version, AOSP tag, API level, toolchain versions, input fingerprint |

The workflow run additionally exposes one artifact,
`build-tools-<version>-aarch64-linux-static.zip`, holding exactly those four
files. It is used only to hand them to the next jobs: `actions/upload-artifact`
does not preserve file modes, so `verify` and `release` run `chmod 0755` after
unpacking. The release publishes the files loose and never ships this archive.

## Continuous integration

`.github/workflows/build-tools-static.yml`

* `schedule` — daily 06:00 Asia/Shanghai (`0 22 * * *` UTC)
* `workflow_dispatch` — inputs `build_tools_version` (empty = newest stable) and
  `force_rebuild`
* jobs — `resolve` → `detect` → `build` → `verify` → `release`

`detect` is a gate. It hashes every input that can change the output — the
resolved build-tools version, the AOSP tag it maps to, the resolved `clang` and
`cmake` versions, and this repository's own build inputs — then compares that
fingerprint with the one recorded in the published `BUILD-INFO.txt`. When they
match the build is skipped entirely and the release is left untouched, so its
publish date always means the last real build.

A build only reaches the release when both of these pass on a native AArch64
runner:

* `readelf` verification — ELF class/machine, absence of `PT_INTERP`, absence of
  `DT_NEEDED`, static section layout
* functional smoke tests — `aapt2 version/compile/link/dump` and
  `zipalign` alignment checks including 16 KiB page alignment

## Nothing is pinned

Every version is resolved at run time instead of being written into the workflow:

| what | where it comes from |
| --- | --- |
| build-tools version | official SDK metadata, newest stable release only |
| AOSP tag | probed: the release tag whose `Build.java` declares the matching API level |
| `clang` | apt.llvm.org, newest stable major (signing key pinned by fingerprint) |
| `cmake` | Kitware release feed, digest verified against the published SHA-256 |

A new build-tools series, a new clang patch release or a new cmake minor version
therefore needs no workflow edit.

## How it is built

The build runs on GitHub Actions only: the workflow fetches the sources with
`get_source.py`, then drives `build.py` to configure and compile the two tools,
with `scripts/verify_elf.py` and `scripts/smoke_test.py` checking the result.
There is no local build path, and none is needed - artifacts come from the
releases.

`scripts/cache_manifest.py` maintains the SHA-256 manifest used to validate the
restored source cache, and `scripts/build_fingerprint.py` computes the gate
fingerprint.

## Layout

| path | purpose |
| --- | --- |
| `build-tools/` | CMake modules for `aapt2` and `zipalign` |
| `lib/` | AOSP library modules both tools depend on |
| `cmake/` | host shims (`host_include/`, `host_compat.h`), platform helpers |
| `patches/` | patches and generated headers applied to the AOSP sources |
| `scripts/` | version resolvers, verifier, smoke tests, cache manifest |
| `repository.json` | AOSP repositories cloned by `get_source.py` |

## License

Apache License 2.0, see [LICENSE.txt](LICENSE.txt).
