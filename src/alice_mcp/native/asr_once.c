/* One future ASR request through the owner's existing local Alice IPC.
 * The default ASR build has no TTS command. ASR_ONCE_TTS builds a separate,
 * privately pinned vocalize-only client sharing the bounded transport/parser.
 * Neither build reads audio, files or credentials or starts recognition.
 * Wire facts: v3rt3p/quasar-protos @ 1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6,
 * yandex_io/protos/{quasar_proto,remoting,alice_state}.proto. Live ASR is unverified.
 * Deployment/execution requires separate owner approval; this is not an MCP gate.
 */
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define FRAME_MAX 65536u
#define STREAM_MAX (4u * 1024u * 1024u)
#define FIELD_MAX 2048u
#define ID_MAX 128u
#define TEXT_MAX 512u
#if defined(ASR_ONCE_TTS) && defined(ASR_ONCE_MUTE)
#error "Speech and mute metadata are separate production binaries"
#endif
#if defined(ASR_ONCE_SPOTTER) && (defined(ASR_ONCE_TTS) || defined(ASR_ONCE_MUTE))
#error "Stock command-spotter events are a separate production binary"
#endif
#ifdef ASR_ONCE_TEST
#define SETUP_SECONDS 5u
#else
#define SETUP_SECONDS 180u
#endif

#if defined(ASR_ONCE_SPOTTER)
/* Read-only AddListener(CommandSpotterCapability); never START/STOP/DISABLE, which would
 * also affect the stock wake word. Verified live on Mini 1 (aliced 9875), 2026-10-31. */
static const unsigned char SUBSCRIBE[] =
    "\x3e\x00\x00\x00\xe2\x0f\x37\x3a\x18" "CommandSpotterCapability"
    "\xa2\x01\x1a\x0a\x18" "CommandSpotterCapability" "\xe8\x0f\xd2\x01";
/* The stock ru-RU-quasar-commands model phrases; anything else is never printed. */
static const char *const PHRASES[] = {
    "дальше", "назад", "выше", "ниже", "вправо", "влево", "вверх", "вниз", "домой",
    "продолжи", "играй", "стоп", "хватит", "пауза", "следующий", "громче", "тише",
    "включи свет", "включи телевизор", "выключи свет", "выключи телевизор",
    "включи блютуз", "выключи блютуз", "лайк", "дизлайк",
};
#elif defined(ASR_ONCE_MUTE)
/* Read-only AddListener(DeviceStateCapability); never a microphone setting. */
static const unsigned char SUBSCRIBE[] =
    "\x38\x00\x00\x00\xe2\x0f\x31\x3a\x15" "DeviceStateCapability"
    "\xa2\x01\x17\x0a\x15" "DeviceStateCapability" "\xe8\x0f\xd2\x01";
#else
/* AddListener(AliceCapability), not AliceCapabilityMethod/START_RECOGNIZE. */
static const unsigned char SUBSCRIBE[] =
    "\x2c\x00\x00\x00\xe2\x0f\x25\x3a\x0f" "AliceCapability"
    "\xa2\x01\x11\x0a\x0f" "AliceCapability" "\xe8\x0f\xd2\x01";
#endif

typedef struct { const unsigned char *data; size_t size; } Slice;
typedef struct { bool present; uint64_t number; Slice bytes; } Field;
typedef struct {
    bool ready, listening;
    unsigned char baseline[ID_MAX], active[ID_MAX];
    size_t baseline_size, active_size;
} Capture;

static const char *failure = "protocol_error";
static volatile sig_atomic_t interrupted = 0;
#if defined(ASR_ONCE_SPOTTER)
static unsigned int spotted_count = 0;
#elif !defined(ASR_ONCE_TTS) && !defined(ASR_ONCE_MUTE)
static unsigned int frames_received = 0, direct_states = 0, listener_states = 0;
static int64_t started_at = 0;
#elif defined(ASR_ONCE_TTS)
static bool tts_may_sent = false, tts_sent = false, tts_started = false;
#elif defined(ASR_ONCE_MUTE)
static unsigned frames_received = 0, device_callbacks = 0, typed_snapshots = 0;
#endif

static void stop(int signum) { (void)signum; interrupted = 1; }

static int64_t now_ms(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) != 0) return -1;
    return (int64_t)t.tv_sec * INT64_C(1000) + (int64_t)t.tv_nsec / INT64_C(1000000);
}

static int blocked(const char *code) {
#ifdef ASR_ONCE_TTS
    (void)fputs(tts_may_sent ? "{\"outcome\":\"unknown\",\"error_code\":\""
                            : "{\"outcome\":\"blocked\",\"error_code\":\"", stdout);
    (void)fputs(code, stdout);
    (void)fputs("\",\"request_sent\":", stdout);
    (void)fputs(tts_sent ? "true" : "false", stdout);
    (void)fputs(",\"request_may_have_been_sent\":", stdout);
    (void)fputs(tts_may_sent ? "true" : "false", stdout);
    (void)fputs(",\"tts_started\":", stdout);
    (void)fputs(tts_started ? "true" : "false", stdout);
    (void)fputs(",\"tts_completed\":false}\n", stdout);
#elif defined(ASR_ONCE_SPOTTER)
    (void)printf("{\"event\":\"end\",\"outcome\":\"unknown\",\"error_code\":\"%s\","
                 "\"spotted\":%u}\n", code, spotted_count);
#elif defined(ASR_ONCE_MUTE)
    (void)printf("{\"outcome\":\"unknown\",\"mute_state\":\"unknown\","
                 "\"audio_captured\":false,\"error_code\":\"%s\",\"diagnostics\":{"
                 "\"frames_received\":%u,\"device_callbacks\":%u,\"typed_snapshots\":%u}}\n",
                 code, frames_received, device_callbacks, typed_snapshots);
#else
    (void)printf("{\"outcome\":\"blocked\",\"capture_ready\":false,\"error_code\":\"%s\","
                 "\"diagnostics\":{\"frames_received\":%u,\"direct_states\":%u,"
                 "\"listener_states\":%u}}\n", code, frames_received, direct_states, listener_states);
#endif
    (void)fflush(stdout);
    return 1;
}

