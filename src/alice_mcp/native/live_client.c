/* live-client: one direct Realtime conversation from the station, like a browser session.
 * The host mints a short-lived client secret (its session settings are bound server-side);
 * the API key never reaches the station. stdin: line 1 the secret (never printed), line 2
 * the opening text, then control lines ("stop"). stdout: JSON event lines only.
 * TLS is the station's own OpenSSL 1.1 through dlopen, like pcm_player uses vendor ALSA.
 * One thread, one poll loop: OpenSSL must not read and write one SSL concurrently.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <netdb.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef LIVE_SSL_LIBRARY
#define LIVE_SSL_LIBRARY "/system/vendor/quasar/libs/libssl.so.1.1"
#endif
#ifndef LIVE_HOST
#define LIVE_HOST "api.openai.com"
#endif
#ifndef LIVE_PORT
#define LIVE_PORT "443"
#endif
#define HOST LIVE_HOST
#define MESSAGE_MAX (1u << 20)
#define QUEUE_MAX (8u << 20) /* about 87 s of queued 48 kHz reply */
#define OPENING_MAX 16384
#define PLAY_FRAME 9600u /* 100 ms at 48 kHz mono S16 */

#ifndef LIVE_FIRMWARE_PATH
#define LIVE_FIRMWARE_PATH "/system/vendor/quasar/maind"
#endif

/* Fixed read-only identity for an already hash-verified file. Never a caller path,
 * permission token or replacement for its first full hash. Ignore access time only. */
