/* Mini 1 ABI experiment only: copy statistics, never audio or pointers.
 * The caller's function result, buffers and errno remain authoritative.
 * No filesystem writes, network listener or blocking operation on the audio path.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#ifndef AUDIT_PREFIX
#define AUDIT_PREFIX "/bkupgrade/data/opt/alice-mcp-vqe-audit-"
#endif

struct report {
    uint32_t magic, kind, index, frames, calls, nonfinite, outside, frames_seen;
    float peak, mean_square;
};
_Static_assert(sizeof(struct report) == 40, "Bounded pointer-free metadata ABI");

#ifdef VQE_AUDIT_COLLECTOR
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <sys/stat.h>
#include <time.h>

static volatile sig_atomic_t stopping;
static void stop(int value) { (void)value; stopping = 1; }

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--clock") == 0) {
        struct timespec value;
        if (clock_gettime(CLOCK_MONOTONIC, &value)) return 2;
        printf("{\"monotonic_ms\":%lld}\n",
               (long long)value.tv_sec * 1000 + value.tv_nsec / 1000000);
        return 0;
    }
    if (argc != 2 || strlen(argv[1]) != 32) return 2;
    for (unsigned i = 0; i < 32; ++i)
        if (!((argv[1][i] >= '0' && argv[1][i] <= '9') ||
              (argv[1][i] >= 'a' && argv[1][i] <= 'f'))) return 2;
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    int length = snprintf(address.sun_path, sizeof(address.sun_path),
                          "%s%s/stats.sock", AUDIT_PREFIX, argv[1]);
    if (length < 0 || (size_t)length >= sizeof(address.sun_path)) return 2;
    struct stat existing, owned;
    if (lstat(address.sun_path, &existing) == 0 || errno != ENOENT) return 2;
    int fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) return 2;
    if (bind(fd, (const struct sockaddr *)&address, sizeof(address)) ||
        chmod(address.sun_path, 0600) || lstat(address.sun_path, &owned)) { close(fd); return 2; }
    signal(SIGTERM, stop); signal(SIGINT, stop);
    struct timespec start, now;
    if (clock_gettime(CLOCK_MONOTONIC, &start)) { close(fd); unlink(address.sun_path); return 2; }
    unsigned packets = 0, rejected = 0, types = 0, calls6 = 0, frames6 = 0;
    unsigned nonfinite = 0, outside = 0, peak = 0, power = 0;
    struct sample { unsigned t_ms, calls, peak, power; } samples[64];
    unsigned sample_count = 0;
    printf("{\"outcome\":\"ready\",\"metadata_only\":true}\n"); fflush(stdout);
    while (!stopping && packets + rejected < 4096) {
        if (clock_gettime(CLOCK_MONOTONIC, &now) || now.tv_sec - start.tv_sec >= 45) break;
        struct pollfd wait = {fd, POLLIN, 0};
        if (poll(&wait, 1, 500) <= 0) continue;
        unsigned char bytes[sizeof(struct report) + 1];
        ssize_t n = recv(fd, bytes, sizeof(bytes), 0);
        if (n < 0) continue;
        struct report message;
        if (n != sizeof(message)) { rejected++; continue; }
        memcpy(&message, bytes, sizeof(message));
        if (message.magic != 0x41565145 || message.kind >= 16 || message.index != 0 ||
            message.frames == 0 || message.frames > 4096 || message.calls > 100000 ||
            message.frames_seen > 25000000 || message.nonfinite > 160 || message.outside > 160 ||
            !isfinite(message.peak) || !isfinite(message.mean_square) || message.peak < 0 ||
            message.peak > 1 || message.mean_square < 0 || message.mean_square > 1) {
            rejected++; continue;
        }
        packets++; types |= 1U << message.kind;
        if (message.kind == 6) {
            if (message.calls > calls6) calls6 = message.calls;
            if (message.frames_seen > frames6) frames6 = message.frames_seen;
            nonfinite += message.nonfinite; outside += message.outside;
            unsigned p = (unsigned)(message.peak * 1000000.0f);
            unsigned q = (unsigned)(message.mean_square * 1000000000.0f);
            if (p > peak) peak = p;
            if (q > power) power = q;
            if (sample_count < 64 && clock_gettime(CLOCK_MONOTONIC, &now) == 0) {
                long long elapsed = (long long)(now.tv_sec - start.tv_sec) * 1000 +
                                    (now.tv_nsec - start.tv_nsec) / 1000000;
                if (elapsed >= 0 && elapsed <= 46000) {
                    samples[sample_count++] = (struct sample){(unsigned)elapsed, message.calls, p, q};
                }
            }
        }
    }
    close(fd);
    int clean = lstat(address.sun_path, &existing) == 0 && S_ISSOCK(existing.st_mode) &&
                existing.st_ino == owned.st_ino && existing.st_dev == owned.st_dev &&
                unlink(address.sun_path) == 0;
    printf("{\"outcome\":\"complete\",\"metadata_only\":true,\"packets\":%u,"
           "\"rejected\":%u,\"types_bitmap\":%u,\"type6_calls\":%u,\"type6_frames\":%u,"
           "\"nonfinite\":%u,\"outside\":%u,\"peak_millionths\":%u,"
           "\"mean_square_billionths\":%u,\"cleanup_verified\":%s,"
           "\"started_monotonic_ms\":%lld,\"samples\":[",
           packets, rejected, types, calls6, frames6, nonfinite, outside, peak, power,
           clean ? "true" : "false", (long long)start.tv_sec * 1000 + start.tv_nsec / 1000000);
    for (unsigned i = 0; i < sample_count; ++i) {
        printf("%s{\"t_ms\":%u,\"calls\":%u,\"peak_millionths\":%u,"
               "\"mean_square_billionths\":%u}", i ? "," : "", samples[i].t_ms,
               samples[i].calls, samples[i].peak, samples[i].power);
    }
    printf("]}\n");
    return clean ? 0 : 1;
}
#else
typedef void *(*output_fn)(void *, unsigned, unsigned, const float **, size_t *);
static output_fn original;
static int audit_fd = -1;
static struct sockaddr_un target;
static socklen_t target_size;
static unsigned calls[16], frames_seen[16];

__attribute__((constructor)) static void prepare(void) {
    int saved = errno;
    original = (output_fn)dlsym(RTLD_NEXT, "YandexVqeC_GetOutputChannelData");
    const char *path = getenv("ALICE_VQE_AUDIT_SOCKET");
    const size_t prefix = sizeof(AUDIT_PREFIX) - 1;
    if (!path || strnlen(path, sizeof(target.sun_path)) != prefix + 32 + 11 ||
        strncmp(path, AUDIT_PREFIX, prefix) || strcmp(path + prefix + 32, "/stats.sock"))
        goto done;
    for (size_t i = prefix; i < prefix + 32; ++i)
        if (!((path[i] >= '0' && path[i] <= '9') || (path[i] >= 'a' && path[i] <= 'f')))
            goto done;
    target.sun_family = AF_UNIX;
    memcpy(target.sun_path, path, prefix + 32 + 12);
    target_size = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + strlen(path) + 1);
    audit_fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
done:
    errno = saved;
}

void *YandexVqeC_GetOutputChannelData(void *engine, unsigned kind, unsigned index,
                                    const float **data, size_t *frames) {
    if (!original) _exit(125); /* A pinned original is mandatory; rollback guard handles failure. */
    void *error = original(engine, kind, index, data, frames);
    int saved = errno;
    if (!error && audit_fd >= 0 && kind < 16 && index == 0 && data && frames && *data &&
        *frames > 0 && *frames <= 4096) {
        unsigned count = __atomic_add_fetch(&calls[kind], 1, __ATOMIC_RELAXED);
        unsigned seen = __atomic_add_fetch(&frames_seen[kind], (unsigned)*frames, __ATOMIC_RELAXED);
        if ((count & 63) == 1) {
            struct report out = {0};
            out.magic = 0x41565145; out.kind = kind; out.index = index;
            out.frames = (unsigned)*frames; out.calls = count; out.frames_seen = seen;
            /* Public Plum names type 6/index 0 as vqe_0. Mini 1 remains an experiment:
             * plausibility summaries do not qualify it for audio export or ASR.
             */
            if (kind == 6) {
                size_t n = *frames < 160 ? *frames : 160;
                for (size_t i = 0; i < n; ++i) {
                    float value;
                    memcpy(&value, *data + i, sizeof(value));
                    if (!isfinite(value)) { out.nonfinite++; continue; }
                    if (value > 1.0f || value < -1.0f) { out.outside++; continue; }
                    float magnitude = value < 0 ? -value : value;
                    if (magnitude > out.peak) out.peak = magnitude;
                    out.mean_square += value * value;
                }
                out.mean_square /= (float)n;
            }
            (void)sendto(audit_fd, &out, sizeof(out), MSG_DONTWAIT | MSG_NOSIGNAL,
                         (const struct sockaddr *)&target, target_size);
        }
    }
    errno = saved;
    return error;
}
#endif
