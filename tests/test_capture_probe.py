"""Synthetic capture-coexistence facts; never opens PCM nodes, captures or touches Quasar."""

import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from alice_mcp import capture_probe, core

SENTINEL = "SENSITIVE_SENTINEL"


def _write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def _station_tree(tmp_path: Path) -> None:
    pcm = tmp_path / "proc/asound/card0/pcm2c"
    _write(pcm / "info", "card: 0\ndevice: 2\nsubdevice: 0\nstream: CAPTURE\n"
           "id: PDM-dummy multicodec-2\nname: \nsubdevices_count: 1\nsubdevices_avail: 0\n")
    _write(pcm / "sub0/status", "state: RUNNING\nowner_pid   : 123\nhw_ptr      : 1000\n")
    _write(pcm / "sub0/hw_params", "access: RW_INTERLEAVED\nformat: S32_LE\n"
           "subformat: STD\nchannels: 4\nrate: 48000 (48000/1)\nperiod_size: 768\n"
           "buffer_size: 3072\n")
    idle = tmp_path / "proc/asound/card0/pcm3c"
    _write(idle / "info", "card: 0\ndevice: 3\nid: loopback;" + SENTINEL + "\n"
           "subdevices_count: 2\nsubdevices_avail: 2\n")
    _write(idle / "sub0/status", "closed\n")
    _write(idle / "sub0/hw_params", "closed\n")
    _write(tmp_path / "proc/asound/card0/pcm0p/info", "stream: PLAYBACK\n")
    for pid, role, links in (("123", "--platformd", {"5": "/dev/snd/pcmC0D2c",
                                                      "6": "socket:[777]"}),
                             ("456", "--aliced", {"3": "socket:[888]"}),
                             ("999", "other", {"1": "/dev/null"})):
        _write(tmp_path / f"proc/{pid}/cmdline", f"maind\0{role}\0")
        _write(tmp_path / f"proc/{pid}/environ", f"PATH=/bin\0TOKEN={SENTINEL}\0")
        for fd, target in links.items():
            (tmp_path / f"proc/{pid}/fd").mkdir(parents=True, exist_ok=True)
            os.symlink(target, tmp_path / f"proc/{pid}/fd/{fd}")
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt" \
             "   uid  timeout inode\n"
    _write(tmp_path / "proc/net/tcp", header
           + "   0: 0100007F:2693 00000000:0000 0A 00000000:00000000 00:00000000 "
             "00000000     0        0 888 1 0 100 0 0 10 0\n"
           + "   1: 00000000:07A9 00000000:0000 0A 00000000:00000000 00:00000000 "
             "00000000     0        0 777 1 0 100 0 0 10 0\n"
           + "   2: 0100007F:2693 0A0B0C0D:D431 01 00000000:00000000 00:00000000 "
             "00000000     0        0 999 1 0 100 0 0 10 0\n")
    _write(tmp_path / "proc/net/tcp6", header)
    _write(tmp_path / "proc/sysvipc/shm",
           "       key      shmid perms       size  cpid  lpid nattch   uid   gid\n"
           "      1024          1   600       4096   123   123      1     0     0\n")
    _write(tmp_path / "sys/fs/selinux/enforce", "1\n")
    _write(tmp_path / "etc/asound.conf",
           "pcm.dmixer {\n  type dmix\n  ipc_key 1024\n  slave { pcm \"hw:0,2\" }\n}\n"
           "pcm.!default {\n  type plug\n  slave.pcm \"dmixer\"\n}\n"
           "pcm.dmicarray {\n  type hw\n  card 0\n  device 2\n}\n")
    _write(tmp_path / "system/vendor/quasar/quasar.cfg",
           '{"aliced": {"port": 9875}, "audiod": {"inRate": 48000, "outRate": 16000, '
           '"micChannels": 4, "spkChannels": 2, "device": "dmicarray", "nested": {"port": 1}, '
           '"private_label": "' + SENTINEL + '"}, "audiosender": {"availableChannels": '
           '["raw_mic_0", "raw_spk_0", "vqe_0"], "historySeconds": 10}, '
           '"glagold": {"port": 1961}}\n')
    _write(tmp_path / "system/vendor/quasar/maind",
           b"\x7fELF\0quasar.proto.IOChannelRequest\0quasar.proto.IOAudioChannelsData\0")


