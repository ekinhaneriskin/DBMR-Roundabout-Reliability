# DBMR Roundabout Reliability

Code, configuration, validation summaries, and final aggregated outputs for **“Congestion-Induced Behavioral Escalation and Dynamic Reliability at Oversaturated Roundabouts”** by Ekinhan Eriskin, Serdal Terzi, and Halim Ceylan.

## Campaign

The final campaign contains **15,600 SUMO runs** across six exit-capacity geometries, two demand levels (3000 and 3200 veh/h), 100 Monte Carlo seeds, one all-L0 control condition, and 12 dynamic conditions defined by `p = {0.25, 0.50, 0.75, 1.00}` and `T_up = {15, 30, 60} s`.

All vehicles enter in L0. `p` is the fraction susceptible to congestion-induced adaptation, not an initial assertive-driver share. Eligible vehicles can progress sequentially through `L0 -> L1 -> L2 -> L3 -> L4` as congestion pressure accumulates.

The final audit reports 15,600 complete runs, 14,400 dynamic-control system pairs, 57,600 private-benefit summaries, 100/100 seed tasks passing, common-random-number integrity passing, and mechanical integrity passing.

## Repository contents

- `dbmr_final_model.py` — core DBMR simulation model
- `dbmr_final_validation.py` — network generation, validation, and paired-analysis utilities
- `dbmr_six_scenario_networks_v2.py` — six exit-capacity geometries
- `dbmr_final_campaign_configuration_audit_v2_100x6.py` — campaign matrix and network audit
- `dbmr_final_campaign_preflight_v2_100x6.py` — eight-run preflight
- `dbmr_truba_seed_array_worker_v2.py` — production worker
- `dbmr_truba_aggregate_v2.py` — final aggregation and integrity audit
- `config/` — final model specification and exact 100-seed list
- `results/` — final aggregated campaign outputs
- `validation/openDD/` — derived openDD validation summaries
- `docs/` — reproducibility and TRUBA execution notes
- `provenance/` — archived hash-matched campaign source snapshots

## Software

The final campaign used Eclipse SUMO **1.27.1**, Python **3.12**, a `0.2 s` simulation step, `1.0 s` behavioral polling, and `0.05 m` lateral resolution. Demand is injected through `t = 3600 s`; simulation continues to `t = 4200 s` for clearance.

Python dependencies are listed in `requirements.txt`. SUMO/libsumo and `netconvert` must be installed separately.

## Behavioral pressure

At each behavioral poll:

```text
speed_deficit = clip(1 - speed / allowed_speed, 0, 1)
wait_share    = clip(accumulated_wait / elapsed, 0, 1)
excess        = max(0, elapsed - freeflow)
delay_share   = excess / (excess + freeflow)
local_halt    = clip(halting_vehicles / vehicles_on_current_edge, 0, 1)

memory_burden = 1 - (1 - wait_share)(1 - delay_share)
local_context = 1 - (1 - speed_deficit)(1 - local_halt)
P             = clip(memory_burden * local_context, 0, 1)
q_up          = 1 - exp[-(P / T_up) * dt]
```

The full L0-L4 parameter ladder is in `config/DBMR_FINAL_SPEC.json` and `docs/METHODS_REPRODUCIBILITY.md`.

## Collapse definition

Collapse is recorded when `t > 60 s`, at least one vehicle remains active, the four-approach halted count exceeds 6, and either no active vehicle has exceeded `0.3 m/s` for more than 30 s or no arrival has occurred for more than 300 s. Recording collapse does not terminate the simulation.

## openDD validation

Raw openDD trajectories are not redistributed. The files in `validation/openDD/` are derived aggregate summaries used for empirical plausibility assessment, falsification, and model-form diagnosis. The openDD analysis was not used as full parameter calibration. L0-L4 are mechanistic response states rather than empirically discovered driver classes.

The accepted-gap measure is a geometric temporal-clearance proxy at detected roundabout entry and is not interpreted as a critical-gap estimate. Absolute L2/L4 braking and jerk are treated as model-sensitive quantities.

Reference: Breuer, A., Termöhlen, J.-A., Homoceanu, S., & Fingscheidt, T. (2020). *openDD: A Large-Scale Roundabout Drone Dataset*. IEEE ITSC. https://doi.org/10.1109/ITSC45102.2020.9294301

## Results

The final result files are:

- `results/CORE_RUN_LEVEL_RESULTS_100x6.csv`
- `results/SYSTEM_PAIRED_EFFECTS_100x6.csv`
- `results/PRIVATE_BENEFIT_PAIRED_100x6.csv`
- `results/CRN_ASSIGNMENT_AUDIT_100x6.csv`
- `results/FINAL_CAMPAIGN_AUDIT_100x6.json`

Run `python repo_integrity_check.py` to verify result row counts, campaign audit status, current release files, and the archived campaign-source hashes.

## Reproduction

For a TRUBA rerun, first run:

```bash
bash dbmr_truba_check_environment.sh
bash submit_dbmr_truba_pipeline.sh
```

The dependency chain performs configuration/network audit, preflight, production array execution, and final aggregation. See `docs/TRUBA_EXECUTION.md`.


## License

Source code and scripts in this repository are licensed under the **MIT License**. See `LICENSE`.

Repository-produced aggregated results and derived validation data are licensed under the **Creative Commons Attribution 4.0 International (CC BY 4.0) License**. See `DATA_LICENSE.md`.

Raw openDD data are not redistributed in this repository and remain subject to the terms of the original openDD source.

## Citation

Citation metadata are provided in `CITATION.cff`.
