/* Bounded playback only; stdin is canonical mono S16 WAV, never a path or URL.
 * ALSA public ABI: alsa-project/alsa-lib v1.2.13 include/pcm.h.
 * Mini 1 candidate: kernel playback 0 (TDM-B), still needs audible qualification.
 * Generic /etc dmixer device 2 is capture-only on this board; do not copy it.
 * Plug converts to the Mini 1 candidate's 48 kHz/S32/stereo geometry.
 * Kernel readback is required before writes; no global config hooks or IPC mixer.
 * No capture, mixer, recovery/replay, firmware change or cloud request.
 */
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#ifndef PCM_ALSA_LIBRARY
#define PCM_ALSA_LIBRARY "/system/vendor/quasar/libs/libasound.so.2"
#endif
#ifndef PCM_HW_PARAMS_PATH
#define PCM_HW_PARAMS_PATH "/proc/asound/card0/pcm0p/sub0/hw_params"
#endif
#define WAV_MAX (44u + 48000u * 2u * 20u)
/* ALSA plug slave constraints and duplicate policy: alsa-lib PCM plugins reference.
 * Candidate geometry is based on reviewed Mini 1 metadata, not a universal profile. */
static const char route[] =
    "pcm.alice_playback { type plug route_policy duplicate slave { "
    "pcm { type hw card 0 device 0 } format S32_LE channels 2 rate 48000 } }";