static int firmware_state(void) {
    int fd = open(LIVE_FIRMWARE_PATH, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return 1;
    struct stat s;
    int ok = fstat(fd, &s) == 0;
    close(fd);
    if (!ok || !S_ISREG(s.st_mode) || s.st_uid != geteuid() || (s.st_mode & 0022) ||
        s.st_size <= 0 || s.st_size > (64 << 20)) return 1;
    printf("%ju:%ju:%ju:%ju:%ju:%ju:%ju:%jd:%jd:%jd:%jd.%09ld:%jd.%09ld\n",
           (uintmax_t)s.st_dev, (uintmax_t)s.st_ino, (uintmax_t)s.st_mode,
           (uintmax_t)s.st_nlink, (uintmax_t)s.st_uid, (uintmax_t)s.st_gid,
           (uintmax_t)s.st_rdev, (intmax_t)s.st_size, (intmax_t)s.st_blocks,
           (intmax_t)s.st_blksize, (intmax_t)s.st_mtim.tv_sec, (long)s.st_mtim.tv_nsec,
           (intmax_t)s.st_ctim.tv_sec, (long)s.st_ctim.tv_nsec);
    return fflush(stdout) == 0 ? 0 : 1;
}

static const double HALFBAND[8] = {0.628849, -0.189990, 0.093178, -0.048502, 0.023978,
                                   -0.010417, 0.003535, -0.000631};
static void *(*p_method)(void), *(*p_ctx_new)(void *), *(*p_new)(void *);
static int (*p_load)(void *, const char *, const char *), (*p_set_fd)(void *, int);
static long (*p_ctrl)(void *, int, long, void *);
static int (*p_set1_host)(void *, const char *), (*p_connect)(void *);
static int (*p_read)(void *, void *, int), (*p_write)(void *, const void *, int);
static int (*p_pending)(const void *), (*p_get_error)(const void *, int);
static long (*p_verify_result)(const void *);
static void (*p_set_verify)(void *, int, void *);
static void *ssl, *ctx, *library;
static void (*p_free)(void *), (*p_ctx_free)(void *);
static int sock_fd = -1;
static int64_t started;

static int64_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static void emit(const char *json) { printf("%s\n", json); fflush(stdout); }

static bool write_all(int fd, const unsigned char *data, size_t size) {
    while (size) {
        ssize_t n = write(fd, data, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        data += n; size -= (size_t)n;
    }
    return true;
}

static bool tls_write(const unsigned char *data, size_t size) {
    while (size) {
        int n = p_write(ssl, data, (int)(size > 65536 ? 65536 : size));
        if (n <= 0) return false;
        data += n; size -= (size_t)n;
    }
    return true;
}

/* Received TLS bytes; one whole frame always fits. After the upgrade, one SSL_read per
 * readable socket and whole-frame parsing: the loop never waits inside a frame or a ping. */
static unsigned char inbuf[MESSAGE_MAX + 65536];
static size_t in_start, in_end;
static int tls_fill(void) {  /* 1 data, 0 nothing yet (non-data record), -1 closed/failed */
    if (in_start) { memmove(inbuf, inbuf + in_start, in_end - in_start);
                    in_end -= in_start; in_start = 0; }
    if (in_end == sizeof(inbuf)) return -1;
    int n = p_read(ssl, inbuf + in_end, (int)(sizeof(inbuf) - in_end));
    if (n > 0) { in_end += (size_t)n; return 1; }
    return p_get_error(ssl, n) == 2 /* SSL_ERROR_WANT_READ */ ? 0 : -1;
}
static bool tls_exact(unsigned char *out, size_t size) {  /* Upgrade response only. */
    while (size) {
        if (in_start == in_end && tls_fill() <= 0) return false;
        size_t n = in_end - in_start < size ? in_end - in_start : size;
        memcpy(out, inbuf + in_start, n);
        in_start += n; out += n; size -= n;
    }
    return true;
}

static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
static size_t b64_encode(const unsigned char *in, size_t size, char *out) {
    size_t o = 0;
    for (size_t i = 0; i < size; i += 3) {
        uint32_t v = (uint32_t)in[i] << 16 | (i + 1 < size ? (uint32_t)in[i + 1] << 8 : 0) |
                     (i + 2 < size ? in[i + 2] : 0);
        out[o++] = B64[v >> 18 & 63]; out[o++] = B64[v >> 12 & 63];
        out[o++] = i + 1 < size ? B64[v >> 6 & 63] : '=';
        out[o++] = i + 2 < size ? B64[v & 63] : '=';
    }
    out[o] = 0;
    return o;
}
static long b64_decode(const char *in, size_t size, unsigned char *out) {
    uint32_t v = 0; int bits = 0; long o = 0;
    for (size_t i = 0; i < size && in[i] != '='; ++i) {
        const char *p = strchr(B64, in[i]);
        if (!p || !in[i]) return -1;
        v = v << 6 | (uint32_t)(p - B64); bits += 6;
        if (bits >= 8) { bits -= 8; out[o++] = (unsigned char)(v >> bits); }
    }
    return o;
}

static bool random_bytes(unsigned char *out, size_t size) {
    int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
    bool ok = fd >= 0 && read(fd, out, size) == (ssize_t)size;
    if (fd >= 0) close(fd);
    return ok;
}

static bool ws_send(int opcode, const unsigned char *payload, size_t size) {
    unsigned char header[14]; size_t h = 0;
    header[h++] = (unsigned char)(0x80 | opcode);
    if (size < 126) header[h++] = (unsigned char)(0x80 | size);
    else if (size < 65536) { header[h++] = 0x80 | 126; header[h++] = (unsigned char)(size >> 8);
                             header[h++] = (unsigned char)size; }
    else { header[h++] = 0x80 | 127;
           for (int i = 7; i >= 0; --i) header[h++] = (unsigned char)((uint64_t)size >> (8 * i)); }
    unsigned char mask[4];
    if (!random_bytes(mask, 4)) return false;
    memcpy(header + h, mask, 4); h += 4;
    unsigned char *frame = malloc(h + size);
    if (!frame) return false;
    memcpy(frame, header, h);
    for (size_t i = 0; i < size; ++i) frame[h + i] = payload[i] ^ mask[i % 4];
    bool ok = tls_write(frame, h + size);
    free(frame);
    return ok;
}
static bool ws_text(const char *text) { return ws_send(1, (const unsigned char *)text, strlen(text)); }

/* One frame from inbuf: -1 close/failure, 0 incomplete, 1 consumed (*out: a whole text
 * message to free, or NULL after a control frame or fragment). */
static char *partial; static size_t partial_size;
static int ws_parse(char **out, size_t *out_size) {
    *out = NULL;
    size_t have = in_end - in_start, header = 2;
    const unsigned char *p = inbuf + in_start;
    if (have < 2) return 0;
    if (p[1] & 0x80) return -1;  /* Servers never mask. */
    uint64_t length = p[1] & 0x7f;
    if (length == 126) { if (have < 4) return 0; length = (uint64_t)p[2] << 8 | p[3]; header = 4; }
    else if (length == 127) {
        if (have < 10) return 0;
        length = 0; for (int i = 0; i < 8; ++i) length = length << 8 | p[2 + i];
        header = 10;
    }
    if (length > MESSAGE_MAX || partial_size + length > MESSAGE_MAX) return -1;
    if (have < header + length) return 0;
    const unsigned char *payload = p + header;
    int opcode = p[0] & 0x0f; bool fin = p[0] & 0x80;
    in_start += header + (size_t)length;
    if (opcode == 8) return -1;
    if (opcode == 9) return ws_send(10, payload, (size_t)length) ? 1 : -1;
    if (opcode == 10) return 1;
    if (opcode != 0 && opcode != 1) return -1;
    char *grown = realloc(partial, partial_size + (size_t)length + 1);
    if (!grown) return -1;
    partial = grown;
    memcpy(partial + partial_size, payload, (size_t)length);
    partial_size += (size_t)length;
    if (!fin) return 1;
    partial[partial_size] = 0;
    *out = partial; *out_size = partial_size;
    partial = NULL; partial_size = 0;
    return 1;
}

static int tcp_connect(void) {
    struct addrinfo hints = {0}, *list = NULL;
    hints.ai_family = AF_UNSPEC; hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(HOST, LIVE_PORT, &hints, &list)) return -1;
    int fd = -1;
    for (struct addrinfo *a = list; a && fd < 0; a = a->ai_next) {
        fd = socket(a->ai_family, a->ai_socktype | SOCK_CLOEXEC, a->ai_protocol);
        struct timeval timeout = {10, 0};
        if (fd >= 0) setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof timeout);
        if (fd >= 0) setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
        int one = 1;
        if (fd >= 0) setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        if (fd >= 0 && connect(fd, a->ai_addr, a->ai_addrlen)) { close(fd); fd = -1; }
    }
    freeaddrinfo(list);
    return fd;
}

static const char *load_tls(void) {
    void *handle = dlopen(LIVE_SSL_LIBRARY, RTLD_NOW | RTLD_LOCAL);
    if (!handle) return "tls_unavailable";
#define LOAD(target, name) do { *(void **)(&(target)) = dlsym(handle, (name)); \
                                if (!(target)) return "tls_unavailable"; } while (0)
    LOAD(p_method, "TLS_client_method"); LOAD(p_ctx_new, "SSL_CTX_new");
    LOAD(p_load, "SSL_CTX_load_verify_locations"); LOAD(p_set_verify, "SSL_CTX_set_verify");
    LOAD(p_new, "SSL_new"); LOAD(p_set_fd, "SSL_set_fd"); LOAD(p_ctrl, "SSL_ctrl");
    LOAD(p_set1_host, "SSL_set1_host"); LOAD(p_connect, "SSL_connect");
    LOAD(p_read, "SSL_read"); LOAD(p_write, "SSL_write"); LOAD(p_pending, "SSL_pending");
    LOAD(p_verify_result, "SSL_get_verify_result"); LOAD(p_get_error, "SSL_get_error");
    LOAD(p_free, "SSL_free"); LOAD(p_ctx_free, "SSL_CTX_free");
    library = handle;
    return NULL;
}

