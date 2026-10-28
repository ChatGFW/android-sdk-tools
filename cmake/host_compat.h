/*
 * Copyright © 2022 Github Lzhiyong
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef SDK_TOOLS_HOST_COMPAT_H
#define SDK_TOOLS_HOST_COMPAT_H

/*
 * Forced include for the AOSP derived libraries of a host build, see CMakeLists.txt.
 *
 * AOSP compiles these sources through Soong, where the libc declarations are always
 * in scope, so some files call memcpy/strlen without including <string.h>
 * (libs/androidfw/BigBufferStream.cpp, libs/androidfw/ApkParsing.cpp) and fail with
 *   error: use of undeclared identifier 'memcpy'
 * on a glibc host.  Pull the three headers in for those targets instead of patching
 * every such file.
 *
 * libbase must NOT get this header: posix_strerror_r.cpp undefines _GNU_SOURCE right
 * before its own #include <string.h> to get the POSIX int returning strerror_r, and
 * including <string.h> here first would lock in the GNU char* variant.
 *
 * Only C headers are used so the shim is valid for C sources as well; assembly
 * sources are excluded at the point where it is attached.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * Some aapt2 headers (e.g. AppInfo.h) use uint32_t while only including <optional>,
 * <set> and <string>; under Soong the fixed-width types are always in scope.
 */
#include <stdint.h>

#endif  // SDK_TOOLS_HOST_COMPAT_H
