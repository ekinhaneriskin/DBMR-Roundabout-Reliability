# TRUBA execution

## Design

The production matrix contains six geometries, two demand levels, 100 seeds, and 13 conditions per geometry-demand-seed combination, for 15,600 runs.

One SLURM array task corresponds to one seed index and executes 156 runs. The supplied array configuration is `0-99%48`, with four CPUs per task.

## Environment

Required:

- Python 3.12 with `numpy`, `pandas`, and `tqdm`
- SUMO 1.27.1
- `sumo` and `netconvert` on `PATH`
- importable `libsumo`

Check the environment:

```bash
bash dbmr_truba_check_environment.sh
```

## Submission

```bash
bash submit_dbmr_truba_pipeline.sh
```

The dependency chain runs:

1. configuration and network audit;
2. eight-run preflight;
3. 100-task seed array;
4. final aggregation and audit.

Monitor jobs with:

```bash
squeue -u "$USER"
sacct -j <JOBIDS> --format=JobID,JobName,State,Elapsed,ExitCode
```

Completed runs are resumable from their summary files. The final aggregation requires all seed-task reports to pass.