static void close_session(void) {
    if (ssl) { ws_send(8, (const unsigned char *)"\x03\xe8", 2); p_free(ssl); ssl = NULL; }
    if (sock_fd >= 0) { close(sock_fd); sock_fd = -1; }
    if (ctx) { p_ctx_free(ctx); ctx = NULL; }
}

static const char *open_session(const char *ca, const char *model, const char *secret) {
    const char *failed = library ? NULL : load_tls();
    if (failed) return failed;
    ctx = p_ctx_new(p_method());
    if (!ctx || p_load(ctx, ca, NULL) != 1) return "ca_unavailable";
    p_set_verify(ctx, 1 /* SSL_VERIFY_PEER */, NULL);
    int fd = tcp_connect();
    if (fd < 0) return "connect_failed";
    sock_fd = fd;
    ssl = p_new(ctx);
    if (!ssl || p_set_fd(ssl, fd) != 1 || p_ctrl(ssl, 55, 0, (void *)HOST) != 1 ||
        p_set1_host(ssl, HOST) != 1 || p_connect(ssl) != 1 || p_verify_result(ssl) != 0)
        return "tls_failed";
    unsigned char nonce[16]; char key[32];
    if (!random_bytes(nonce, 16)) return "connect_failed";
    b64_encode(nonce, 16, key);
    char request[4608];
    int n = snprintf(request, sizeof request,
                     "GET /v1/realtime?model=%s HTTP/1.1\r\nHost: " HOST "\r\nUpgrade: websocket\r\n"
                     "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n"
                     "%s: Bearer %s\r\n\r\n", model, key, "Authorization", secret);
    bool sent = n > 0 && (size_t)n < sizeof request && tls_write((unsigned char *)request, (size_t)n);
    memset(request, 0, sizeof request);
    if (!sent) return "connect_failed";
    /* Status line and headers; an accept-key mismatch is a protocol failure. */
    char headers[8192]; size_t h = 0;
    while (h < sizeof headers - 1) {
        if (!tls_exact((unsigned char *)headers + h, 1)) return "handshake_rejected";
        h++;
        if (h >= 4 && !memcmp(headers + h - 4, "\r\n\r\n", 4)) break;
    }
    headers[h] = 0;
    if (strncmp(headers, "HTTP/1.1 101", 12)) return "handshake_rejected";
    /* From now on a non-data TLS record must return to the poll loop, never block it. */
    p_ctrl(ssl, 78 /* SSL_CTRL_CLEAR_MODE */, 4 /* SSL_MODE_AUTO_RETRY */, NULL);
    return NULL;
}

/* 24 -> 48 kHz half-band, then the player's quiet scale (1/4, within +-8191). */
static int history[16]; static int history_size = 7;
static int16_t quiet(double v) {
    long r = (long)(v >= 0 ? v + 0.5 : v - 0.5) / 4;
    return (int16_t)(r > 8191 ? 8191 : r < -8191 ? -8191 : r);
}
static size_t upsample(const unsigned char *pcm, size_t size, int16_t *out) {
    size_t produced = 0;
    for (size_t s = 0; s < size / 2; ++s) {
        history[history_size++] = (int16_t)(pcm[s * 2] | pcm[s * 2 + 1] << 8);
        if (history_size < 16) continue;
        double mid = 0;
        for (int k = 0; k < 8; ++k) mid += HALFBAND[k] * (history[7 - k] + history[8 + k]);
        out[produced++] = quiet(history[7]);
        out[produced++] = quiet(mid);
        memmove(history, history + 1, 15 * sizeof(int));
        history_size = 15;
    }
    return produced;
}
static void upsample_reset(void) { memset(history, 0, sizeof history); history_size = 7; }

/* 16 -> 24 kHz linear, continuous across blocks. */
static int mic_tail; static bool mic_have; static int mic_phase;
static size_t resample(const unsigned char *pcm, size_t size, int16_t *out) {
    int in[1602]; size_t n = 0, o = 0;
    if (mic_have) in[n++] = mic_tail;
    for (size_t i = 0; i + 1 < size && n < 1602; i += 2) in[n++] = (int16_t)(pcm[i] | pcm[i + 1] << 8);
    while (n > 1 && mic_phase < (int)(n - 1) * 3) {
        int i = mic_phase / 3, f = mic_phase % 3;
        out[o++] = (int16_t)((in[i] * (3 - f) + in[i + 1] * f) / 3);
        mic_phase += 2;
    }
    if (n) { mic_phase -= (int)(n - 1) * 3; mic_tail = in[n - 1]; mic_have = true; }
    return o;
}

static pid_t spawn(const char *path, char *const argv[], int *in_fd, int *out_fd) {
    int in[2] = {-1, -1}, out[2] = {-1, -1};
    if ((in_fd && pipe2(in, O_CLOEXEC)) || (out_fd && pipe2(out, O_CLOEXEC))) return -1;
    pid_t pid = fork();
    if (pid == 0) {
        if (in_fd) dup2(in[0], 0); else { int z = open("/dev/null", O_RDONLY); dup2(z, 0); }
        if (out_fd) dup2(out[1], 1); else { int z = open("/dev/null", O_WRONLY); dup2(z, 1); }
        execv(path, argv);
        _exit(127);
    }
    if (in_fd) { close(in[0]); *in_fd = in[1]; }
    if (out_fd) { close(out[1]); *out_fd = out[0]; }
    return pid;
}

