## 1. Baseline reproducibility (n = 0, 5 reps; CV = sd/mean, sample sd)

| policy | victim | cpu_eps | mem_mibs | io_iops | cpu_lat_p99_ms | io_p99_us |
|---|---|---|---|---|---|---|
| default | vm | 7715 / 0.5% | 5.869e+04 / 2.0% | 3.609e+04 / 20.2% ⚠ | 0.32 / 0.0% | 806.9 / 20.1% ⚠ |
| default | ct | 7762 / 0.9% | 5.823e+04 / 1.8% | 3.595e+04 / 13.4% ⚠ | 0.314 / 3.6% | 641.4 / 12.3% ⚠ |
| float | vm | 7663 / 1.4% | 5.89e+04 / 0.8% | 3.657e+04 / 1.7% | 0.326 / 4.1% | 764.3 / 4.4% |
| float | ct | 7761 / 0.3% | 5.944e+04 / 0.7% | 4.225e+04 / 1.7% | 0.314 / 1.7% | 548.5 / 3.9% |
| dedicated | vm | 7688 / 0.6% | 5.928e+04 / 2.1% | 2.807e+04 / 0.7% | 0.324 / 4.7% | 972.4 / 1.2% |
| dedicated | ct | 7736 / 0.7% | 5.962e+04 / 1.7% | 4.199e+04 / 2.6% | 0.312 / 2.7% | 561.2 / 2.1% |

cell = mean / CV; ⚠ = CV > 5 % (throughput) or > 10 % (p99)

## 2. Density n* (first n where mean relative p99 ≥ 2, linear interpolation over n = 0,1,2,4,8)

Interval: n* of the per-n max curve (earliest) – n* of the per-n min curve (latest) across the 5 reps.