static bool varint(Slice data, size_t *offset, uint64_t *out) {
    *out = 0;
    for (unsigned int i = 0; i < 10; ++i) {
        if (*offset >= data.size) return false;
        unsigned int byte = data.data[(*offset)++];
        if (i == 9 && byte > 1) return false;
        *out |= (uint64_t)(byte & 127u) << (i * 7u);
        if (byte < 128) return i == 0 || byte != 0;
    }
    return false;
}

/* A bounded field reader, not a general schema/reflection or RPC interface. */
static bool get(Slice message, unsigned int wanted, unsigned int expected, Field *out) {
    *out = (Field){0};
    size_t offset = 0, count = 0;
    while (offset < message.size) {
        uint64_t key, value = 0, length = 0;
        Slice bytes = {0};
        if (++count > FIELD_MAX || !varint(message, &offset, &key) ||
            key >> 3 == 0 || key >> 3 > 536870911u) return false;
        unsigned int wire = (unsigned int)(key & 7u);
        if (wire == 0) {
            if (!varint(message, &offset, &value)) return false;
        } else {
            if (wire == 2) {
                if (!varint(message, &offset, &length)) return false;
            } else if (wire == 1) length = 8;
            else if (wire == 5) length = 4;
            else return false;
            if (length > message.size - offset) return false;
            bytes = (Slice){message.data + offset, (size_t)length};
            offset += (size_t)length;
        }
        if (key >> 3 == wanted) {
            if (out->present || wire != expected) return false;
            *out = (Field){true, value, bytes};
        }
    }
    return true;
}

#if !defined(ASR_ONCE_MUTE) && !defined(ASR_ONCE_SPOTTER)
static bool valid_id(Slice id) {
    if (id.size > ID_MAX) return false;
    for (size_t i = 0; i < id.size; ++i)
        if (id.data[i] < 33 || id.data[i] > 126) return false;
    return true;
}

static bool same_id(Slice id, const unsigned char *other, size_t size) {
    return id.size == size && (size == 0 || memcmp(id.data, other, size) == 0);
}

static bool valid_text(Slice text) {
    if (text.size == 0 || text.size > TEXT_MAX) return false;
    size_t offset = 0, characters = 0;
    while (offset < text.size) {
        uint32_t c = text.data[offset++], minimum = 0;
        unsigned int extra = 0;
        if (c < 128) { /* ASCII. */ }
        else if (c >= 194 && c <= 223) { c &= 31; minimum = 128; extra = 1; }
        else if (c >= 224 && c <= 239) { c &= 15; minimum = 2048; extra = 2; }
        else if (c >= 240 && c <= 244) { c &= 7; minimum = 65536; extra = 3; }
        else return false;
        if (extra > text.size - offset) return false;
        for (unsigned int i = 0; i < extra; ++i) {
            uint32_t b = text.data[offset++];
            if ((b & 192u) != 128u) return false;
            c = (c << 6) | (b & 63u);
        }
        if (c < minimum || c > 1114111u || (c >= 55296 && c <= 57343) ||
            c < 32 || (c >= 127 && c <= 159) || (c >= 8234 && c <= 8238) ||
            (c >= 8294 && c <= 8297) || ++characters > 256) return false;
    }
    return true;
}
#endif

#if !defined(ASR_ONCE_TTS) && !defined(ASR_ONCE_MUTE) && !defined(ASR_ONCE_SPOTTER)
static bool emit_text(Slice text) {
    if (fputs("{\"outcome\":\"complete\",\"capture_ready\":false,\"text\":\"", stdout) < 0)
        return false;
    for (size_t i = 0; i < text.size; ++i) {
        unsigned char c = text.data[i];
        if ((c == '"' || c == '\\') && fputc('\\', stdout) == EOF) return false;
        if (fputc((int)c, stdout) == EOF) return false;
    }
    return fputs("\"}\n", stdout) >= 0 && fflush(stdout) == 0;
}