def _run(tmp_path: Path, command: str) -> str:
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    pidof = shims / "pidof"
    pidof.write_text('#!/bin/sh\ncase "$1" in maind) echo "123 456";; '
                     "quasar_launcher2) echo 77;; *) exit 1;; esac\n")
    pidof.chmod(0o700)
    for prefix in ("/proc/", "/sys/", "/etc/", "/system/"):
        command = command.replace(prefix, str(tmp_path) + prefix)
    env = {**os.environ, "PATH": str(shims) + ":" + os.environ["PATH"]}
    return subprocess.run(["sh"], input=command, text=True, capture_output=True,
                          env=env, timeout=20, check=True).stdout


def test_capture_probe_is_fixed_read_only_and_registered() -> None:
    command = core.PROBES["capture"]
    assert command == capture_probe.COMMAND
    subprocess.run(["sh", "-n", "-c", command], check=True)
    for forbidden in ("arecord", "aplay", "amixer", "kill", "mount", "insmod", "modprobe",
                      "rm ", "q stop", "q restart", "tee ", "LD_PRELOAD", "dd ", "mkdir"):
        assert forbidden not in command


def test_capture_probe_on_synthetic_station(tmp_path: Path) -> None:
    _station_tree(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*")
              if p.is_file() and "shims" not in p.parts}
    output = _run(tmp_path, core.PROBES["capture"])
    assert SENTINEL not in output and "0A0B0C0D" not in output
    facts = core.parse_probe("capture", output + "private=" + SENTINEL + "\n")
    assert SENTINEL not in str(facts)
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*")
                      if p.is_file() and "shims" not in p.parts}
    assert facts["maind_processes"] == 2 and facts["launcher_running"] is True
    assert facts["selinux"] == "enforcing" and facts["sound_cards"] == 1
    mic, idle = facts["capture_pcms"]
    assert mic == {"card": 0, "device": 2, "id": "PDM-dummy_multicodec-2", "state": "running",
                   "subdevices": 1, "subdevices_available": 0, "format": "S32_LE",
                   "channels": 4, "rate": 48000, "period_frames": 768, "buffer_frames": 3072,
                   "holders": 1, "quasar_holders": 1, "quasar_roles": ["platformd"]}
    assert idle["state"] == "closed" and idle["holders"] == 0 and idle["format"] is None
    assert idle["id"] == "unknown"
    assert facts["alsa_config"]["pcms"] == [
        {"name": "dmixer", "type": "dmix", "in_quasar_cfg": False},
        {"name": "default", "type": "plug", "in_quasar_cfg": False},
        {"name": "dmicarray", "type": "hw", "in_quasar_cfg": True}]
    assert facts["alsa_config"]["ipc_keys"] == 1
    assert facts["sysv_shm"] == {"segments": 1, "attached": 1, "alsa_key_matches": 1,
                                 "created_by_quasar": 1}
    assert facts["maind_alsa_config_override"] is False
    cfg = facts["quasar_config"]
    assert cfg["sections"] == {"aliced": True, "audiod": True, "audiosender": True,
                               "glagold": True}
    assert cfg["channels"] == {"vqe_0": True, "raw_mic_0": True, "raw_spk_0": True,
                               "available_channels_key": True}
    assert cfg["audiod"] == {"in_rate": 48000, "out_rate": 16000, "mic_channels": 4,
                             "spk_channels": 2}
    assert cfg["ports"] == {"aliced": 9875, "audiod": None, "audiosender": None,
                            "glagold": 1961}
    assert facts["quasar_listeners"] == [{"role": "aliced", "port": 9875, "scope": "loopback"},
                                         {"role": "platformd", "port": 1961, "scope": "any"}]
    assert facts["io_channel_type_names"] is True
    assert facts["audio_captured"] is False and facts["quasar_changed"] is False
    assert facts["capture_verified"] is False
    paths = facts["capture_paths"]
    assert paths["second_hw_substream"] == "excluded"
    assert paths["alsa_dsnoop_shared"] == "unknown"
    assert paths["vqe_preload_hook"] == "excluded_requires_quasar_restart"
    assert paths["quasar_io_channel_subscription"] == "schema_names_present_untested"


def test_capture_paths_classify_direct_hardware_ownership(tmp_path: Path) -> None:
    _station_tree(tmp_path)
    (tmp_path / "proc/sysvipc/shm").write_text("key shmid perms size cpid lpid nattch\n")
    facts = core.parse_probe("capture", _run(tmp_path, core.PROBES["capture"]))
    assert facts["capture_paths"]["alsa_dsnoop_shared"] == \
        "excluded_without_quasar_reconfiguration"