| policy | victim | stress | p99 metric | rel p99 at n=1,2,4,8 (mean) | n* | interval |
|---|---|---|---|---|---|---|
| default | vm | cpu | cpu_lat_p99_ms | 1.07, 1.07, 1.57, 1.70 | n = 8 still < 2 (1.70) | — |
| default | vm | cpu | io_p99_us | 1.06, 1.23, 1.59, 9.22 | 4.22 | 4.13 – 4.39 |
| default | vm | mem | cpu_lat_p99_ms | 1.07, 1.14, 1.61, 1.72 | n = 8 still < 2 (1.72) | — |
| default | vm | mem | io_p99_us | 1.11, 1.18, 1.95, 10.17 | 4.02 | 3.12 – 4.27 |
| default | vm | io | cpu_lat_p99_ms | 2.24, 1.37, 1.44, 1.55 | ≤ 1 (interp. 0.81) | 0.30 – >8 |
| default | vm | io | io_p99_us | 1.74, 4.50, 5.00, 7.44 | 1.09 | 0.82 – 1.37 |
| default | ct | cpu | cpu_lat_p99_ms | 11.08, 11.39, 13.70, 14.07 | ≤ 1 (interp. 0.10) | 0.08 – 4.13 |
| default | ct | cpu | io_p99_us | 0.85, 0.95, 3.38, 5.28 | 2.87 | 2.30 – >8 |
| default | ct | mem | cpu_lat_p99_ms | 10.99, 11.40, 12.79, 14.20 | ≤ 1 (interp. 0.10) | 0.08 – 4.12 |
| default | ct | mem | io_p99_us | 0.92, 1.06, 3.79, 7.26 | 2.69 | 2.26 – 4.34 |
| default | ct | io | cpu_lat_p99_ms | 3.68, 6.98, 3.96, 4.12 | ≤ 1 (interp. 0.37) | 0.10 – >8 |
| default | ct | io | io_p99_us | 1.70, 5.37, 6.44, 8.54 | 1.08 | 0.92 – 1.21 |
| float | vm | cpu | cpu_lat_p99_ms | 1.04, 1.07, 1.36, 1.66 | n = 8 still < 2 (1.66) | — |
| float | vm | cpu | io_p99_us | 1.11, 1.26, 2.04, 8.73 | 3.90 | 3.35 – 4.14 |
| float | vm | mem | cpu_lat_p99_ms | 1.05, 1.14, 1.56, 1.67 | n = 8 still < 2 (1.67) | — |
| float | vm | mem | io_p99_us | 1.11, 1.27, 2.18, 10.07 | 3.61 | 3.10 – 4.00 |
| float | vm | io | cpu_lat_p99_ms | 1.41, 1.41, 1.41, 1.54 | n = 8 still < 2 (1.54) | — |
| float | vm | io | io_p99_us | 1.71, 5.04, 6.00, 7.81 | 1.09 | 0.74 – 1.28 |
| float | ct | cpu | cpu_lat_p99_ms | 1.03, 1.06, 1.49, 13.60 | 4.17 | 4.07 – 4.30 |
| float | ct | cpu | io_p99_us | 1.05, 1.12, 1.46, 1.71 | n = 8 still < 2 (1.71) | — |
| float | ct | mem | cpu_lat_p99_ms | 1.06, 1.13, 1.60, 13.94 | 4.13 | 4.08 – 4.27 |
| float | ct | mem | io_p99_us | 1.14, 1.32, 1.79, 2.44 | 5.28 | 3.55 – >8 |
| float | ct | io | cpu_lat_p99_ms | 1.54, 1.40, 1.49, 1.66 | n = 8 still < 2 (1.66) | 0.86 – >8 |
| float | ct | io | io_p99_us | 1.94, 6.47, 7.54, 10.29 | 1.01 | 0.97 – 1.05 |
| dedicated | vm | cpu | cpu_lat_p99_ms | 1.05, 1.08, 1.57, 1.70 | n = 8 still < 2 (1.70) | — |
| dedicated | vm | cpu | io_p99_us | 1.01, 1.02, 1.70, 2.11 | 6.95 | 4.89 – 7.56 |
| dedicated | vm | mem | cpu_lat_p99_ms | 1.06, 1.14, 4.17, 1.69 | 2.57 | 2.12 – >8 |
| dedicated | vm | mem | io_p99_us | 1.03, 1.11, 1.67, 2.10 | 7.08 | 6.60 – 7.33 |
| dedicated | vm | io | cpu_lat_p99_ms | 1.44, 1.37, 1.40, 1.54 | n = 8 still < 2 (1.54) | — |
| dedicated | vm | io | io_p99_us | 1.43, 3.48, 4.22, 6.05 | 1.28 | 1.21 – 1.45 |
| dedicated | ct | cpu | cpu_lat_p99_ms | 1.04, 1.07, 1.64, 1.79 | n = 8 still < 2 (1.79) | — |
| dedicated | ct | cpu | io_p99_us | 0.94, 1.02, 1.41, 1.62 | n = 8 still < 2 (1.62) | — |
| dedicated | ct | mem | cpu_lat_p99_ms | 1.06, 1.15, 1.67, 1.78 | n = 8 still < 2 (1.78) | — |
| dedicated | ct | mem | io_p99_us | 1.01, 1.20, 1.58, 1.72 | n = 8 still < 2 (1.72) | — |
| dedicated | ct | io | cpu_lat_p99_ms | 1.39, 1.44, 1.51, 1.65 | n = 8 still < 2 (1.65) | — |
| dedicated | ct | io | io_p99_us | 1.90, 5.98, 7.25, 10.32 | 1.03 | 1.00 – 1.10 |

## 3. Pilot comparison: v2 mean relative value inside pilot min–max (n = 2, 4, 8)

| metric | inside | total | share |
|---|---|---|---|
| cpu_eps | 36 | 54 | 67% |
| mem_mibs | 44 | 54 | 81% |
| io_iops | 33 | 54 | 61% |
| cpu_lat_p99_ms | 34 | 54 | 63% |
| io_p99_us | 27 | 54 | 50% |

Outside the pilot range by more than 0.05 (absolute, relative units):