/* Returns 1 only for one new final request, never for a response/partial/cache. */
static int observe(Slice message, Capture *capture, int64_t *deadline, unsigned int seconds) {
    Field kind, payload, plain_state, plain_remote;
    Field object, listener, method, state, status, id, request, final, text;
    failure = "protocol_error";
    if (!get(message, 253, 0, &kind) || !get(message, 252, 2, &payload) ||
        !get(message, 3, 2, &plain_state) || !get(message, 210, 2, &plain_remote)) return -1;
    /* Accept the schema's full QuasarMessage as well as the packed 252/253 form.
     * Never combine competing representations or two different state sources. */
    if (!kind.present && !payload.present) {
        if (plain_state.present && plain_remote.present) return -1;
        if (plain_state.present) { kind.number = 3; payload = plain_state; }
        else if (plain_remote.present) { kind.number = 210; payload = plain_remote; }
        else return 0;
    } else if (!kind.present || !payload.present || plain_state.present || plain_remote.present)
        return -1;
    /* QuasarMessage.alice_state=3 is already AliceState, not a Remoting wrapper.
     * Both requests and listener callbacks use QuasarMessage.remoting=210. */
    if (kind.number == 3) { state = payload; direct_states++; }
    else if (kind.number == 210) {
        if (!get(payload.bytes, 7, 2, &object)) return -1;
        if (!object.present || object.bytes.size != 15 ||
            memcmp(object.bytes.data, "AliceCapability", 15) != 0) return 0;
        if (!get(payload.bytes, 18, 2, &listener)) return -1;
        if (!listener.present) return 0;
        if (!get(listener.bytes, 1, 0, &method) || !method.present) return -1;
        if (method.number == 4) { failure = "recognition_failed"; return -1; }
        if (method.number != 0) return 0;
        if (!get(listener.bytes, 2, 2, &state) || !state.present) return -1;
        listener_states++;
    } else return 0;
    if (!get(state.bytes, 1, 0, &status) || !status.present || status.number > 3 ||
        !get(state.bytes, 7, 2, &id) || !valid_id(id.bytes)) return -1;
    if (!capture->ready) {
        /* Discard an owner's warm-up and any cached/busy state until a real IDLE.
         * This never makes the warm-up itself eligible for transcript export. */
        if (status.number != 0) return 0;
        int64_t current = now_ms();
        if (current < 0 || *deadline - current < 2000) { failure = "timeout"; return -1; }
        capture->baseline_size = id.bytes.size;
        if (id.bytes.size) memcpy(capture->baseline, id.bytes.data, id.bytes.size);
        capture->ready = true;
        *deadline = current + (int64_t)seconds * 1000;
        if (printf("{\"outcome\":\"ready\",\"capture_ready\":true,\"remaining_seconds\":%lld,"
                   "\"elapsed_seconds\":%lld}\n", (long long)seconds,
                   (long long)((current-started_at)/1000)) < 0 || fflush(stdout) != 0) {
            failure = "output_closed"; return -1;
        }
        return 0;
    }
    if (status.number == 1) capture->listening = true;
    if (!capture->listening) return 0;
    if (id.bytes.size) {
        if (capture->active_size && !same_id(id.bytes, capture->active, capture->active_size)) {
            failure = "request_changed"; return -1;
        }
        if (!capture->active_size &&
            !same_id(id.bytes, capture->baseline, capture->baseline_size)) {
            memcpy(capture->active, id.bytes.data, id.bytes.size);
            capture->active_size = id.bytes.size;
        }
    }
    if (capture->active_size && same_id(id.bytes, capture->active, capture->active_size)) {
        if (!get(state.bytes, 15, 2, &request)) return -1;
        if (request.present) {
            if (!get(request.bytes, 2, 0, &final) || final.number > 1) return -1;
            if (final.present && final.number == 1) {
                if (!get(request.bytes, 1, 2, &text)) return -1;
                if (!text.present || !valid_text(text.bytes)) {
                    failure = "invalid_text"; return -1;
                }
                if (!emit_text(text.bytes)) { failure = "output_closed"; return -1; }
                return 1;
            }
        }
    }
    if (status.number == 0) { failure = "no_final_text"; return -1; }
    return 0;
}
#endif

static bool wait_fd(int fd, short events, int64_t deadline) {
    while (!interrupted) {
        int64_t current = now_ms();
        if (current < 0) { failure = "clock_error"; return false; }
        int64_t remaining = deadline - current;
        if (remaining <= 0) { failure = "timeout"; return false; }
        struct pollfd p = {fd, events, 0};
        int result = poll(&p, 1, remaining > INT_MAX ? INT_MAX : (int)remaining);
        if (result < 0 && errno == EINTR) continue;
        if (result < 0) { failure = "ipc_io_error"; return false; }
        if (result == 0) { failure = "timeout"; return false; }
        if (p.revents & events) return true;
        if ((events & POLLIN) && (p.revents & POLLHUP)) return true;
        failure = "ipc_closed"; return false;
    }
    failure = "interrupted";
    return false;
}

static bool read_exact(int fd, unsigned char *data, size_t size, int64_t deadline) {
    size_t offset = 0;
    while (offset < size) {
        if (!wait_fd(fd, POLLIN, deadline)) return false;
        ssize_t n = read(fd, data + offset, size - offset);
        if (n < 0 && (errno == EINTR || errno == EAGAIN)) continue;
        if (n <= 0) { failure = "ipc_closed"; return false; }
        offset += (size_t)n;
    }
    return true;
}

static int open_ipc(int64_t deadline) {
    failure = "ipc_unavailable";
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    if (fcntl(fd, F_SETFD, FD_CLOEXEC) < 0 || fcntl(fd, F_SETFL, O_NONBLOCK) < 0) goto bad;
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_port = htons(9875);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) != 0) {
        if (errno != EINPROGRESS || !wait_fd(fd, POLLOUT, deadline)) goto bad;
        int error = 0;
        socklen_t length = sizeof(error);
        if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length) != 0 || error != 0) goto bad;
    }
    return fd;
bad:
    (void)close(fd);
    return -1;
}

static bool send_all(int fd, const unsigned char *data, size_t size, int64_t deadline) {
    size_t offset = 0;
    while (offset < size) {
        if (!wait_fd(fd, POLLOUT, deadline)) return false;
        ssize_t n = write(fd, data + offset, size - offset);
        if (n < 0 && (errno == EINTR || errno == EAGAIN)) continue;
        if (n <= 0) { failure = "ipc_io_error"; return false; }
        offset += (size_t)n;
    }
    return true;
}

static int connect_ipc(int64_t deadline) {
    int fd = open_ipc(deadline);
    if (fd < 0) return -1;
    if (!send_all(fd, SUBSCRIBE, sizeof(SUBSCRIBE) - 1, deadline)) {
        (void)close(fd);
        return -1;
    }
    return fd;
}

#if defined(ASR_ONCE_MUTE)
/* RawCapability(type=17) -> State(3) -> DeviceState(1) -> explicit MicsMuted(26).
 * No device identifiers, account state, network fields, raw frames or speech leave
 * this process. Absence is unknown, never the proto2 default "false".
 */
