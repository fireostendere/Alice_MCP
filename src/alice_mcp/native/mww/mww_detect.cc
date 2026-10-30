/* microWakeWord detector: stdin raw 16 kHz S16LE mono PCM -> stdout JSON lines.
 *
 * Features: TFLM microfrontend sources from pymicro-features 2.0.2 (the exact
 * extractor microWakeWord trains with), 30 ms window, 10 ms step, 40 channels.
 * Inference: TFLite Micro reference kernels; streaming int8 models with internal
 * resource-variable state are compiled into this binary by mww_build.py, so one
 * hash pin covers code and weights. Opens no files or sockets, writes only
 * stdout (typed JSON lines) and stderr (TFLM diagnostics), stops at EOF, signal,
 * --max-seconds of audio or a wall-clock alarm. Detections are untrusted data.
 */
#include <errno.h>
#include <math.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "tensorflow/lite/experimental/microfrontend/lib/frontend.h"
#include "tensorflow/lite/experimental/microfrontend/lib/frontend_util.h"
#include "tensorflow/lite/micro/micro_allocator.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/micro/micro_resource_variable.h"
#include "tensorflow/lite/micro/schema/schema_generated.h"

struct MwwModel {
  const char *name;
  const unsigned char *data;
  unsigned size;
  const char *sha256;
};
#include "mww_models.h" /* generated: kModels[], kModelCount */

namespace {
constexpr int kRate = 16000, kStep = 160, kFeatures = 40;
constexpr size_t kArena = 160 * 1024, kVarArena = 4096;
constexpr int kMaxWindow = 50;
volatile sig_atomic_t g_stop = 0;
void on_signal(int sig) {
  if (sig == SIGALRM) _exit(124); /* wall-clock bound, even inside a stuck read */
  g_stop = 1;
}

struct Detector {
  const MwwModel *model;
  tflite::MicroInterpreter *interpreter;
  TfLiteTensor *input;
  int stride, filled;
  float in_scale;
  int in_zero;
  uint8_t window[kMaxWindow];
  int window_count, window_pos;
  int64_t cooldown_until_ms;
  unsigned detections;
  alignas(16) uint8_t arena[kArena];
  alignas(16) uint8_t var_arena[kVarArena];
};

bool parse_number(const char *text, double low, double high, double *out) {
  char *end = nullptr;
  errno = 0;
  double value = strtod(text, &end);
  if (errno || end == text || *end || !(value >= low && value <= high)) return false;
  *out = value;
  return true;
}

void usage_error(const char *what) {
  printf("{\"event\":\"error\",\"error\":\"%s\"}\n", what);
  fflush(stdout);
}

int8_t quantize(uint16_t feature, float scale, int zero) {
  /* pymicro-features scale (1/25.6, exact in binary) then the model's input params,
   * round-half-even in double; identical to the Python reference. */
  double value = rint((double)((float)feature * 0.0390625f) / (double)scale) + zero;
  return (int8_t)(value < -128 ? -128 : value > 127 ? 127 : value);
}
}  // namespace

