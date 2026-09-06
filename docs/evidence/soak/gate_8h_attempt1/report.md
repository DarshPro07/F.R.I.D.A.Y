# A-051 soak report - INCONCLUSIVE

ran 31763.1 s of 28800.0 planned; 8941 cycles; 958 samples; window 3600 s after a 1588 s warm-up


INCONCLUSIVE: 1 sampling gap(s) - the host slept or the process was starved, so part of the window was never measured: 12130s at t=19633
## series (first window median -> last window median)

| series | first | last | delta | max | monotonic growth |
|---|---|---|---|---|---|
| rss_mb | 236.77 | 457.18 | 220.41 | 510.21 | **YES** |
| handles | 604.0 | 628.0 | 24.0 | 628.0 | no |
| threads | 6.0 | 6.0 | 0.0 | 11.0 | no |
| children | 4.0 | 6.0 | 2.0 | 8.0 | no |
| sqlite_conns | 3.0 | 3.0 | 0.0 | 6.0 | no |
| tokens | 62292.0 | 166890.0 | 104598.0 | 166890.0 | **YES** |
| provider_calls | 10012.0 | 26822.0 | 16810.0 | 26822.0 | **YES** |
| log_lines | 3337.0 | 8941.0 | 5604.0 | 8941.0 | **YES** |
| queue_depth | 9.0 | 1.0 | -8.0 | 21.0 | no |
| cpu_pct | 85.2 | 0.1 | -85.1 | 100.2 | no |
| host_ram_pct | 95.6 | 97.5 | 1.9 | 99.0 | no |
| host_cpu_pct | 15.5 | 0.2 | -15.3 | 30.6 | no |

host during the run: RAM 95.6% -> 97.5% (max 99.0%), CPU max 30.6%; workers shed by the governor: 0; governor thresholds: relaxed (RAM/CPU critical lines raised for this run - shedding NOT under test)

## RSS attributed per move (in-run, not a probe)

| move | calls | total KB | KB/call |
|---|---|---|---|
| hermes | 8047 | 3233812 | +401.9 |
| hermes_crash | 894 | 256588 | +287.0 |
| hermes_stop_start | 357 | 22856 | +64.0 |
| scheduler | 8941 | 11916 | +1.3 |
| handoff | 8941 | 156 | +0.0 |
| budget | 2980 | -12824 | -4.3 |
| cancel | 2235 | -16936 | -7.6 |
| provider | 8941 | -66432 | -7.4 |
| db | 8941 | -102540 | -11.5 |
| worker_crash | 596 | -144952 | -243.2 |
| nonce | 8941 | -725712 | -81.2 |
| objective | 8941 | -2021520 | -226.1 |

## workload per hour

- budget_refused: 337.8
- budget_resumed: 337.8
- cancels: 253.3
- db_writes: 50668.2
- handoffs: 1013.4
- hermes_crash_recovered: 101.3
- hermes_delegate: 1013.4
- hermes_stop_start: 40.5
- nonces: 1013.4
- objective: 1013.4
- objective_completed: 810.7
- objective_partial: 202.7
- provider_failover_ok: 1013.4
- provider_ok: 1013.4
- scheduler_claims: 1013.4
- worker_crashes_recovered: 67.6

## violations (0)

- none

## errors (0)

- none

FAIL: monotonic growth on rss_mb
