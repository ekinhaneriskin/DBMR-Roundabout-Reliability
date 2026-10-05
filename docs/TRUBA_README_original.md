# DBMR TRUBA 100-seed × 6-geometry campaign

## Frozen scientific design

Behavioral ladder remains **L0-L4 (five levels)**. No L5 is added.

Geometry family:

| Scenario | Double exits | Pattern |
|---|---:|---|
| S1 | 0 | all single |
| S4 | 1 | N double |
| S5 | 2 | adjacent N/E double |
| S2 | 2 | opposite N/S double |
| S6 | 3 | N/E/S double |
| S3 | 4 | all double |

Core design:

- demands: 3000, 3200 veh/h
- seeds: 100
- per scenario-demand-seed: 1 CONTROL + 12 DYNAMIC
- total: 6 × 2 × 100 × 13 = **15,600 runs**
- controls: 1,200
- dynamic: 14,400
- CRN pair groups: 1,200
- falsification 1800/2600 remains separate

The exact frozen first 50 seeds from V1 are preserved. Fifty deterministic
additional seeds are appended by the V2 configuration audit.

## Why the TRUBA array is by seed

One array element = one `seed_index`.

Each array element executes:

- 6 scenarios
- 2 demands
- 13 conditions

= **156 runs per array element**.

The SLURM array is `0-99%48`; each array element requests four CPUs and launches
four validated DBMR workers. Maximum requested concurrency is therefore
48 × 4 = 192 CPU cores.

## Pipeline

`submit_dbmr_truba_pipeline.sh` submits one dependency chain:

1. zero-simulation configuration/network audit
2. 8-run preflight
3. 100-element resumable seed array
4. no-simulation final aggregation/audit

If any earlier stage fails, later stages do not start.

## Required project files

Put this bundle in the root of `DBMR_M4_Final` alongside at least:

- `dbmr_final_model.py`
- `dbmr_final_validation.py`
- `outputs/DBMR_MODEL_FORM_FREEZE_V1/`
- `outputs/DBMR_FINAL_VALIDATION_V1/`
- `outputs/DBMR_FINAL_CAMPAIGN_CONFIG_V1/FINAL_CAMPAIGN_SEEDS.json`

Transferring the full project except the macOS `.venv` is the safest option.

## TRUBA environment

The scripts require:

- Linux Python capable of importing `numpy`, `pandas`, `tqdm`
- SUMO **1.27.1**
- `sumo` and `netconvert` on `PATH`
- Python `libsumo` importable

`dbmr_truba_runtime.sh` first loads TRUBA `comp/python/3.12.2` unless
`DBMR_PYTHON` is supplied.

If SUMO is under `$HOME/opt/sumo-1.27.1`, the runtime script finds it
automatically. Otherwise export `SUMO_HOME` before submission.

Do not submit the pipeline until:

```bash
bash dbmr_truba_check_environment.sh
```

ends with:

```text
ENVIRONMENT_CHECK_OK: True
```

## One-command launch

After environment check passes:

```bash
bash submit_dbmr_truba_pipeline.sh
```

The submission script prints four job IDs.

Monitor:

```bash
squeue -u "$USER"
```

and:

```bash
sacct -j <JOBIDS> --format=JobID,JobName,State,Elapsed,ExitCode
```

Logs:

```bash
ls -lh truba_logs/
```

## Resumability

The validated DBMR `run_jobs()` method skips a run when its per-run summary JSON
exists and has an empty error field.

Therefore:
- a node failure does not invalidate completed runs;
- resubmitting a seed task reruns only missing/error runs;
- array tasks are isolated by seed index;
- final aggregation requires all 100 seed-task reports to be PASS.

## Final outputs

After aggregate job succeeds:

```text
outputs/DBMR_FINAL_CAMPAIGN_V2_100x6/reports/
```

contains:

- `CORE_RUN_LEVEL_RESULTS_100x6.csv`
- `SYSTEM_PAIRED_EFFECTS_100x6.csv`
- `PRIVATE_BENEFIT_PAIRED_100x6.csv`
- `CRN_ASSIGNMENT_AUDIT_100x6.csv`
- `FINAL_CAMPAIGN_AUDIT_100x6.json`

Private-benefit sign is frozen as:

```text
delta_TT_adv_s = TT_control - TT_dynamic
```

Positive means the dynamic/adaptive run is individually advantageous in
travel-time terms.

The private-benefit table includes:
- median ΔTT
- p90 ΔTT
- winner share
- loser share
- ALL / ELIGIBLE / NONELIGIBLE / ESCALATED groups

`ESCALATED` remains post-treatment descriptive only.

## Current interpretation guardrail

Absolute L2/L4 braking and jerk remain model-form sensitive. Collision/teleport
counts are simulation robustness indicators and must not be interpreted as
real-world crash probabilities.
