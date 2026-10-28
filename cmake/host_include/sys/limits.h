/*
 * Host (glibc) stand-in for bionic's <sys/limits.h>.
 *
 * bionic splits the C limits out into sys/limits.h (PATH_MAX, NAME_MAX, PAGE_SIZE
 * and friends, plus the <linux/limits.h> UAPI values); glibc only ships <limits.h>
 * and has no <sys/limits.h> at all.  android-17's
 * core/libpackagelistparser/packagelistparser.cpp includes <sys/limits.h>
 * unconditionally, so the host build needs this shim: it forwards to the real
 * <limits.h> and fills in the few mnemonics glibc leaves out.
 *
 * Only added to the include path for host builds; the NDK/bionic build keeps the
 * real header.
 */
#ifndef TAIXU_HOST_SYS_LIMITS_H
#define TAIXU_HOST_SYS_LIMITS_H

#include <limits.h>

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

#ifndef NAME_MAX
#define NAME_MAX 255
#endif

#ifndef PAGE_SIZE
#define PAGE_SIZE 4096
#endif

#ifndef PAGE_MASK
#define PAGE_MASK (~(PAGE_SIZE - 1))
#endif

/* bionic's <limits.h> spells out these POSIX-ish aliases; glibc does not. */
#ifndef UID_MAX
#define UID_MAX UINT_MAX
#endif

#ifndef GID_MAX
#define GID_MAX UINT_MAX
#endif

#ifndef SIZE_T_MAX
#define SIZE_T_MAX ULONG_MAX
#endif

#endif /* TAIXU_HOST_SYS_LIMITS_H */
