"""Synthetic VQE interposition: no station, household sound or raw PCM export."""

import json
import os
import select
import shutil
import socket
import struct
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def short_socket_root() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="vqa-", dir="/tmp/opencode") as directory:
        yield Path(directory)


def test_vqe_audit_preserves_original_and_exports_only_statistics(
    tmp_path: Path, short_socket_root: Path,
) -> None:
    compiler = shutil.which("gcc")
    if compiler is None:
        pytest.skip("Host C compiler unavailable")
    original = tmp_path / "original.c"
    original.write_text(r'''
#include <errno.h>
#include <stddef.h>
static float samples[160];
void *YandexVqeC_GetOutputChannelData(void *e, unsigned k, unsigned i,
                                    const float **data, size_t *frames) {
    (void)e; (void)k; (void)i;
    for (unsigned n=0; n<160; n++) samples[n]=0.125f;
    *data=samples; *frames=160; errno=77; return 0;
}
''')
    driver = tmp_path / "driver.c"
    driver.write_text(r'''
#include <errno.h>
#include <stddef.h>
extern void *YandexVqeC_GetOutputChannelData(void *,unsigned,unsigned,const float **,size_t *);
int main(void) {
    for(unsigned n=0;n<128;n++) {
        const float *data=0; size_t frames=0;
        void *error=YandexVqeC_GetOutputChannelData(0,6,0,&data,&frames);
        if(error || errno!=77 || frames!=160 || !data || data[0]!=0.125f) return 1;
    }
    return 0;
}
''')
    prefix = str(short_socket_root / "audit-")
    directory = Path(prefix + "a" * 32)
    directory.mkdir(mode=0o700)
    address = str(directory / "stats.sock")
    if len(address.encode()) >= 108:
        pytest.skip("Synthetic Unix socket path exceeds native ABI")
    source = Path(__file__).resolve().parents[1] / "src/alice_mcp/native/vqe_audit.c"
    commands = [
        [compiler, "-shared", "-fPIC", str(original), "-o", str(tmp_path / "original.so")],
        [compiler, "-shared", "-fPIC", "-O2", "-Wall", "-Wextra", "-Werror",
         '-DAUDIT_PREFIX="' + prefix + '"', str(source), "-ldl", "-o", str(tmp_path / "audit.so")],
        [compiler, str(driver), str(tmp_path / "original.so"), "-o", str(tmp_path / "driver")],
    ]
    for command in commands:
        subprocess.run(command, check=True, capture_output=True, timeout=30)
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(address)
        receiver.settimeout(2)
        env = {**os.environ, "LD_PRELOAD": str(tmp_path / "audit.so"),
               "ALICE_VQE_AUDIT_SOCKET": address}
        result = subprocess.run([str(tmp_path / "driver")], env=env, capture_output=True, timeout=5)
        assert result.returncode == 0 and result.stdout == b"" and result.stderr == b""
        packet = receiver.recv(1024)
        assert len(packet) == 40
        magic, kind, index, frames, calls, invalid, outside, seen, peak, power = struct.unpack(
            "<8I2f", packet,
        )
        assert magic == 0x41565145 and (kind, index, frames) == (6, 0, 160)
        assert calls >= 1 and seen >= 160 and invalid == outside == 0
        assert peak == 0.125 and power == 0.015625
    # Missing receiver and an invalid opt-in path must leave the stock call functional.
    for missing_receiver in (address, "/unapproved/stats.sock"):
        env["ALICE_VQE_AUDIT_SOCKET"] = missing_receiver
        result = subprocess.run([str(tmp_path / "driver")], env=env, capture_output=True, timeout=5)
        assert result.returncode == 0 and result.stdout == result.stderr == b""


def test_vqe_collector_is_bounded_and_removes_only_its_socket(
    tmp_path: Path, short_socket_root: Path,
) -> None:
    compiler = shutil.which("gcc")
    if compiler is None:
        pytest.skip("Host C compiler unavailable")
    prefix = str(short_socket_root / "audit-")
    identifier = "b" * 32
    directory = Path(prefix + identifier)
    directory.mkdir(mode=0o700)
    address = str(directory / "stats.sock")
    executable = tmp_path / "collector"
    source = Path(__file__).resolve().parents[1] / "src/alice_mcp/native/vqe_audit.c"
    subprocess.run([compiler, "-O2", "-Wall", "-Wextra", "-Werror", "-DVQE_AUDIT_COLLECTOR",
                    '-DAUDIT_PREFIX="' + prefix + '"', str(source), "-o", str(executable)],
                   check=True, capture_output=True, timeout=30)
    clock = subprocess.run([str(executable), "--clock"], capture_output=True, check=True, timeout=3)
    assert type(json.loads(clock.stdout)["monotonic_ms"]) is int
    assert not Path(address).exists()
    with subprocess.Popen([str(executable), identifier], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 3)[0]
        assert json.loads(process.stdout.readline(1024))["metadata_only"]
        packet = struct.pack("<8I2f", 0x41565145, 6, 0, 160, 1, 0, 0, 160, 0.125, 0.015625)
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
            sender.settimeout(3)
            for _ in range(4095):
                sender.sendto(packet, address)
            sender.sendto(b"invalid", address)
        output, _ = process.communicate(timeout=5)
        result = json.loads(output)
        assert process.returncode == 0 and result["cleanup_verified"]
        assert result["packets"] == 4095 and result["rejected"] == 1
        assert result["type6_frames"] == 160 and result["peak_millionths"] == 125000
        assert type(result["started_monotonic_ms"]) is int
        assert 1 <= len(result["samples"]) <= 64
        assert result["samples"][0]["peak_millionths"] == 125000
        assert result["samples"][0]["mean_square_billionths"] == 15625000
        assert all(set(sample) == {"t_ms", "calls", "peak_millionths", "mean_square_billionths"}
                   and 0 <= sample["t_ms"] <= 46000 for sample in result["samples"])
        assert not Path(address).exists()
