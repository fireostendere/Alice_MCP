/* Bounded IO-channel level pilot: one loopback connection, numbers only.
 * Sends one fixed, schema-backed opening chosen by PREAMBLE (or nothing for
 * listen_only), reads io_audio_channels_data (158) for at most 5 seconds, then
 * sends unsubscribe in the same envelope. Samples are reduced to per-channel
 * peak/mean-square in a reused buffer. Non-audio frames yield only lengths, field
 * numbers, wire types and the packed kind; no audio, payload bytes or text leave
 * this process. Wire facts: v3rt3p/quasar-protos @ 1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6
 * yandex_io/protos/{quasar_proto,yandex_io}.proto; the 252/253 packed envelope is
 * the one asr_once.c uses. Which server answers is unverified (docs/IO_CHANNEL.md).
 */
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define FRAME_MAX (256u * 1024u)
#define STREAM_MAX (32u * 1024u * 1024u)
#define FRAMES_MAX 20000u
#define FIELD_MAX 4096u
#define CHANNELS_MAX 16u
#define LABEL_MAX 24u
#define META_MAX 16u
#define TOP_MAX 8u
#define PATHS_MAX 16u

typedef struct { const unsigned char *data; size_t size; } Slice;
typedef struct { bool present; uint64_t number; Slice bytes; } Field;
typedef struct {
    uint64_t type, rate, format, frames, bytes, duration, samples, peak, squares;
    char name[LABEL_MAX + 1];
} Channel;
typedef struct {
    int64_t length, kind;
    const char *payload;
    unsigned int top_count, path_count, top[TOP_MAX][2], paths[PATHS_MAX][2];
} Meta;

enum { NONE, LISTEN, FULL, HUB };
static const char *const PREAMBLES[] = {"none", "listen_only", "full_envelope",
                                        "hub_retrieve_state"};
/* PackedMessage{252: IOHubMessage{retrieve_state: {}}, 253: QuasarMessage.io_hub_message=183}. */
static const unsigned char HUB_STATE[] = {9, 0, 0, 0, 0xe2, 0x0f, 2, 0x0a, 0, 0xe8, 0x0f, 0xb7, 1};

static Meta metas[META_MAX];
static unsigned int meta_count = 0;
static Channel channels[CHANNELS_MAX];
static unsigned channel_count = 0, frames = 0, audio_frames = 0, other_frames = 0;
static uint64_t total_bytes = 0;
static int64_t started = 0, first_audio = -1;
static const char *failure = "none";
static volatile sig_atomic_t interrupted = 0;
static unsigned char buffer[FRAME_MAX];

static void stop(int signum) { (void)signum; interrupted = 1; }

static int64_t now_ms(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) != 0) return -1;
    return (int64_t)t.tv_sec * INT64_C(1000) + (int64_t)t.tv_nsec / INT64_C(1000000);
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

/* One bounded protobuf field; returns 0 at end, 1 for a field, -1 when malformed. */
static int next(Slice message, size_t *offset, unsigned int *number, unsigned int *wire,
                uint64_t *value, Slice *bytes) {
    uint64_t key, length = 0;
    if (*offset >= message.size) return 0;
    if (!varint(message, offset, &key) || key >> 3 == 0 || key >> 3 > 536870911u) return -1;
    *number = (unsigned int)(key >> 3);
    *wire = (unsigned int)(key & 7u);
    *value = 0;
    *bytes = (Slice){0};
    if (*wire == 0) return varint(message, offset, value) ? 1 : -1;
    if (*wire == 2) { if (!varint(message, offset, &length)) return -1; }
    else if (*wire == 1) length = 8;
    else if (*wire == 5) length = 4;
    else return -1;
    if (length > message.size - *offset) return -1;
    *bytes = (Slice){message.data + *offset, (size_t)length};
    *offset += (size_t)length;
    return 1;
}

