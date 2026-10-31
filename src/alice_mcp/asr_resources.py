"""Fixed passive resource probe before considering an on-station recognizer."""

COUNTERS = ("mem_total", "mem_available", "swap_total", "swap_free", "zram_bytes",
            "overcommit", "cpu_count", "data_free_kib", "system_free_kib")
FLAGS = ("zram_present", "squashfs")

COMMAND = r'''
LC_ALL=C; export LC_ALL
exec 2>/dev/null
awk '/^MemTotal:/ {print "mem_total=" $2}
     /^MemAvailable:/ {print "mem_available=" $2}
     /^SwapTotal:/ {print "swap_total=" $2}
     /^SwapFree:/ {print "swap_free=" $2}' /proc/meminfo
printf 'overcommit='; cat /proc/sys/vm/overcommit_memory
awk '/^processor[[:space:]]*:/ {n++} END {print "cpu_count=" n+0}' /proc/cpuinfo
if [ -r /sys/block/zram0/disksize ]; then
    echo zram_present=1
    printf 'zram_bytes='; cat /sys/block/zram0/disksize
else echo zram_present=0; echo zram_bytes=0; fi
if grep -q 'squashfs' /proc/filesystems; then echo squashfs=1; else echo squashfs=0; fi
df -Pk /data | awk 'NR==2 {print "data_free_kib=" $4}'
df -Pk /system | awk 'NR==2 {print "system_free_kib=" $4}'
'''