static int mute_event(Slice message) {
    Field kind, payload, direct_state, plain_remote, object, changed, stored, id;
    Field holder, type, raw, state, device, muted;
    if (!get(message, 253, 0, &kind) || !get(message, 252, 2, &payload) ||
        !get(message, 3, 2, &direct_state) || !get(message, 210, 2, &plain_remote)) return -1;
    if (!kind.present && !payload.present) {
        if (direct_state.present && plain_remote.present) return -1;
        if (!plain_remote.present) return 0;
        kind.number = 210; payload = plain_remote;
    } else if (!kind.present || !payload.present || direct_state.present || plain_remote.present)
        return -1;
    if (kind.number != 210) return 0;
    if (!get(payload.bytes, 7, 2, &object)) return -1;
    if (!object.present || object.bytes.size != 21 ||
        memcmp(object.bytes.data, "DeviceStateCapability", 21) != 0) return 0;
    if (!get(payload.bytes, 3, 2, &changed)) return -1;
    if (!changed.present) return 0;
    device_callbacks++;
    if (!get(changed.bytes, 2, 2, &stored)) return -1;
    if (!stored.present) return 0;
    if (!get(stored.bytes, 1, 2, &id) || !get(stored.bytes, 6, 2, &holder)) return -1;
    if (!id.present || id.bytes.size != 21 ||
        memcmp(id.bytes.data, "DeviceStateCapability", 21) != 0 || !holder.present) return 0;
    if (!get(holder.bytes, 2, 0, &type) || !get(holder.bytes, 1, 2, &raw)) return -1;
    if (!type.present || type.number != 17 || !raw.present) return 0;
    typed_snapshots++;
    if (!get(raw.bytes, 3, 2, &state)) return -1;
    if (!state.present) return 0;
    if (!get(state.bytes, 1, 2, &device)) return -1;
    if (!device.present) return 0;
    if (!get(device.bytes, 26, 0, &muted) || (muted.present && muted.number > 1)) return -1;
    return muted.present ? (muted.number ? 2 : 1) : 0;
}

int main(int argc, char **argv) {
    bool test = false, watch = false;
#ifdef ASR_ONCE_TEST
    if (argc == 2 && strcmp(argv[1], "--test-subscription") == 0) {
        for (size_t i = 0; i < sizeof(SUBSCRIBE) - 1; ++i) (void)printf("%02x", SUBSCRIBE[i]);
        (void)putchar('\n'); return 0;
    }
    test = argc == 2 && strcmp(argv[1], "--test-mute-stream") == 0;
    if (argc == 2 && strcmp(argv[1], "--test-mute-watch") == 0) test = watch = true;
#endif
    if (argc == 2 && strcmp(argv[1], "--watch-mute") == 0) watch = true;
    if (argc != 2 || (!test && !watch && strcmp(argv[1], "--mute") != 0)) return 2;
    struct sigaction action = {0};
    action.sa_handler = stop;
    (void)sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) || sigaction(SIGTERM, &action, NULL) ||
        sigaction(SIGHUP, &action, NULL) || signal(SIGPIPE, SIG_IGN) == SIG_ERR)
        return blocked("signal_error");
    int64_t start = now_ms();
    if (start < 0) return blocked("clock_error");
    int64_t deadline = start + (watch ? 20000 : 5000);
    int fd = test ? STDIN_FILENO : connect_ipc(start + 5000);
    if (fd < 0) return blocked(failure);
    unsigned char header[4], bytes[FRAME_MAX];
    size_t total = 0;
    unsigned seen = 0, observations = 0;
    int latest = 0;
    for (unsigned n = 0; n < 4096; ++n) {
        if (!read_exact(fd, header, sizeof(header), deadline)) break;
        uint32_t size = (uint32_t)header[0] | (uint32_t)header[1] << 8 |
                        (uint32_t)header[2] << 16 | (uint32_t)header[3] << 24;
        if (size == 0 || size > FRAME_MAX || total > STREAM_MAX - size - 4u) {
            failure = "frame_limit"; break;
        }
        total += size + 4u;
        if (!read_exact(fd, bytes, size, deadline)) break;
        frames_received++;
        int value = mute_event((Slice){bytes, size});
        if (value < 0) { failure = "protocol_error"; break; }
        if (value > 0) {
            seen |= (unsigned)value;
            observations++;
            latest = value;
            if (watch) continue;
            (void)close(fd);
            (void)printf("{\"outcome\":\"complete\",\"mute_state\":\"%s\","
                         "\"current_observation_only\":true,\"audio_captured\":false}\n",
                         value == 2 ? "muted" : "unmuted");
            return 0;
        }
    }
    (void)close(fd);
    if (watch && latest > 0 && (strcmp(failure, "timeout") == 0 ||
                              (test && strcmp(failure, "ipc_closed") == 0))) {
        (void)printf("{\"outcome\":\"complete\",\"mute_state\":\"%s\","
                     "\"current_observation_only\":true,\"audio_captured\":false,"
                     "\"seen_states_bitmap\":%u,\"observations\":%u}\n",
                     latest == 2 ? "muted" : "unmuted", seen, observations);
        return 0;
    }
    return blocked(failure);
}
#elif defined(ASR_ONCE_SPOTTER)
/* Returns 0 for unrelated frames, -1 for malformed ones, else 1 + allowlist index. */
static int spotter_event(Slice message) {
    Field kind, payload, plain_remote, object, listener, method, phrase;
    if (!get(message, 253, 0, &kind) || !get(message, 252, 2, &payload) ||
        !get(message, 210, 2, &plain_remote)) return -1;
    if (!kind.present && !payload.present) {
        if (!plain_remote.present) return 0;
        kind.number = 210; payload = plain_remote;
    } else if (!kind.present || !payload.present || plain_remote.present) return -1;
    if (kind.number != 210) return 0;
    if (!get(payload.bytes, 7, 2, &object) || !get(payload.bytes, 15, 2, &listener)) return -1;
    if (!object.present || object.bytes.size != 24 ||
        memcmp(object.bytes.data, "CommandSpotterCapability", 24) != 0 || !listener.present)
        return 0;
    if (!get(listener.bytes, 1, 0, &method) || !get(listener.bytes, 3, 2, &phrase)) return -1;
    if (!method.present || method.number != 2 || !phrase.present) return 0;  /* ON_PHRASE_SPOTTED */
    for (size_t i = 0; i < sizeof(PHRASES) / sizeof(PHRASES[0]); ++i) {
        size_t size = strlen(PHRASES[i]);
        if (phrase.bytes.size == size && memcmp(phrase.bytes.data, PHRASES[i], size) == 0)
            return (int)i + 1;
    }
    return 0;
}

