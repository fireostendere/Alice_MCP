"""Fixed passive Wi-Fi metadata and bounded on-device kernel-log classification."""

COUNTERS = (
    "wifi_interfaces", "wifi_carrier_known", "wifi_carrier_up", "wifi_default_routes",
    "kernel_window_lines", "wifi_disconnect_markers", "wifi_connect_markers",
    "wifi_driver_fault_markers", "suspend_markers", "resume_markers", "oom_markers",
    "last_wifi_disconnect_uptime_seconds", "last_wifi_connect_uptime_seconds",
)
FLAGS = ("routes_readable", "supplicant_running", "dhcp_client_running", "kernel_log_available")

COMMAND = r'''
LC_ALL=C; export LC_ALL
exec 2>/dev/null
interfaces=0; known=0; up=0; routes=0; readable=0
if [ -r /proc/net/route ]; then readable=1; fi
for node in /sys/class/net/*; do
    if [ ! -d "$node/wireless" ] && [ ! -e "$node/phy80211" ]; then continue; fi
    interfaces=$((interfaces + 1))
    carrier=$(cat "$node/carrier")
    case "$carrier" in
        0|1) known=$((known + 1)); up=$((up + carrier));;
    esac
    if [ "$readable" = 1 ]; then
        name=${node##*/}
        count=$(awk -v iface="$name" '$1 == iface && $2 == "00000000" &&
            $8 == "00000000" && $4 ~ /[13579bBdDfF]$/ {n++} END {print n+0}' /proc/net/route)
        routes=$((routes + count))
    fi
done
printf 'wifi_interfaces=%s\nwifi_carrier_known=%s\nwifi_carrier_up=%s\n' \
    "$interfaces" "$known" "$up"
printf 'routes_readable=%s\nwifi_default_routes=%s\n' "$readable" "$routes"
if pidof wpa_supplicant >/dev/null; then echo supplicant_running=1
else echo supplicant_running=0; fi
if pidof udhcpc dhcpcd >/dev/null; then echo dhcp_client_running=1
else echo dhcp_client_running=0; fi
available=0
if dmesg >/dev/null 2>&1; then available=1; fi
printf 'kernel_log_available=%s\n' "$available"
if [ "$available" = 1 ]; then dmesg 2>/dev/null | tail -n 400 | head -c 65536; fi | awk '
{
    lines++; text=tolower($0); stamp=0
    if (match($0, /^[[:space:]]*\[[[:space:]]*[0-9]+([.][0-9]+)?\]/)) {
        value=substr($0, RSTART, RLENGTH); sub(/^.*\[/, "", value); stamp=int(value+0)
    }
    wifi=(text ~ /wlan[0-9]|wireless|cfg80211|brcm|dhd|wifi|wi-fi|sdio/)
    if (wifi && text ~ /deauth|disassoc|link (is )?down|disconnect(ed|ing)/) {
        disconnected++; last_disconnected=stamp
    }
    connect=(text ~ /(^|[^a-z])(associated|connected)([^a-z]|$)|link (is )?up/)
    if (wifi && connect) {
        connected++; last_connected=stamp
    }
    fault=(text ~ /firmware.*(crash|trap|hang)|sdio.*(timeout|error|fail)/ ||
           text ~ /bus.*(down|error)|tx.*timeout/)
    if (wifi && fault)
        faults++
    if (text ~ /pm: suspend|suspend entry|suspending console/) suspended++
    if (text ~ /pm: resume|suspend exit|restarting tasks/) resumed++
    if (text ~ /out of memory|oom-killer|killed process /) oom++
}
END {
    printf "kernel_window_lines=%d\n", lines
    printf "wifi_disconnect_markers=%d\nwifi_connect_markers=%d\n", disconnected, connected
    printf "wifi_driver_fault_markers=%d\n", faults
    printf "suspend_markers=%d\nresume_markers=%d\noom_markers=%d\n", suspended, resumed, oom
    printf "last_wifi_disconnect_uptime_seconds=%d\n", last_disconnected
    printf "last_wifi_connect_uptime_seconds=%d\n", last_connected
}'
'''
