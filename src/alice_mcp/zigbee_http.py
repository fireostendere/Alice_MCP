"""Mini 3 Pro Zigbee through the firmware's own local panel (`zigbee.httpControl`, port 9233).

Stock Quasar stays the coordinator; nothing talks to the radio UART. The panel must be
enabled in the station's quasar.cfg by the owner (docs/SMART_HOME.md). Requests go to the
station's loopback over pinned SSH with stock `wget`: nothing is installed.
"""

from __future__ import annotations

import hashlib
from typing import Any

from alice_mcp import device_runtime as rt
from alice_mcp.core import SSHInput

ACTIONS = frozenset({"zigbee.permit_join", "zigbee.on_off"})
MODEL = "station-mini3-pro"

# Values above this line come from `command`: fixed words, digits and lowercase hex only.
SCRIPT = r"""
set -u
PATH=/usr/bin:/bin:/usr/sbin:/sbin
U=http://localhost:9233
out() { printf '{"outcome":"%s"%s}\n' "$1" "${2:-}"; exit 0; }
tr '\000' '\n' < /proc/device-tree/compatible | grep -qx 'yandex,pickle' || out blocked
[ "$(sha256sum < /system/vendor/quasar/maind | cut -c1-64)" = "$firmware" ] || out blocked
status=$(wget -qO- "$U/status" 2>/dev/null) || out blocked
case "$status" in *'"network":true'*) ;; *) out blocked;; esac
state=$(printf '%s|%s' "$status" "$digest" | sha256sum | cut -c1-64)
[ "$mode" = inspect ] && out ready ",\"state_sha256\":\"$state\""
[ "$state" = "$expected" ] || out conflict
# A 200 only means the firmware accepted the request, never that a device reacted.
wget -qO- --post-data "$body" "$U$route" >/dev/null 2>&1 || out unknown
out applied ',"command_succeeded":true'
"""


def command(request: dict[str, Any], *, source: str = SCRIPT) -> SSHInput:
    """One stock-shell script for the exact Pickle profile; `source` is replaced only by tests."""
    try:
        mode, action = request["mode"], request["action"]
        rt._require(rt._keys(request, {"schema", "mode", "model", "policy", "action", "params",
                                       "nonce"}, {"expected_sha256"}))
        rt._require(request["schema"] == rt.SCHEMA and request["model"] == MODEL
                    and action in ACTIONS and rt._match(rt.HEX32, request["nonce"]))
        expected = request.get("expected_sha256", "")
        rt._require(mode == "apply" and rt._match(rt.HEX64, expected)
                    or mode == "inspect" and "expected_sha256" not in request)
        policy = rt.validate_policy(request["policy"], MODEL)
        firmware = policy.get("firmware_sha256")
        rt._require(rt.allows(policy, action) and rt._match(rt.HEX64, firmware))
        params = rt.validate_parameters(action, request["params"], policy, MODEL)
    except (ValueError, TypeError, KeyError):
        raise ValueError("invalid_request") from None
    if action == "zigbee.permit_join":
        route, body = "/permit_join", f"duration={params['duration_seconds']}"
    else:
        route = "/on_off/" + params["command"]
        body = f"address={params['address']}&endpoint={params['endpoint']}"
    # The plan is bound to this exact request and script, not only to the network status.
    digest = hashlib.sha256(f"{route}?{body}|{source}".encode()).hexdigest()
    header = (f"mode={mode}\nfirmware={firmware}\ndigest={digest}\nexpected={expected}\n"
              f"route={route}\nbody='{body}'\n")
    return SSHInput("sh -s", (header + source).encode())