/* Fixed stock requests only, sent like Quasar's own clients (full QuasarMessage{210}).
 * Volume goes through Alice's normal text path, so Quasar itself sets its level, ring and
 * state; activation toggles only the stock wake word, never Quasar. No caller text. */
typedef struct { unsigned char data[1024]; size_t size; } Out;
static const char *const LEVEL_WORDS[11] = {
    "ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять", "десять",
};

static bool out_var(Out *o, uint64_t value) {
    do {
        if (o->size >= sizeof(o->data)) return false;
        unsigned char byte = (unsigned char)(value & 127u);
        value >>= 7;
        o->data[o->size++] = (unsigned char)(byte | (value ? 128u : 0u));
    } while (value);
    return true;
}

static bool out_int(Out *o, unsigned int number, uint64_t value) {
    return out_var(o, (uint64_t)number << 3) && out_var(o, value);
}

static bool out_bytes(Out *o, unsigned int number, const void *data, size_t size) {
    if (!out_var(o, (uint64_t)number << 3 | 2u) || !out_var(o, size) ||
        size > sizeof(o->data) - o->size) return false;
    memcpy(o->data + o->size, data, size);
    o->size += size;
    return true;
}

static bool out_frame(Out *frames, const char *object, unsigned int method_field, const Out *method) {
    Out remote = {0}, message = {0};
    if (!out_bytes(&remote, 7, object, strlen(object)) ||
        !out_bytes(&remote, method_field, method->data, method->size) ||
        !out_bytes(&message, 210, remote.data, remote.size) ||
        frames->size + 4 + message.size > sizeof(frames->data)) return false;
    for (unsigned int i = 0; i < 4; ++i)
        frames->data[frames->size++] = (unsigned char)(message.size >> (8 * i));
    memcpy(frames->data + frames->size, message.data, message.size);
    frames->size += message.size;
    return true;
}

static bool text_frame(Out *frames, const char *text, const char *id) {
    char event[128];
    int length = snprintf(event, sizeof event, "{\"type\":\"text_input\",\"text\":\"%s\"}", text);
    Out vins = {0}, method = {0};
    return length > 0 && (size_t)length < sizeof event &&
           out_bytes(&vins, 1, id, strlen(id)) && out_bytes(&vins, 2, event, (size_t)length) &&
           out_int(&vins, 18, 1) &&                               /* TEXT_INPUT */
           out_int(&method, 1, 0) && out_bytes(&method, 2, vins.data, vins.size) &&  /* START_REQUEST */
           out_frame(frames, "AliceCapability", 8, &method);
}

static bool activation_frame(Out *frames, unsigned int value) {
    Out method = {0};
    return out_int(&method, 1, value) &&
           out_frame(frames, "ActivationSpotterCapability", 14, &method);
}

/* Returns false for anything outside the fixed table. */
static bool control_frames(const char *command, const char *arg, const char *id, Out *frames) {
    if (strcmp(command, "--send-volume-level") == 0) {
        if (strlen(arg) == 0 || strlen(arg) > 2) return false;
        unsigned int level = 0;
        for (const char *p = arg; *p; ++p) {
            if (*p < '0' || *p > '9') return false;
            level = level * 10u + (unsigned int)(*p - '0');
        }
        if (level > 10) return false;
        char text[64];
        (void)snprintf(text, sizeof text, "громкость %s", LEVEL_WORDS[level]);
        return text_frame(frames, text, id);
    }
    if (strcmp(command, "--send-volume-step") == 0) {
        if (strcmp(arg, "louder") == 0) return text_frame(frames, "громче", id);
        if (strcmp(arg, "quieter") == 0) return text_frame(frames, "тише", id);
        return false;
    }
    if (strcmp(command, "--send-activation") == 0) {
        if (strcmp(arg, "off") == 0) return activation_frame(frames, 5);              /* DISABLE */
        if (strcmp(arg, "on") == 0)                                   /* ENABLE, then START */
            return activation_frame(frames, 4) && activation_frame(frames, 2);
    }
    return false;
}

static bool request_id(char out[37]) {
    unsigned char r[16];
    int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return false;
    bool ok = read(fd, r, sizeof r) == (ssize_t)sizeof r;
    (void)close(fd);
    r[6] = (unsigned char)((r[6] & 15u) | 64u);
    r[8] = (unsigned char)((r[8] & 63u) | 128u);
    (void)snprintf(out, 37, "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
                   r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11],
                   r[12], r[13], r[14], r[15]);
    return ok;
}

