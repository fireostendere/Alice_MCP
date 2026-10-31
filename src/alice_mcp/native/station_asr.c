/* Operator-installed, bounded on-station Vosk decoder. PCM16/16 kHz on stdin,
 * untrusted JSON results on stdout. No microphone, shell, network or actions here.
 * Compressed model files fit the Mini 1 data volume; one temporary memfd at a time
 * provides the seekable descriptor libstdc++ needs. No decompressed file hits flash.
 * The owner-side launcher verifies the binary/library/model pins before execution.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <malloc.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#include <zlib.h>

static const char *model_root;
static volatile sig_atomic_t interrupt_requested;
static void recognition_mode(int signal_number) {
    interrupt_requested = signal_number == SIGUSR1;
}
static const char stop_grammar[] =
    "[\"стоп\",\"хватит\",\"замолчи\",\"прекрати\",\"остановись\","
    "\"хватит говорить\",\"хватит разговаривать\",\"закончи разговор\",\"стоп разговор\",\"[unk]\"]";
static long long milliseconds(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) _exit(2);
    return (long long)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int inflate_file(const char *path) {
    gzFile compressed = gzopen(path, "rb");
    if (!compressed) return -1;
    if (gzdirect(compressed)) { gzclose(compressed); errno = EIO; return -1; }
    /* Mini 1 has the syscall (Linux 4.9), but its glibc 2.23 has no wrapper. */
    int fd = (int)syscall(SYS_memfd_create, "alice-model", MFD_CLOEXEC);
    if (fd < 0) { gzclose(compressed); return -1; }
    char buffer[65536];
    size_t total = 0;
    int count;
    while ((count = gzread(compressed, buffer, sizeof buffer)) > 0) {
        total += (size_t)count;
        if (total > 34u * 1024u * 1024u) { count = -1; break; }
        size_t done = 0;
        while (done < (size_t)count) {
            ssize_t n = write(fd, buffer + done, (size_t)count - done);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) break;
            done += (size_t)n;
        }
        if (done != (size_t)count) { count = -1; break; }
    }
    int closed = gzclose(compressed);
    if (count < 0 || closed != Z_OK || lseek(fd, 0, SEEK_SET) < 0) {
        close(fd); errno = EIO; return -1;
    }
    return fd;
}

/* Exported only inside this executable; never install a system-wide preload. */
FILE *fopen64(const char *path, const char *mode) {
    FILE *(*original)(const char *, const char *) = dlsym(RTLD_NEXT, "fopen64");
    if (!original) return NULL;
    if (!model_root || strncmp(path, model_root, strlen(model_root)) ||
        path[strlen(model_root)] != '/' || (strcmp(mode, "r") && strcmp(mode, "rb")))
        return original(path, mode);
    int fd = inflate_file(path);
    if (fd < 0) return NULL;
    FILE *file = fdopen(fd, mode);
    if (!file) close(fd);
    return file;
}

FILE *fopen(const char *path, const char *mode) { return fopen64(path, mode); }

static bool emit(const char *event, const char *result, size_t bytes, long long elapsed) {
    if (!result || strnlen(result, 8193) > 8192) return false;
    /* Vosk produces JSON; the host still validates shape/UTF-8/bounds before use. */
    if (printf("{\"event\":\"%s\",\"result\":", event) < 0) return false;
    /* Vosk pretty-prints results; keep each transport event on exactly one line. */
    for (const char *p = result; *p; p++)
        if (putchar(*p == '\n' || *p == '\r' ? ' ' : (unsigned char)*p) == EOF) return false;
    return printf(",\"audio_ms\":%zu,\"elapsed_ms\":%lld,\"trusted\":false}\n",
                  bytes / 32, elapsed) >= 0 && !fflush(stdout);
}