/* Reply audio waiting for its real-time slot in the player (at most 300 ms queued there). */
static unsigned char *queue_data; static size_t queue_head, queue_end, queue_cap;
static int64_t playing_until;
static size_t queued(void) { return queue_end - queue_head; }
static void queue_clear(void) { queue_head = queue_end = 0; }
static bool enqueue(const int16_t *pcm, size_t samples) {
    size_t bytes = samples * 2;
    if (queued() + bytes > QUEUE_MAX) return false;
    if (queue_end + bytes > queue_cap) {
        memmove(queue_data, queue_data + queue_head, queued());
        queue_end -= queue_head; queue_head = 0;
        if (queue_end + bytes > queue_cap) {
            size_t cap = queue_end + bytes + (1u << 20);
            unsigned char *grown = realloc(queue_data, cap);
            if (!grown) return false;
            queue_data = grown; queue_cap = cap;
        }
    }
    for (size_t i = 0; i < samples; ++i) {
        queue_data[queue_end + i * 2] = (unsigned char)(pcm[i] & 0xff);
        queue_data[queue_end + i * 2 + 1] = (unsigned char)((uint16_t)pcm[i] >> 8);
    }
    queue_end += bytes;
    return true;
}
static bool player_frame(int fd, char kind, const unsigned char *data, uint32_t size) {
    unsigned char header[5] = {(unsigned char)kind, (unsigned char)size, (unsigned char)(size >> 8),
                               (unsigned char)(size >> 16), (unsigned char)(size >> 24)};
    return write_all(fd, header, 5) && (!size || write_all(fd, data, size));
}
static bool pump(int fd) {
    while (queued() && playing_until - now_ms() < 300) {
        uint32_t n = queued() < PLAY_FRAME ? (uint32_t)queued() : PLAY_FRAME;
        if (!player_frame(fd, 'A', queue_data + queue_head, n)) return false;
        queue_head += n;
        if (queue_head == queue_end) queue_clear();
        int64_t now = now_ms();
        if (playing_until < now) playing_until = now;
        playing_until += (int64_t)n * 1000 / 96000;
    }
    return true;
}

static bool read_line(char *out, size_t size) {
    for (size_t n = 0; n + 1 < size; ++n) {
        if (read(0, out + n, 1) != 1) return false;
        if (out[n] == '\n') { out[n] = 0; return n > 0; }
    }
    return false;  /* Over-long line: refuse rather than read its tail as controls. */
}

static bool field(const char *json, const char *name, const char **value, size_t *size) {
    char key[40];
    snprintf(key, sizeof key, "\"%s\":", name);
    const char *p = strstr(json, key);
    if (!p) return false;
    p += strlen(key);
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') ++p;  /* JSON whitespace */
    if (*p++ != '"') return false;
    const char *end = strchr(p, '"');
    if (!end) return false;
    *value = p; *size = (size_t)(end - p);
    return true;
}
static bool is(const char *value, size_t size, const char *text) {
    return size == strlen(text) && !memcmp(value, text, size);
}

/* Microphone ring: one capture feeds the wake detector and every session. */
#define RING (16000u * 2 * 8)  /* eight seconds of 16 kHz S16 */
static unsigned char ring[RING];
static uint64_t fed, mic_sent, wake_offset;
/* Per-session microphone numbers for the host's diagnosis; never the audio itself. */
static uint64_t mic_bytes, mic_buffered; static int mic_peak, mic_sent_peak; static double mic_sumsq;
/* The owner's voice in vqe_0 peaked near 1,000 (about -30 dBFS): too quiet for server VAD.
 * Speech sent to the model is raised toward an 8,000 peak, at most 16x, and at most 4x while
 * the reply plays so its echo residue cannot interrupt it. The wake detector stays raw. */
static double agc_envelope, agc_gain;

static void agc(unsigned char *block, size_t size, bool playing) {
    size_t count = size / 2;
    int peak = 0;
    for (size_t i = 0; i < count; ++i) {
        int v = (int16_t)(block[i * 2] | block[i * 2 + 1] << 8);
        if ((v < 0 ? -v : v) > peak) peak = v < 0 ? -v : v;
    }
    agc_envelope -= agc_envelope * 0.05 * (double)count / 1600.0;  /* ~0.5 dB per 100 ms */
    if (peak > agc_envelope) agc_envelope = peak;
    double target = 8000.0 / (agc_envelope > 250 ? agc_envelope : 250);
    double cap = playing ? 4 : 16;
    target = target > cap ? cap : target < 1 ? 1 : target;
    if (agc_gain <= 0 || target < agc_gain) agc_gain = target;  /* Fast attack, slow release. */
    for (size_t i = 0; i < count; ++i) {
        double g = agc_gain + (target - agc_gain) * (double)(i + 1) / (double)count;
        double x = (int16_t)(block[i * 2] | block[i * 2 + 1] << 8) * g;
        long v = (long)(x >= 0 ? x + 0.5 : x - 0.5);
        v = v > 32767 ? 32767 : v < -32768 ? -32768 : v;
        if ((v < 0 ? -v : v) > mic_sent_peak) mic_sent_peak = (int)(v < 0 ? -v : v);
        block[i * 2] = (unsigned char)(v & 0xff);
        block[i * 2 + 1] = (unsigned char)((uint16_t)(int16_t)v >> 8);
    }
    agc_gain = target;
}

static void ring_put(const unsigned char *data, size_t size) {
    while (size) {
        size_t at = (size_t)(fed % RING), n = size < RING - at ? size : RING - at;
        memcpy(ring + at, data, n);
        data += n; size -= n; fed += n;
    }
}

