"""Synthetic climate-module log; never reads a real station."""

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from alice_mcp import server
from alice_mcp.core import PROBES, Controller, SafetyError, parse_probe

LINE = "[1:2] [2026-01-02 {time}] [cradle] [info] [{name}_sensor_capability.cpp:81] Got {text}"


def run(tmp_path: Path, lines: list[str], now: str) -> str:
    log = tmp_path / "maind.log"
    log.write_text("\n".join(lines) + "\n")
    command = PROBES["climate"].replace("/data/quasar/daemons_logs/maind.log", str(log))
    assert "$(date +%H:%M:%S)" in command
    command = command.replace("$(date +%H:%M:%S)", now)
    return subprocess.run(["sh", "-c", command], capture_output=True, text=True, check=True).stdout


def test_latest_reading_is_typed_and_aged(tmp_path: Path) -> None:
    lines = [
        LINE.format(time="23:58:00.100", name="co2", text="CO2 data response, co2_ppm=900"),
        "[1:2] [2026-01-02 23:58:30.000] [other] [info] co2_ppm=1 temperature_celsius=99",
        LINE.format(time="23:59:00.100", name="temperature",
                    text="temperature data response, temperature_celsius=23.5269"),
        LINE.format(time="23:59:00.200", name="co2", text="CO2 data response, co2_ppm=1229"),
        LINE.format(time="23:59:00.300", name="humidity",
                    text="humidity data response, humidity_percent=43.0482"),
    ]
    assert parse_probe("climate", run(tmp_path, lines, "00:00:10")) == {
        "temperature_celsius": 23.527, "humidity_percent": 43.048, "co2_ppm": 1229,
        "age_seconds": 70}  # Across midnight.


def test_firmware_0374_reports_its_corrected_values(tmp_path: Path) -> None:
    lines = [
        LINE.format(time="11:06:01.926", name="temperature",
                    text="temperature data response, temperature_celsius=19.2641, correction=2, "
                         "corrected_temperature_celsius=17.2641"),
        LINE.format(time="11:06:01.930", name="co2", text="CO2 data response, co2_ppm=1847"),
        LINE.format(time="11:06:01.935", name="humidity",
                    text="humidity data response, rawHumidityPercent=63.4165, correction=5.6804, "
                         "humidityPercent=69.0969"),
    ]
    assert parse_probe("climate", run(tmp_path, lines, "11:06:11")) == {
        "temperature_celsius": 17.264, "humidity_percent": 69.097, "co2_ppm": 1847,
        "age_seconds": 10}


def test_reading_survives_a_log_rotation(tmp_path: Path) -> None:
    (tmp_path / "maind.log.1").write_text("\n".join([
        LINE.format(time="12:00:00.100", name="temperature", text="temperature_celsius=20.0"),
        LINE.format(time="12:00:00.200", name="humidity", text="humidity_percent=40.0"),
        LINE.format(time="12:00:00.300", name="co2", text="co2_ppm=500")]) + "\n")
    lines = ["[1:2] [2026-01-02 12:00:30.000] [other] [info] rotated"]
    assert parse_probe("climate", run(tmp_path, lines, "12:00:40")) == {
        "temperature_celsius": 20.0, "humidity_percent": 40.0, "co2_ppm": 500,
        "age_seconds": 40}


def test_no_module_or_wild_values_are_incompatible(tmp_path: Path) -> None:
    with pytest.raises(SafetyError):
        parse_probe("climate", run(tmp_path, ["[1:2] [2026-01-02 00:00:00.0] [x] y"], "00:00:10"))
    with pytest.raises(SafetyError):
        parse_probe("climate", "temperature_milli=23000\nhumidity_milli=100001\n"
                               "co2_ppm=500\nage_seconds=5\n")
    with pytest.raises(SafetyError):
        parse_probe("climate", "temperature_milli=23000\nhumidity_milli=40000\n"
                               "co2_ppm=500\nage_seconds=5\nextra=SENTINEL\nco2_ppm=1\n")


@pytest.mark.parametrize("temperature", ["23..5", "23.5SENTINEL", "-2.5", "nan"])
def test_malformed_log_measurements_fail_closed(tmp_path: Path, temperature: str) -> None:
    lines = [LINE.format(time="12:00:00.100", name="temperature",
                         text=f"temperature_celsius={temperature}"),
             LINE.format(time="12:00:00.200", name="humidity", text="humidity_percent=40.0"),
             LINE.format(time="12:00:00.300", name="co2", text="co2_ppm=500")]
    with pytest.raises(SafetyError):
        parse_probe("climate", run(tmp_path, lines, "12:00:10"))


def test_climate_is_reachable_from_the_public_server(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def runner(station, command):
        calls.append(command)
        return "temperature_milli=23500\nhumidity_milli=42000\nco2_ppm=500\nage_seconds=5\n"

    controller = Controller(tmp_path / "unused.json", writes=False, runner=runner)
    monkeypatch.setattr(controller, "station", lambda _: SimpleNamespace(native_mcp=None))
    monkeypatch.setattr(server, "controller", controller)
    assert "climate" in server.get_capabilities()["probes"]
    result = asyncio.run(server.station_probe("desk", "climate"))
    assert result["data"]["temperature_celsius"] == 23.5
    assert calls == [PROBES["climate"]]