/* A singular field of the expected wire type; duplicates are rejected. */
static bool get(Slice message, unsigned int wanted, unsigned int expected, Field *out) {
    size_t offset = 0;
    unsigned int number, wire, count = 0;
    uint64_t value;
    Slice bytes;
    int status;
    *out = (Field){0};
    while ((status = next(message, &offset, &number, &wire, &value, &bytes)) == 1) {
        if (++count > FIELD_MAX) return false;
        if (number != wanted) continue;
        if (out->present || wire != expected) return false;
        *out = (Field){true, value, bytes};
    }
    return status == 0;
}

static bool add_channel(Slice item) {
    Field type, name, data, rate, format, duration;
    if (!get(item, 1, 0, &type) || !get(item, 2, 2, &name) || !get(item, 3, 2, &data) ||
        !get(item, 7, 0, &rate) || !get(item, 8, 0, &format) || !get(item, 10, 0, &duration) ||
        type.number > 1000 || rate.number > 768000 || format.number > 64 ||
        duration.number > 60000) { failure = "protocol_error"; return false; }
    char label[LABEL_MAX + 1] = "-";
    bool valid = name.bytes.size >= 1 && name.bytes.size <= LABEL_MAX;
    for (size_t i = 0; valid && i < name.bytes.size; ++i) {
        unsigned char c = name.bytes.data[i];
        valid = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_';
    }
    if (valid) {
        memcpy(label, name.bytes.data, name.bytes.size);
        label[name.bytes.size] = '\0';
    }
    Channel *slot = NULL;
    for (unsigned int i = 0; i < channel_count && !slot; ++i)
        if (channels[i].type == type.number && channels[i].rate == rate.number &&
            channels[i].format == format.number && strcmp(channels[i].name, label) == 0)
            slot = &channels[i];
    if (!slot) {
        if (channel_count == CHANNELS_MAX) { failure = "channel_limit"; return false; }
        slot = &channels[channel_count++];
        *slot = (Channel){.type = type.number, .rate = rate.number, .format = format.number};
        memcpy(slot->name, label, sizeof(label));
    }
    slot->frames++;
    slot->bytes += data.bytes.size;
    slot->duration += duration.number;
    /* ponytail: S16LE interpretation only; bytes_per_sample_estimate flags other formats. */
    for (size_t i = 0; i + 1 < data.bytes.size; i += 2) {
        int32_t sample = (int16_t)(uint16_t)(data.bytes.data[i] |
                                             (unsigned int)data.bytes.data[i + 1] << 8);
        uint64_t magnitude = (uint64_t)(sample < 0 ? -sample : sample);
        if (magnitude > slot->peak) slot->peak = magnitude;
        slot->squares += magnitude * magnitude;
        slot->samples++;
    }
    return true;
}

/* 1 audio, 0 other message, -1 malformed or bounded failure. */
static int observe(Slice message) {
    Field kind, payload, plain;
    size_t offset = 0;
    unsigned int number, wire, count = 0;
    uint64_t value;
    Slice item;
    int status;
    failure = "protocol_error";
    if (!get(message, 253, 0, &kind) || !get(message, 252, 2, &payload) ||
        !get(message, 158, 2, &plain)) return -1;
    if (!kind.present && !payload.present) {
        if (!plain.present) { failure = "none"; return 0; }
        payload = plain;
    } else if (!kind.present || !payload.present || plain.present) return -1;
    else if (kind.number != 158) { failure = "none"; return 0; }
    while ((status = next(payload.bytes, &offset, &number, &wire, &value, &item)) == 1) {
        if (++count > FIELD_MAX) return -1;
        if (number != 1) continue;
        if (wire != 2 || !add_channel(item)) return -1;
    }
    if (status != 0) return -1;
    failure = "none";
    return 1;
}

/* Text has almost no bytes below 0x20; protobuf keys and short lengths usually do.
 * String-like bytes are never parsed, so their content cannot leak as field numbers. */
static bool string_like(Slice s) {
    size_t control = 0;
    for (size_t i = 0; i < s.size; ++i)
        if (s.data[i] < 0x20) control++;
    return s.size > 0 && control * 10 < s.size;
}