static int send_control(const char *command, const char *arg) {
    char id[37];
    Out frames = {0};
    if (!request_id(id)) return blocked("clock_error");
    if (!control_frames(command, arg, id, &frames)) return 2;
    int64_t start = now_ms();
    if (start < 0) return blocked("clock_error");
    int fd = open_ipc(start + 5000);
    if (fd < 0) return blocked(failure);
    bool sent = send_all(fd, frames.data, frames.size, start + 5000);
    struct timespec pause = {0, 300000000L};  /* Let aliced read before the socket closes. */
    (void)nanosleep(&pause, NULL);
    (void)close(fd);
    if (!sent) return blocked(failure);
    (void)printf("{\"event\":\"sent\",\"outcome\":\"sent\"}\n");
    return 0;
}

/* Waits for IPC data; any byte or EOF on stdin (the SSH control channel) means stop. */
static int wait_spotter(int fd, bool control, int64_t deadline) {
    while (!interrupted) {
        int64_t current = now_ms();
        if (current < 0) { failure = "clock_error"; return -1; }
        if (current >= deadline) return 0;
        struct pollfd p[2] = {{fd, POLLIN, 0}, {STDIN_FILENO, POLLIN, 0}};
        int64_t remaining = deadline - current;
        int result = poll(p, control ? 2u : 1u, remaining > INT_MAX ? INT_MAX : (int)remaining);
        if (result < 0 && errno == EINTR) continue;
        if (result < 0) { failure = "ipc_io_error"; return -1; }
        if (control && p[1].revents) return 0;
        if (p[0].revents) return 1;
    }
    return 0;
}

int main(int argc, char **argv) {
    bool test = false;
#ifdef ASR_ONCE_TEST
    if (argc == 2 && strcmp(argv[1], "--test-subscription") == 0) {
        for (size_t i = 0; i < sizeof(SUBSCRIBE) - 1; ++i) (void)printf("%02x", SUBSCRIBE[i]);
        (void)putchar('\n'); return 0;
    }
    test = argc == 2 && strcmp(argv[1], "--test-spotter-stream") == 0;
    if (argc == 4 && strcmp(argv[1], "--test-frame") == 0) {
        Out frames = {0};
        if (!control_frames(argv[2], argv[3], "00000000-0000-4000-8000-000000000000", &frames))
            return 2;
        for (size_t i = 0; i < frames.size; ++i) (void)printf("%02x", frames.data[i]);
        (void)putchar('\n');
        return 0;
    }
#endif
    if (argc == 3 && strncmp(argv[1], "--send-", 7) == 0) {
        struct sigaction ignore = {0};
        ignore.sa_handler = SIG_IGN;
        if (sigaction(SIGPIPE, &ignore, NULL)) return blocked("signal_error");
        return send_control(argv[1], argv[2]);
    }
    unsigned int seconds = 3660;
    if (!test) {
        if (argc != 3 || strcmp(argv[1], "--watch-spotter") != 0 || strlen(argv[2]) > 4 ||
            argv[2][0] == '\0') goto usage;
        seconds = 0;
        for (const char *p = argv[2]; *p; ++p) {
            if (*p < '0' || *p > '9') goto usage;
            seconds = seconds * 10u + (unsigned int)(*p - '0');
        }
        if (seconds < 1 || seconds > 3660) goto usage;
    }
    struct sigaction action = {0};
    action.sa_handler = stop;
    (void)sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) || sigaction(SIGTERM, &action, NULL) ||
        sigaction(SIGHUP, &action, NULL) || signal(SIGPIPE, SIG_IGN) == SIG_ERR)
        return blocked("signal_error");
    int64_t start = now_ms();
    if (start < 0) return blocked("clock_error");
    int64_t deadline = start + (int64_t)seconds * 1000;
    int fd = test ? STDIN_FILENO : connect_ipc(start + 5000);
    if (fd < 0) return blocked(failure);
    unsigned char header[4], bytes[FRAME_MAX];
    const char *reason = "deadline";
    for (;;) {
        int ready = wait_spotter(fd, !test, deadline);
        if (ready < 0) { (void)close(fd); return blocked(failure); }
        if (ready == 0) { reason = "stopped"; break; }
        if (!read_exact(fd, header, sizeof(header), now_ms() + 5000)) {
            if (test && strcmp(failure, "ipc_closed") == 0) { reason = "stopped"; break; }
            (void)close(fd); return blocked(failure);
        }
        uint32_t size = (uint32_t)header[0] | (uint32_t)header[1] << 8 |
                        (uint32_t)header[2] << 16 | (uint32_t)header[3] << 24;
        if (size == 0 || size > FRAME_MAX) { (void)close(fd); return blocked("frame_limit"); }
        if (!read_exact(fd, bytes, size, now_ms() + 5000)) { (void)close(fd); return blocked(failure); }
        int found = spotter_event((Slice){bytes, size});
        if (found < 0) { (void)close(fd); return blocked("protocol_error"); }
        if (found > 0) {
            spotted_count++;
            if (printf("{\"event\":\"spotter\",\"phrase\":\"%s\",\"trusted\":false}\n",
                       PHRASES[found - 1]) < 0 || fflush(stdout) != 0) {
                (void)close(fd); return blocked("output_closed");
            }
        }
    }
    if (now_ms() >= deadline) reason = "deadline";
    (void)close(fd);
    (void)printf("{\"event\":\"end\",\"outcome\":\"complete\",\"reason\":\"%s\","
                 "\"spotted\":%u}\n", reason, spotted_count);
    return 0;
