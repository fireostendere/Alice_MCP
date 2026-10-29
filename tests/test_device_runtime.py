"""Synthetic filesystem checks only; no physical Station compatibility claims."""

import base64
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from alice_mcp import device_runtime as runtime


def put(root, name, data):
    path = root / name.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def setup(root):
    put(root, "/system/vendor/quasar/maind", b"synthetic firmware")
    config = put(root, "/data/opt/player/settings.json", b'{"voice":"private-original","keep":1}')
    policy = {
        "firmware_sha256": hashlib.sha256(b"synthetic firmware").hexdigest(),
        "allowed_actions": sorted(runtime.ACTIONS),
        "configs": {"speaker": {"path": "/data/opt/player/settings.json",
            "pointer": ["voice"], "kind": "voice", "choices": {"new": "private-new"}}},
    }
    return policy, config


def request(policy, action="config.select", params=None):
    return {"schema": "alice.device.v1", "mode": "inspect", "model": "station-lite",
            "policy": policy, "action": action, "params": params or {
                "binding": "speaker", "option": "new"}, "nonce": "a" * 32}


def apply(req, root):
    preview = runtime.dispatch(req, root)
    assert preview["outcome"] == "ready", preview
    return runtime.dispatch(dict(req, mode="apply", expected_sha256=preview["state_sha256"]), root)


def test_preview_readonly_and_private_values_never_returned(tmp_path):
    policy, config = setup(tmp_path)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    result = runtime.dispatch(request(policy), tmp_path)
    assert result["outcome"] == "ready"
    assert "private" not in json.dumps(result)
    assert before == sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    assert json.loads(config.read_text())["voice"] == "private-original"


def test_config_optimistic_checks_backup_restore_and_replay(tmp_path):
    policy, config = setup(tmp_path)
    req = request(policy)
    preview = runtime.dispatch(req, tmp_path)
    config.write_text('{"voice":"changed"}')
    assert runtime.dispatch(dict(req, mode="apply", expected_sha256=preview["state_sha256"]),
                            tmp_path)["outcome"] == "conflict"
    result = apply(req, tmp_path)
    assert result["outcome"] == "applied"
    assert "private" not in json.dumps(result)
    assert json.loads(config.read_text())["voice"] == "private-new"
    restore = request(policy, "restore", {"backup_id": result["backup_id"]})
    stale = runtime.dispatch(restore, tmp_path)
    config.write_text('{"voice":"external"}')
    assert runtime.dispatch(dict(restore, mode="apply", expected_sha256=stale["state_sha256"]),
                            tmp_path)["outcome"] == "conflict"
    config.write_text('{"voice": "private-new"}')
    # Exact modified bytes, not semantic equivalence, are required.
    assert runtime.dispatch(restore, tmp_path)["outcome"] == "conflict"


def test_restore_success(tmp_path):
    policy, config = setup(tmp_path)
    original = config.read_bytes()
    result = apply(request(policy), tmp_path)
    restored = apply(request(policy, "restore", {"backup_id": result["backup_id"]}), tmp_path)
    assert restored["restoration_verified"] is True
    assert config.read_bytes() == original
    assert runtime.dispatch(request(policy, "restore", {"backup_id": result["backup_id"]}),
                            tmp_path)["outcome"] == "blocked"


@pytest.mark.parametrize("change", ["extra", "bool", "secret", "native", "endpoint", "traversal"])
def test_policy_hostile(change):
    p = {"firmware_sha256": "a" * 64, "allowed_actions": []}
    if change == "extra":
        p["shell"] = "id"
    elif change == "bool":
        p["max_volume"] = True
    else:
        binding = {"path": "/data/opt/player/settings.json", "pointer": ["voice"],
                   "kind": "voice", "choices": {"new": "ok"}}
        if change == "secret":
            binding["pointer"] = ["token", "voice"]
        elif change == "native":
            binding["path"] = "/system/vendor/quasar/quasar.cfg"
        elif change == "endpoint":
            binding.update(kind="endpoint", pointer=["url"], choices={"new": "https://" + "u:p@x/"})
        else:
            binding["path"] = "/data/opt/../tokens.json"
        p["configs"] = {"test": binding}
    with pytest.raises(ValueError, match="invalid_policy"):
        runtime.validate_policy(p, "station-lite")