typedef struct _snd_pcm snd_pcm_t;
static volatile sig_atomic_t interrupted;
static void stop(int sig) {
    if (sig == SIGALRM) _exit(124); /* Also bound a stuck vendor-library call. */
    interrupted = 1;
}
static uint32_t le32(const unsigned char *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static unsigned le16(const unsigned char *p) { return p[0] | (unsigned)p[1] << 8; }
static int64_t now_ms(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return -1;
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static bool hardware_parameters(void) {
    FILE *file = fopen(PCM_HW_PARAMS_PATH, "r");
    if (!file) return false;
    char text[513];
    size_t size = fread(text, 1, sizeof(text) - 1, file);
    bool valid = feof(file) && !ferror(file);
    if (fclose(file)) valid = false;
    if (!valid || memchr(text, '\0', size)) return false;
    text[size] = '\0';
    unsigned seen = 0;
    char *save = NULL;
    for (char *line = strtok_r(text, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        if (!strncmp(line, "format:", 7)) {
            if ((seen & 1) || strcmp(line, "format: S32_LE")) return false;
            seen |= 1;
        } else if (!strncmp(line, "channels:", 9)) {
            if ((seen & 2) || strcmp(line, "channels: 2")) return false;
            seen |= 2;
        } else if (!strncmp(line, "rate:", 5)) {
            if ((seen & 4) || (strcmp(line, "rate: 48000 (48000/1)") &&
                               strcmp(line, "rate: 48000"))) return false;
            seen |= 4;
        }
    }
    return seen == 7;
}

int main(int argc, char **argv) {
    int output = fcntl(STDOUT_FILENO, F_DUPFD_CLOEXEC, 3);
    if (output < 0 || dup2(STDERR_FILENO, STDOUT_FILENO) < 0) return 2;
    /* Keep the typed channel separate from vendor constructor/destructor stdout. */
    bool attempted = false, complete = false, clean = true;
    bool diagnostic = argc == 2 && strcmp(argv[1], "--check") == 0;
    bool configuration_parsed = false;
    bool hardware_verified = false;
    int64_t start = -1, elapsed = -1;
    const char *stage = "request";
    int alsa_status = 0;
    unsigned rate = 0;
    unsigned long written = 0, frames = 0;
    size_t size = 0;
    unsigned char *wav = NULL;
    void *library = NULL;
    void *config = NULL, *input = NULL;
    snd_pcm_t *pcm = NULL;
    int (*config_top)(void **) = NULL, (*config_load)(void *, void *) = NULL;
    int (*input_open)(void **, const char *, long) = NULL;
    int (*input_close)(void *) = NULL, (*config_delete)(void *) = NULL;
    int (*pcm_open)(snd_pcm_t **, const char *, int, int, void *) = NULL;
    int (*set_params)(snd_pcm_t *, int, int, unsigned, unsigned, int, unsigned) = NULL;
    long (*writei)(snd_pcm_t *, const void *, unsigned long) = NULL;
    int (*pcm_wait)(snd_pcm_t *, int) = NULL;
    int (*pcm_nonblock)(snd_pcm_t *, int) = NULL;
    int (*drain)(snd_pcm_t *) = NULL;
    int (*drop)(snd_pcm_t *) = NULL;
    int (*pcm_close)(snd_pcm_t *) = NULL;
    struct sigaction action = {0};
    action.sa_handler = stop;
    sigemptyset(&action.sa_mask);
    if (argc != 2 || (!diagnostic && strcmp(argv[1], "--play")) ||
        sigaction(SIGALRM, &action, NULL) ||
        sigaction(SIGTERM, &action, NULL) || sigaction(SIGINT, &action, NULL)) goto done;
    alarm(diagnostic ? 5 : 25);
    if (diagnostic) goto load_library;
    wav = malloc(WAV_MAX + 1);
    if (!wav) goto done;
    while (!interrupted && size <= WAV_MAX) {
        ssize_t count = read(STDIN_FILENO, wav + size, WAV_MAX + 1 - size);
        if (count == 0) break;
        if (count < 0) goto done;
        size += (size_t)count;
    }
    if (interrupted || size < 46 || size > WAV_MAX || memcmp(wav, "RIFF", 4) ||
        memcmp(wav + 8, "WAVEfmt ", 8) || le32(wav + 4) != size - 8 ||
        le32(wav + 16) != 16 || le16(wav + 20) != 1 || le16(wav + 22) != 1 ||
        le16(wav + 32) != 2 || le16(wav + 34) != 16 || memcmp(wav + 36, "data", 4) ||
        le32(wav + 40) != size - 44 || (size - 44) % 2) goto done;
    rate = le32(wav + 24);
    if (rate < 16000 || rate > 48000 || le32(wav + 28) != rate * 2 ||
        size - 44 > rate * 2 * 20) goto done;
    for (size_t i = 44; i < size; i += 2) {
        int sample = (int)le16(wav + i);
        if (sample >= 32768) sample -= 65536;
        if (sample < -8191 || sample > 8191) goto done;
    }
    frames = (unsigned long)(size - 44) / 2;
load_library:
    stage = "library";
    library = dlopen(PCM_ALSA_LIBRARY, RTLD_NOW | RTLD_LOCAL);
    if (!library) goto done;
#define LOAD(target, name) do { *(void **)(&(target)) = dlsym(library, (name)); \
                                if (!(target)) goto done; } while (0)
    stage = "symbols";
    LOAD(config_top, "snd_config_top"); LOAD(config_load, "snd_config_load");
    LOAD(input_open, "snd_input_buffer_open"); LOAD(input_close, "snd_input_close");
    LOAD(config_delete, "snd_config_delete");
    LOAD(pcm_open, "snd_pcm_open_lconf"); LOAD(set_params, "snd_pcm_set_params");
    LOAD(writei, "snd_pcm_writei"); LOAD(pcm_wait, "snd_pcm_wait");
    LOAD(pcm_nonblock, "snd_pcm_nonblock");
    LOAD(drain, "snd_pcm_drain"); LOAD(drop, "snd_pcm_drop"); LOAD(pcm_close, "snd_pcm_close");
    stage = "configuration";
    alsa_status = config_top(&config);
    if (alsa_status < 0) goto done;
    alsa_status = input_open(&input, route, (long)strlen(route));
    if (alsa_status < 0) goto done;
    alsa_status = config_load(config, input);
    if (alsa_status < 0) goto done;
    configuration_parsed = true;
    if (diagnostic) { complete = true; goto done; }
    /* Public enums: playback=0, nonblock=1, S16_LE=2, RW_INTERLEAVED=3. */
    stage = "open";
    if (interrupted || (alsa_status = pcm_open(&pcm, "alice_playback", 0, 1, config)) < 0) goto done;
    stage = "parameters";
    if ((alsa_status = set_params(pcm, 2, 3, 1, rate, 1, 100000)) < 0) goto done;
    stage = "hardware";
    hardware_verified = hardware_parameters();
    if (!hardware_verified || interrupted) goto done;
    stage = "clock";
    start = now_ms();
    if (start < 0) goto done;
    while (written < frames && !interrupted) {
        stage = "clock";
        int64_t now = now_ms();
        if (now < start || now - start >= 23000) goto done;
        stage = "write";
        attempted = true;
        long count = writei(pcm, wav + 44 + written * 2, frames - written);
        alsa_status = (int)count;
        if (count == -EAGAIN || count == 0) {
            stage = "wait";
            if ((alsa_status = pcm_wait(pcm, 100)) < 0) goto done;
        } else if (count < 0 || (unsigned long)count > frames - written) goto done;
        else written += (unsigned long)count;
    }
    if (interrupted) goto done;
    /* Drain owns the completion wait: a nonblocking drain can enter DRAINING,
     * where the write-readiness wait is not a reliable completion predicate.
     * The process alarm still hard-bounds a stuck vendor blocking call. */
    stage = "blocking";
    if ((alsa_status = pcm_nonblock(pcm, 0)) < 0 || interrupted) goto done;
    stage = "drain";
    if ((alsa_status = drain(pcm)) < 0 || interrupted) goto done;
    complete = true;
done:
    if (start >= 0) {
        int64_t end = now_ms();
        if (end < start || end - start > 25000) { complete = false; stage = "clock"; }
        else elapsed = end - start;
    }
    if (pcm) {
        int status;
        if (!complete && (status = drop(pcm)) < 0) { clean = false; alsa_status = status; }
        if ((status = pcm_close(pcm)) < 0) { clean = false; alsa_status = status; }
    }
    int status;
    if (input && (status = input_close(input)) < 0) { clean = false; alsa_status = status; }
    if (config && (status = config_delete(config)) < 0) { clean = false; alsa_status = status; }
    if (library) dlclose(library);
    free(wav);
    alarm(0);
    if (!clean) { complete = false; stage = "cleanup"; }
    if (diagnostic) {
        int emitted = dprintf(output, "{\"outcome\":\"%s\",\"diagnostic_only\":true,\"stage\":\"%s\","
               "\"library_loaded\":%s,\"configuration_parsed\":%s,\"alsa_return_code\":%d,"
               "\"pcm_opened\":false,\"audio_played\":false}\n",
               complete ? "ready" : clean ? "blocked" : "unknown",
               complete ? "ready" : clean ? stage : "cleanup", library ? "true" : "false",
               configuration_parsed ? "true" : "false", alsa_status);
        close(output);
        return emitted < 0 ? 2 : complete ? 0 : 1;
    }
    int emitted = dprintf(output, "{\"outcome\":\"%s\",\"playback_started\":%s,\"playback_completed\":%s,"
           "\"cleanup_verified\":%s,\"frames_written\":%lu,\"sample_rate\":%u,"
            "\"input_frames\":%lu,\"stage\":\"%s\",\"alsa_return_code\":%d,"
            "\"hardware_parameters_verified\":%s,\"playback_elapsed_ms\":%" PRId64 "}\n",
           complete ? "complete" : attempted || !clean ? "unknown" : "blocked",
           written ? "true" : "false", complete ? "true" : "false", clean ? "true" : "false",
            written, rate, frames, complete ? "complete" : stage, alsa_status,
            hardware_verified ? "true" : "false", elapsed);
    close(output);
    return emitted < 0 ? 2 : complete ? 0 : 1;
}