| metric | policy | victim | stress | n | v2 mean | pilot min–max |
|---|---|---|---|---|---|---|
| cpu_eps | default | ct | mem | 4 | 0.55 | 0.36–0.44 |
| io_iops | default | ct | cpu | 2 | 1.11 | 0.62–1.01 |
| io_iops | default | ct | mem | 2 | 1.04 | 0.45–0.96 |
| io_iops | float | ct | cpu | 2 | 0.87 | 0.93–0.99 |
| cpu_lat_p99_ms | default | ct | mem | 4 | 12.79 | 13.53–27.10 |
| cpu_lat_p99_ms | float | vm | cpu | 4 | 1.36 | 1.59–1.72 |
| cpu_lat_p99_ms | float | vm | mem | 4 | 1.56 | 1.62–1.75 |
| cpu_lat_p99_ms | float | ct | cpu | 4 | 1.49 | 1.57–1.72 |
| cpu_lat_p99_ms | float | ct | cpu | 8 | 13.60 | 12.37–13.53 |
| cpu_lat_p99_ms | float | ct | mem | 8 | 13.94 | 12.37–13.53 |
| cpu_lat_p99_ms | dedicated | vm | cpu | 4 | 1.57 | 1.66–1.75 |
| cpu_lat_p99_ms | dedicated | vm | mem | 4 | 4.17 | 1.72–1.75 |
| cpu_lat_p99_ms | dedicated | ct | cpu | 4 | 1.64 | 1.74–1.77 |
| cpu_lat_p99_ms | dedicated | ct | mem | 4 | 1.67 | 1.74–1.77 |
| io_p99_us | default | vm | io | 2 | 4.50 | 4.64–5.27 |
| io_p99_us | default | vm | io | 4 | 5.00 | 5.78–6.73 |
| io_p99_us | default | ct | io | 4 | 6.44 | 6.74–7.88 |
| io_p99_us | default | ct | io | 8 | 8.54 | 9.07–10.37 |
| io_p99_us | float | vm | cpu | 8 | 8.73 | 6.67–8.37 |
| io_p99_us | float | ct | cpu | 2 | 1.12 | 0.92–1.01 |
| io_p99_us | float | ct | mem | 2 | 1.32 | 1.12–1.27 |
| io_p99_us | float | ct | mem | 4 | 1.79 | 1.60–1.71 |
| io_p99_us | dedicated | vm | cpu | 2 | 1.02 | 1.09–1.15 |
| io_p99_us | dedicated | vm | cpu | 4 | 1.70 | 1.49–1.62 |
| io_p99_us | dedicated | vm | io | 2 | 3.48 | 3.77–4.21 |
| io_p99_us | dedicated | vm | io | 4 | 4.22 | 4.57–5.07 |
| io_p99_us | dedicated | vm | io | 8 | 6.05 | 6.25–6.37 |
| io_p99_us | dedicated | ct | mem | 8 | 1.72 | 1.82–1.95 |
| io_p99_us | dedicated | ct | io | 2 | 5.98 | 6.22–6.79 |
| io_p99_us | dedicated | ct | io | 4 | 7.25 | 7.33–8.00 |

## 4. CPU PSI vs CPU throughput (Spearman ρ; CPU stress + baselines)

| subset | victim | N | ρ | p |
|---|---|---|---|---|
| all | vm | 75 | -0.50 | 5e-06 |
| all | ct | 75 | -0.83 | 7e-20 |
| default | vm | 25 | -0.50 | 0.01 |
| default | ct | 25 | -0.67 | 0.0003 |
| float | vm | 25 | -0.62 | 0.0009 |
| float | ct | 25 | -0.67 | 0.0003 |
| dedicated | vm | 25 | -0.40 | 0.05 |
| dedicated | ct | 25 | -0.64 | 0.0005 |

dedicated, CPU stress, n = 8: rel throughput vm 0.849 / ct 0.835; cpu PSI vm 0.41 % / ct 1.75 %; host cpu PSI 49.8 %

IO PSI under disk stress, n = 8 (mean %): default/vm 12.6, default/ct 23.1, float/vm 11.5, float/ct 5.5, dedicated/vm 42.4, dedicated/ct 9.4

## 5. Policy default: CPU throughput vs number of victim CPUs shared with busy neighbours (overlap_max)

                            N  thr_mean  thr_min  thr_max  p99_mean
