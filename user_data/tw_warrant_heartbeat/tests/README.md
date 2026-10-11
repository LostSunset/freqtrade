# Product classification regressions

Run from the repository root using Python 3.10+ (install `tzdata` on Windows):

```powershell
python -m unittest discover -s user_data/tw_warrant_heartbeat/tests -v
```

The tests use exact official reference rows in `fixtures/official_product_references.json`,
with source URLs, capture time and full-payload SHA256 values. Synthetic variants exercise
missing fields, conflicting direction, CBBC suffixes and Top100 replacement. No live
exchange or broker access is needed.

The shared classifier runs in both market builders, before the ranking universe is formed.
TWSE [warrant introduction](https://www.twse.com.tw/downloads/zh/stockSearch/warrant_introduce_zh.pdf)
pages 2–3 distinguish plain warrants from C/B/X/Y bull/bear and extendable certificates.
The classifier corroborates code, official name and call/put direction, uses TWSE `類別`,
and vetoes TPEx positive cap/floor evidence. A missing or conflicting required field is
quarantined as unknown. Plain reset warrants remain eligible. New official categories
require review before admission.

The v6 cache returns raw official reference records after coverage validation; the same
builder classifier then handles live and cache records. Coverage validation is not the
product filter. Each excluded record has a reason and its source evidence in the output's
`data_status`; accepted ranked records also retain classification evidence.

The production workflow's same-day precheck requires both markets to use the current
classification policy before skipping regeneration. Its embedded Python is exercised by
`test_snapshot_precheck.py`, including old same-day snapshots and normal no-change runs.

For a captured official snapshot, run the actual v6/v5 pipeline offline:

```powershell
python user_data/tw_warrant_heartbeat/tests/replay_official_inputs.py --code-dir user_data/tw_warrant_heartbeat --inputs-dir PATH_TO_INPUTS --output-dir NEW_OUTPUT_DIRECTORY --mode live
python user_data/tw_warrant_heartbeat/tests/replay_official_inputs.py --code-dir user_data/tw_warrant_heartbeat --inputs-dir PATH_TO_INPUTS --output-dir ANOTHER_NEW_DIRECTORY --mode cache
```

The input directory contains `manifest.json` and gzip JSON payloads. Each manifest source
has `file`, `sha256` (decompressed bytes), `url` and `ok`; it also records `base_sha`,
`execution_date` and `close_date`. The replay verifies every hash before and after execution,
blocks network calls, uses captured history for carry-forward prices, and writes outside
the input directory. Cache mode simulates reference endpoint failures and exercises real
gzip cache fallback and coverage checks. Runtime/fallback metadata can differ between modes;
the ranking, funnel, candidates and classification audit must match.

This change does not alter ranking, price or expiry thresholds, or fill missing volumes.
The added unknown-volume count exposes that separate limitation; it does not establish
complete market-volume coverage.
