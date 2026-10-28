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
# Toolchain independent platform settings.
#
#   SDK_STATIC  link fully static executables (default ON)
#   SDK_STRIP   strip symbols of the executables at link time (default ON)
#
# Every executable links `sdk_system_libs`, which carries the system / runtime
# libraries (zlib, threads, dl, m) for the current toolchain, instead of
# hard coding NDK specific names such as `c++_static`.

option(SDK_STATIC "Link fully static executables" ON)
option(SDK_STRIP "Strip the executables at link time" ON)

string(TOLOWER "${CMAKE_SYSTEM_NAME}" SDK_TARGET_OS)

# global flags for C/C++ (not for assembler sources)
add_compile_options(
    "$<$<COMPILE_LANGUAGE:C,CXX>:-fPIC>"
    "$<$<COMPILE_LANGUAGE:C,CXX>:-Wno-attributes>"
    # -pipe keeps the preprocessed text in memory instead of on disk; it changes
    # how the compiler works, never what it emits
    "$<$<COMPILE_LANGUAGE:C,CXX>:-pipe>"
    # One section per function and per variable. Without this the linker sees a
    # single monolithic .text and cannot drop anything, so every object file that
    # protobuf and abseil hand it lands in the binary whether aapt2 calls it or
    # not. With sections, --gc-sections can start from the entry point and discard
    # the rest. Costs a few percent of compile time, removes no reachable code.
    "$<$<COMPILE_LANGUAGE:C,CXX>:-ffunction-sections>"
    "$<$<COMPILE_LANGUAGE:C,CXX>:-fdata-sections>"
    )

# Reachability-based dead code removal at link time. Constructors and anything
# reachable from the entry point are kept, so static libc initialisers survive.
add_link_options(-Wl,--gc-sections)

# Audit hook: with SDK_GC_REPORT set to a non-empty value, the linker prints every
# section it discards, one line per function or variable. Used to verify that what
# disappears was never reachable. Off by default so ordinary build logs stay readable.
if(DEFINED ENV{SDK_GC_REPORT} AND NOT "$ENV{SDK_GC_REPORT}" STREQUAL "")
    message(STATUS "gc-sections audit enabled: linker will report discarded sections")
    add_link_options(-Wl,--print-gc-sections)
endif()

if(SDK_STATIC)
    # prefer static archives for every find_library()
    set(CMAKE_FIND_LIBRARY_SUFFIXES .a)
    string(APPEND CMAKE_EXE_LINKER_FLAGS " -static")
endif()

if(SDK_STRIP)
    string(APPEND CMAKE_EXE_LINKER_FLAGS_RELEASE " -s")
    string(APPEND CMAKE_EXE_LINKER_FLAGS_MINSIZEREL " -s")
endif()

# libpng (and others) call find_library(M_LIBRARY m); libm ships with libc here
set(M_LIBRARY m CACHE STRING "math library")

set(THREADS_PREFER_PTHREAD_FLAG ON)
find_package(Threads REQUIRED)

add_library(sdk_system_libs INTERFACE)
target_link_libraries(sdk_system_libs INTERFACE
    sdk_zlib
    Threads::Threads
    ${CMAKE_DL_LIBS}
    m
    )
