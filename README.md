# DBMR Roundabout Reliability

Reproducibility package for the manuscript **“Congestion-Induced Behavioral Escalation and Dynamic Reliability at Oversaturated Roundabouts”** by Ekinhan Eriskin, Serdal Terzi, and Halim Ceylan.

## Study design

The Dynamic Behavioral Mixture Reliability (DBMR) experiment evaluates whether congestion-induced behavioral adaptation changes mobility, finite-horizon reliability, and user-level outcomes at oversaturated roundabouts.

The final audited campaign contains **15,600 SUMO runs**:

- 6 exit-capacity geometries (S1, S4, S5, S2, S6, S3),
- 2 core demands (3000 and 3200 veh/h),
- 100 Monte Carlo seeds,
- 1 all-L0 control plus 12 dynamic conditions per geometry-demand-seed group,
- dynamic conditions: `p = {0.25, 0.50, 0.75, 1.00}` × `T_up = {15, 30, 60} s`.

All vehicles enter at L0. `p` is the fraction **susceptible/eligible to adapt under experienced congestion**, not an initial aggressive-driver share.

## Final campaign status

The supplied final audit reports:

- `CORE_15600_CAMPAIGN_COMPLETE`,
- 100/100 seed tasks passed,
- 15,600 run-level results,
- 14,400 paired system-effect rows,
- 57,600 paired private-benefit rows,
- common-random-number integrity passed,
- mechanical integrity passed.

See `results/FINAL_CAMPAIGN_AUDIT_100x6.json`.

## Repository layout

```text
README.md
CITATION.cff
requirements.txt

# Frozen scientific/runtime code (root layout preserves original relative paths)
dbmr_final_model.py
dbmr_final_validation.py
dbmr_six_scenario_networks_v2.py
dbmr_final_campaign_configuration_audit_v2_100x6.py
dbmr_campaign_v2_common.py
dbmr_final_campaign_preflight_v2_100x6.py
dbmr_truba_seed_array_worker_v2.py
dbmr_truba_aggregate_v2.py
*.slurm / *.sh

outputs/                       Minimal frozen provenance needed by the runners
results/                       Final aggregated 100-seed × 6-geometry outputs
validation/openDD/             Derived openDD plausibility/validation summaries only
docs/                          Reproducibility and methods notes
provenance/                    Seed-domain correction and archive provenance
```

## Software environment

The final campaign used:

- Eclipse SUMO **1.27.1**,
- Python **3.12** on TRUBA,
- simulation step `0.2 s`,
- behavioral pressure polling interval `1.0 s`,
- lateral resolution `0.05 m`,
- demand injection from `t = 0` to `3600 s`,
- simulation continuation to `4200 s`.

**There is no separate 600-s warm-up in the final campaign.** The final 600 s are a post-demand clearance period after the 3600-s demand injection horizon.

Python packages required by the included scripts are listed in `requirements.txt`. SUMO/libsumo and netconvert must be installed separately.

## DBMR congestion-pressure mechanism

At each 1-s behavioral poll:

```text
speed_deficit = clip(1 - speed / allowed_speed, 0, 1)
wait_share    = clip(accumulated_wait / elapsed, 0, 1)
excess        = max(0, elapsed - freeflow)
delay_share   = excess / (excess + freeflow)
local_halt    = clip(halting_vehicles / vehicles_on_current_edge, 0, 1)

memory_burden = 1 - (1 - wait_share)(1 - delay_share)
local_context = 1 - (1 - speed_deficit)(1 - local_halt)
P             = clip(memory_burden * local_context, 0, 1)

q_up = 1 - exp[-(P / T_up) * dt]
```

Eligible vehicles can progress only one level per poll: `L0 → L1 → L2 → L3 → L4`. Exact parameter vectors are documented in `docs/METHODS_REPRODUCIBILITY.md` and frozen in `outputs/DBMR_FINAL_VALIDATION_V1/DBMR_FINAL_SPEC_CANDIDATE.json`.

## Collapse detector

A run is marked collapsed at the first behavioral-poll time satisfying all of the following:

