# Duplicate-guard name threshold (held-out split)

From `20261028_baseline_minimax-m3_k8_5a8fd9a.json`: 10 names the model created for an agent that already existed, 18 names it created for a genuinely new matter, and 5 near-duplicate pairs from the generator. Only the name score is swept.

| threshold | missed merges | false merges | total |
|---|---|---|---|
| 0.50 | 10 | 10 | 20 |
| 0.55 | 10 | 0 | 10 |
| 0.60 | 10 | 0 | 10 |
| 0.65 | 10 | 0 | 10 |
| 0.70 | 10 | 0 | 10 |
| 0.75 | 10 | 0 | 10 |
| 0.80 | 10 | 0 | 10 |
| 0.85 | 10 | 0 | 10 |
| 0.90 | 10 | 0 | 10 |
| 0.95 | 10 | 0 | 10 |

Chosen: 0.95 (fewest mistakes; ties go to the higher threshold, which interrupts the model less). The server default is not changed by this script.
Every threshold from 0.55 to 0.95 gives the same counts. The closest a duplicate name came to its gold agent's name was 0.20, so the name score alone catches none of the duplicates the model made here; the guard's shared-id check is what can.