/* Session state; reset before each session. */
static int player_in = -1, player_out = -1;
static pid_t player = -1;
static bool in_session, ready, responding, talking, first_audio, create_after_done;
static int barge_ins, tools_pending;  /* function calls the host still has to answer */
static int64_t last_activity, session_deadline;
static char opening[OPENING_MAX];
static int16_t samples_out[MESSAGE_MAX / 4 * 3 + 64];  /* 2 per decoded 24 kHz sample */
static bool standby, speaking;  /* speaking: a "say" session, text in, no microphone */
static bool preparing;  /* Connected text-only socket; no player or response until "speak". */
static bool ask_mode, input_closed, answered;

static void stamp(const char *event) {
    char line[128];
    snprintf(line, sizeof line, "{\"event\":\"%s\",\"ms\":%lld}", event,
             (long long)(now_ms() - started));
    emit(line);
}

static bool send_mic(void) {  /* Everything captured since mic_sent, oldest first. */
    if (input_closed) return true;  /* One question: subsequent room speech stays local. */
    if (fed - mic_sent > RING) mic_sent = fed - RING;
    while (mic_sent + 2 <= fed) {
        unsigned char block[3200];
        size_t n = fed - mic_sent < sizeof block ? (size_t)(fed - mic_sent) : sizeof block;
        n &= ~(size_t)1;
        for (size_t i = 0; i < n; ++i) block[i] = ring[(mic_sent + i) % RING];
        mic_sent += n;
        for (size_t i = 0; i + 1 < n; i += 2) {
            int v = (int16_t)(block[i] | block[i + 1] << 8);
            if (v < 0) v = -v;
            if (v > mic_peak) mic_peak = v;
            mic_sumsq += (double)v * v;
        }
        mic_bytes += n;
        agc(block, n, queued() || now_ms() < playing_until + 300);
        int16_t audio[2500]; static char text[8000];
        size_t count = resample(block, n, audio);
        int len = snprintf(text, sizeof text, "{\"type\":\"input_audio_buffer.append\",\"audio\":\"");
        len += (int)b64_encode((unsigned char *)audio, count * 2, text + len);
        snprintf(text + len, sizeof text - (size_t)len, "\"}");
        if (count && !ws_text(text)) return false;
    }
    return true;
}

/* One server event; NULL to go on, or the reason this session ends. */
static bool send_opening(void) {
    static char text[OPENING_MAX * 6 + 256], escaped[OPENING_MAX * 6];
    size_t e = 0;
    for (const char *c = opening; *c && e < sizeof escaped - 7; ++c) {
        if (*c == '"' || *c == '\\') { escaped[e++] = '\\'; escaped[e++] = *c; }
        else if ((unsigned char)*c < 0x20) e += (size_t)snprintf(escaped + e, 7, "\\u%04x", *c);
        else escaped[e++] = *c;
    }
    escaped[e] = 0;
    snprintf(text, sizeof text, "{\"type\":\"conversation.item.create\",\"item\":{\"type\":"
             "\"message\",\"role\":\"user\",\"content\":[{\"type\":\"input_text\","
             "\"text\":\"%s\"}]}}", escaped);
    if (!ws_text(text) || !ws_text("{\"type\":\"response.create\"}")) return false;
    responding = true;
    return true;
}

static bool update_session(const char *config) {
    if (*config != '{' || strlen(config) > 16000) return false;
    char update[16100];
    snprintf(update, sizeof update, "{\"type\":\"session.update\",\"session\":%s}", config);
    return ws_text(update);
}

static const char *handle(const char *message) {
    const char *type, *value; size_t type_size, value_size;
    if (!field(message, "type", &type, &type_size)) return "failed";
    if (is(type, type_size, "error")) return "api_error";
    if (preparing) {
        if (is(type, type_size, "session.created") && !ready) {
            ready = true;
            stamp("prepared");
            return NULL;
        }
        if (is(type, type_size, "rate_limits.updated")) return NULL;
        return "unexpected_warm_event";  /* Never play unsolicited audio or forward tools. */
    }
    if ((is(type, type_size, "session.created") ||
         (ask_mode && is(type, type_size, "session.updated"))) && !ready) {
        ready = true;
        /* Standby: the owner is already speaking; send it from the wake word on. */
        mic_sent = standby ? (wake_offset > fed ? fed : wake_offset) : fed;
        mic_buffered = speaking ? 0 : fed - mic_sent;
        mic_tail = 0; mic_have = false; mic_phase = 0;
        if (opening[0] && !send_opening()) return "failed";
        stamp("ready");
        if (!speaking && !send_mic()) return "failed";
    } else if (is(type, type_size, "response.created")) {
        responding = true;
    } else if (is(type, type_size, "response.output_audio.delta") &&
               field(message, "delta", &value, &value_size)) {
        unsigned char *audio = malloc(value_size);
        long n = audio ? b64_decode(value, value_size, audio) : -1;
        if (n < 0 || n % 2) { free(audio); return "failed"; }
        if (!first_audio) { first_audio = true; stamp("first_audio"); }
        size_t count = upsample(audio, (size_t)n, samples_out);
        free(audio);
        if (!enqueue(samples_out, count)) return "output_limit";
        last_activity = now_ms();
    } else if (is(type, type_size, "response.function_call_arguments.done")) {
        /* The host checks and runs the call; the raw event goes out on one line. */
        static char out[16384 + 64];
        size_t n = strlen(message);
        if (n > 16384) return "failed";
        int len = snprintf(out, sizeof out, "{\"event\":\"tool\",\"message\":%s}", message);
        for (int i = 0; i < len; ++i) if (out[i] == '\n' || out[i] == '\r') out[i] = ' ';
        tools_pending++;
        emit(out);
    } else if (is(type, type_size, "response.done")) {
        if (field(message, "status", &value, &value_size) &&
            !is(value, value_size, "completed") && !is(value, value_size, "cancelled"))
            return "response_failed";
        unsigned char zeros[16] = {0};
        if (!enqueue(samples_out, upsample(zeros, sizeof zeros, samples_out))) return "output_limit";
        upsample_reset();
        responding = false; last_activity = now_ms();
        if (create_after_done) {  /* A tool result arrived during this reply: answer now. */
            create_after_done = false;
            if (!ws_text("{\"type\":\"response.create\"}")) return "failed";
            responding = true;
        } else if (ask_mode && !tools_pending) {
            answered = first_audio;
            if (!answered) { /* Wake alone: no answer; keep the bounded command window open. */
                input_closed = false;
                if (!send_mic()) return "failed"; /* Preserve a question spoken during silence. */
            }
        }
    } else if (is(type, type_size, "input_audio_buffer.speech_started")) {
        if (ask_mode && input_closed) return NULL;
        queue_clear(); upsample_reset();                 /* Barge-in: forget queued reply, */
        if (!player_frame(player_in, 'F', NULL, 0)) return "player_failed";
        playing_until = now_ms();                        /* and drop the board's buffer now. */
        barge_ins++; last_activity = now_ms(); talking = true;
        stamp("speech_started");
    } else if (is(type, type_size, "input_audio_buffer.speech_stopped")) {
        if (ask_mode && input_closed) return NULL;
        if (ask_mode) { input_closed = true; responding = true; }
        last_activity = now_ms(); talking = false;
        stamp("speech_stopped");
    }
    return NULL;
}

