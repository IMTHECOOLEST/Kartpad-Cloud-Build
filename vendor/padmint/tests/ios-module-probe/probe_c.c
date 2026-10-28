/* C, optimized: Apple's string.h and stdio.h then use their fortified forms
   (secure/_string.h, secure/_strings.h, secure/_stdio.h), as BlueWake's runtime does. */
#undef NDEBUG  /* Exercise assert's C declaration even in the Release probe. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <pthread.h>
#include <string.h>
#include <sys/mman.h>

static_assert(sizeof(void *) == 8, "iOS modules are 64-bit");

/* Keep typed references to the worker-pool APIs even under optimized builds.
   CI checks the linked imports; it does not execute iOS code on the build host. */
#define REQUIRE_RUNTIME(name) \
    __attribute__((visibility("default"))) __typeof__(&name) const padmint_probe_##name = &name
REQUIRE_RUNTIME(atexit);
REQUIRE_RUNTIME(strtol);
REQUIRE_RUNTIME(pthread_create);
REQUIRE_RUNTIME(pthread_join);
REQUIRE_RUNTIME(pthread_once);
REQUIRE_RUNTIME(pthread_mutex_lock);
REQUIRE_RUNTIME(pthread_mutex_unlock);
REQUIRE_RUNTIME(pthread_cond_broadcast);
REQUIRE_RUNTIME(pthread_cond_signal);
REQUIRE_RUNTIME(pthread_cond_wait);
#undef REQUIRE_RUNTIME

__attribute__((visibility("default"))) int padmint_module_probe_c(char *out, unsigned long size, int value) {
    assert(out != NULL);
    char buffer[32];
    memset(buffer, 0, sizeof buffer);
    snprintf(buffer, sizeof buffer, "padmint %d", value);
    memcpy(out, buffer, size < sizeof buffer ? size : sizeof buffer);
    return (int)strlen(buffer) + (int)ldexp(1.0, value & 3);
}

__attribute__((visibility("default"))) int padmint_module_probe_mapping(void) {
    const size_t size = 4096;
    void *region = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (region == MAP_FAILED) return -1;
    ((char *)region)[0] = 42;
    return munmap(region, size);
}