def test_capture_paths_report_dsnoop_candidate(tmp_path: Path) -> None:
    _station_tree(tmp_path)
    (tmp_path / "etc/asound.conf").write_text(
        "pcm.dmicarray {\n type dsnoop\n ipc_key 1024\n slave.pcm \"hw:0,2\"\n}\n")
    facts = core.parse_probe("capture", _run(tmp_path, core.PROBES["capture"]))
    assert facts["capture_paths"]["alsa_dsnoop_shared"] == "candidate_untested"


def test_capture_probe_tolerates_missing_files(tmp_path: Path) -> None:
    (tmp_path / "proc").mkdir()
    facts = core.parse_probe("capture", _run(tmp_path, core.PROBES["capture"]))
    assert facts["capture_pcms"] == [] and facts["quasar_config"] is None
    assert facts["alsa_config"] is None and facts["io_channel_type_names"] is None
    assert facts["selinux"] == "absent"
    assert facts["capture_paths"]["second_hw_substream"] == "unknown"


def test_capture_probe_uses_bounded_stdin(monkeypatch: Any, tmp_path: Path) -> None:
    _station_tree(tmp_path)
    output = _run(tmp_path, core.PROBES["capture"])
    calls = []

    def runner(station: Any, command: core.SSHInput) -> str:
        calls.append(command)
        assert command.command == "sh -s" and len(command.data) < 120 * 1024
        return output

    controller = core.Controller(None, writes=False, runner=runner)
    monkeypatch.setattr(controller, "station", lambda _: SimpleNamespace(native_mcp={}))
    assert controller.probe("desk", "capture")["data"]["sound_cards"] == 1
    assert len(calls) == 1


@pytest.mark.parametrize("change", [
    ("maind_processes=", "maind_processes=x"),
    ("capture_0_state=running", "capture_0_state=running\ncapture_0_state=closed"),
    ("capture_0_channels=4", "capture_0_channels=99999"),
    ("capture_count=2", "capture_count=3"),
    ("listener_0_port=9875", "listener_0_port=70000"),
    ("alsa_conf_sha256=", "alsa_conf_sha256=zz"),
])
def test_capture_probe_rejects_incompatible_output(tmp_path: Path,
                                                   change: tuple[str, str]) -> None:
    _station_tree(tmp_path)
    output = _run(tmp_path, core.PROBES["capture"])
    assert change[0] in output
    head, tail = output.split(change[0], 1)
    if change[0].endswith("="):
        tail = tail.split("\n", 1)[1]
        broken = head + change[1] + "\n" + tail
    else:
        broken = head + change[1] + tail
    with pytest.raises(core.SafetyError):
        core.parse_probe("capture", broken)


def test_owner_cli_uses_explicit_pinned_ssh(monkeypatch: Any, tmp_path: Path,
                                            capsys: Any) -> None:
    _station_tree(tmp_path)
    output = _run(tmp_path, core.PROBES["capture"])
    seen = []

    class FakeController:
        def __init__(self, inventory: Path, *, writes: bool) -> None:
            assert writes is False
            self.runner = lambda station, command: seen.append((station, command)) or output

        def station(self, alias: str) -> Any:
            return SimpleNamespace(id=alias, native_mcp={"url": "x"})

    from alice_mcp import vqe_experiment
    monkeypatch.setattr(core, "Controller", FakeController)
    monkeypatch.setattr(vqe_experiment, "_ssh", lambda s: SimpleNamespace(native_mcp={}))
    monkeypatch.setenv("ALICE_MCP_INVENTORY", str(tmp_path / "inventory.json"))
    assert capture_probe.main(["--station", "desk"]) == 0
    printed = capsys.readouterr().out
    assert '"evidence_class":"analytical"' in printed and SENTINEL not in printed
    assert len(seen) == 1 and seen[0][0].native_mcp == {}
    assert seen[0][1].command == "sh -s"


def test_device_awk_avoids_concatenation_before_parenthesis() -> None:
    # Station BusyBox 1.26.2 awk parses `var (expr)` as a call to an undefined
    # function and exits, silently dropping the Quasar-held capture PCM.
    calls = re.findall(r"\b([A-Za-z_]\w*) \(", capture_probe.COMMAND)
    assert set(calls) <= {"if", "while", "for", "in", "printf", "print", "return"}
