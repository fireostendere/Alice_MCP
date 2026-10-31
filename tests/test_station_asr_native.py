"""Synthetic C API stand-in, including compressed-model file IO through C++ streams."""

import gzip
import json
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src/alice_mcp/native/station_asr.c"


@pytest.mark.parametrize("compressed_library", [False, True])
@pytest.mark.parametrize("seconds", [10, 3600])
def test_native_asr_reads_compressed_model_and_rejects_odd_pcm(
    tmp_path: Path, compressed_library: bool, seconds: int,
) -> None:
    cc, cxx = shutil.which("cc"), shutil.which("c++")
    if not cc or not cxx:
        pytest.skip("C/C++ compilers required for the native stand-in")
    model = tmp_path / "model"
    model.mkdir()
    (model / "weights").write_bytes(gzip.compress(b"synthetic model\n", mtime=0))
    stub = tmp_path / "stub.cc"
    stub.write_text(r'''
#include <fstream>
#include <string>
#include <fcntl.h>
#include <cstdlib>
static int model_loads = 0, decoder_bytes = 0;
extern "C" {
void vosk_set_log_level(int) {}
void *vosk_model_new(const char *path) {
    if (++model_loads != 1) return nullptr;
    // The bounded microphone writer must survive the decoder's first slow chunk.
    if (fcntl(0, F_GETPIPE_SZ) < 1024 * 1024) return nullptr;
    std::ifstream in(std::string(path) + "/weights"); std::string line;
    std::getline(in, line); return line == "synthetic model" ? (void*)1 : nullptr;
}
void vosk_model_free(void*) {}
void *vosk_recognizer_new(void*, float) { decoder_bytes = 0; return (void*)2; }
void *vosk_recognizer_new_grm(void*, float, const char*) { decoder_bytes = 0; return (void*)3; }
void vosk_recognizer_free(void*) {}
int vosk_recognizer_accept_waveform(void*, const char*, int n) {
    decoder_bytes += n;
    if (getenv("LONG_UTTERANCE")) return decoder_bytes > 30 * 32000 ? -1 : 0;
    return 1;
}
const char *vosk_recognizer_result(void *r) {
    return r == (void*)3 ? "{\"text\":\"[unk] стоп\"}" :
                          "{\n  \"text\" : \"synthetic command\"\n}";
}
const char *vosk_recognizer_partial_result(void*) { return "{\"partial\":\"\"}"; }
const char *vosk_recognizer_final_result(void*) { return "{\"text\":\"\"}"; }
}
''')
    library, binary = tmp_path / "libstub.so", tmp_path / "recognizer"
    subprocess.run([cxx, "-shared", "-fPIC", str(stub), "-o", str(library)], check=True)
    if compressed_library:
        data = gzip.compress(library.read_bytes(), mtime=0)
        library = library.with_suffix(".so.gz")
        library.write_bytes(data)
    subprocess.run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    "-Wl,--export-dynamic", str(SOURCE), "-lz", "-ldl", "-o", str(binary)],
                   check=True)
    args = [str(binary), str(library), str(model), str(seconds), "pcm16"]
    run = subprocess.run(args, input=bytes(3200), capture_output=True, timeout=10)
    assert run.returncode == 0, run.stderr
    events = [json.loads(line) for line in run.stdout.splitlines()]
    assert events[0]["event"] == "ready"
    assert events[1]["result"]["text"] == "synthetic command"
    assert events[1]["trusted"] is False
    assert events[-1]["audio_ms"] == 100
    assert events[-1]["event"] == "end"
    invalid = subprocess.run([*args[:3], "3601", "pcm16"], input=b"", capture_output=True)
    assert invalid.returncode == 2
    bad = subprocess.run(args, input=b"\0", capture_output=True, timeout=10)
    assert bad.returncode != 0
    assert b'"event":"end"' not in bad.stdout
    # Mode changes preserve the model and audio clock, without a second recognizer.
    with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE) as live:
        assert live.stdin is not None and live.stdout is not None
        assert json.loads(live.stdout.readline())["event"] == "ready"
        live.send_signal(signal.SIGUSR1)
        live.stdin.write(bytes(3200))
        live.stdin.flush()
        interrupted = json.loads(live.stdout.readline())
        assert interrupted["event"] == "interrupt_final"
        assert interrupted["result"]["text"] == "[unk] стоп"
        live.send_signal(signal.SIGUSR2)
        live.stdin.write(bytes(3200))
        live.stdin.flush()
        assert json.loads(live.stdout.readline())["result"]["text"] == "synthetic command"
        live.stdin.close()
        tail = [json.loads(line) for line in live.stdout]
        assert tail[-1]["event"] == "end" and tail[-1]["audio_ms"] == 200
        assert live.wait(timeout=5) == 0
    if seconds == 3600:
        # Continuous noise must not grow one decoder until the station OOM-kills it.
        long = subprocess.run(args, input=bytes(31 * 32000), capture_output=True,
                              env={"LONG_UTTERANCE": "1"}, timeout=10)
        assert long.returncode == 0, long.stderr
        events = [json.loads(line) for line in long.stdout.splitlines()]
        assert events[-1]["audio_ms"] == 31000
        assert any(e["event"] == "final" and e["audio_ms"] == 30000 for e in events)
    (model / "weights").write_bytes(b"not gzip")
    corrupt = subprocess.run(args, input=b"", capture_output=True, timeout=10)
    assert corrupt.returncode != 0
