#
# Copyright © 2022 Github Lzhiyong
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

cmake_minimum_required(VERSION 3.19)

# some third-party projects still declare cmake_minimum_required < 3.5,
# which is rejected by CMake 4.x unless a policy minimum is provided
if(NOT DEFINED CMAKE_POLICY_VERSION_MINIMUM)
    set(CMAKE_POLICY_VERSION_MINIMUM 3.5 CACHE STRING "")
endif()

project(sdk-tools C CXX ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 20)
# A host build compiles the android-17 sources directly, and those already need
# C++23 (libbase/file.cpp calls std::string::resize_and_overwrite), which is what
# AOSP itself compiles them with.  Keep the NDK toolchains on C++20 so older
# libc++ releases keep working.
set(CMAKE_CXX_STANDARD 23)
# AOSP compiles these sources through Soong, whose default clang flags turn this
# diagnostic off.  android-17's androidfw/LocaleDataLookup.h declares constexpr
# helpers that reinterpret_cast a pointer, so they can never be constant evaluated;
# clang reports that as an error unless the warning is disabled, exactly as Soong does.
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    add_compile_options(-Wno-invalid-constexpr)

    # abseil's ABSL_ATTRIBUTE_LIFETIME_CAPTURE_BY expands to lifetime_capture_by(this),
    # which clang 23 renamed to lifetime_capture_by_this. The old spelling still works
    # but is deprecated, and abseil headers get pulled into nearly every translation
    # unit, so a single build emits over six thousand identical warnings that bury
    # anything worth reading. Nothing in this tree uses the attribute directly.
    add_compile_options(-Wno-deprecated-attributes)
endif()
# android-17 sources include bionic-only headers unconditionally (for example
# <sys/system_properties.h>).  glibc has no such header, so a stub header
# directory is put first on the include path.
include_directories(BEFORE ${PROJECT_SOURCE_DIR}/cmake/host_include)

# Note: androidfw/Asset.h uses std::variant without including <variant>, which
# only compiles because older libstdc++ reaches that header transitively.  It is
# fixed in get_source.py by inserting the include into Asset.h itself rather than
# with a global -include here: <variant> pulls in <cstring>, whose features.h
# would then lock __USE_GNU before libbase/posix_strerror_r.cpp can undefine
# _GNU_SOURCE, turning strerror_r into the GNU char* variant and breaking it.

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

list(PREPEND CMAKE_MODULE_PATH ${PROJECT_SOURCE_DIR}/cmake/modules)

# platform tools version, read from patches/misc/platform_tools_version.h
file(STRINGS ${PROJECT_SOURCE_DIR}/patches/misc/platform_tools_version.h _tools_version_line
    REGEX "^#define[ \t]+PLATFORM_TOOLS_VERSION[ \t]+\"[^\"]+\"")
string(REGEX REPLACE "^.*\"([^\"]+)\".*$" "\\1" TOOLS_VERSION "${_tools_version_line}")
if(NOT TOOLS_VERSION)
    message(FATAL_ERROR "can't read PLATFORM_TOOLS_VERSION from patches/misc/platform_tools_version.h")
endif()

set(SRC ${PROJECT_SOURCE_DIR}/src)

# 64-bit off_t for lseek.
add_definitions(-D_FILE_OFFSET_BITS=64)

# bundled zlib must exist before any find_package(ZLIB) of the third-party projects
include(cmake/zlib.cmake)
# toolchain independent settings (static link, system libraries ...)
include(cmake/platform.cmake)

message(STATUS "sdk-tools version: ${TOOLS_VERSION}")
message(STATUS "target: ${SDK_TARGET_OS}-${CMAKE_SYSTEM_PROCESSOR}, compiler: ${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}")
message(STATUS "static link: ${SDK_STATIC}, strip: ${SDK_STRIP}")