static const char *ca_path = "/system/vendor/quasar/ca-certificates.crt", *player_path;
static long idle_ms = 7000;

static void start_player(void) {
    char *player_argv[] = {(char *)player_path, "--stream", NULL};
    player = spawn(player_path, player_argv, &player_in, &player_out);
}

static void session_begin(long seconds, bool warm) {
    ready = responding = talking = first_audio = create_after_done = false;
    ask_mode = input_closed = answered = false;
    barge_ins = tools_pending = 0;
    queue_clear(); upsample_reset(); playing_until = 0;
    mic_bytes = mic_buffered = 0; mic_peak = mic_sent_peak = 0; mic_sumsq = 0;
    agc_envelope = agc_gain = 0;
    in_start = in_end = 0; free(partial); partial = NULL; partial_size = 0;
    in_session = true;
    preparing = warm;
    if (warm) speaking = true;
    last_activity = now_ms(); session_deadline = now_ms() + seconds * 1000;
    if (!warm) start_player();
}

/* Ends the session: drop or drain the reply, report the player, close the socket. */
static void session_end(const char *reason) {
    if (preparing) {
        char end[160];
        snprintf(end, sizeof end, "{\"event\":\"prepare_end\",\"reason\":\"%s\"}", reason);
        emit(end);
    } else if (player > 0) {
        if (!strcmp(reason, "idle")) while (queued() && pump(player_in)) usleep(20000);
        else if (queued() || !strcmp(reason, "stopped")) player_frame(player_in, 'F', NULL, 0);
        player_frame(player_in, 'E', NULL, 0);
        close(player_in);
        char report[1024] = {0}; ssize_t got = 0, n;
        while (got < (ssize_t)sizeof report - 1 &&
               (n = read(player_out, report + got, sizeof report - 1 - (size_t)got)) > 0) got += n;
        report[strcspn(report, "\n")] = 0;
        close(player_out);
        waitpid(player, NULL, 0);
        char end[1600];
        snprintf(end, sizeof end, "{\"event\":\"%s\",\"reason\":\"%s\",\"barge_ins\":%d,"
                 "\"elapsed_ms\":%lld,\"player\":%s,\"mic\":{\"buffered_ms\":%llu,"
                 "\"sent_ms\":%llu,\"peak\":%d,\"sent_peak\":%d,\"mean_square\":%llu}}",
                 standby ? "session_end" : "end", reason, barge_ins,
                 (long long)(now_ms() - started), report[0] == '{' ? report : "null",
                 (unsigned long long)(mic_buffered / 32), (unsigned long long)(mic_bytes / 32),
                 mic_peak, mic_sent_peak,
                 (unsigned long long)(mic_bytes ? mic_sumsq / (double)(mic_bytes / 2) : 0));
        emit(end);
    } else {
        char end[200];
        snprintf(end, sizeof end, "{\"event\":\"%s\",\"reason\":\"%s\",\"barge_ins\":0,"
                 "\"player\":null}", standby ? "session_end" : "end", reason);
        emit(end);
    }
    player = -1; player_in = player_out = -1;
    close_session();
    queue_clear();
    in_session = speaking = preparing = false;
}

static void session_open(const char *model, long seconds, char *secret) {
    session_begin(seconds, false);
    const char *failed = player < 0 ? "player_unavailable" : open_session(ca_path, model, secret);
    memset(secret, 0, strlen(secret));
    if (failed) { session_end(failed); return; }
    stamp("connected");
}