def test_policy_accepts_explicit_preauthorization_only_for_allowed_bounded_actions():
    policy = {"firmware_sha256": "a" * 64,
              "allowed_actions": ["led.effect", "audio.volume", "audio.play"],
              "preauthorized_actions": ["led.effect", "audio.volume", "audio.play"]}
    assert runtime.validate_policy(policy, "station-lite")["preauthorized_actions"] == [
        "audio.play", "audio.volume", "led.effect",
    ]


def test_empty_preauthorization_preserves_existing_policy_and_backup_hashes():
    policy = {"firmware_sha256": "a" * 64, "allowed_actions": ["audio.play"]}
    original = runtime.validate_policy(policy, "station-lite")
    assert "preauthorized_actions" not in original
    assert runtime.validate_policy(
        dict(policy, preauthorized_actions=[]), "station-lite",
    ) == original


@pytest.mark.parametrize("actions", [
    True, "audio.play", [True], ["audio.play", "audio.play"], ["unknown"],
    ["config.select"], ["sound.replace"], ["media.upload"], ["restore"],
    ["led.capture"], ["speech.listen"], ["led.replay"], ["led.pulse"], ["led.animate"],
    ["quasar:restart"],
])
def test_policy_rejects_unsafe_or_malformed_preauthorization(actions):
    policy = {"firmware_sha256": "a" * 64, "allowed_actions": sorted(runtime.ACTIONS),
              "preauthorized_actions": actions}
    with pytest.raises(ValueError, match="invalid_policy"):
        runtime.validate_policy(policy, "station-lite")


def test_preauthorization_cannot_grant_an_action_missing_from_allowlist():
    with pytest.raises(ValueError, match="invalid_policy"):
        runtime.validate_policy({"firmware_sha256": "a" * 64, "allowed_actions": [],
                                 "preauthorized_actions": ["audio.play"]}, "station-lite")


@pytest.mark.parametrize("action,params", [
    ("audio.volume", {"percent": True}), ("audio.volume", {"percent": 31}),
    ("led.effect", {"red": 33, "green": 0, "blue": 0, "count": 1, "period_ms": 500}),
    ("restore", {"backup_id": "../x"}), ("audio.play", {"asset": "url", "duration_seconds": 1}),
    ("audio.volume", {"percent": 1, "shell": "id"}),
])
def test_parameters_hostile(action, params):
    with pytest.raises(ValueError, match="invalid_parameters"):
        runtime.validate_parameters(action, params, {}, "station-lite")


def test_symlink_rejected(tmp_path):
    policy, config = setup(tmp_path)
    config.rename(config.with_suffix(".old"))
    config.symlink_to(config.with_suffix(".old"))
    assert runtime.dispatch(request(policy), tmp_path)["outcome"] == "blocked"


def test_led_restores_on_failure(tmp_path, monkeypatch):
    policy, _ = setup(tmp_path)
    nodes = []
    for color in ("red", "green", "blue"):
        for ring in range(1, 4):
            base = f"/sys/class/leds/micro:{color}:ring{ring}"
            put(tmp_path, base + "/max_brightness", b"255")
            put(tmp_path, base + "/trigger", b"[none] timer")
            nodes.append(put(tmp_path, base + "/brightness", b"7"))
    def fail(_):
        raise OSError("private failure")
    monkeypatch.setattr(runtime.time, "sleep", fail)
    result = apply(request(policy, "led.effect", {"red": 2, "green": 3, "blue": 4,
                                                 "count": 1, "period_ms": 500}), tmp_path)
    assert result["outcome"] == "unknown"
    assert result["restoration_verified"] is True
    assert all(node.read_bytes() == b"7" for node in nodes)


