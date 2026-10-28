# Eval comparison — eval_20261028T161111Z

Procedure: reviewed 2026-10-28 · layout: layout-b · cases: 9

| agent | independently correct | interventions | avg ms | model calls |
| --- | --- | --- | --- | --- |
| demo-assisted | 9/9 | 3 | 1.4 | 0 |
| instructed-baseline | 6/9 | 4 | 1.8 | 0 |
| macro-baseline | 0/9 | 9 | 1.1 | 0 |

## Reading
- demo-assisted: follows the reviewed procedure; M4 rejects surface to a human.
- instructed-baseline: guesses blank as zero, self-confirms zero, abandons rejects.
- macro-baseline: replays 3 recorded steps against unknown IDs; resolves nothing.

Metric definitions and limitations: see comparison.json.
