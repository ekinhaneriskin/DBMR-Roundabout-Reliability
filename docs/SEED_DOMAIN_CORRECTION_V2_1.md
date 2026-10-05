# V2.1 seed-domain correction

The predeclared V2 seed stream used `uint32` values. SUMO 1.27.1 accepts positive signed-32-bit seed values, so out-of-range values were mapped deterministically as:

```text
seed31 = seed32 & 0x7fffffff
```

Seed order was preserved. Of the 100 seeds, 56 were already valid and 44 were mapped. The resulting 100 seeds remained unique. No behavioral, geometry, demand, or model parameter changed.

The exact seed list used by the final campaign is stored in `config/FINAL_100_SEEDS_USED_V2_1.json`.
