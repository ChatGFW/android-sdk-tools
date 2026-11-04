# android-sdk-tools

**简体中文** | [English](README_en.md)

![platform](https://img.shields.io/badge/platform-linux%2Farm64-lightgrey)
![linkage](https://img.shields.io/badge/linkage-fully%20static-brightgreen)
![license](https://img.shields.io/badge/license-Apache--2.0-blue)

面向 AArch64 Linux 的 Android 构建工具。从 AOSP 源码编译，仅产出两个 host 工具，
均为**完全静态链接**的可执行文件：

* 无 `PT_INTERP` —— 可在任意 AArch64 内核上直接执行，无需动态加载器
* 无 `DT_NEEDED` —— 运行时不依赖任何系统库，glibc 已静态链入

因此容器、精简镜像与交叉编译环境中无需预先安装匹配版本的 glibc。

## 快速开始

```bash
base=https://github.com/ChatGFW/android-sdk-tools/releases/latest/download
curl -LO "$base/aapt2" -LO "$base/zipalign" -LO "$base/SHA256SUMS"

sha256sum -c SHA256SUMS          # 校验完整性
chmod +x aapt2 zipalign
./aapt2 version
```

## 兼容性

| 项 | 要求 |
| --- | --- |
| 架构 | `linux/arm64`（AArch64） |
| 内核 | 支持执行 AArch64 ELF 即可，无最低版本要求 |
| libc | 无需，已静态链入 |
| 其他依赖 | 无 |

x86_64 主机可经 qemu-user 运行，但未经验证，不在支持范围内。

## Release 资产

| 文件 | 内容 |
| --- | --- |
| `aapt2` | 静态 AArch64 可执行文件 |
| `zipalign` | 静态 AArch64 可执行文件 |
| `SHA256SUMS` | 供 `sha256sum -c` 校验 |
| `BUILD-INFO.txt` | build-tools 版本、AOSP tag、API level、工具链版本、输入指纹 |

## 持续集成

工作流：`.github/workflows/build-tools-static.yml`

* `schedule` —— 每日瞄准北京时间 19:55。cron 写 `27 6 * * *` UTC，比目标提前 5h28m——GitHub 的调度器实测会把定时任务推后 4h43m~5h57m（2026-10-29~11-01 四次样本，排队耗时均为 0），因此实际启动落在北京时间 19:10~20:24，中心约 19:47
* `workflow_dispatch` —— 输入参数 `build_tools_version`（留空表示最新稳定版）、`force_rebuild`、`gc_report`（在链接时打印被回收的段，用于审计体积来源，不改变产物）
* job 依赖链 —— `resolve` → `detect` → `build` → `verify` → `release`

`detect` 为构建门禁（gate）。它将所有可能改变产物的输入归约为单一指纹——解析出的
build-tools 版本、其对应的 AOSP tag、解析出的 `clang` 与 `cmake` 版本，以及本仓库
自身的构建输入——并与已发布 `BUILD-INFO.txt` 中记录的指纹比对。两者一致时跳过构建，
Release 保持不变，因此其发布时间始终对应最后一次实际构建。

产物仅在原生 AArch64 runner 上通过下列两项检查后才会进入 Release：

* `readelf` 静态性校验 —— ELF class/machine、无 `PT_INTERP`、无 `DT_NEEDED`、静态段布局
* 功能冒烟测试 —— `aapt2` 的 version/compile/link/dump，以及 `zipalign` 的对齐检查（含 16 KiB 页对齐）

第二个工作流 `.github/workflows/cleanup-runs.yml` 负责抑制 Actions 记录无限增长。
每三天一次（cron `34 20 */3 * *` UTC），删除三天前已结束的运行记录，以及三天未被恢复过的 Actions 缓存；
排队中或正在运行的记录一律不动，Release 与 tag 也不受影响。
`workflow_dispatch` 接受 `keep_days`（默认 3）与 `dry_run`——后者只列出待删项而不实际删除。
保留期限按各自 API 的计量口径计算：运行记录看创建时间，缓存看最后访问时间，
因此很久以前写入但仍在被恢复的缓存会被保留。

## 版本全部动态解析

所有版本均在运行时解析，不硬编码于工作流：

| 项目 | 来源 |
| --- | --- |
| build-tools 版本 | 官方 SDK 元数据，仅取最新稳定版 |
| AOSP tag | 探测确定：`Build.java` 声明的 API level 与 build-tools 主版本一致的正式 tag |
| `clang` | apt.llvm.org，最新稳定主版本（签名公钥按指纹固定） |
| `cmake` | Kitware 发布源，摘要按官方公布的 SHA-256 校验 |

因此 build-tools 发布新系列、clang 发布补丁版或 cmake 升级小版本时，工作流无需修改。

## 从源码构建

构建仅在 GitHub Actions 上执行，本仓库不提供本地构建路径，产物一律从 Release 获取。

流水线通过 `get_source.py` 克隆 AOSP 源码并应用补丁，通过 `build.py` 配置并编译，
由 `scripts/verify_elf.py` 与 `scripts/smoke_test.py` 完成验收。
版本探测由三个脚本承担：`scripts/resolve_build_tools.py`（官方 SDK 元数据与 AOSP tag）、
`scripts/resolve_clang_version.py`（apt.llvm.org 索引）、`scripts/resolve_cmake_version.py`（Kitware 发布源），
三者都带 `GH_TOKEN` 调用 GitHub API——匿名限额按出口 IP 计算，Actions runner 与他人共享地址，必然被限流。
源码缓存以 SHA-256 清单校验（`scripts/cache_manifest.py`），
门禁指纹由 `scripts/build_fingerprint.py` 计算。
开工前的两项前置检查由 `scripts/check_aapt2_sources.py`（核对
`build-tools/*.cmake` 中手写的源码清单是否仍覆盖该 AOSP tag 的 `Android.bp`）与
`scripts/gen_content_res_flags.py`（按 tag 生成 `libandroidfw` 所需的 aconfig 标志头）承担。

job 之间通过单个 artifact（`build-tools-<版本>-aarch64-linux-static.zip`）传递产物；
`actions/upload-artifact` 不保留文件权限位，下游解包后执行 `chmod 0755`。
Release 发布的是独立文件，不包含该归档。

## 目录结构

| 路径 | 用途 |
| --- | --- |
| `.github/workflows/` | 构建流水线与运行记录清理 |
| `build-tools/` | `aapt2` 与 `zipalign` 的 CMake 模块 |
| `lib/` | 两个工具依赖的 AOSP 库模块 |
| `cmake/` | 宿主侧兼容层与平台辅助 |
| `patches/` | AOSP 源码补丁与生成头文件 |
| `scripts/` | 版本解析、构建校验、缓存管理 |
| `build.py` | 配置并编译，CI 的编译入口 |
| `get_source.py` | 克隆 AOSP 源码并应用补丁 |
| `CMakeLists.txt` | 顶层构建入口 |
| `repository.json` | AOSP 仓库清单 |

## 许可证

Apache License 2.0，见 [LICENSE.txt](LICENSE.txt)。
