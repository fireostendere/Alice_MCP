"""Restricted SSH execution, private inventory and one-shot service previews."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODELS = frozenset({
    "station-lite", "station-lite2", "station-mini", "station-mini2", "station-mini3",
    "station-mini3-pro", "station-midi", "station-2", "station-3", "station-street",
    "station-gen1", "station-max", "yandex-hub", "jbl-link-music",
})
BUILDROOT_MODELS = MODELS - {"station-gen1", "station-max", "jbl-link-music"}
MAX_OUTPUT = 65536
PROBES = {
    "health": "LC_ALL=C; export LC_ALL; "
    "printf 'uptime='; cut -d ' ' -f 1 /proc/uptime; "
    "awk '/^MemTotal:/ {print \"mem_total=\" $2} "
    "/^MemAvailable:/ {print \"mem_available=\" $2}' /proc/meminfo; "
    "printf 'arch='; uname -m",
    "storage": "LC_ALL=C df -Pk /data | awk 'NR==2 "
    "{print \"total_kib=\" $2; print \"used_kib=\" $3; print \"free_kib=\" $4}'",
    "services": "for p in quasar_launcher2 dropbear shairport-sync updatesd; do "
    "if pidof \"$p\" >/dev/null 2>&1; then printf '%s=1\\n' \"$p\"; "
    "else printf '%s=0\\n' \"$p\"; fi; done",
    "audio": "if test -d /dev/snd; then echo sound_devices=1; else echo sound_devices=0; fi",
    "reboot": "printf 'reason='; cat /sys/class/aml_info/reboot_reason",
    # Only counts leave the station. Do not transport raw logs to the model.
    "logs": "test -r /data/quasar/daemons_logs/maind.log && "
    "tail -n 200 /data/quasar/daemons_logs/maind.log | head -c 32768 | "
    "awk 'BEGIN {e=0; w=0} /[Ee][Rr][Rr][Oo][Rr]/ {e++} "
    "/[Ww][Aa][Rr][Nn]/ {w++} END {print \"errors=\" e; print \"warnings=\" w}'",
}
STATE_COMMAND = (
    "test -f /etc/init.d/S99quasar && "
    "printf 'script_sha256=' && sha256sum /etc/init.d/S99quasar | cut -d ' ' -f 1; "
    "if pidof quasar_launcher2 >/dev/null 2>&1; then echo launcher=1; else echo launcher=0; fi"
)


class SafetyError(Exception):
    """Safe, fixed error text suitable for MCP output."""


@dataclass(frozen=True)
class Station:
    id: str
    model: str
    host: str
    port: int
    user: str
    identity_file: Path
    known_hosts_file: Path
    allow_service_actions: bool
    quasar_script_sha256: str
    config_sha256: str
    trust_sha256: str


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _check_ssh_file(path: Path, *, private: bool = False) -> None:
    """Reject mutable trust paths and OpenSSH path expansions, without reading keys."""
    if not path.is_absolute() or not path.is_file() or any(c in str(path) for c in "%$\r\n"):
        raise ValueError("explicit SSH file required")
    for candidate in (path, *path.parents):
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("SSH symlinks unsupported")
        if os.name != "nt":
            if info.st_uid not in {0, os.getuid()}:
                raise ValueError("untrusted SSH path owner")
            sticky_directory = stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX
            if info.st_mode & 0o022 and not sticky_directory:
                raise ValueError("writable SSH trust path")
    if private and os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError("private identity permissions required")


def load_inventory(path: Path | None) -> dict[str, Station]:
    """Load operator-provided data; never return its paths or endpoints to MCP."""
    if path is None:
        return {}
    try:
        path = path.resolve(strict=True)
        checkout = Path(__file__).resolve().parents[2]
        if (checkout / ".git").exists() and path.is_relative_to(checkout):
            raise ValueError("inventory belongs outside Git")
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise ValueError("private inventory permissions required")
        with path.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError("inventory too large")
        data = json.loads(raw, object_pairs_hook=_unique)
        if not isinstance(data, dict) or set(data) != {"stations"}:
            raise ValueError("unexpected root fields")
        entries = data["stations"]
        if not isinstance(entries, list) or len(entries) > 64:
            raise ValueError("invalid stations")
        result: dict[str, Station] = {}
        required = {"id", "model", "host", "port", "user", "identity_file", "known_hosts_file"}
        for entry in entries:
            if not isinstance(entry, dict) or not required <= entry.keys():
                raise ValueError("missing fields")
            if entry.keys() - required - {"allow_service_actions", "quasar_script_sha256"}:
                raise ValueError("unexpected fields")
            for key in required - {"port"}:
                if not isinstance(entry[key], str):
                    raise ValueError("invalid field type")
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", entry["id"]):
                raise ValueError("invalid alias")
            if entry["id"] in result or entry["model"] not in MODELS:
                raise ValueError("invalid identity")
            host = entry["host"]
            try:
                ipaddress.ip_address(host)
                if "%" in host:
                    raise ValueError("scoped addresses unsupported")
            except ValueError:
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host):
                    raise ValueError("invalid host") from None
            if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", entry["user"]):
                raise ValueError("invalid user")
            if type(entry["port"]) is not int or not 1 <= entry["port"] <= 65535:
                raise ValueError("invalid port")
            key_path = Path(entry["identity_file"])
            hosts_path = Path(entry["known_hosts_file"])
            _check_ssh_file(key_path, private=True)
            _check_ssh_file(hosts_path)
            enabled = entry.get("allow_service_actions", False)
            script_sha = entry.get("quasar_script_sha256", "")
            if type(enabled) is not bool or not isinstance(script_sha, str):
                raise ValueError("invalid policy")
            if script_sha and not re.fullmatch(r"[a-f0-9]{64}", script_sha):
                raise ValueError("invalid digest")
            # Bind previews to public trust data and key metadata, without reading private keys.
            stamp = key_path.stat()
            with hosts_path.open("rb") as source:
                host_keys = source.read(65537)
            if len(host_keys) > 65536:
                raise ValueError("host trust file too large")
            trust = host_keys + repr((
                stamp.st_dev, stamp.st_ino, stamp.st_size, stamp.st_mtime_ns,
                stamp.st_ctime_ns, stamp.st_mode, stamp.st_uid,
            )).encode()
            result[entry["id"]] = Station(
                entry["id"], entry["model"], host, entry["port"], entry["user"],
                key_path, hosts_path, enabled, script_sha,
                hashlib.sha256(raw).hexdigest(), hashlib.sha256(trust).hexdigest(),
            )
        return result
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise SafetyError(
            "Private inventory invalid; check schema, files and permissions locally"
        ) from None


def ssh_argv(station: Station, command: str) -> list[str]:
    hosts = str(station.known_hosts_file).replace("\\", "\\\\").replace('"', '\\"')
    return [
        "ssh", "-F", "none", "-T", "-p", str(station.port), "-i", str(station.identity_file),
        "-o", f'UserKnownHostsFile="{hosts}"',
        "-o", "GlobalKnownHostsFile=none", "-o", "StrictHostKeyChecking=yes",
        "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no", "-o", "PreferredAuthentications=publickey",
        "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
        "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=5",
        "-o", "ConnectionAttempts=1", "-o", "LogLevel=ERROR",
        f"{station.user}@{station.host}", command,
    ]


def run_ssh(station: Station, command: str) -> str:
    """Bound runtime and output; never expose subprocess exceptions or stderr."""
    try:
        with subprocess.Popen(
            ssh_argv(station, command), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        ) as process:
            assert process.stdout is not None
            stream = process.stdout
            output: list[bytes] = []

            def drain() -> None:
                content = stream.read(MAX_OUTPUT + 1)
                output.append(content)
                if len(content) > MAX_OUTPUT:
                    process.kill()

            thread = threading.Thread(target=drain, daemon=True)
            thread.start()
            try:
                code = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise SafetyError("SSH timed out; do not automatically retry writes") from None
            finally:
                thread.join(timeout=5)
            if thread.is_alive() or not output or len(output[0]) > MAX_OUTPUT or code:
                raise SafetyError("SSH failed or exceeded output limit; inspect locally")
            return output[0].decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        raise SafetyError("SSH unavailable or failed; inspect locally") from None


def _fields(text: str) -> dict[str, str]:
    if len(text) > MAX_OUTPUT:
        raise SafetyError("Probe exceeded output limit")
    pairs = [line.split("=", 1) for line in text.splitlines() if "=" in line]
    if len({p[0] for p in pairs}) != len(pairs):
        raise SafetyError("Ambiguous probe output")
    return dict(pairs)


def _number(fields: dict[str, str], key: str) -> int:
    value = fields.get(key, "")
    if not re.fullmatch(r"[0-9]{1,15}", value):
        raise SafetyError("Probe unavailable or incompatible")
    return int(value)


def parse_probe(probe: str, text: str) -> dict[str, Any]:
    """Return only typed allowlisted values, never raw remote strings."""
    fields = _fields(text)
    if probe == "health":
        uptime = fields.get("uptime", "")
        if not re.fullmatch(r"[0-9]{1,12}(\.[0-9]{1,6})?", uptime):
            raise SafetyError("Probe unavailable or incompatible")
        arch = fields.get("arch", "")
        if arch not in {"armv7l", "armv8l", "aarch64", "arm", "unknown"}:
            arch = "unknown"
        return {
            "uptime_seconds": float(uptime), "memory_total_kib": _number(fields, "mem_total"),
            "memory_available_kib": _number(fields, "mem_available"), "kernel_arch": arch,
        }
    if probe in {"storage", "logs"}:
        keys = (
            ("total_kib", "used_kib", "free_kib") if probe == "storage" else ("errors", "warnings")
        )
        return {key: _number(fields, key) for key in keys}
    if probe in {"services", "audio"}:
        bool_keys = (
            ("quasar_launcher2", "dropbear", "shairport-sync", "updatesd")
            if probe == "services" else ("sound_devices",)
        )
        result: dict[str, Any] = {}
        for key in bool_keys:
            value = _number(fields, key)
            if value not in {0, 1}:
                raise SafetyError("Probe unavailable or incompatible")
            result[key] = bool(value)
        return result
    if probe == "reboot":
        reason = fields.get("reason", "").strip()
        reasons = {"normal", "watchdog_reboot", "cold_boot", "software_reboot"}
        return {"reason": reason if reason in reasons else "unknown"}
    raise SafetyError("Unknown probe")


@dataclass(frozen=True)
class Plan:
    station: Station
    action: str
    digest: str
    expires: float


class Controller:
    def __init__(
        self, inventory: Path | None, *, writes: bool = False,
        runner: Callable[[Station, str], str] = run_ssh,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.inventory = inventory
        self.writes = writes
        self.runner = runner
        self.clock = clock
        self.plans: dict[str, Plan] = {}
        self._plan_lock = threading.Lock()
        self._execution_lock = threading.Lock()

    def station(self, station_id: str) -> Station:
        stations = load_inventory(self.inventory)
        if station_id not in stations:
            raise SafetyError("Unknown station alias")
        return stations[station_id]

    def probe(self, station_id: str, probe: str) -> dict[str, Any]:
        if probe not in PROBES:
            raise SafetyError("Unknown probe")
        station = self.station(station_id)
        return {"evidence_class": "analytical", "data": parse_probe(
            probe, self.runner(station, PROBES[probe]),
        )}

    def _state(self, station: Station) -> tuple[str, bool]:
        if not self.writes or not station.allow_service_actions:
            raise SafetyError("Service writes disabled by operator policy")
        if os.name == "nt":
            raise SafetyError("Service writes require POSIX file trust checks")
        if station.model not in BUILDROOT_MODELS or not station.quasar_script_sha256:
            raise SafetyError("A locally reviewed Buildroot Quasar script digest is required")
        fields = _fields(self.runner(station, STATE_COMMAND))
        if fields.get("script_sha256") != station.quasar_script_sha256:
            raise SafetyError("Quasar script differs from locally reviewed digest")
        running = _number(fields, "launcher")
        if running not in {0, 1}:
            raise SafetyError("Incompatible service state")
        return station.quasar_script_sha256, bool(running)

    def _digest(self, station: Station, action: str) -> tuple[str, bool]:
        script, running = self._state(station)
        digest = hashlib.sha256(repr((
            station, action, script, running,
        )).encode()).hexdigest()
        return digest, running

    def preview_service(self, station_id: str, action: str) -> dict[str, str]:
        if action not in {"start", "restart"}:
            raise SafetyError("Only Quasar start and restart are supported; stop is prohibited")
        station = self.station(station_id)
        digest, _ = self._digest(station, action)
        with self._plan_lock:
            self.plans = {
                key: plan for key, plan in self.plans.items() if plan.expires > self.clock()
            }
            if len(self.plans) >= 64:
                raise SafetyError("Too many pending previews")
            plan_id = secrets.token_hex(16)
            self.plans[plan_id] = Plan(station, action, digest, self.clock() + 300)
        return {"plan_id": plan_id, "expected_sha256": digest, "station_id": station_id,
                "action": action, "expires_in_seconds": "300",
                "impact": "Voice and playback may be interrupted; no files are edited"}

    def take_plan(self, plan_id: str, expected_sha256: str) -> Plan:
        with self._plan_lock:
            plan = self.plans.pop(plan_id, None)
        if plan is None or self.clock() >= plan.expires:
            raise SafetyError("Preview missing, consumed or expired")
        if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256) or not secrets.compare_digest(
            plan.digest, expected_sha256,
        ):
            raise SafetyError("Preview digest mismatch; create a new preview")
        return plan

    def execute_plan(self, plan: Plan) -> dict[str, Any]:
        # ponytail: serialize service actions globally; split by station if throughput matters.
        with self._execution_lock:
            return self._execute_plan(plan)

    def _execute_plan(self, plan: Plan) -> dict[str, Any]:
        if not math.isfinite(plan.expires) or self.clock() >= plan.expires:
            raise SafetyError("Preview expired")
        station = self.station(plan.station.id)
        digest, _ = self._digest(station, plan.action)
        if digest != plan.digest:
            raise SafetyError("Station or private configuration changed; create a new preview")
        if self.clock() >= plan.expires:
            raise SafetyError("Preview expired during state validation")
        try:
            self.runner(station, f"/etc/init.d/S99quasar {plan.action}")
            _, running = self._state(station)
            return {"outcome": "command_succeeded" if running else "verification_failed",
                    "launcher_running": running, "restart_verified": False, "do_not_retry": True,
                    "evidence_class": "analytical"}
        except SafetyError:
            return {"outcome": "unknown", "do_not_retry": True,
                    "next_step": "Inspect station state locally before another action"}

    def apply_service(self, plan_id: str, expected_sha256: str) -> dict[str, Any]:
        """Internal test helper; MCP must elicit approval before execute_plan."""
        return self.execute_plan(self.take_plan(plan_id, expected_sha256))
