#!/bin/bash
# One-command submission:
# config audit -> 8-run preflight -> 100-task seed array -> final aggregation
set -euo pipefail

DBMR_PROJECT="${DBMR_PROJECT:-$(pwd)}"
cd "$DBMR_PROJECT"
mkdir -p truba_logs

CFG_JOB="$(sbatch --parsable dbmr_truba_config_audit.slurm)"
PRE_JOB="$(sbatch --parsable --dependency=afterok:${CFG_JOB} dbmr_truba_preflight.slurm)"
ARRAY_JOB="$(sbatch --parsable --dependency=afterok:${PRE_JOB} dbmr_truba_seed_array.slurm)"
AGG_JOB="$(sbatch --parsable --dependency=afterok:${ARRAY_JOB} dbmr_truba_aggregate.slurm)"

cat <<EOF
DBMR TRUBA PIPELINE SUBMITTED
config_job:    ${CFG_JOB}
preflight_job: ${PRE_JOB}
array_job:     ${ARRAY_JOB}
aggregate_job: ${AGG_JOB}

Monitor:
  squeue -u \$USER
  sacct -j ${CFG_JOB},${PRE_JOB},${ARRAY_JOB},${AGG_JOB} --format=JobID,JobName,State,Elapsed,ExitCode

Live logs:
  ls -lh truba_logs/
EOF
