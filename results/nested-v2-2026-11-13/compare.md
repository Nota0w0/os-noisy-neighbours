# nested-2026-10-19 vs nested-v2-2026-11-13

|  | nested-2026-10-19 | nested-v2-2026-11-13 |
|---|---|---|
| platform | NCPU=12, PVE 9.2.2, Intel(R) Core(TM) i5-14400 | PVE 9.2.2, virtualised (vmware), Intel(R) Core(TM) i5-14400, NCPU=12 |
| repetitions | 3 | 5 |
| configurations | 66 | 84 |

## Uncontended baseline (absolute, policy = default)

| metric | victim | nested-2026-10-19 | nested-v2-2026-11-13 | nested-v2-2026-11-13 / nested-2026-10-19 |
|---|---|---|---|---|
| CPU events/s | vm | 7690.3 | 7715.0 | 1.00 |
| CPU events/s | ct | 7717.3 | 7762.4 | 1.01 |
| memory MiB/s | vm | 59108.9 | 58686.3 | 0.99 |
| memory MiB/s | ct | 59594.8 | 58230.6 | 0.98 |
| disk IOPS | vm | 37846.5 | 36087.6 | 0.95 |
| disk IOPS | ct | 39987.4 | 35949.2 | 0.90 |
| CPU p99 ms | vm | 0.3 | 0.3 | 0.98 |
| CPU p99 ms | ct | 0.3 | 0.3 | 1.00 |
| disk p99 µs | vm | 763.2 | 806.9 | 1.06 |
| disk p99 µs | ct | 596.7 | 641.4 | 1.08 |
| wake-up p99 µs | vm | 1190.2 | 1194.4 | 1.00 |
| wake-up p99 µs | ct | 1133.3 | 1139.0 | 1.01 |

## Finding 1–2: CPU throughput under CPU stress, n = 8

Pilot: the container falls behind under `default` and `float`, not under `dedicated`; the VM stays near 0.85.

| policy | victim | nested-2026-10-19 | nested-v2-2026-11-13 |
|---|---|---|---|
| default | vm | 0.85 (0.84–0.85) | 0.85 (0.84–0.86) |
| default | ct | 0.46 (0.43–0.51) | 0.46 (0.42–0.56) |
| float | vm | 0.85 (0.85–0.86) | 0.85 (0.84–0.88) |
| float | ct | 0.55 (0.53–0.56) | 0.58 (0.52–0.67) |
| dedicated | vm | 0.85 (0.84–0.86) | 0.85 (0.84–0.86) |
| dedicated | ct | 0.84 (0.82–0.84) | 0.83 (0.82–0.85) |

## Finding 3: disk IOPS under CPU stress, n = 8

Pilot: the VM keeps its CPU time but loses most of its I/O unless its CPUs are dedicated.

| policy | victim | nested-2026-10-19 | nested-v2-2026-11-13 |
|---|---|---|---|
| default | vm | 0.11 (0.10–0.12) | 0.13 (0.09–0.15) |
| default | ct | 0.62 (0.49–0.73) | 0.70 (0.52–0.80) |
| float | vm | 0.20 (0.15–0.24) | 0.17 (0.13–0.20) |
| float | ct | 0.69 (0.68–0.70) | 0.71 (0.69–0.73) |
| dedicated | vm | 0.65 (0.64–0.67) | 0.65 (0.64–0.66) |
| dedicated | ct | 0.73 (0.71–0.77) | 0.75 (0.72–0.77) |

## Finding 4a: memory bandwidth under memory stress, policy = dedicated

Pilot: both guest types lose the same amount.

| n | victim | nested-2026-10-19 | nested-v2-2026-11-13 |
|---|---|---|---|
| 1 | vm | – | 0.86 (0.83–0.88) |
| 1 | ct | – | 0.85 (0.83–0.88) |
| 2 | vm | 0.71 (0.69–0.74) | 0.71 (0.69–0.75) |
| 2 | ct | 0.69 (0.66–0.72) | 0.71 (0.69–0.71) |
| 4 | vm | 0.49 (0.48–0.50) | 0.50 (0.48–0.53) |
| 4 | ct | 0.48 (0.47–0.48) | 0.49 (0.46–0.51) |
| 8 | vm | 0.46 (0.45–0.48) | 0.47 (0.46–0.48) |
| 8 | ct | 0.45 (0.44–0.45) | 0.45 (0.43–0.47) |

## Finding 4b: absolute disk performance under disk stress, n = 8, policy = default

Pilot: VM and container converge to the same absolute numbers.

| victim | metric | nested-2026-10-19 | nested-v2-2026-11-13 |
|---|---|---|---|
| vm | IOPS | 7692 | 7687 |
| vm | p99 µs | 5887 | 5826 |
| ct | IOPS | 7361 | 7758 |
| ct | p99 µs | 5734 | 5446 |

## Baseline drift within a block (end / start, 1.00 = stable)

| policy | victim | nested-2026-10-19 CPU | nested-2026-10-19 IOPS | nested-v2-2026-11-13 CPU | nested-v2-2026-11-13 IOPS |
|---|---|---|---|---|---|
| default | vm | 1.00 (1.00–1.01) | 0.92 (0.91–0.94) | 1.00 (0.98–1.01) | 1.20 (0.81–1.42) |
| default | ct | 0.97 (0.89–1.01) | 1.01 (0.95–1.04) | 1.00 (0.99–1.02) | 1.13 (0.97–1.30) |
| float | vm | 1.00 (1.00–1.00) | 1.07 (0.88–1.26) | 1.00 (0.99–1.01) | 0.99 (0.85–1.05) |
| float | ct | 1.00 (0.99–1.02) | 0.97 (0.93–1.01) | 0.99 (0.95–1.01) | 0.97 (0.88–1.02) |
| dedicated | vm | 1.00 (1.00–1.01) | 0.99 (0.95–1.05) | 1.00 (0.99–1.01) | 1.00 (0.98–1.01) |
| dedicated | ct | 0.99 (0.99–1.00) | 1.00 (0.98–1.02) | 1.00 (0.98–1.01) | 0.99 (0.94–1.04) |

## Configurations that differ by more than 0.10 (throughput relative to baseline)

| policy | victim | stress | n | metric | nested-2026-10-19 | nested-v2-2026-11-13 | difference |
|---|---|---|---|---|---|---|---|
| default | ct | mem | 2 | disk IOPS | 0.75 (0.45–0.96) | 1.04 (0.89–1.20) | +0.29 |
| default | ct | cpu | 2 | disk IOPS | 0.87 (0.62–1.01) | 1.11 (0.95–1.30) | +0.24 |
| default | ct | mem | 4 | CPU events/s | 0.41 (0.36–0.44) | 0.55 (0.38–0.87) | +0.13 |
| float | vm | mem | 2 | disk IOPS | 0.95 (0.82–1.04) | 0.84 (0.78–0.91) | -0.11 |
| default | ct | cpu | 4 | CPU events/s | 0.44 (0.30–0.58) | 0.55 (0.30–0.87) | +0.11 |

270 metric rows exist in only one of the two result sets (different matrix or an incomplete run).