1. `t > 60 s`;
2. at least one vehicle remains active;
3. either no active vehicle has speed above `0.3 m/s` for more than `30 s`, **or** no vehicle arrival has occurred for more than `300 s`; and
4. the total number of halted vehicles on the four approach edges is **greater than 6**.

The diagnostic is non-terminating: the simulation continues after the first collapse timestamp is recorded.

## V2.1 SUMO seed-domain correction

The originally predeclared V2 100-seed stream was generated in the `uint32` domain. SUMO 1.27.1 rejects seed values above the positive signed-32-bit range. Before completion of the final campaign, the orchestration layer was corrected without changing the behavioral model:

```text
seed31 = seed32 & 0x7fffffff
```

Seed-index order was preserved, 56 already-valid seeds were unchanged, and 44 out-of-range values were deterministically mapped to the SUMO-compatible domain. The final 100 seeds in the results exactly match `provenance/FINAL_100_SEEDS_USED_V2_1.json`. The pre-fix audit script and raw `uint32` seed vector are retained under `provenance/pre_v2_1/` for transparency.

The root `dbmr_final_campaign_configuration_audit_v2_100x6.py` includes this V2.1 compatibility correction and an explicit SUMO seed-range gate.

## openDD use

Raw openDD trajectories are **not redistributed** here. The files under `validation/openDD/` are derived aggregate artifacts used for empirical plausibility assessment, falsification, and model-form diagnosis. They were **not** used as a full parameter-calibration dataset for the final DBMR campaign.

The accepted-gap quantity in these artifacts is a **geometric temporal-clearance proxy at detected roundabout entry** and must not be interpreted as an empirical critical gap.

Reference:

Breuer, A., Termöhlen, J.-A., Homoceanu, S., & Fingscheidt, T. (2020). *openDD: A Large-Scale Roundabout Drone Dataset*. IEEE ITSC. https://doi.org/10.1109/ITSC45102.2020.9294301

`HOMOLOGOUS_VALIDATION_DECISION_PRE_FREEZE.json` is retained as an intermediate historical audit. Its pre-freeze decision was superseded by the later explicit model-form freeze in `outputs/DBMR_MODEL_FORM_FREEZE_V1/DBMR_MODEL_FORM_FREEZE_V1.json`; it should not be read as the final campaign status.

## Reproducing the configuration and campaign

The repository root intentionally mirrors the original runtime layout. Before a full rerun, confirm SUMO 1.27.1, `sumo`, `netconvert`, and importable `libsumo`.

The original TRUBA dependency pipeline was:

1. zero-simulation configuration/network audit,
2. 8-run preflight,
3. seed-index production array,
4. final aggregation/audit.

See `docs/TRUBA_README_original.md` and the root SLURM/shell scripts. Full production execution can be computationally expensive; the final aggregate outputs needed to reproduce manuscript tables and statistics are already provided in `results/`.

## Results data

- `results/CORE_RUN_LEVEL_RESULTS_100x6.csv` — 15,600 run-level rows
- `results/SYSTEM_PAIRED_EFFECTS_100x6.csv` — 14,400 dynamic-control paired rows
- `results/PRIVATE_BENEFIT_PAIRED_100x6.csv` — 57,600 paired group summaries
- `results/CRN_ASSIGNMENT_AUDIT_100x6.csv` — common-random-number assignment audit
- `results/FINAL_CAMPAIGN_AUDIT_100x6.json` — final integrity gate

Run `python repo_integrity_check.py` for a lightweight repository-level verification that does not rerun SUMO.

## Data and code availability statement

A manuscript-ready statement is provided in `docs/DATA_AND_CODE_AVAILABILITY.md`. Replace the repository placeholder with the public GitHub URL after publication.

## Citation

A machine-readable citation is provided in `CITATION.cff`. The journal DOI can be added after publication.

## License

A public reuse license has **not yet been applied**. The authors should select the code and data licenses before the repository is made public. The proposed option is MIT for source code and CC BY 4.0 for derived data/validation artifacts.
