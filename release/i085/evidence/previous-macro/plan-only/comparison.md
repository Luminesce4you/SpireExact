# i082 / i085 paired native comparison

This report audits recorded native replay artifacts. Reporting itself executes no native replay.

| Run | State | Audited verified win | First verified seconds | Native seconds (known only) | Unknown cost rows |
|---|---|---:|---:|---:|---:|
| 101-s271828-i082 | not_run | False | None | unknown | unknown |
| 101-s271828-i085 | not_run | False | None | unknown | unknown |
| 101-s271828-i085-routes | not_run | False | None | unknown | unknown |
| 42-s271828-i085 | not_run | False | None | unknown | unknown |
| 42-s271828-i085-routes | not_run | False | None | unknown | unknown |
| 42-s271828-i082 | not_run | False | None | unknown | unknown |

## Paired results

Negative restricted-time deltas favor the candidate. Unsolved runs use the common time cap; missing wins timestamps are not imputed.

### i085

Auditable pairs: 0; candidate-only wins: 0; i082-only wins: 0.
Mean restricted-time delta: None s; seed-cluster interval: None.
Omitted pairs: 2. See JSON for exact reasons.

### i085-routes

Auditable pairs: 0; candidate-only wins: 0; i082-only wins: 0.
Mean restricted-time delta: None s; seed-cluster interval: None.
Omitted pairs: 2. See JSON for exact reasons.

## Detailed evidence

`comparison.json` contains per-kind native/beam/prefix costs with missingness, exact gate-entry counts, F1/F2 records, cache statistics, macro/route telemetry, resource classifications and verification failures.

Stage crossings are timed at completed-result availability, not at the unrecorded instant inside a rollout.
Synthetic samples never count as gate passes or verified wins. Empty or interrupted runs remain listed.
Intervals with few game seeds are descriptive and should not be used to declare a production improvement.
