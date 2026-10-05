# V2.1 seed-domain compatibility correction

## Why it was needed

The predeclared V2 campaign used a deterministic `numpy.random.SeedSequence(...).generate_state(..., dtype=uint32)` stream. During TRUBA production, SUMO 1.27.1 rejected seed values above `2,147,483,647`, revealing an orchestration compatibility issue rather than a scientific/model failure.

## Frozen correction

The correction maps the raw predeclared seed stream to the positive signed-31-bit domain:

```text
seed31 = seed32 & 0x7fffffff
```

This preserves seed index/order. In the actual 100-seed campaign:

- 56 values were already in range and were unchanged;
- 44 values were deterministically mapped;
- the resulting 100 values remained unique;
- the validated 12-seed prefix remained numerically unchanged;
- no behavioral, geometry, demand, or model parameter changed.

## Provenance files

- `provenance/pre_v2_1/FINAL_100_SEEDS_RAW_UINT32_PRE_FIX.json` — raw pre-correction V2 stream
- `provenance/FINAL_100_SEEDS_USED_V2_1.json` — exact normalized seed vector, verified against the final run-level results
- `provenance/pre_v2_1/dbmr_final_campaign_configuration_audit_v2_100x6_PRE_FIX.py` — original pre-fix configuration-audit source supplied with the TRUBA bundle
- root `dbmr_final_campaign_configuration_audit_v2_100x6.py` — corrected V2.1 reproducibility version

This was an implementation-domain correction, not post-hoc model tuning.