/* One host line. NULL to go on; "exit" ends the process (standby) after its session. */
static const char *command(char *line) {
    static const char prefix[] = "tool {\"type\":\"conversation.item.create\",\"item\":"
                                 "{\"type\":\"function_call_output\",\"call_id\":\"";
    if (!strcmp(line, "stop")) {
        if (in_session) session_end("stopped");
        return standby ? "stopped" : "exit";
    }
    if (!strcmp(line, "cancel")) { if (in_session) session_end("stopped"); return NULL; }
    if (!strcmp(line, "continue") || !strncmp(line, "continue ", 9)) {
        if (!in_session || tools_pending <= 0) return "invalid_control";
        if (!ask_mode) return NULL;
        if (line[8] && !update_session(line + 9)) {
            session_end("failed"); return NULL;
        }
        ask_mode = input_closed = false;
        mic_sent = fed;  /* Never replay the question or speech received while answering. */
        return NULL;
    }
    if (standby && !strncmp(line, "askwarm ", 8)) {
        char *config;
        long seconds = strtol(line + 8, &config, 10);
        if (*config++ != ' ' || *config != '{' || strlen(config) > 16000 ||
            seconds < 1 || seconds > 180) return "invalid_control";
        if (!preparing || !ready || now_ms() >= session_deadline) {
            if (preparing) session_end("deadline");
            emit("{\"event\":\"session_end\",\"reason\":\"not_prepared\",\"player\":null}");
            return NULL;
        }
        preparing = speaking = ready = false;
        ask_mode = true;
        session_deadline = now_ms() + seconds * 1000;
        last_activity = now_ms();
        opening[0] = 0;
        start_player();
        if (player < 0) session_end("player_unavailable");
        else if (!update_session(config)) session_end("failed");
        return NULL;
    }
    if (standby && !strncmp(line, "speak ", 6)) {
        char *text;
        long seconds = strtol(line + 6, &text, 10);
        if (*text != ' ' || !text[1] || seconds < 1 || seconds > 180 ||
            strlen(text + 1) >= sizeof opening) return "invalid_control";
        if (!preparing || !ready || now_ms() >= session_deadline) {
            if (preparing) session_end("deadline");
            emit("{\"event\":\"session_end\",\"reason\":\"not_prepared\",\"player\":null}");
            return NULL;
        }
        preparing = false;  /* Consume once; failure from here is never retried. */
        session_deadline = now_ms() + seconds * 1000;
        strcpy(opening, text + 1);
        start_player();
        if (player < 0) session_end("player_unavailable");
        else if (!send_opening()) session_end("failed");
        return NULL;
    }
    bool say = standby && !strncmp(line, "say ", 4);
    bool ask = standby && !strncmp(line, "ask ", 4);
    bool warm = standby && !strncmp(line, "prepare ", 8);
    if (standby && (say || ask || warm || !strncmp(line, "session ", 8)) && (!in_session || preparing)) {
        if (preparing) session_end("replaced");
        char *model = line + (say || ask ? 4 : 8), *seconds = strchr(model, ' '), *ephemeral, *text = NULL;
        if (!seconds) return "invalid_control";
        *seconds++ = 0;
        ephemeral = strchr(seconds, ' ');
        if (!ephemeral || strncmp(model, "gpt-realtime-", 13) || strlen(model) > 64) return "invalid_control";
        *ephemeral++ = 0;
        if (say) {  /* The rest of the line is the literal text to speak. */
            text = strchr(ephemeral, ' ');
            if (!text || !text[1] || strlen(text + 1) >= sizeof opening) return "invalid_control";
            *text++ = 0;
        }
        long bound = strtol(seconds, NULL, 10);
        if (bound < 1 || bound > 900 || !*ephemeral || strlen(ephemeral) > 4096) return "invalid_control";
        for (const char *c = ephemeral; *c; ++c) if (*c <= ' ' || *c > '~') return "invalid_control";
        strcpy(opening, text ? text : "");  /* Wake sessions listen; they have no opening. */
        if (warm) {
            if (strcmp(model, "gpt-realtime-2.1-mini") || bound > 120) return "invalid_control";
            session_begin(bound, true);
            const char *failed = open_session(ca_path, model, ephemeral);
            memset(ephemeral, 0, strlen(ephemeral));
            if (failed) session_end(failed);
            return NULL;
        }
        session_open(model, bound, ephemeral);
        speaking = say && in_session;
        ask_mode = ask && in_session;
        return NULL;
    }
    if (strncmp(line, prefix, sizeof prefix - 1) || !in_session || tools_pending <= 0)
        return "invalid_control";
    const char *failed = !ws_text(line + 5) ? "failed" : NULL;
    if (!failed) {
        tools_pending--;
        last_activity = now_ms();
        if (responding) create_after_done = true;  /* Never two replies at once. */
        else if (!ws_text("{\"type\":\"response.create\"}")) failed = "failed";
        else responding = true;
    }
    if (failed) session_end(failed);
    return NULL;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--firmware-state") == 0) return firmware_state();
    const char *capture = NULL, *detector_path = NULL, *model = "gpt-realtime-2.1";
    long max_seconds = 600;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--capture")) capture = argv[i + 1];
        else if (!strcmp(argv[i], "--player")) player_path = argv[i + 1];
        else if (!strcmp(argv[i], "--wake-detector")) detector_path = argv[i + 1];
        else if (!strcmp(argv[i], "--model")) model = argv[i + 1];
        else if (!strcmp(argv[i], "--ca")) ca_path = argv[i + 1];
        else if (!strcmp(argv[i], "--max-seconds")) max_seconds = strtol(argv[i + 1], NULL, 10);
        else if (!strcmp(argv[i], "--idle-ms")) idle_ms = strtol(argv[i + 1], NULL, 10);
        else { emit("{\"event\":\"end\",\"reason\":\"invalid_request\"}"); return 2; }
    }
    signal(SIGPIPE, SIG_IGN);
    started = now_ms();
    standby = detector_path != NULL;
    static char secret[4100];
    if (!player_path || max_seconds < 1 || max_seconds > 3700 || idle_ms < 100 ||
        (standby && !capture) || (!standby && max_seconds > 900) ||
        (!standby && (!read_line(secret, sizeof secret) || !read_line(opening, sizeof opening)))) {
        emit("{\"event\":\"end\",\"reason\":\"invalid_request\"}"); return 2;
    }
    for (const char *c = secret; *c; ++c)
        if (*c <= ' ' || *c > '~') { emit("{\"event\":\"end\",\"reason\":\"invalid_request\"}"); return 2; }
    int mic_out = -1, det_in = -1, det_out = -1, windows = 0;
    pid_t mic = -1, detector = -1;
    if (standby) {
        char *argv_d[] = {(char *)detector_path, "--max-seconds", "3700", "--gain-db", "0", NULL};
        detector = spawn(detector_path, argv_d, &det_in, &det_out);
        if (detector < 0) { emit("{\"event\":\"end\",\"reason\":\"wake_failed\"}"); return 1; }
    }
    int64_t deadline = started + max_seconds * 1000;
    const char *reason = NULL;
    if (!standby) session_open(model, max_seconds, secret);
    static char control[65536], wake_line[4096];
    size_t control_size = 0, wake_size = 0;
    while (!reason) {
        if (!standby && !in_session) break;  /* One session, already reported. */
        int64_t now = now_ms();
        if (now >= deadline) { reason = "deadline"; break; }
        if (in_session) {
            if (now >= session_deadline) { session_end("deadline"); continue; }
            if (!preparing && ready && !responding && !talking && !tools_pending && !queued() &&
                now >= playing_until && now - last_activity >=
                    (speaking || (ask_mode && answered) ? 200 : ask_mode ? 3000 : idle_ms)) {
                session_end("idle"); continue;
            }
            if (!preparing && !pump(player_in)) { session_end("player_failed"); continue; }
        }
        if (capture && mic < 0 && (standby || ready) && windows < max_seconds / 60 + 1) {
            char *mic_argv[] = {(char *)capture, "--stream", "9850", "60", NULL};
            mic = spawn(capture, mic_argv, NULL, &mic_out);
            if (mic > 0) fcntl(mic_out, 1031 /* F_SETPIPE_SZ */, 1 << 20);  /* ride out TLS */
            windows++;
        }
        struct pollfd fds[4] = {{0, POLLIN, 0}, {mic_out, POLLIN, 0}, {det_out, POLLIN, 0},
                                {in_session ? sock_fd : -1, POLLIN, 0}};
        bool buffered = in_session && p_pending(ssl) > 0;
        int polled = poll(fds, 4, buffered ? 0 : 20);
        if (polled < 0 && errno != EINTR) { reason = "failed"; break; }
        if (fds[0].revents & (POLLIN | POLLHUP)) {
            ssize_t n = read(0, control + control_size, sizeof control - 1 - control_size);
            if (n <= 0) { if (in_session) session_end("stopped"); reason = "stopped"; break; }
            control_size += (size_t)n;
            char *line_end;
            while (!reason && (line_end = memchr(control, '\n', control_size))) {
                *line_end = 0;
                const char *ended = command(control);
                if (ended && strcmp(ended, "exit")) reason = ended;
                else if (ended) reason = "stopped";
                size_t used = (size_t)(line_end + 1 - control);
                memmove(control, line_end + 1, control_size - used);
                control_size -= used;
            }
            if (!reason && control_size == sizeof control - 1) reason = "invalid_control";
            if (reason) { if (in_session) session_end(reason); break; }
        }
        if (mic_out >= 0 && fds[1].revents & (POLLIN | POLLHUP)) {
            unsigned char block[3200];
            ssize_t n = read(mic_out, block, sizeof block);
            if (n <= 0) { close(mic_out); mic_out = -1; waitpid(mic, NULL, 0); mic = -1; }
            else {
                ring_put(block, (size_t)n);
                if (standby && !write_all(det_in, block, (size_t)n)) { reason = "wake_failed"; break; }
                if (in_session && ready && !speaking && !send_mic()) session_end("failed");
            }
        }
        if (det_out >= 0 && fds[2].revents & (POLLIN | POLLHUP)) {
            ssize_t n = read(det_out, wake_line + wake_size, sizeof wake_line - 1 - wake_size);
            if (n <= 0) { reason = "wake_failed"; break; }
            wake_size += (size_t)n;
            char *line_end;
            while ((line_end = memchr(wake_line, '\n', wake_size))) {
                *line_end = 0;
                if (wake_line[0] == '{') {  /* Forwarded raw; the host checks every line. */
                    const char *t = strstr(wake_line, "\"event\":\"wake\"") ? strstr(wake_line, "\"t_ms\":") : NULL;
                    if (t) wake_offset = (uint64_t)strtoull(t + 7, NULL, 10) * 32;
                    static char out[4200];
                    snprintf(out, sizeof out, "{\"event\":\"detector\",\"line\":%s}", wake_line);
                    emit(out);
                }
                size_t used = (size_t)(line_end + 1 - wake_line);
                memmove(wake_line, line_end + 1, wake_size - used);
                wake_size -= used;
            }
            if (wake_size == sizeof wake_line - 1) { reason = "wake_failed"; break; }
        }
        if (!in_session) continue;
        if ((buffered || fds[3].revents & (POLLIN | POLLHUP | POLLERR)) && tls_fill() < 0) {
            session_end("failed"); continue;
        }
        const char *ended = NULL;
        for (;;) {
            char *message; size_t size;
            int parsed = ws_parse(&message, &size);
            if (parsed < 0) { ended = "failed"; break; }
            if (parsed == 0) break;
            if (!message) continue;  /* Ping answered, pong or fragment: keep going. */
            ended = handle(message);
            free(message);
            if (ended) break;
        }
        if (ended) session_end(ended);
    }
    if (in_session) session_end(reason ? reason : "stopped");
    if (mic > 0) { kill(mic, SIGTERM); waitpid(mic, NULL, 0); }
    if (detector > 0) { close(det_in); kill(detector, SIGTERM); waitpid(detector, NULL, 0); }
    if (standby) {
        char end[200];
        snprintf(end, sizeof end, "{\"event\":\"end\",\"reason\":\"%s\"}", reason ? reason : "stopped");
        emit(end);
        return reason && !strcmp(reason, "stopped") ? 0 : 1;
    }
    return 0;
}
