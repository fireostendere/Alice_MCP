"""Execute the fixed upgrade transaction against disposable local daemons only."""

import hashlib
import io
import ipaddress
import json
import os
import shlex
import shutil
import socket
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from alice_mcp import agent_provision, agent_upgrade, agent_upgrade_script
from alice_mcp.core import SafetyError, SSHInput


@pytest.mark.parametrize("mode", ["success", "config_changed", "claim_busy", "bad_archive",
                                  "child_active", "new_exits", "symlink", "pid_move_fails",
                                  "identity_changed_after_stop", "quasar_invalid",
                                  "signal_after_stop", "signal_after_kill",
                                  "concurrent_credential", "pending_stop", "truncated_helper",
                                  "tls_fails", "config_move_fails"])
def test_existing_installation_upgrade_or_guarded_rollback_without_quasar_changes(
    tmp_path: Path, monkeypatch: Any, mode: str,
) -> None:
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("Synthetic upgrade transaction needs an existing C compiler")
    data = tmp_path / "data"
    root = data / "opt/alice-mcp-agent"
    root.mkdir(parents=True, mode=0o700)
    with socket.socket() as reservation:
        reservation.bind((str(ipaddress.IPv4Address(0x7F000001)), 0))
        port = reservation.getsockname()[1]
    program = tmp_path / "agent.c"
    program.write_text(r'''
#include <arpa/inet.h>
#include <signal.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>
int main(void) {
    alarm(60);
#if FAIL
    return 2;
#endif
    int fd = socket(AF_INET, SOCK_STREAM, 0), yes = 1;
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET; address.sin_port = htons(PORT);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (fd < 0 || setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes)) ||
        bind(fd, (struct sockaddr *)&address, sizeof(address)) || listen(fd, 4)) return 3;
#if CHILD
    if (!fork()) { alarm(30); for (;;) pause(); }
#endif
    for (;;) pause();
}
''')
    old, new = root / "agent", tmp_path / "new-agent"
    for target, fail, child in [(old, 0, int(mode == "child_active")),
                                (new, int(mode == "new_exits"), 0)]:
        subprocess.run([compiler, "-O2", "-Wall", "-Wextra", "-Werror", f"-DPORT={port}",
                        f"-DFAIL={fail}", f"-DCHILD={child}", str(program), "-o", str(target)],
                       check=True, capture_output=True, timeout=30)
    for name in agent_provision.NAMES[1:]:
        (root / name).write_bytes(("synthetic original " + name).encode())
        (root / name).chmod(0o600 if name != "start" else 0o500)
    old.chmod(0o500)
    (root / "pcm-player").write_bytes(b"synthetic reviewed PCM")
    (root / "pcm-player").chmod(0o500)
    names = (*agent_provision.NAMES, "pcm-player")
    originals = {name: (root / name).read_bytes() for name in names}
    old_hashes = [hashlib.sha256(originals[name]).hexdigest() for name in names]
    content = {"agent": new.read_bytes(), "pcm-player": b"synthetic reviewed PCM",
               "config.json": b"synthetic new config", "start": b"synthetic new startup"}
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, body in content.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    hashes = [hashlib.sha256(body).hexdigest() for body in content.values()]
    firmware, boot, init = (tmp_path / name for name in ("firmware", "boot", "init"))
    firmware.write_bytes(b"synthetic reviewed firmware")
    boot.write_bytes(b"synthetic boot")
    init.write_text(f'#!/bin/sh\nexec {root}/start "$@"\n')
    init.chmod(0o500)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "pidof").write_text(f"#!/bin/sh\nprintf '%s\\n' {os.getpid()}\n")
    if mode == "quasar_invalid":
        (tools / "pidof").write_text("#!/bin/sh\nprintf '99999999\\n'\n")
    (tools / "pidof").chmod(0o500)
    if mode in {"pid_move_fails", "config_move_fails"}:
        pattern = "*/agent.pid" if mode == "pid_move_fails" else "*/.upgrade-*/config.json"
        (tools / "mv").write_text(f'#!/bin/sh\ncase "$1" in {pattern}) exit 1;; esac\n'
                                 f'exec {shutil.which("mv")} "$@"\n')
        (tools / "mv").chmod(0o500)
    elif mode == "concurrent_credential":
        (tools / "mv").write_text(f'''#!/bin/sh
{shutil.which("mv")} "$@" || exit $?
case "$1" in */.upgrade-*/agent)
    printf 'concurrent credential change' > {root}/credential;;
esac
''')
        (tools / "mv").chmod(0o500)
    source = agent_upgrade_script.SCRIPT.replace("OWNER_UID=0", f"OWNER_UID={os.getuid()}")
    source = source.replace("DATA=/data", "DATA=" + shlex.quote(str(data)))
    for before, after in [("/system/vendor/quasar/maind", firmware),
                          ("/proc/sys/kernel/random/boot_id", boot),
                          ("/etc/init.d/S99zz-alice-mcp", init)]:
        source = source.replace(before, str(after))
    source = source.replace(":480B$", f":{port:04X}$")
    identity_marker, signal_marker = tmp_path / "identity", tmp_path / "killed"
    pending_marker = tmp_path / "pending-stop"
    if mode == "identity_changed_after_stop":
        source = source.replace("starttime() {", f'''starttime() {{
if [ -f {identity_marker} ] && [ "$1" = "$(cat {identity_marker})" ]; then
    printf '0\\n'; return
fi
''', 1)
        source = f'''kill() {{
    if [ "$1" = -STOP ]; then
        command kill "$@"; printf '%s\\n' "$2" > {identity_marker}
    else
        [ "$1" != -KILL ] || printf 'unsafe kill\\n' > {signal_marker}
        command kill "$@"
    fi
}}
''' + source
    elif mode == "pending_stop":
        # Model a queued STOP not yet visible as /proc state T. CONT must still
        # cancel it; deliberately do not create an actual uninterruptible task.
        source = f'''kill() {{
    if [ "$1" = -STOP ]; then
        printf 'queued stop\\n' > {pending_marker}
        command kill -HUP "$$"
    elif [ "$1" = -CONT ]; then
        rm -f {pending_marker}
        command kill "$@"
    else command kill "$@"; fi
}}
''' + source
    elif mode in {"signal_after_stop", "signal_after_kill"}:
        interrupted_signal = "-STOP" if mode == "signal_after_stop" else "-KILL"
        source = f'''kill() {{
    if [ "$1" = {interrupted_signal} ]; then
        if [ "$1" = -KILL ]; then
            (sleep 0.15; command kill -KILL "$2") &
        else command kill "$@"; fi
        command kill -HUP "$$"
    else command kill "$@"; fi
}}
''' + source
    env = {**os.environ, "PATH": str(tools) + ":" + os.environ["PATH"]}
    nonce = "b" * 32
    old_process = subprocess.Popen([str(old)], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
    (root / "agent.pid").write_text(str(old_process.pid) + "\n")
    (root / "agent.pid").chmod(0o600)
    tls_calls = []

    def verify_tls() -> None:
        # The actual transaction must reach this barrier with the new executable,
        # but the exact old config. No new fields may reach the old resident.
        tls_calls.append(1)
        assert (root / "agent").read_bytes() == content["agent"]
        assert (root / "config.json").read_bytes() == originals["config.json"]
        live_pid = int((root / "agent.pid").read_text())
        assert live_pid != old_process.pid
        assert Path(f"/proc/{live_pid}/exe").readlink() == root / "agent"
        if mode == "tls_fails":
            raise SafetyError("Synthetic TLS failure")

    monkeypatch.setattr(agent_provision, "ssh_argv", lambda s, c: shlex.split(c))

    def run(expected: str = "", body: bytes | None = None) -> dict[str, Any]:
        package = payload.getvalue() if body is None else body
        bundle: Any = SimpleNamespace(package=payload.getvalue(),
                                      sha256=hashlib.sha256(payload.getvalue()).hexdigest(),
                                      layout="direct", old_hashes=tuple(old_hashes),
                                      hashes=tuple(hashes))
        station: Any = SimpleNamespace(controls={
            "firmware_sha256": hashlib.sha256(firmware.read_bytes()).hexdigest(),
        })
        command = agent_upgrade._command(station, bundle, nonce, source, expected)
        if isinstance(command, SSHInput):
            args, wire = shlex.split(command.command), command.data
        else:
            prefix = source.encode()
            if mode == "truncated_helper":
                prefix = prefix[:-1]
            args, wire = shlex.split(command), prefix + package
        if expected and mode != "truncated_helper":
            monkeypatch.setenv("PATH", env["PATH"])
            try:
                return agent_provision._transfer(station, command, wire, verify_tls=verify_tls)
            except SafetyError:
                return {"outcome": "unknown", "do_not_retry": True}
        result = subprocess.run(args, input=wire, env=env, capture_output=True, timeout=20)
        if expected and mode == "truncated_helper":
            assert result.returncode != 0 and result.stdout == b""
            assert not (root / (".upgrade-" + nonce)).exists()
            return {"outcome": "unknown", "do_not_retry": True}
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        return json.loads(result.stdout)

    try:
        preview = run()
        if mode == "quasar_invalid":
            assert preview["outcome"] == "blocked"
            assert old_process.poll() is None
            return
        assert preview["outcome"] == "ready"
        assert old_process.poll() is None
        if mode == "config_changed":
            (root / "config.json").write_bytes(b"concurrent owner change")
        elif mode == "claim_busy":
            (root / ".agent-lock").mkdir()
        elif mode == "symlink":
            (root / "start").unlink()
            (root / "start").symlink_to(init)
        corrupt = b"corrupt".ljust(len(payload.getvalue()), b"\0")
        result = run(preview["state_sha256"], corrupt if mode == "bad_archive" else None)
        assert result.get("do_not_retry", True) is True
        if mode == "concurrent_credential":
            assert result["outcome"] == "unknown"
            assert (root / "credential").read_bytes() == b"concurrent credential change"
        elif mode == "success":
            assert result == {"outcome": "installed", "quasar_changed": False,
                              "autostart_registered": True, "package_verified": True,
                              "staging_cleaned": True, "do_not_retry": True}
            assert all((root / name).read_bytes() == body for name, body in content.items())
            backup = root / (".upgrade-" + nonce)
            assert (backup / "old-agent").read_bytes() == originals["agent"]
            assert (backup / "old-config.json").read_bytes() == originals["config.json"]
            assert not (backup / ".package.tar").exists()
            assert tls_calls == [1]
        elif mode in {"new_exits", "pid_move_fails", "signal_after_kill", "tls_fails",
                      "config_move_fails"}:
            assert result["outcome"] == "unknown"
            assert all((root / name).read_bytes() == body for name, body in originals.items())
            assert (root / "pcm-player").read_bytes() == originals["pcm-player"]
            restored_pid = int((root / "agent.pid").read_text())
            assert Path(f"/proc/{restored_pid}/exe").readlink() == root / "agent"
        else:
            assert result["outcome"] in {"blocked", "unknown"}
            assert old_process.poll() is None
            assert old.read_bytes() == originals["agent"]
            assert (root / "pcm-player").read_bytes() == originals["pcm-player"]
            if mode in {"identity_changed_after_stop", "signal_after_stop"}:
                assert not signal_marker.exists()
                state = Path(f"/proc/{old_process.pid}/status").read_text()
                assert "State:\tT" not in state
            if mode == "pending_stop":
                assert not pending_marker.exists()
        assert init.read_text() == f'#!/bin/sh\nexec {root}/start "$@"\n'
        preserved = ("certificate.pem", "private-key.pem", "credential", "tts-once", "asr-once")
        assert all((root / name).read_bytes() == originals[name] for name in preserved
                   if name != "credential" or mode != "concurrent_credential")
    finally:
        # Only these disposable test daemons, including a deliberately active test child.
        for path in Path("/proc").glob("[0-9]*/exe"):
            try:
                if path.readlink() == root / "agent":
                    os.kill(int(path.parent.name), 9)
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
        old_process.wait(timeout=3)