int main(int argc, char **argv) {
  double threshold = 0.98, window_arg = 5, cooldown_arg = 2500, max_seconds = 0, gain_db = 0;
  bool trace = false;
  for (int i = 1; i < argc; i++) {
    const char *next = i + 1 < argc ? argv[i + 1] : nullptr;
    bool ok = true;
    if (!strcmp(argv[i], "--trace")) {
      trace = true;
      continue;
    } else if (!strcmp(argv[i], "--threshold") && next) {
      ok = parse_number(next, 0.01, 1.0, &threshold);
    } else if (!strcmp(argv[i], "--window") && next) {
      ok = parse_number(next, 1, kMaxWindow, &window_arg) && window_arg == floor(window_arg);
    } else if (!strcmp(argv[i], "--cooldown-ms") && next) {
      ok = parse_number(next, 0, 60000, &cooldown_arg);
    } else if (!strcmp(argv[i], "--gain-db") && next) {
      ok = parse_number(next, 0, 30, &gain_db); /* calibration knob; PCAN handles most level */
    } else if (!strcmp(argv[i], "--max-seconds") && next) {
      ok = parse_number(next, 1, 86400, &max_seconds);
    } else {
      ok = false;
    }
    if (!ok) {
      usage_error("usage");
      return 2;
    }
    i++;
  }
  if (max_seconds <= 0) {
    usage_error("max_seconds_required");
    return 2;
  }
  struct sigaction action = {};
  action.sa_handler = on_signal;
  sigemptyset(&action.sa_mask);
  if (sigaction(SIGTERM, &action, nullptr) || sigaction(SIGINT, &action, nullptr) ||
      sigaction(SIGALRM, &action, nullptr) || signal(SIGPIPE, SIG_DFL) == SIG_ERR) {
    return 2;
  }
  /* Live audio arrives in real time; allow slack for startup and slow consumers. */
  alarm((unsigned)max_seconds + 60);

  FrontendConfig config;
  config.window.size_ms = 30;
  config.window.step_size_ms = 10;
  config.filterbank.num_channels = kFeatures;
  config.filterbank.lower_band_limit = 125.0f;
  config.filterbank.upper_band_limit = 7500.0f;
  config.noise_reduction.smoothing_bits = 10;
  config.noise_reduction.even_smoothing = 0.025f;
  config.noise_reduction.odd_smoothing = 0.06f;
  config.noise_reduction.min_signal_remaining = 0.05f;
  config.pcan_gain_control.enable_pcan = 1;
  config.pcan_gain_control.strength = 0.95f;
  config.pcan_gain_control.offset = 80.0f;
  config.pcan_gain_control.gain_bits = 21;
  config.log_scale.enable_log = 1;
  config.log_scale.scale_shift = 6;
  static FrontendState frontend;
  if (!FrontendPopulateState(&config, &frontend, kRate)) {
    usage_error("frontend");
    return 3;
  }

  static tflite::MicroMutableOpResolver<24> resolver;
  if (resolver.AddCallOnce() || resolver.AddVarHandle() || resolver.AddReadVariable() ||
      resolver.AddAssignVariable() || resolver.AddReshape() || resolver.AddStridedSlice() ||
      resolver.AddConcatenation() || resolver.AddConv2D() || resolver.AddDepthwiseConv2D() ||
      resolver.AddFullyConnected() || resolver.AddLogistic() || resolver.AddQuantize() ||
      resolver.AddDequantize() || resolver.AddMul() || resolver.AddAdd() || resolver.AddMean() ||
      resolver.AddAveragePool2D() || resolver.AddMaxPool2D() || resolver.AddPad() ||
      resolver.AddPack() || resolver.AddSplitV() || resolver.AddSplit() ||
      resolver.AddTranspose()) {
    usage_error("resolver");
    return 3;
  }
  static Detector detectors[kModelCount];
  printf("{\"event\":\"ready\",\"rate\":%d,\"threshold\":%.4f,\"window\":%d,"
         "\"cooldown_ms\":%d,\"gain_db\":%g,\"models\":[",
         kRate, threshold, (int)window_arg, (int)cooldown_arg, gain_db);
  for (int m = 0; m < kModelCount; m++) {
    Detector &d = detectors[m];
    d.model = &kModels[m];
    const tflite::Model *model = tflite::GetModel(d.model->data);
    if (model->version() != TFLITE_SCHEMA_VERSION) {
      usage_error("schema");
      return 3;
    }
    tflite::MicroAllocator *variables_allocator =
        tflite::MicroAllocator::Create(d.var_arena, kVarArena);
    tflite::MicroResourceVariables *variables =
        variables_allocator ? tflite::MicroResourceVariables::Create(variables_allocator, 20)
                            : nullptr;
    tflite::MicroAllocator *allocator = tflite::MicroAllocator::Create(d.arena, kArena);
    if (!variables || !allocator) {
      usage_error("arena");
      return 3;
    }
    d.interpreter = new tflite::MicroInterpreter(model, resolver, allocator, variables);
    if (d.interpreter->AllocateTensors() != kTfLiteOk) {
      usage_error("allocate");
      return 3;
    }
    d.input = d.interpreter->input(0);
    TfLiteTensor *output = d.interpreter->output(0);
    if (d.input->type != kTfLiteInt8 || output->type != kTfLiteUInt8 ||
        d.input->dims->size != 3 || d.input->dims->data[0] != 1 ||
        d.input->dims->data[2] != kFeatures || d.input->dims->data[1] < 1 ||
        d.input->dims->data[1] > 10 || output->params.zero_point != 0 ||
        output->params.scale != 1.0f / 256.0f) { /* TFLite uint8 LOGISTIC output */
      usage_error("model_shape");
      return 3;
    }
    d.stride = d.input->dims->data[1];
    d.in_scale = d.input->params.scale;
    d.in_zero = d.input->params.zero_point;
    printf("%s{\"name\":\"%s\",\"sha256\":\"%s\",\"stride\":%d,\"arena_bytes\":%u}",
           m ? "," : "", d.model->name, d.model->sha256, d.stride,
           (unsigned)d.interpreter->arena_used_bytes());
  }
  printf("]}\n");
  fflush(stdout);

  const int window = (int)window_arg;
  const double gain = pow(10.0, gain_db / 20.0);
  const uint64_t limit_samples = (uint64_t)(max_seconds * kRate);
  uint64_t samples = 0;
  int16_t chunk[kStep];
  size_t have = 0;
  const char *reason = "eof";
  while (!g_stop) {
    if (samples >= limit_samples) {
      reason = "limit";
      break;
    }
    ssize_t got = read(STDIN_FILENO, (char *)chunk + have, sizeof(chunk) - have);
    if (got < 0 && errno == EINTR) continue;
    if (got < 0) {
      reason = "read_error";
      break;
    }
    if (got == 0) break;
    have += (size_t)got;
    if (have < sizeof(chunk)) continue;
    have = 0;
    samples += kStep;
    if (gain != 1.0) {
      for (int k = 0; k < kStep; k++) { /* saturating, round half to even like numpy */
        double v = rint(chunk[k] * gain);
        chunk[k] = (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
      }
    }
    size_t used = 0;
    FrontendOutput features = FrontendProcessSamples(&frontend, chunk, kStep, &used);
    if (used != (size_t)kStep) {
      reason = "frontend";
      break;
    }
    if (features.size != (size_t)kFeatures) continue; /* first two hops fill the window */
    int64_t t_ms = (int64_t)(samples * 1000 / kRate);
    for (int m = 0; m < kModelCount; m++) {
      Detector &d = detectors[m];
      int8_t *row = d.input->data.int8 + d.filled * kFeatures;
      for (int f = 0; f < kFeatures; f++) row[f] = quantize(features.values[f], d.in_scale, d.in_zero);
      if (++d.filled < d.stride) continue;
      d.filled = 0;
      if (d.interpreter->Invoke() != kTfLiteOk) {
        usage_error("invoke");
        return 3;
      }
      uint8_t q = d.interpreter->output(0)->data.uint8[0];
      if (trace) printf("{\"event\":\"p\",\"model\":\"%s\",\"t_ms\":%lld,\"q\":%u}\n",
                        d.model->name, (long long)t_ms, (unsigned)q);
      d.window[d.window_pos] = q;
      d.window_pos = (d.window_pos + 1) % window;
      if (d.window_count < window) d.window_count++;
      if (d.window_count < window || t_ms < d.cooldown_until_ms) continue;
      unsigned sum = 0, peak = 0;
      for (int k = 0; k < window; k++) {
        sum += d.window[k];
        if (d.window[k] > peak) peak = d.window[k];
      }
      /* Double math in the same order as mww_eval.detect, so both agree at the boundary. */
      double mean = (double)sum / window / 256.0;
      if (mean < threshold) continue;
      d.detections++;
      d.cooldown_until_ms = t_ms + (int64_t)cooldown_arg;
      d.window_count = 0; /* require a fresh full window after the cooldown */
      printf("{\"event\":\"wake\",\"model\":\"%s\",\"t_ms\":%lld,\"probability\":%.4f,"
             "\"peak\":%.4f,\"last\":%.4f,\"trusted\":false}\n",
             d.model->name, (long long)t_ms, mean, peak / 256.0, q / 256.0);
      fflush(stdout);
    }
    if (trace) fflush(stdout);
  }
  if (g_stop) reason = "signal";
  printf("{\"event\":\"end\",\"reason\":\"%s\",\"audio_ms\":%llu,\"detections\":[", reason,
         (unsigned long long)(samples * 1000 / kRate));
  for (int m = 0; m < kModelCount; m++) printf("%s%u", m ? "," : "", detectors[m].detections);
  printf("]}\n");
  fflush(stdout);
  return strcmp(reason, "read_error") && strcmp(reason, "frontend") ? 0 : 1;
}
