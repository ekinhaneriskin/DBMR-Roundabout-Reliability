#!/usr/bin/env python3
from pathlib import Path
import hashlib, json
import pandas as pd

ROOT = Path(__file__).resolve().parent

def sha256(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for chunk in iter(lambda:f.read(1<<20), b''):
            h.update(chunk)
    return h.hexdigest()

expected_hashes = {
    'dbmr_final_model.py': '2b643c2f2e47e3fa6f7ca11a118e00c9167086addfb15843adb8d2016c6a626b',
    'dbmr_final_validation.py': 'd902014773a09a42c5d33cef9a8fe4e419c89a21ecf04bda4fca7bad8e16d009',
    'outputs/DBMR_FINAL_VALIDATION_V1/DBMR_FINAL_SPEC_CANDIDATE.json': '4b6aa902cfb90409e70031b0a62210cc5dfba9c5da81b1f6f22851d8e14cdcb7',
}
for rel, exp in expected_hashes.items():
    obs=sha256(ROOT/rel)
    assert obs==exp, f'hash mismatch: {rel}\n{obs}\n{exp}'

counts={
    'results/CORE_RUN_LEVEL_RESULTS_100x6.csv':15600,
    'results/SYSTEM_PAIRED_EFFECTS_100x6.csv':14400,
    'results/PRIVATE_BENEFIT_PAIRED_100x6.csv':57600,
}
for rel,n in counts.items():
    got=sum(1 for _ in open(ROOT/rel,encoding='utf-8'))-1
    assert got==n, f'row-count mismatch: {rel} {got} != {n}'

audit=json.load(open(ROOT/'results/FINAL_CAMPAIGN_AUDIT_100x6.json'))
assert audit.get('campaign_pass') is True
assert all(audit.get('checks',{}).values())

prov=json.load(open(ROOT/'provenance/FINAL_100_SEEDS_USED_V2_1.json'))
runs=pd.read_csv(ROOT/'results/CORE_RUN_LEVEL_RESULTS_100x6.csv',usecols=['seed'])
observed=list(dict.fromkeys(runs['seed'].astype('int64').tolist()))
assert observed==prov['seeds']
assert len(observed)==100 and max(observed)<=2_147_483_647 and min(observed)>0

print('REPO_INTEGRITY_CHECK_PASS: True')
print('final_campaign_status:', audit.get('status'))
print('run_rows: 15600')
print('system_pair_rows: 14400')
print('private_benefit_rows: 57600')
print('seed_n: 100')