usage:
    (void)fputs("Usage: quasar-client --watch-spotter SECONDS (1..3660) | --send-volume-level 0..10"
                " | --send-volume-step louder|quieter | --send-activation on|off\n", stderr);
    return 2;
}
#elif !defined(ASR_ONCE_TTS)
static int capture_once(int fd, int64_t deadline, unsigned int seconds) {
    Capture capture = {0};
    unsigned char data[FRAME_MAX], header[4];
    size_t total = 0;
    int64_t current = now_ms();
    if (current < 0 || deadline - current < 2000) return blocked("timeout");
    if (printf("{\"outcome\":\"awaiting_idle\",\"capture_ready\":false,"
               "\"remaining_seconds\":%lld}\n", (long long)((deadline-current)/1000)) < 0 ||
        fflush(stdout) != 0) return blocked("output_closed");
    for (unsigned int frames = 0; frames < 4096; ++frames) {
        if (!read_exact(fd, header, sizeof(header), deadline)) return blocked(failure);
        uint32_t size = (uint32_t)header[0] | (uint32_t)header[1] << 8 |
                        (uint32_t)header[2] << 16 | (uint32_t)header[3] << 24;
        if (size == 0 || size > FRAME_MAX || total > STREAM_MAX - size - 4u)
            return blocked("frame_limit");
        total += (size_t)size + 4u;
        if (!read_exact(fd, data, (size_t)size, deadline)) return blocked(failure);
        frames_received++;
        int result = observe((Slice){data, (size_t)size}, &capture, &deadline, seconds);
        if (result == 1) return 0;
        if (result < 0) return blocked(failure);
    }
    return blocked("frame_limit");
}

int main(int argc, char **argv) {
    bool test_stream = false;
#ifdef ASR_ONCE_TEST
    if (argc == 2 && strcmp(argv[1], "--test-subscription") == 0) {
        for (size_t i = 0; i < sizeof(SUBSCRIBE) - 1; ++i) (void)printf("%02x", SUBSCRIBE[i]);
        (void)putchar('\n');
        return 0;
    }
    test_stream = argc == 3 && strcmp(argv[1], "--test-stream") == 0;
#endif
    if (argc != 3 || (!test_stream && strcmp(argv[1], "--listen") != 0)) goto usage;
    unsigned int seconds = 0;
    if (strlen(argv[2]) > 2 || argv[2][0] == '\0') goto usage;
    for (const char *p = argv[2]; *p; ++p) {
        if (*p < '0' || *p > '9') goto usage;
        seconds = seconds * 10u + (unsigned int)(*p - '0');
    }
    if (seconds < 5 || seconds > 60) goto usage;
    struct sigaction action = {0};
    action.sa_handler = stop;
    (void)sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) || sigaction(SIGTERM, &action, NULL) ||
        sigaction(SIGHUP, &action, NULL) || signal(SIGPIPE, SIG_IGN) == SIG_ERR)
        return blocked("signal_error");
    int64_t start = now_ms();
    if (start < 0) return blocked("clock_error");
    started_at = start;
    int64_t deadline = start + (int64_t)SETUP_SECONDS * 1000;
    int fd = test_stream ? STDIN_FILENO : connect_ipc(start + 5000);
    if (fd < 0) return blocked(failure);
    int result = capture_once(fd, deadline, seconds);
    (void)close(fd);
    return result;
usage:
    (void)fputs("Usage: asr-once --listen SECONDS (5..60); separate owner approval required.\n", stderr);
    return 2;
}
#else
/* The TTS build can send only START_VOCALIZE(Text, Append), never a dialogue command. */
typedef struct { unsigned char data[1024]; size_t size; } TtsBuffer;

static bool tts_var(TtsBuffer *out, uint64_t value) {
    do {
        if (out->size >= sizeof(out->data)) return false;
        unsigned char byte = (unsigned char)(value & 127u);
        value >>= 7;
        out->data[out->size++] = (unsigned char)(byte | (value ? 128u : 0u));
    } while (value);
    return true;
}

static bool tts_int(TtsBuffer *out, unsigned int number, uint64_t value) {
    return tts_var(out, (uint64_t)number * 8) && tts_var(out, value);
}

static bool tts_bytes(TtsBuffer *out, unsigned int number, Slice value) {
    if (!tts_var(out, (uint64_t)number * 8 + 2) || !tts_var(out, value.size) ||
        value.size > sizeof(out->data) - out->size) return false;
    if (value.size) memcpy(out->data + out->size, value.data, value.size);
    out->size += value.size;
    return true;
}

static bool tts_request(Slice text, Slice id, TtsBuffer *frame) {
    TtsBuffer directive = {0}, method = {0}, remote = {0}, body = {0};
    if (!tts_bytes(&directive, 1, text) || !tts_int(&directive, 2, 1) ||
        !tts_int(&method, 1, 18) || !tts_int(&method, 3, 1) || !tts_bytes(&method, 10, id) ||
        !tts_bytes(&method, 13, (Slice){directive.data, directive.size}) ||
        !tts_bytes(&remote, 7, (Slice){(const unsigned char *)"AliceCapability", 15}) ||
        !tts_bytes(&remote, 8, (Slice){method.data, method.size}) ||
        !tts_bytes(&body, 252, (Slice){remote.data, remote.size}) || !tts_int(&body, 253, 210) ||
        body.size > sizeof(frame->data) - 4) return false;
    for (unsigned int i = 0; i < 4; ++i)
        frame->data[i] = (unsigned char)(((uint32_t)body.size >> (i * 8u)) & 255u);
    memcpy(frame->data + 4, body.data, body.size);
    frame->size = body.size + 4;
    return true;
}