int main(int argc, char **argv) {
    if (argc != 5 || argv[1][0] != '/' || argv[2][0] != '/') return 2;
    char *end;
    long seconds = strtol(argv[3], &end, 10);
    if (*end || seconds < 1 || seconds > 3600 || strcmp(argv[4], "pcm16")) return 2;
    struct sigaction mode_action = {0};
    mode_action.sa_handler = recognition_mode;
    mode_action.sa_flags = SA_RESTART;
    if (sigemptyset(&mode_action.sa_mask) || sigaction(SIGUSR1, &mode_action, NULL) ||
        sigaction(SIGUSR2, &mode_action, NULL)) return 2;
    struct stat input;
    if (fstat(STDIN_FILENO, &input)) return 2;
    /* Keep short decode stalls away from the microphone writer's 1 s deadline. */
    if (S_ISFIFO(input.st_mode) &&
        fcntl(STDIN_FILENO, F_SETPIPE_SZ, 1024 * 1024) < 1024 * 1024) return 2;
    struct rlimit core = {0, 0};
    if (setrlimit(RLIMIT_CORE, &core)) return 2;
    FILE *score = fopen("/proc/self/oom_score_adj", "w");
    if (!score) return 2;
    if (fputs("1000\n", score) < 0 || fclose(score)) return 2;
    alarm((unsigned)seconds + 60u);
    const char *library_path = argv[1];
    int library_fd = -1;
    char fd_path[64];
    size_t path_length = strlen(library_path);
    if (path_length > 3 && !strcmp(library_path + path_length - 3, ".gz")) {
        library_fd = inflate_file(library_path);
        if (library_fd < 0) return 3;
        snprintf(fd_path, sizeof fd_path, "/proc/self/fd/%d", library_fd);
        library_path = fd_path;
    }
    void *library = dlopen(library_path, RTLD_NOW | RTLD_LOCAL);
    if (library_fd >= 0) close(library_fd);
    if (!library) return 3;
    void (*quiet)(int) = dlsym(library, "vosk_set_log_level");
    void *(*load)(const char *) = dlsym(library, "vosk_model_new");
    void (*free_model)(void *) = dlsym(library, "vosk_model_free");
    void *(*recognizer)(void *, float) = dlsym(library, "vosk_recognizer_new");
    void *(*grammar_recognizer)(void *, float, const char *) =
        dlsym(library, "vosk_recognizer_new_grm");
    void (*free_recognizer)(void *) = dlsym(library, "vosk_recognizer_free");
    int (*accept)(void *, const char *, int) = dlsym(library, "vosk_recognizer_accept_waveform");
    const char *(*result)(void *) = dlsym(library, "vosk_recognizer_result");
    const char *(*partial)(void *) = dlsym(library, "vosk_recognizer_partial_result");
    const char *(*final)(void *) = dlsym(library, "vosk_recognizer_final_result");
    if (!quiet || !load || !free_model || !recognizer || !grammar_recognizer ||
        !free_recognizer || !accept ||
        !result || !partial || !final) return 3;
    quiet(-1);
    model_root = argv[2];
    long long started = milliseconds();
    void *model = load(model_root);
    if (!model) return 4;
    void *rec = recognizer(model, 16000.0f);
    if (!rec) { free_model(model); return 4; }
    printf("{\"event\":\"ready\",\"rate\":16000,\"load_ms\":%lld}\n",
           milliseconds() - started);
    if (fflush(stdout)) return 5;
    size_t bytes = 0, decoder_bytes = 0, limit = (size_t)seconds * 32000u;
    char pcm[3200], previous[8193] = "";
    int status = 0;
    bool interrupt_only = false;
    started = milliseconds();
    while (bytes < limit) {
        size_t n = fread(pcm, 1, sizeof pcm, stdin);
        if (!n) { if (ferror(stdin)) status = 5; break; }
        if (n % 2) { status = 5; break; }
        if (interrupt_only != (interrupt_requested != 0)) {
            interrupt_only = interrupt_requested != 0;
            free_recognizer(rec);
            malloc_trim(0);
            rec = interrupt_only ? grammar_recognizer(model, 16000.0f, stop_grammar) :
                                   recognizer(model, 16000.0f);
            if (!rec) { status = 4; break; }
            decoder_bytes = 0;
            previous[0] = '\0';
        }
        bytes += n;
        decoder_bytes += n;
        int endpoint = accept(rec, pcm, (int)n);
        bool rotate = decoder_bytes >= (interrupt_only ? 3u : 30u) * 32000u ||
                      (endpoint > 0 && decoder_bytes >= (interrupt_only ? 1u : 15u) * 32000u);
        const char *text = endpoint ? result(rec) : rotate ? final(rec) : partial(rec);
        if (endpoint < 0 || !text || strnlen(text, sizeof previous) >= sizeof previous) {
            status = 5; break;
        }
        const char *event = endpoint || rotate ?
            (interrupt_only ? "interrupt_final" : "final") :
            (interrupt_only ? "interrupt_partial" : "partial");
        if ((endpoint || rotate || strcmp(text, previous)) &&
            !emit(event, text, bytes, milliseconds() - started)) {
            status = 5; break;
        }
        strcpy(previous, endpoint || rotate ? "" : text);
        if (rotate) {
            /* Bound frontend/graph caches: continuous decoding otherwise OOMs Mini 1. */
            free_recognizer(rec);
            malloc_trim(0);
            rec = interrupt_only ? grammar_recognizer(model, 16000.0f, stop_grammar) :
                                   recognizer(model, 16000.0f);
            if (!rec) { status = 4; break; }
            decoder_bytes = 0;
        }
    }
    if (!status && !emit(interrupt_only ? "interrupt_final" : "final", final(rec), bytes,
                         milliseconds() - started)) status = 5;
    struct rusage usage;
    if (!status && getrusage(RUSAGE_SELF, &usage)) status = 5;
    if (!status) {
        printf("{\"event\":\"end\",\"audio_ms\":%zu,\"elapsed_ms\":%lld,"
               "\"rss_kib\":%ld}\n", bytes / 32, milliseconds() - started, usage.ru_maxrss);
        if (fflush(stdout)) status = 5;
    }
    if (rec) free_recognizer(rec);
    free_model(model);
    dlclose(library);
    return status;
}