static bool parses(Slice message) {
    size_t offset = 0;
    unsigned int number, wire, count = 0;
    uint64_t value;
    Slice bytes;
    int status;
    while ((status = next(message, &offset, &number, &wire, &value, &bytes)) == 1)
        if (++count > FIELD_MAX) return false;
    return status == 0;
}

static void add_path(Meta *m, unsigned int a, unsigned int b) {
    for (unsigned int i = 0; i < m->path_count; ++i)
        if (m->paths[i][0] == a && m->paths[i][1] == b) return;
    if (m->path_count == PATHS_MAX) return;
    m->paths[m->path_count][0] = a;
    m->paths[m->path_count++][1] = b;
}

/* Numbers only: length, top-level field/wire pairs, packed kind, and field-number paths
 * two levels into the payload (252 bytes, or the single field of a full QuasarMessage). */
static void describe(Slice frame) {
    if (meta_count == META_MAX) return;
    Meta *m = &metas[meta_count++];
    *m = (Meta){.length = (int64_t)frame.size, .kind = -1, .payload = "absent"};
    size_t offset = 0;
    unsigned int number, wire, count = 0;
    uint64_t value;
    Slice bytes, payload = {0}, first = {0};
    bool packed = false;
    int status;
    while ((status = next(frame, &offset, &number, &wire, &value, &bytes)) == 1) {
        if (++count > FIELD_MAX) { status = -1; break; }
        bool seen = false;
        for (unsigned int i = 0; i < m->top_count; ++i)
            seen = seen || (m->top[i][0] == number && m->top[i][1] == wire);
        if (!seen && m->top_count < TOP_MAX) {
            m->top[m->top_count][0] = number;
            m->top[m->top_count++][1] = wire;
        }
        if (number == 253 && wire == 0 && value <= 536870911u) m->kind = (int64_t)value;
        if (number == 252 && wire == 2) { payload = bytes; packed = true; }
        if (count == 1) first = bytes;
    }
    if (status != 0) { *m = (Meta){.length = (int64_t)frame.size, .kind = -1,
                                   .payload = "unparsed"}; return; }
    if (!packed) {
        if (count != 1 || m->top[0][1] != 2) return;
        payload = first;
    }
    if (payload.size == 0) { m->payload = "empty"; return; }
    if (string_like(payload)) { m->payload = "string_like"; return; }
    if (!parses(payload)) { m->payload = "unparsed"; return; }
    m->payload = "message";
    offset = 0;
    while (next(payload, &offset, &number, &wire, &value, &bytes) == 1) {
        add_path(m, number, 0);
        if (wire != 2 || bytes.size == 0 || string_like(bytes) || !parses(bytes)) continue;
        size_t inner = 0;
        unsigned int nested, nested_wire;
        Slice ignored;
        while (next(bytes, &inner, &nested, &nested_wire, &value, &ignored) == 1)
            add_path(m, number, nested);
    }
}

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

