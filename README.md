# android-sdk-tools — linux/arm64 静态版 `aapt2` + `zipalign`

**简体中文** | [English](README_en.md)

从 AOSP 源码构建，且只构建两个主机工具，产物是**完全静态链接的 AArch64 Linux 可执行文件**：

* 无 `PT_INTERP` —— 任何 AArch64 内核都能直接运行，不需要动态加载器
* 无 `DT_NEEDED` —— 运行时不依赖任何系统库，glibc 也一并静态链接进去

## Release 资产

| 文件 | 内容 |
| --- | --- |
| `aapt2` | 静态 AArch64 可执行文件 |
| `zipalign` | 静态 AArch64 可执行文件 |
| `SHA256SUMS` | `sha256sum -c SHA256SUMS` 校验 |
| `BUILD-INFO.txt` | build-tools 版本、AOSP tag、API level、工具链版本、输入指纹 |

流水线运行还会产出一个 artifact：`build-tools-<版本>-aarch64-linux-static.zip`，
里面正好是上面这 4 个文件，仅用于在 job 之间传递。`actions/upload-artifact`
不保留文件的 mode 位，因此 `verify` 与 `release` 解包后都会先 `chmod 0755` 再运行。
Release 侧发布的是散装文件，不包含这个 zip。

## 持续集成

工作流文件：`.github/workflows/build-tools-static.yml`

* `schedule` —— 每天北京时间 06:00（cron `0 22 * * *` UTC）
* `workflow_dispatch` —— 参数 `build_tools_version`（留空表示最新稳定版）、`force_rebuild`
* 任务链 —— `resolve` → `detect` → `build` → `verify` → `release`

`detect` 是构建闸门。它把所有能改变产物的输入折算成一个指纹——解析出的 build-tools
版本、其对应的 AOSP tag、解析出的 `clang` 与 `cmake` 版本，以及本仓库自己的构建输入——
再与已发布的 `BUILD-INFO.txt` 中记录的指纹比对。两者一致时整个构建被跳过，
Release 完全不动，因此它的发布时间始终代表"最后一次真实构建"。

只有在原生 AArch64 runner 上通过下面两项，产物才会进入 Release：

* `readelf` 静态性校验 —— ELF class/machine、无 `PT_INTERP`、无 `DT_NEEDED`、静态段布局
* 功能冒烟测试 —— `aapt2` 的 version/compile/link/dump，以及 `zipalign` 的对齐检查（含 16 KiB 页对齐）

## 版本全部动态解析

所有版本都在运行时解析，不写进工作流：

| 项目 | 来源 |
| --- | --- |
| build-tools 版本 | 官方 SDK 元数据，只取最新稳定版 |
| AOSP tag | 探测得出：取其 `Build.java` 声明的 API level 与 build-tools 主版本一致的那个正式 tag |
| `clang` | apt.llvm.org，最新稳定主版本（签名公钥按指纹固定） |
| `cmake` | Kitware 发布源，按官方公布的 SHA-256 校验摘要 |

因此 Android 发布新的 build-tools 系列、clang 发布补丁版或 cmake 升小版本时，
工作流一行都不用改。

## 构建方式

构建只在 GitHub Actions 上完成：工作流用 `get_source.py` 拉取源码，再用 `build.py`
配置并编译这两个工具，`scripts/verify_elf.py` 与 `scripts/smoke_test.py` 负责验收。
本仓库不提供本地构建路径，产物一律从 Release 获取。

`scripts/cache_manifest.py` 生成用于校验源码缓存完整性的 SHA-256 清单，
`scripts/build_fingerprint.py` 计算闸门指纹。

## 目录结构

| 路径 | 用途 |
| --- | --- |
| `build-tools/` | `aapt2` 与 `zipalign` 的 CMake 模块 |
| `lib/` | 这两个工具依赖的 AOSP 库模块 |
| `cmake/` | 宿主侧兼容层（`host_include/`、`host_compat.h`）与平台辅助脚本 |
| `patches/` | 应用到 AOSP 源码的补丁与生成头文件 |
| `scripts/` | 版本解析、ELF 校验、冒烟测试、缓存清单 |
| `repository.json` | `get_source.py` 克隆的 AOSP 仓库清单 |

## 许可证

Apache License 2.0，见 [LICENSE.txt](LICENSE.txt)。