victim stress overlap_max                                          
ct     cpu    0.0           3      0.89     0.87     0.91      1.22
              1.0          10      0.48     0.44     0.55     13.87
              2.0           7      0.45     0.30     0.56     15.54
       mem    0.0           3      0.89     0.87     0.90      1.25
              1.0          10      0.48     0.44     0.54     13.82
              2.0           7      0.44     0.38     0.49     14.99
vm     cpu    2.0           5      0.92     0.89     0.94      1.07
              3.0           1      0.88     0.88     0.88      1.06
              4.0           4      0.89     0.88     0.90      1.08
              7.0           2      0.87     0.86     0.88      1.42
              8.0           3      0.86     0.86     0.87      1.68
              12.0          5      0.85     0.85     0.86      1.70
       mem    2.0           5      0.92     0.89     0.94      1.08
              3.0           1      0.89     0.89     0.89      1.09
              4.0           4      0.89     0.88     0.89      1.16
              7.0           2      0.86     0.85     0.87      1.48
              8.0           3      0.85     0.85     0.85      1.69
              12.0          5      0.84     0.83     0.84      1.72

## 6. Extra numbers

Baseline means by policy:

metric            io_iops  cpu_eps  mem_mibs
policy    victim                            
dedicated ct      41987.1   7735.9   59624.6
          vm      28068.0   7688.1   59275.3
default   ct      35949.2   7762.4   58230.6
          vm      36087.6   7715.0   58686.3
float     ct      42247.0   7761.0   59440.5
          vm      36566.5   7662.9   58903.9

Mean relative values (all n):

metric                     rel_cpu_eps  rel_cpu_lat_p99_ms  rel_mem_mibs  rel_io_iops  rel_io_p99_us
policy    stress victim n                                                                           
dedicated cpu    ct     1        0.935               1.039         0.923        0.948          0.935
                        2        0.896               1.071         0.885        0.937          1.024
                        4        0.866               1.635         0.629        0.811          1.412
                        8        0.835               1.790         0.533        0.746          1.617
                 vm     1        0.932               1.051         0.924        1.020          1.014
                        2        0.895               1.076         0.891        1.016          1.024
                        4        0.862               1.566         0.668        0.777          1.700
                        8        0.849               1.701         0.544        0.649          2.106
          io     ct     1        0.867               1.386         0.870        0.607          1.898
                        2        0.865               1.442         0.869        0.288          5.984
                        4        0.847               1.507         0.844        0.233          7.251
                        8        0.828               1.647         0.834        0.173         10.317
                 vm     1        0.856               1.442         0.870        0.684          1.434
                        2        0.856               1.373         0.860        0.426          3.479
                        4        0.835               1.404         0.845        0.345          4.217
                        8        0.826               1.537         0.822        0.256          6.046
          mem    ct     1        0.915               1.065         0.849        0.910          1.012
                        2        0.891               1.154         0.707        0.858          1.199
                        4        0.861               1.667         0.487        0.621          1.584
                        8        0.837               1.783         0.452        0.607          1.722
                 vm     1        0.926               1.064         0.860        1.003          1.034
                        2        0.890               1.138         0.710        0.990          1.111
                        4        0.795               4.173         0.501        0.693          1.673
                        8        0.845               1.694         0.467        0.629          2.098
default   cpu    ct     1        0.573              11.080         0.702        1.119          0.854
                        2        0.537              11.386         0.657        1.107          0.946
                        4        0.548              13.701         0.550        0.864          3.379
                        8        0.461              14.069         0.353        0.698          5.282
                 vm     1        0.920               1.069         0.927        1.013          1.060
                        2        0.891               1.075         0.889        0.952          1.227
                        4        0.864               1.575         0.706        0.762          1.586
                        8        0.852               1.700         0.546        0.127          9.219
          io     ct     1        0.716               3.681         0.744        0.723          1.701
                        2        0.758               6.977         0.799        0.358          5.366
                        4        0.736               3.964         0.771        0.278          6.436
                        8        0.716               4.119         0.748        0.217          8.541
                 vm     1        0.739               2.238         0.799        0.572          1.745
                        2        0.862               1.369         0.879        0.369          4.495
                        4        0.825               1.444         0.829        0.310          4.997
                        8        0.810               1.550         0.821        0.220          7.439
          mem    ct     1        0.569              10.988         0.684        1.074          0.925
                        2        0.538              11.398         0.629        1.040          1.063
                        4        0.546              12.791         0.449        0.705          3.791
                        8        0.445              14.205         0.341        0.539          7.262
                 vm     1        0.916               1.075         0.853        1.012          1.114
                        2        0.886               1.144         0.714        0.924          1.179
                        4        0.853               1.606         0.506        0.677          1.951
                        8        0.839               1.719         0.464        0.108         10.169