def test_root_redirect_does_not_execute(tmp_path, monkeypatch):
    policy, _ = setup(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("real subprocess attempted")
    monkeypatch.setattr(subprocess, "run", forbidden)
    assert runtime.dispatch(request(policy, "audio.volume", {"percent": 2}),
                            tmp_path)["outcome"] == "blocked"


def test_standalone_duplicate_json_and_sanitization():
    source = Path(runtime.__file__).read_text()
    payload = base64.b64encode(b'{"schema":"alice.device.v1","schema":"private"}').decode()
    result = subprocess.run([sys.executable, "-I", "-c", source, payload],
                            capture_output=True, text=True, check=True)
    parsed = json.loads(result.stdout)
    assert parsed["outcome"] == "blocked"
    assert "private" not in result.stdout
    assert not result.stderr


def test_media_upload_and_restore_created_file(tmp_path):
    policy, _ = setup(tmp_path)
    data = b"RIFF\x04\x00\x00\x00WAVEsynthetic"
    (tmp_path / "data/sounds").mkdir()
    policy["media"] = {"chime": {"path": "/data/sounds/chime.wav",
                                 "sha256": hashlib.sha256(data).hexdigest()}}
    params = {"asset": "chime", "content_base64": base64.b64encode(data).decode()}
    result = apply(request(policy, "media.upload", params), tmp_path)
    target = tmp_path / "data/sounds/chime.wav"
    assert target.read_bytes() == data
    assert apply(request(policy, "restore", {"backup_id": result["backup_id"]}),
                 tmp_path)["restoration_verified"]
    assert not target.exists()
    params["content_base64"] = base64.b64encode(b"#!/bin/sh\n").decode()
    with pytest.raises(ValueError):
        runtime.validate_parameters("media.upload", params, policy, "station-lite")


def test_volume_persistent_restore_with_mocked_mixer(tmp_path, monkeypatch):
    policy, _ = setup(tmp_path)
    volume = [22]
    calls = []
    def command(root, args):
        calls.append(args)
        if args[1] == "sset":
            volume[0] = int(args[-1][:-1])
        return f"  Front Left: Playback 1 [{volume[0]}%]\n".encode()
    monkeypatch.setattr(runtime, "_command", command)
    result = apply(request(policy, "audio.volume", {"percent": 12}), tmp_path)
    assert volume[0] == 12
    assert apply(request(policy, "restore", {"backup_id": result["backup_id"]}),
                 tmp_path)["restoration_verified"]
    assert volume[0] == 22
    assert all(args[:3] in (["amixer", "sget", "Master"], ["amixer", "sset", "Master"])
               for args in calls)
    monkeypatch.setattr(runtime, "_command", lambda *args: b"[10%] [11%]")
    assert runtime.dispatch(request(policy, "audio.volume", {"percent": 2}),
                            tmp_path)["outcome"] == "blocked"


def test_framebuffer_and_geometry_failclosed(tmp_path, monkeypatch):
    policy, _ = setup(tmp_path)
    frame = put(tmp_path, "/dev/fb0", bytes(range(18)))
    for field, value in (("virtual_size", b"6,1"), ("bits_per_pixel", b"24"), ("stride", b"18")):
        put(tmp_path, "/sys/class/graphics/fb0/" + field, value)
    req = request(policy, "led.effect", {"red": 2, "green": 3, "blue": 4,
                                         "count": 1, "period_ms": 500})
    req["model"] = "station-mini3"
    observed = []
    monkeypatch.setattr(runtime.time, "sleep", lambda _: observed.append(frame.read_bytes()))
    assert apply(req, tmp_path)["restoration_verified"]
    assert observed[0] == bytes([4, 3, 2]) * 6
    assert frame.read_bytes() == bytes(range(18))
    put(tmp_path, "/sys/class/graphics/fb0/stride", b"24")
    assert runtime.dispatch(req, tmp_path)["outcome"] == "blocked"


def test_native_endpoint_only_and_no_value_leak(tmp_path):
    policy, _ = setup(tmp_path)
    target = put(tmp_path, "/system/vendor/quasar/quasar.cfg",
                 b'{"uniProxyUrl":"https://old.invalid/", "other":true}')
    policy["configs"] = {"backend": {"path": runtime.NATIVE, "pointer": ["uniProxyUrl"],
        "kind": "endpoint", "choices": {"chosen": "https://chosen.invalid/"}}}
    result = apply(request(policy, "config.select", {"binding": "backend", "option": "chosen"}),
                   tmp_path)
    assert result["reload_required"] is True
    assert "invalid" not in json.dumps(result)
    assert json.loads(target.read_bytes())["other"] is True


def test_active_trigger_and_unknown_models_no_led_writes(tmp_path):
    policy, _ = setup(tmp_path)
    req = request(policy, "led.effect", {"red": 1, "green": 1, "blue": 1,
                                         "count": 1, "period_ms": 500})
    for model in ("station-mini", "station-midi", "station-lite2"):
        req["model"] = model
        assert runtime.dispatch(req, tmp_path)["outcome"] == "blocked"
    assert not (tmp_path / "data/opt/alice-mcp").exists()


def test_config_duplicate_and_nonfinite_rejected(tmp_path):
    policy, config = setup(tmp_path)
    for raw in (b'{"voice":"a","voice":"b"}', b'{"voice":"a","other":NaN}'):
        config.write_bytes(raw)
        assert runtime.dispatch(request(policy), tmp_path)["outcome"] == "blocked"


def test_backup_parent_symlink_rejected(tmp_path):
    policy, _ = setup(tmp_path)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "data/opt/alice-mcp").symlink_to(target)
    req = request(policy)
    preview = runtime.dispatch(req, tmp_path)
    assert runtime.dispatch(dict(req, mode="apply", expected_sha256=preview["state_sha256"]),
                            tmp_path)["outcome"] == "blocked"
    assert not list(target.iterdir())


