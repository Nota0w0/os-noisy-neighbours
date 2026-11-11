#!/usr/bin/env bash
# =====================================================================
#  bench_io.sh prep <size_gb>
#  bench_io.sh run  <duration_s> <iodepth> <size_gb> [bs]
#
#  Victim disk benchmark (runs inside the VM or the CT).
#  "prep" lays out the test file once, so "run" never measures
#  block allocation. "run" does 4k random reads with O_DIRECT and
#  prints exactly one JSON line:
#   {"tool":"fio-randread","throughput":..,"unit":"IOPS",
#    "mean_ms":..,"p95_ms":..,"max_ms":..,"duration_s":..}
#  p95_ms is the 95th-percentile completion latency (clat).
# =====================================================================
set -euo pipefail
FILE="${BENCH_IO_FILE:-/opt/bench/victim.dat}"
MODE="${1:-}"
to_mib() { awk -v g="$1" 'BEGIN{ printf "%dM", g*1024 }'; }   # fio rejects fractional "G" sizes

case "$MODE" in
  prep)
    SIZE="${2:?size_gb}"
    mkdir -p "$(dirname "$FILE")"
    fio --name=prep --filename="$FILE" --size="$(to_mib "$SIZE")" --rw=write --bs=1M \
        --direct=1 --ioengine=libaio --iodepth=8 --output=/dev/null
    echo '{"prep":"ok"}'
    ;;
  run)
    DUR="${2:?duration_s}"; QD="${3:?iodepth}"; SIZE="${4:?size_gb}"; BS="${5:-4k}"
    [[ -f "$FILE" ]] || { echo "bench_io: $FILE missing, run 'bench_io.sh prep' first" >&2; exit 1; }
    OUT="$(fio --name=victim --filename="$FILE" --size="$(to_mib "$SIZE")" --rw=randread \
               --bs="$BS" --iodepth="$QD" --ioengine=libaio --direct=1 \
               --time_based --runtime="$DUR" --output-format=json)"
    printf '%s' "$OUT" | python3 -c '
import json, sys
raw = sys.stdin.read()
doc = json.loads(raw[raw.index("{"):])          # skip any warning lines before the JSON
rd = doc["jobs"][0]["read"]
clat = rd["clat_ns"]
pct = clat.get("percentile", {})
key = next((k for k in pct if abs(float(k) - 95.0) < 1e-6), None)
if key is None:
    sys.exit("bench_io: fio output has no 95th percentile")
print(json.dumps({
    "tool": "fio-randread",
    "throughput": round(rd["iops"], 1),
    "unit": "IOPS",
    "mean_ms": round(clat["mean"] / 1e6, 4),
    "p95_ms": round(pct[key] / 1e6, 4),
    "max_ms": round(clat["max"] / 1e6, 4),
    "duration_s": float(sys.argv[1]),
}))
' "$DUR"
    ;;
  *)
    echo "usage: bench_io.sh prep <size_gb> | run <duration_s> <iodepth> <size_gb> [bs]" >&2
    exit 2
    ;;
esac