static bool write_all(int fd, const unsigned char *data, size_t size, int64_t deadline) {
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

static int connect_ipc(unsigned int port, int64_t deadline) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    if (fcntl(fd, F_SETFD, FD_CLOEXEC) < 0 || fcntl(fd, F_SETFL, O_NONBLOCK) < 0) goto bad;
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_port = htons((uint16_t)port);
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

/* A fixed-size JSON writer; avoiding stdio keeps the helper inside the octal transfer bound. */
static char json[24576];
static size_t out_size = 0;

static void put(const char *text) {
    while (*text && out_size < sizeof(json) - 1) json[out_size++] = *text++;
}

static void put_int(int64_t value) {
    char digits[24];
    size_t count = 0;
    uint64_t magnitude = value < 0 ? (uint64_t)0 - (uint64_t)value : (uint64_t)value;
    if (value < 0) put("-");
    do { digits[count++] = (char)('0' + magnitude % 10u); magnitude /= 10u; } while (magnitude);
    while (count && out_size < sizeof(json) - 1) json[out_size++] = digits[--count];
}

static void key(const char *name, int64_t value) {
    put(",\""); put(name); put("\":"); put_int(value);
}

static void flag(const char *name, bool value) {
    put(",\""); put(name); put(value ? "\":true" : "\":false");
}

static void pairs(unsigned int (*items)[2], unsigned int count, bool skip_zero) {
    put("[");
    for (unsigned int i = 0; i < count; ++i) {
        put(i ? ",[" : "["); put_int(items[i][0]);
        if (!skip_zero || items[i][1]) { put(","); put_int(items[i][1]); }
        put("]");
    }
    put("]");
}

static int report(const char *outcome, bool connected, bool subscribed, bool unsubscribed) {
    int64_t current = now_ms();
    put("{\"outcome\":\""); put(outcome); put("\",\"error_code\":\""); put(failure); put("\"");
    flag("connected", connected); flag("subscribe_sent", subscribed);
    flag("unsubscribe_sent", unsubscribed);
    key("frames", frames); key("audio_frames", audio_frames); key("other_frames", other_frames);
    key("bytes", (int64_t)total_bytes); key("first_audio_ms", first_audio);
    key("elapsed_ms", current < 0 ? -1 : current - started);
    put(",\"audio_exported\":false,\"channels\":[");
    for (unsigned int i = 0; i < channel_count; ++i) {
        const Channel *c = &channels[i];
        uint64_t expected = c->rate * c->duration / 1000u;
        put(i ? ",{\"name\":\"" : "{\"name\":\""); put(c->name); put("\"");
        key("type", (int64_t)c->type); key("sample_rate", (int64_t)c->rate);
        key("format", (int64_t)c->format); key("frames", (int64_t)c->frames);
        key("bytes", (int64_t)c->bytes); key("duration_ms", (int64_t)c->duration);
        key("bytes_per_sample_estimate",
            expected ? (int64_t)((c->bytes + expected / 2u) / expected) : -1);
        key("peak_s16", (int64_t)c->peak);
        key("mean_square_s16", (int64_t)(c->samples ? c->squares / c->samples : 0));
        put("}");
    }
    put("],\"other\":[");
    for (unsigned int i = 0; i < meta_count; ++i) {
        Meta *m = &metas[i];
        put(i ? ",{\"payload\":\"" : "{\"payload\":\""); put(m->payload); put("\"");
        key("length", m->length); key("kind", m->kind);
        put(",\"top\":"); pairs(m->top, m->top_count, false);
        put(",\"paths\":"); pairs(m->paths, m->path_count, true);
        put("}");
    }
    put("]}\n");
    for (size_t offset = 0; offset < out_size;) {
        ssize_t n = write(STDOUT_FILENO, json + offset, out_size - offset);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        offset += (size_t)n;
    }
    return strcmp(outcome, "complete") == 0 ? 0 : 1;
}

/* IOChannelRequest{field: {}} in the packed envelope, or as a full QuasarMessage{159: ...}. */
static size_t channel_request(unsigned char *frame, int preamble, unsigned int field) {
    unsigned char tag = (unsigned char)(field << 3 | 2u);
    const unsigned char full[] = {5, 0, 0, 0, 0xfa, 0x09, 2, tag, 0};
    const unsigned char packed[] = {9, 0, 0, 0, 0xe2, 0x0f, 2, tag, 0, 0xe8, 0x0f, 0x9f, 0x01};
    if (preamble == FULL) { memcpy(frame, full, sizeof(full)); return sizeof(full); }
    memcpy(frame, packed, sizeof(packed));
    return sizeof(packed);
}

static bool number(const char *text, unsigned int low, unsigned int high, unsigned int *out) {
    *out = 0;
    if (!*text || strlen(text) > 5 || (text[0] == '0')) return false;
    for (const char *p = text; *p; ++p) {
        if (*p < '0' || *p > '9') return false;
        *out = *out * 10u + (unsigned int)(*p - '0');
    }
    return *out >= low && *out <= high;
}

int main(int argc, char **argv) {
    unsigned int port, seconds, request;
    int preamble = -1;
    if (argc != 6 || strcmp(argv[1], "--levels") != 0 || !number(argv[2], 1024, 65535, &port) ||
        !number(argv[4], 1, 5, &seconds)) goto usage;
    if (strcmp(argv[3], "main") == 0) request = 1;
    else if (strcmp(argv[3], "vqe") == 0) request = 3;
    else if (strcmp(argv[3], "raw") == 0) request = 4;
    else goto usage;
    for (int i = 0; i < 4; ++i)
        if (strcmp(argv[5], PREAMBLES[i]) == 0) preamble = i;
    if (preamble < 0) goto usage;
    started = now_ms();
    if (started < 0) { failure = "clock_error"; return report("blocked", false, false, false); }
    struct sigaction action = {0};
    action.sa_handler = stop;
    (void)sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) || sigaction(SIGTERM, &action, NULL) ||
        sigaction(SIGHUP, &action, NULL) || signal(SIGPIPE, SIG_IGN) == SIG_ERR) {
        failure = "signal_error";
        return report("blocked", false, false, false);
    }
    int64_t window = started + (int64_t)seconds * 1000;
    int fd = connect_ipc(port, started + 2000);
    if (fd < 0) { failure = "ipc_unavailable"; return report("blocked", false, false, false); }
    bool requested = preamble != LISTEN, opened = true;
    unsigned char message[16];
    if (requested) {
        size_t size = channel_request(message, preamble, request);
        opened = (preamble != HUB || write_all(fd, HUB_STATE, sizeof(HUB_STATE), started + 2000))
                 && write_all(fd, message, size, started + 2000);
    }
    size_t total = 0;
    unsigned char header[4];
    while (opened && frames < FRAMES_MAX) {
        if (!read_exact(fd, header, sizeof(header), window)) break;
        uint32_t size = (uint32_t)header[0] | (uint32_t)header[1] << 8 |
                        (uint32_t)header[2] << 16 | (uint32_t)header[3] << 24;
        if (size == 0 || size > FRAME_MAX || total > STREAM_MAX - size - 4u) {
            failure = "frame_limit"; break;
        }
        total += (size_t)size + 4u;
        if (!read_exact(fd, buffer, size, window)) break;
        frames++;
        total_bytes = total;
        int kind = observe((Slice){buffer, size});
        if (kind != 1) describe((Slice){buffer, size});
        if (kind < 0) break;
        if (kind == 0) { other_frames++; continue; }
        if (first_audio < 0) first_audio = now_ms() - started;
        audio_frames++;
    }
    if (frames >= FRAMES_MAX) failure = "frame_limit";
    bool window_done = opened && strcmp(failure, "timeout") == 0;
    const char *reason = failure;
    /* Unsubscribe is attempted even after a failure, but never retried or reconnected. */
    interrupted = 0;  /* A termination signal still gets one bounded unsubscribe attempt. */
    bool unsubscribed = false;
    if (requested && opened) {
        size_t size = channel_request(message, preamble, 6);
        unsubscribed = write_all(fd, message, size, now_ms() + 500);
    }
    (void)close(fd);
    failure = reason;
    if (window_done) failure = audio_frames ? "none" : "no_audio_frames";
    if (!opened && strcmp(failure, "timeout") == 0) failure = "ipc_io_error";
    bool ok = window_done && audio_frames && (unsubscribed || !requested);
    if (window_done && audio_frames && requested && !unsubscribed)
        failure = "unsubscribe_unverified";
    return report(ok ? "complete" : "unknown", true, requested && opened, unsubscribed);
usage:
    {
        static const char text[] = "Usage: io-channel-levels --levels PORT main|vqe|raw "
                                   "SECONDS none|listen_only|full_envelope|hub_retrieve_state\n";
        (void)!write(STDERR_FILENO, text, sizeof(text) - 1);
    }
    return 2;
}
