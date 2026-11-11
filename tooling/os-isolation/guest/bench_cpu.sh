#!/usr/bin/env bash
# =====================================================================
#  bench_cpu.sh <duration_s> <threads>
#  Victim CPU benchmark (runs inside the VM or the CT).
#  Prints exactly one JSON line on stdout:
#   {"tool":"sysbench-cpu","throughput":..,"unit":"events/s",
#    "mean_ms":..,"p95_ms":..,"max_ms":..,"duration_s":..}
#  p95_ms is the 95th-percentile latency of one sysbench event
#  (one prime-number search); it rises when the victim's threads
#  have to wait for a CPU.
# =====================================================================
set -euo pipefail
DUR="${1:-60}"
THR="${2:-2}"

OUT="$(sysbench cpu --threads="$THR" --time="$DUR" --percentile=95 run)"

printf '%s\n' "$OUT" | python3 -c '
import json, re, sys
t = sys.stdin.read()
def num(pattern):
    m = re.search(pattern, t)
    if not m:
        sys.exit("bench_cpu: cannot parse sysbench output: " + pattern)
    return float(m.group(1))
print(json.dumps({
    "tool": "sysbench-cpu",
    "throughput": num(r"events per second:\s*([\d.]+)"),
    "unit": "events/s",
    "mean_ms": num(r"\bavg:\s*([\d.]+)"),
    "p95_ms": num(r"95th percentile:\s*([\d.]+)"),
    "max_ms": num(r"\bmax:\s*([\d.]+)"),
    "duration_s": float(sys.argv[1]),
}))
' "$DUR"
