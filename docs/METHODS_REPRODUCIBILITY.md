# Methods reproducibility

## Runtime settings

- Eclipse SUMO 1.27.1
- Python 3.12
- simulation step: 0.2 s
- behavioral polling: 1.0 s
- lateral resolution: 0.05 m
- demand injection: 0-3600 s
- simulation end: 4200 s

The final 600 s form a post-demand clearance period, not a warm-up.

## Pressure index

```text
speed_deficit = clip(1 - speed / max(allowed_speed, 0.1), 0, 1)
wait_share = clip(accumulated_wait / max(elapsed, 1.0), 0, 1)
excess = max(0, elapsed - freeflow)
delay_share = excess / (excess + max(freeflow, 1e-6))
local_halt = clip(halting_vehicles / vehicles_on_current_edge, 0, 1)
memory_burden = 1 - (1-wait_share)(1-delay_share)
local_context = 1 - (1-speed_deficit)(1-local_halt)
P = clip(memory_burden * local_context, 0, 1)
q_up = 1 - exp[-(P/T_up)dt]
```

Locked free-flow reference times are 30.6 s (right), 40.8 s (straight), and 51.0 s (left).

## Collapse detector

Collapse is first recorded when `t > 60 s`, at least one vehicle remains active, the total halted count on the four approach edges is greater than 6, and either no active vehicle has speed above 0.3 m/s for more than 30 s or no arrival has occurred for more than 300 s. The detector records the first collapse time and the run continues.

## L0-L4 parameter ladder

| Parameter | L0 | L1 | L2 | L3 | L4 |
|---|---:|---:|---:|---:|---:|
| tau | 2.1 | 1.54986 | 0.999729 | 0.670135 | 0.340541 |
| minGap | 3 | 2.5 | 2 | 1.75 | 1.5 |
| accel | 2.6 | 2.6 | 2.6 | 2.6 | 2.6 |
| decel | 4.5 | 4.38602 | 4.27205 | 4.45195 | 4.63186 |
| apparentDecel | 4.5 | 4.38602 | 4.27205 | 4.45195 | 4.63186 |
| emergencyDecel | 9 | 8.77205 | 8.54409 | 8.90391 | 9.26373 |
| sigma | 0.2 | 0.325 | 0.45 | 0.585 | 0.72 |
| speedFactor | 0.69528 | 0.716195 | 0.73711 | 0.758025 | 0.77894 |
| speedDev | 0.04 | 0.0288247 | 0.0176493 | 0.0434837 | 0.069318 |
| minGapLat | 0.85 | 0.806472 | 0.762944 | 0.631472 | 0.5 |
| maxSpeedLat | 0.65 | 0.8 | 0.95 | 1.2 | 1.45 |
| lcSpeedGain | 0.6 | 0.323096 | 0.0461923 | 0.02643 | 0.00666764 |
| lcKeepRight | 1 | 0.549338 | 0.0986752 | 0.053407 | 0.00813879 |
| lcSublane | 0.75 | 0.7455 | 0.740999 | 0.8705 | 1 |
| lcAssertive | 0.75 | 0.925 | 1.1 | 1.45 | 1.8 |
| lcImpatience | 0 | 0.07 | 0.14 | 0.295 | 0.45 |
| lcSigma | 0.06 | 0.085 | 0.11 | 0.145 | 0.18 |
| actionStepLength | 0.2 | 0.2 | 0.2 | 0.2 | 0.2 |
| latAlignment | center | center | nice | nice | arbitrary |
| lcPushy | 0 | 0.04 | 0.08 | 0.135112 | 0.190224 |

`lcPushy` is active only when ego speed is below 10 km/h, at least one neighbor lies within 10 m, and all neighbors within 10 m are also below 10 km/h. Otherwise effective `lcPushy = 0`.

## openDD plausibility summary

| Metric | openDD pooled p05-p95 | L0 | L2 | L4 |
|---|---:|---:|---:|---:|
| THW median (s) | 0.967-5.622 | 2.776 | 2.038 | 1.633 |
| THW p10 (s) | 0.778-4.931 | 2.172 | 1.357 | 0.839 |
| Accepted-gap proxy median (s) | 1.754-12.490 | 4.505 | 2.861 | 3.098 |
| Braking p95 (m/s²) | 0.202-3.163 | 0.903 | 4.272 | 4.632 |
| Jerk p95 (m/s³) | 1.319-3.908 | 3.700 | 11.154 | 13.047 |

THW and accepted-gap values are used as plausibility anchors. The accepted-gap proxy is not a critical-gap estimate. Absolute L2/L4 braking and jerk remain model-sensitive.