static int tts_event(Slice message, Slice expected_id) {
    Field kind, payload, plain_state, plain_remote, object, listener, method, id;
    failure = "protocol_error";
    if (!get(message, 253, 0, &kind) || !get(message, 252, 2, &payload) ||
        !get(message, 3, 2, &plain_state) || !get(message, 210, 2, &plain_remote)) return -1;
    if (!kind.present && !payload.present) {
        if (plain_state.present && plain_remote.present) return -1;
        if (!plain_remote.present) return 0;  /* Do not inspect or export ASR text. */
        kind.number = 210; payload = plain_remote;
    } else if (!kind.present || !payload.present || plain_state.present || plain_remote.present)
        return -1;
    if (kind.number != 210) return 0;
    if (!get(payload.bytes, 7, 2, &object)) return -1;
    if (!object.present || object.bytes.size != 15 ||
        memcmp(object.bytes.data, "AliceCapability", 15) != 0) return 0;
    if (!get(payload.bytes, 18, 2, &listener)) return -1;
    if (!listener.present) return 0;
    if (!get(listener.bytes, 1, 0, &method) || !method.present ||
        !get(listener.bytes, 11, 2, &id)) return -1;
    if (!id.present || !same_id(id.bytes, expected_id.data, expected_id.size)) return 0;
    if (method.number == 6) tts_started = true;
    if (method.number == 1 && tts_started) return 1;
    if (method.number == 4) { failure = "tts_failed"; return -1; }
    return 0;
}

static int tts_wait(int fd, Slice id, int64_t deadline) {
    unsigned char data[FRAME_MAX], header[4];
    size_t total = 0;
    for (unsigned int frames = 0; frames < 4096; ++frames) {
        if (!read_exact(fd, header, sizeof(header), deadline)) return blocked(failure);
        uint32_t size = (uint32_t)header[0] | (uint32_t)header[1] << 8 |
                        (uint32_t)header[2] << 16 | (uint32_t)header[3] << 24;
        if (size == 0 || size > FRAME_MAX || total > STREAM_MAX - size - 4u)
            return blocked("frame_limit");
        total += (size_t)size + 4u;
        if (!read_exact(fd, data, (size_t)size, deadline)) return blocked(failure);
        int result = tts_event((Slice){data, (size_t)size}, id);
        if (result < 0) return blocked(failure);
        if (result == 1) {
            if (fputs("{\"outcome\":\"complete\",\"request_sent\":true,"
                      "\"request_may_have_been_sent\":true,\"tts_started\":true,"
                      "\"tts_completed\":true}\n", stdout) < 0 || fflush(stdout) != 0) return 1;
            return 0;
        }
    }
    return blocked("frame_limit");
}

int main(int argc, char **argv) {
    bool test_frame = false, test_stream = false;
    const char *nonce = NULL;
#ifdef ASR_ONCE_TEST
    test_frame = argc == 3 && strcmp(argv[1], "--test-tts-frame") == 0;
    test_stream = argc == 3 && strcmp(argv[1], "--test-tts-stream") == 0;
    if (test_frame || test_stream) nonce = argv[2];
#endif
    if (!nonce) {
        if (argc != 4 || strcmp(argv[1], "--say") != 0 || strcmp(argv[2], "20") != 0) goto usage;
        nonce = argv[3];
    }
    Slice id = {(const unsigned char *)nonce, strlen(nonce)};
    if (!valid_id(id) || id.size != 32) goto usage;
    for (size_t i = 0; i < id.size; ++i)
        if (!((nonce[i] >= '0' && nonce[i] <= '9') || (nonce[i] >= 'a' && nonce[i] <= 'f')))
            goto usage;
    struct sigaction action = {0};
    action.sa_handler = stop;
    (void)sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) || sigaction(SIGTERM, &action, NULL) ||
        sigaction(SIGHUP, &action, NULL) || signal(SIGPIPE, SIG_IGN) == SIG_ERR)
        return blocked("signal_error");
    int64_t start = now_ms();
    if (start < 0) return blocked("clock_error");
    int64_t deadline = start + 20000, setup = start + 5000;
    unsigned char header[4], text[TEXT_MAX];
    if (!read_exact(STDIN_FILENO, header, sizeof(header), setup)) return blocked("invalid_text");
    uint32_t size = (uint32_t)header[0] | (uint32_t)header[1] << 8 |
                    (uint32_t)header[2] << 16 | (uint32_t)header[3] << 24;
    if (size == 0 || size > TEXT_MAX || !read_exact(STDIN_FILENO, text, size, setup) ||
        !valid_text((Slice){text, size})) return blocked("invalid_text");
    if (!test_frame && !test_stream) {
        unsigned char extra;
        if (!wait_fd(STDIN_FILENO, POLLIN, setup) || read(STDIN_FILENO, &extra, 1) != 0)
            return blocked("invalid_text");
    }
    TtsBuffer frame = {0};
    if (!tts_request((Slice){text, size}, id, &frame)) return blocked("frame_limit");
#ifdef ASR_ONCE_TEST
    if (test_frame) {
        for (size_t i = 0; i < frame.size; ++i) (void)printf("%02x", frame.data[i]);
        (void)putchar('\n');
        return 0;
    }
#endif
    int fd = test_stream ? STDIN_FILENO : connect_ipc(setup);
    if (fd < 0) return blocked(failure);
    tts_may_sent = true;
    if (!test_stream) {
        size_t offset = 0;
        while (offset < frame.size) {
            if (!wait_fd(fd, POLLOUT, deadline)) { (void)close(fd); return blocked(failure); }
            ssize_t n = write(fd, frame.data + offset, frame.size - offset);
            if (n < 0 && (errno == EINTR || errno == EAGAIN)) continue;
            if (n <= 0) { (void)close(fd); return blocked("ipc_io_error"); }
            offset += (size_t)n;
        }
    }
    tts_sent = true;
    int result = tts_wait(fd, id, deadline);
    (void)close(fd);
    return result;
usage:
    (void)fputs("Usage: tts-once --say 20 REQUEST_ID; bounded text is read from stdin.\n", stderr);
    return 2;
}
#endif