# options of the third-party projects
set(protobuf_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(protobuf_BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
# protobuf 30+ ships its own absl/utf8_range copies and exports them in
# "protobuf-targets"; that export set is only valid for a standalone install and
# makes the generate step fail for an embedded build.  We link them statically
# and never install anything, so turn both installers off.
set(protobuf_INSTALL OFF CACHE BOOL "" FORCE)
set(utf8_range_ENABLE_INSTALL OFF CACHE BOOL "" FORCE)
set(ABSL_PROPAGATE_CXX_STD ON CACHE BOOL "" FORCE)
set(PNG_TESTS OFF CACHE BOOL "" FORCE)

set(PROTOC_COMPILER)

if(DEFINED PROTOC_PATH)
    set(PROTOC_COMPILER ${PROTOC_PATH})
    
    if(NOT EXISTS ${PROTOC_COMPILER})
        unset(PROTOC_PATH CACHE)
        message(FATAL_ERROR "Invalid protoc: ${PROTOC_COMPILER}, please check if the path is correct")
    endif()
    
    # check protoc version
    execute_process(
        COMMAND ${PROTOC_COMPILER} --version
        OUTPUT_VARIABLE PTOTOBUF_VERSION
        WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    )
    
    if(NOT PTOTOBUF_VERSION MATCHES "^libprotoc*")
        unset(PROTOC_PATH CACHE)
        message(FATAL_ERROR "Invalid protoc: ${PROTOC_COMPILER}, this may not be an executable")
    endif()
    
    string(REGEX MATCH "([0-9]+)\\.([0-9]+)\\.([0-9]+)" VERSIONS ${PTOTOBUF_VERSION})
    # please note that if the protobuf version is too high, you may encounter the following error
    # error: This file was generated by a newer version of protoc 
    if(VERSIONS VERSION_GREATER "3.21.12")
        message(WARNING "${PTOTOBUF_VERSION}, the protobuf version is too high, may be incompatible!!")
    endif()
endif()

# thrid-party libraries
# boringssl is deliberately not added as a subdirectory: libincfs only needs its
# headers on the include path, nothing links the crypto/ssl targets. Building it
# would cost a few dozen compile steps for objects that never reach the link line.
add_subdirectory(src/fmtlib EXCLUDE_FROM_ALL)
add_subdirectory(src/pcre EXCLUDE_FROM_ALL)
add_subdirectory(src/libpng EXCLUDE_FROM_ALL)
add_subdirectory(src/zopfli EXCLUDE_FROM_ALL)
add_subdirectory(src/expat/expat EXCLUDE_FROM_ALL)

# AOSP's expat fork ships expat/lib/expat_config.h, and for the quoted include in
# xmlparse.c that file wins over the header cmake generates.  It gates every entropy
# source on __BIONIC__/__GLIBC__, but xmlparse.c includes it before any libc header,
# so on a glibc host __GLIBC__ is not defined yet and the build aborts with
# "You do not have support for any sources of high quality entropy".
# getrandom() is not declared for every -std= level, so point expat at the kernel
# CSPRNG through /dev/urandom instead, which needs nothing but stdio.
if(TARGET expat)
    target_compile_definitions(expat PRIVATE XML_DEV_URANDOM=1)
endif()
add_subdirectory(src/abseil-cpp EXCLUDE_FROM_ALL)
add_subdirectory(src/protobuf EXCLUDE_FROM_ALL)

# Only these two host tools are built, which also keeps the configure step away
# from the components whose sources moved in newer AOSP releases.
set(SDK_TOOLS "aapt2;zipalign" CACHE STRING "build-tools targets to configure")

# building sdk-tools
add_subdirectory(lib)
add_subdirectory(build-tools)

# The AOSP derived sources assume Soong's global include set and some of them call
# memcpy/strlen without including <string.h>; force the shim header on our own
# targets only.  libbase is left out on purpose: posix_strerror_r.cpp needs to
# include <string.h> itself, after undefining _GNU_SOURCE (see cmake/host_compat.h).
foreach(tgt libandroidfw libutils libcutils libziparchive libbuildversion
            libpackagelistparser libincfs liblog
            libaapt2 aapt2 zipalign)
    if(TARGET ${tgt})
        target_compile_options(${tgt} PRIVATE
            "$<$<COMPILE_LANGUAGE:C,CXX>:-include${PROJECT_SOURCE_DIR}/cmake/host_compat.h>")
    endif()
endforeach()