def test_empty_policy_and_public_model_facts():
    assert runtime.validate_policy({}, "station-mini3-pro") == {}
    assert runtime.interfaces("station-mini3-pro")["led"]["supported"] is False
    assert runtime.interfaces("station-midi")["gpio"]["device_tree_properties"] == [
        "zb-reset-gpios", "zb-boot-gpios"]


@pytest.mark.parametrize("exited", [False, True])
def test_playback_single_restricted_pipeline_and_only_own_group(tmp_path, monkeypatch, exited):
    policy, _ = setup(tmp_path)
    data = b"OggS" + bytes(24) + b"OpusHead" + bytes(32)
    put(tmp_path, "/data/sounds/chime.opus", data)
    policy["media"] = {"chime": {"path": "/data/sounds/chime.opus",
                                 "sha256": hashlib.sha256(data).hexdigest()}}
    calls = []
    class Process:
        pid = 12345
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))
        def wait(self, timeout):
            if timeout == 1 and not exited:
                raise subprocess.TimeoutExpired("synthetic", timeout)
            return 0
        def poll(self):
            return 0 if exited else None
    monkeypatch.setattr(subprocess, "Popen", Process)
    monkeypatch.setattr(runtime.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)
    temporary_file = tempfile.TemporaryFile
    monkeypatch.setattr(runtime, "tempfile", SimpleNamespace(
        TemporaryFile=lambda **_: temporary_file(dir=tmp_path)))
    # Exercise playback with a fake / root only; no subprocess or device access occurs.
    monkeypatch.setattr(runtime, "_asset", lambda *args: data)
    req = request(policy, "audio.play", {"asset": "chime", "duration_seconds": 1})
    assert runtime._play(Path("/"), req)["status"] == "applied"
    args, kwargs = calls[0]
    assert len(args) == 3
    assert args[:2] == ["/system/vendor/quasar/maind", "--gst-launch"]
    assert args[2].startswith("filesrc location=/proc/self/fd/")
    assert " ! decodebin ! audioconvert ! audioresample ! alsasink device=default" in args[2]
    assert len(kwargs["pass_fds"]) == 1
    assert kwargs["start_new_session"] is True
    assert "shell" not in kwargs
    assert calls[1][0] == 12345


def test_signal_led_restoration(tmp_path, monkeypatch):
    import signal
    policy, _ = setup(tmp_path)
    for color in ("red", "green", "blue"):
        for ring in range(1, 4):
            base = f"/sys/class/leds/micro:{color}:ring{ring}"
            put(tmp_path, base + "/brightness", b"8")
            put(tmp_path, base + "/max_brightness", b"32")
    def interrupt(_):
        signal.raise_signal(signal.SIGTERM)
    old = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(runtime.time, "sleep", interrupt)
    result = apply(request(policy, "led.effect", {"red": 1, "green": 1, "blue": 1,
                                                 "count": 1, "period_ms": 500}), tmp_path)
    assert result["outcome"] == "unknown"
    assert result["restoration_verified"] is True
    assert signal.getsignal(signal.SIGTERM) == old