float     cpu    ct     1        0.922               1.026         0.910        0.884          1.045
                        2        0.893               1.064         0.880        0.871          1.115
                        4        0.860               1.492         0.667        0.804          1.455
                        8        0.576              13.600         0.388        0.708          1.712
                 vm     1        0.918               1.044         0.904        1.045          1.109
                        2        0.897               1.069         0.886        0.903          1.259
                        4        0.871               1.365         0.688        0.649          2.038
                        8        0.853               1.659         0.543        0.166          8.735
          io     ct     1        0.826               1.543         0.833        0.572          1.943
                        2        0.851               1.395         0.858        0.280          6.467
                        4        0.822               1.491         0.835        0.232          7.543
                        8        0.820               1.664         0.821        0.175         10.286
                 vm     1        0.855               1.412         0.859        0.619          1.705
                        2        0.846               1.407         0.848        0.331          5.042
                        4        0.829               1.414         0.816        0.257          6.002
                        8        0.818               1.544         0.817        0.211          7.808
          mem    ct     1        0.917               1.057         0.851        0.835          1.142
                        2        0.889               1.134         0.711        0.815          1.322
                        4        0.861               1.601         0.494        0.608          1.792
                        8        0.541              13.945         0.376        0.561          2.442
                 vm     1        0.927               1.051         0.865        0.971          1.107
                        2        0.894               1.136         0.718        0.844          1.270
                        4        0.860               1.561         0.504        0.594          2.177
                        8        0.845               1.671         0.457        0.150         10.073

Drift check (post / baseline):

metric           rel_cpu_eps             rel_io_iops            
                        mean   min   max        mean   min   max
policy    victim                                                
dedicated ct            1.00  0.98  1.01        0.99  0.94  1.04
          vm            1.00  0.99  1.01        1.00  0.98  1.01
default   ct            1.00  0.99  1.02        1.13  0.97  1.30
          vm            1.00  0.98  1.01        1.20  0.81  1.42
float     ct            0.99  0.95  1.01        0.97  0.88  1.02
          vm            1.00  0.99  1.01        0.99  0.85  1.05

Memory bandwidth under memory stress, n = 8 (rel mean / min / max):

                  mean   min   max
policy    victim                  
dedicated ct      0.45  0.43  0.47
          vm      0.47  0.46  0.48
default   ct      0.34  0.31  0.39
          vm      0.46  0.45  0.47
float     ct      0.38  0.33  0.40
          vm      0.46  0.45  0.47

Disk under disk stress, n = 8 (absolute means, relative IOPS):

metric            io_iops  io_p99_us  rel_io_iops
policy    victim                                 
dedicated ct      7277.68    5786.82         0.17
          vm      7181.08    5878.58         0.26
default   ct      7758.22    5446.04         0.22
          vm      7686.52    5826.14         0.22
float     ct      7398.32    5629.54         0.18
          vm      7722.36    5957.22         0.21

VM disk IOPS under CPU stress (rel mean / min / max by n):

             mean   min   max
policy    n                  
dedicated 1  1.02  1.00  1.04
          2  1.02  0.97  1.04
          4  0.78  0.74  0.81
          8  0.65  0.64  0.66
default   1  1.01  0.81  1.27
          2  0.95  0.68  1.29
          4  0.76  0.67  0.87
          8  0.13  0.09  0.16
float     1  1.05  0.90  1.14
          2  0.90  0.89  0.94
          4  0.65  0.57  0.75
          8  0.17  0.13  0.20

