/*
 * Host (glibc) stand-in for bionic's <sys/system_properties.h>.
 *
 * android-17 sources include this header unconditionally, but the property API is a
 * bionic feature: glibc has no such header and no such runtime.  The files we compile
 * for the host tools only ever read properties that describe the *device* (selinux
 * policy state, build fingerprints), which do not exist while cross-building aapt2 or
 * zipalign on a workstation.
 *
 * So every entry point is provided as a stub: reads report "no such property" and
 * writes report failure, which is exactly the behaviour those callers already handle
 * for an unset property.  Keeping the declarations (rather than dropping the callers)
 * means the AOSP sources stay untouched and the *Android* build still uses bionic's
 * real header -- this directory is only added to the include path for host builds.
 */
#ifndef TAIXU_HOST_SYS_SYSTEM_PROPERTIES_H
#define TAIXU_HOST_SYS_SYSTEM_PROPERTIES_H

#include <stdint.h>
#include <time.h>

#define PROP_NAME_MAX 32
#define PROP_VALUE_MAX 92

#ifdef __cplusplus
extern "C" {
#endif

typedef struct prop_info prop_info;

/*
 * Do NOT declare __system_property_set/get/find/read_callback here.
 *
 * android-base/properties.h declares them itself (inside #if !defined(__BIONIC__))
 * and libbase/properties.cpp provides the weak host implementations, so a second
 * declaration from this header collides:
 *   error: functions that differ only in their return type cannot be overloaded
 *   (this header said "int __system_property_read_callback", AOSP says "void ...")
 * and the calls in libcutils/properties.cpp become ambiguous.  The symbols are
 * therefore left entirely to libbase, and this header only supplies what bionic's
 * <sys/system_properties.h> adds on top: foreach/serial/wait/add/update/area_init
 * and the __system_properties_init() entry point that libselinux's
 * android_device.c calls.
 */

/* bionic's serial counter; nothing ever mutates properties on the host. */
static inline unsigned int __system_property_serial(const prop_info* pi) {
    (void)pi;
    return 0;
}

static inline int __system_property_read(const prop_info* pi, char* name, char* value) {
    (void)pi;
    if (name != 0) {
        name[0] = '\0';
    }
    if (value != 0) {
        value[0] = '\0';
    }
    return 0;
}

typedef void (*prop_foreach_callback)(const prop_info* pi, void* cookie);

/* bionic returns the number of properties actually visited; libcutils returns it. */
static inline int __system_property_foreach(prop_foreach_callback callback, void* cookie) {
    (void)callback;
    (void)cookie;
    return 0;
}

/* libselinux/src/android/android_device.c */
static inline int __system_properties_init(void) {
    return 0;
}

static inline int __system_property_wait(const prop_info* pi, uint32_t old_serial,
                                         uint32_t* new_serial_ptr,
                                         const struct timespec* relative_timeout) {
    (void)pi;
    (void)relative_timeout;
    if (new_serial_ptr != 0) {
        *new_serial_ptr = old_serial;
    }
    return 0;
}

static inline int __system_property_wait_any(uint32_t old_serial) {
    (void)old_serial;
    return 0;
}

static inline const prop_info* __system_property_find_nth(unsigned int n) {
    (void)n;
    return 0;
}

static inline int __system_property_add(const char* name, unsigned int name_length,
                                        const char* value, unsigned int value_length) {
    (void)name;
    (void)name_length;
    (void)value;
    (void)value_length;
    return -1;
}

static inline int __system_property_update(prop_info* pi, const char* value,
                                           unsigned int value_length) {
    (void)pi;
    (void)value;
    (void)value_length;
    return -1;
}

static inline int __system_property_area_init(void) {
    return -1;
}

#ifdef __cplusplus
}
#endif

#endif /* TAIXU_HOST_SYS_SYSTEM_PROPERTIES_H */