def test_rootfs_timeout_attempts_readonly_restoration(monkeypatch):
    class State:
        f_flag = runtime.os.ST_RDONLY
    monkeypatch.setattr(runtime.os, "statvfs", lambda _: State())
    calls = []
    def command(root, args):
        calls.append(args)
        if "remount,rw" in args:
            raise subprocess.TimeoutExpired("mount", 5)
        return b""
    monkeypatch.setattr(runtime, "_command", command)
    with pytest.raises(subprocess.TimeoutExpired):
        with runtime._writable(Path("/"), runtime.NATIVE, {"allow_rootfs_remount": True}):
            pytest.fail("failed remount must not reach mutation")
    assert calls == [["mount", "-o", "remount,rw", "/"], ["mount", "-o", "remount,ro", "/"]]


def test_atomic_rechecks_parents_at_mutation_boundary(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        runtime._atomic(link / "settings.json", b"{}", 0o600,
                        runtime.os.geteuid(), runtime.os.getegid())
    assert not list(target.iterdir())


def test_runtime_response_matches_host_whitelist(tmp_path):
    from alice_mcp.controls import parse_response
    policy, _ = setup(tmp_path)
    response = parse_response(json.dumps(runtime.dispatch(request(policy), tmp_path)))
    assert response["outcome"] == "ready"


def test_documented_data_symlink_and_new_media_directory(tmp_path):
    policy, _ = setup(tmp_path)
    canonical = tmp_path / "bkupgrade/data"
    canonical.parent.mkdir()
    (tmp_path / "data").rename(canonical)
    (tmp_path / "data").symlink_to("/bkupgrade/data")
    data = b"RIFF\x04\x00\x00\x00WAVEsynthetic"
    policy["media"] = {"chime": {"path": "/data/sounds/chime.wav",
                                  "sha256": hashlib.sha256(data).hexdigest()}}
    req = request(policy, "media.upload", {
        "asset": "chime", "content_base64": base64.b64encode(data).decode(),
    })
    before = list(canonical.rglob("*"))
    assert runtime.dispatch(req, tmp_path)["outcome"] == "ready"
    assert list(canonical.rglob("*")) == before
    result = apply(req, tmp_path)
    assert result["outcome"] == "applied"
    assert (canonical / "sounds/chime.wav").read_bytes() == data


def test_sysfs_emulation_does_not_retain_old_numeric_suffix(tmp_path, monkeypatch):
    policy, _ = setup(tmp_path)
    nodes = []
    for color in ("red", "green", "blue"):
        for ring in range(1, 4):
            base = f"/sys/class/leds/micro:{color}:ring{ring}"
            nodes.append(put(tmp_path, base + "/brightness", b"100\n"))
            put(tmp_path, base + "/max_brightness", b"255")
    observed = []
    monkeypatch.setattr(runtime.time, "sleep", lambda _: observed.append(
        [int(p.read_bytes()) for p in nodes],
    ))
    result = apply(request(policy, "led.effect", {"red": 8, "green": 8, "blue": 8,
                                                  "count": 1, "period_ms": 500}), tmp_path)
    assert observed[0] == [8] * 9
    assert result["restoration_verified"]
    assert all(p.read_bytes() == b"100\n" for p in nodes)


def test_intervening_edit_while_backup_is_saved_is_not_overwritten(tmp_path, monkeypatch):
    policy, config = setup(tmp_path)
    original_save = runtime._save_manifest

    def concurrent_edit(root, manifest):
        original_save(root, manifest)
        config.write_bytes(b'{"voice":"user-edit"}')

    monkeypatch.setattr(runtime, "_save_manifest", concurrent_edit)
    result = apply(request(policy), tmp_path)
    assert result["outcome"] == "conflict"
    assert config.read_bytes() == b'{"voice":"user-edit"}'


def test_nested_existing_native_endpoint_is_allowed(tmp_path):
    policy, _ = setup(tmp_path)
    target = put(tmp_path, "/system/vendor/quasar/quasar.cfg",
                 b'{"speechkit":{"uniProxyUrl":"https://old.invalid/"}}')
    policy["configs"] = {"backend": {"path": runtime.NATIVE,
        "pointer": ["speechkit", "uniProxyUrl"], "kind": "endpoint",
        "choices": {"chosen": "https://chosen.invalid/"}}}
    result = apply(request(policy, "config.select", {"binding": "backend", "option": "chosen"}),
                   tmp_path)
    assert result["outcome"] == "applied"
    assert json.loads(target.read_bytes())["speechkit"]["uniProxyUrl"].startswith("https://chosen")


@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_external_writer_after_final_file_check_is_not_locked(tmp_path, monkeypatch, operation):
    """Characterize the documented race, not a hard CAS guarantee or device result."""
    policy, config = setup(tmp_path)
    if operation == "delete":
        data = b"RIFF\x04\x00\x00\x00WAVEsynthetic"
        policy["media"] = {"chime": {"path": "/data/sounds/chime.wav",
                                      "sha256": hashlib.sha256(data).hexdigest()}}
        created = apply(request(policy, "media.upload", {
            "asset": "chime", "content_base64": base64.b64encode(data).decode(),
        }), tmp_path)
        config = tmp_path / "data/sounds/chime.wav"
        req = request(policy, "restore", {"backup_id": created["backup_id"]})
    else:
        req = request(policy)
    original_check = runtime._assert_target
    injected = []

    def external_writer(parent, name, expected):
        original_check(parent, name, expected)
        if name == config.name:
            config.write_bytes(b"external-write-after-comparison")
            injected.append(True)

    monkeypatch.setattr(runtime, "_assert_target", external_writer)
    result = apply(req, tmp_path)
    assert injected == [True]
    assert result["outcome"] == "applied"
    if operation == "delete":
        assert not config.exists()
    else:
        assert json.loads(config.read_bytes())["voice"] == "private-new"


@pytest.mark.parametrize("restore", [False, True])
def test_external_mixer_writer_after_final_check_is_not_locked(tmp_path, monkeypatch, restore):
    """A noncooperating mixer writer can race both setting volume and restoring it."""
    policy, _ = setup(tmp_path)
    volume = [22]
    injected = []

    def command(root, args):
        if args[1] == "sset":
            volume[0] = 28
            injected.append(True)
            volume[0] = int(args[-1][:-1])
        return f"  Front Left: Playback 1 [{volume[0]}%]\n".encode()

    monkeypatch.setattr(runtime, "_command", command)
    req = request(policy, "audio.volume", {"percent": 12})
    if restore:
        created = apply(req, tmp_path)
        req = request(policy, "restore", {"backup_id": created["backup_id"]})
        injected.clear()
    result = apply(req, tmp_path)
    assert injected == [True]
    assert result["outcome"] == "applied"
    assert volume[0] == (22 if restore else 12)


def test_playback_consumes_verified_snapshot_if_asset_path_is_replaced(tmp_path, monkeypatch):
    policy, _ = setup(tmp_path)
    approved = b"OggS" + bytes(24) + b"OpusHead" + bytes(32)
    asset = put(tmp_path, "/data/sounds/chime.opus", approved)
    policy["media"] = {"chime": {"path": "/data/sounds/chime.opus",
                                  "sha256": hashlib.sha256(approved).hexdigest()}}
    temporary_file = tempfile.TemporaryFile
    monkeypatch.setattr(runtime, "tempfile", SimpleNamespace(
        TemporaryFile=lambda **_: temporary_file(dir=tmp_path)))
    monkeypatch.setattr(runtime, "_asset", lambda *args: asset.read_bytes())
    observed = []

    class Process:
        pid = 12345

        def __init__(self, args, **kwargs):
            # The approved path changes exactly when the native player would open it.
            asset.write_bytes(b"unapproved-content")
            location = args[2].split("location=", 1)[1].split(" !", 1)[0]
            if location == policy["media"]["chime"]["path"]:
                observed.append(asset.read_bytes())
            else:
                assert location.startswith("/proc/self/fd/")
                assert int(location.rsplit("/", 1)[1]) in kwargs["pass_fds"]
                observed.append(Path(location).read_bytes())

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Process)
    monkeypatch.setattr(runtime.os, "killpg", lambda *args: None)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)
    req = request(policy, "audio.play", {"asset": "chime", "duration_seconds": 1})
    assert runtime._play(Path("/"), req)["status"] == "applied"
    assert observed == [approved]
    assert asset.read_bytes() == b"unapproved-content"
