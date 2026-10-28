# How the gold labels are made

Every label in `routing.jsonl` is written by `evals/generate.py` from the tuple that builds the case, never by
a model. Regenerate with `python -m evals.generate`; a test fails if the committed files drift from it.

| Category | Gold | Built from |
|---|---|---|
| explicit | reuse the target | message names the client and workflow (one template names the agent verbatim) |
| entity | reuse the target | message carries only a policy number, email or thread id from the target's log |
| paraphrase | reuse the target | business type + city + a workflow synonym; negatives share the type or the city |
| pronoun | reuse the target | history holds the target's report; message is "tell her yes" style |
| near_duplicate | reuse the target | spouses, same-name businesses in two cities, endorsement vs claim, 2025 vs 2026 renewal |
| should_create | create | new client, or same client with a different task, or a new reminder |
| ambiguous | clarify between both targets | two open COIs for the client named in the history |
| parallel | both targets | one message, two unrelated matters |
| agent_message | reuse the target | an email-watcher notification carrying the target's entities |

`target_ids` and `hard_negative_ids` are always in the case's roster; `client_key` marks whose matter it is,
so a reuse of another client's agent is reported as cross-client. Split: template `t0` of each category is
`heldout` (for tuning k and thresholds), the rest is `report`.

## Round two: `routing_v2.jsonl`

Written by `python -m evals.generate --version 2` (seed 20261029) into `routing_v2.jsonl` and
`fixtures/agents_pool_v2.jsonl`, and locked by `routing_v2.sha256`; a test fails if either file drifts from the
generator or the hash. Labels come from the tuples exactly as in round one, with the same pool construction.
What differs:

- New clients: surnames, business stems, first names, contacts, cities and occupations are disjoint from the
  round-one case lists and from the filler lists.
- New templates: two per category (three for `near_duplicate`), ids `<category>.v2t<n>`, with different sentence
  shapes. Every case is `split=report`: nothing is tuned on this set. Case ids start with `v2-`.
- The policy line: round one drew a matter's line again even when the agent's name already carried one, so in
  `agents_pool.jsonl` 1,147 of the 2,433 auto/home matters name one line over the other line's policy. A
  `World(seed, version=2)` keeps the caller's line (0 such mismatches in `agents_pool_v2.jsonl`); round-one files
  regenerate unchanged.
- `discriminators`: for each gold agent, the term in its record that tells it apart from the hard negatives
  (for `create`, the term the new matter differs on; empty for `ambiguous`). A test checks that each one is in
  the gold agent's record.

| Template | Gold | What the message relies on |
|---|---|---|
| explicit.v2t0 | reuse | client named; the same workflow is open on both of its lines, so the line decides |
| explicit.v2t1 | reuse | client named, workflow label; the client has a second matter open |
| entity.v2t0 | reuse | only the claim number from the target's last report |
| entity.v2t1 | reuse | only the policy number; all three agents are with the same carrier |
| paraphrase.v2t0 | reuse | occupation + workflow synonym, no name or city; negatives share the city or the occupation |
| paraphrase.v2t1 | reuse | city + business-type paraphrase + workflow synonym; negatives share one of the two |
| pronoun.v2t0 | reuse | replies from a woman and a man in the history (order alternates); only "her"/"him" picks |
| pronoun.v2t1 | reuse | one business reply in the history; "go back to them"; another business got the same ask |
| near_duplicate.v2t0 | reuse | two businesses with the same stem in the same city; the trade decides (alternating gold) |
| near_duplicate.v2t1 | reuse | one client renewing auto and home; "car" or "house" decides (alternating gold) |
| near_duplicate.v2t2 | reuse | two claims for one client; the date of loss decides (alternating gold) |
| should_create.v2t0 | create | the client has a quote on one line and asks for the other line priced |
| should_create.v2t1 | create | a new client who shares occupation and city with an existing one |
| ambiguous.v2t0 | clarify | two claims for the client in the history; the message gives no date |
| ambiguous.v2t1 | clarify | two clients with the same first name, each with a renewal |
| parallel.v2t0 | both | a bulleted list: one matter by policy number, one by client name |
| parallel.v2t1 | both | one by business-type paraphrase + city, one by client name |
| agent_message.v2t0 | reuse | a carrier notice carrying only the policy number; same-carrier negatives |
| agent_message.v2t1 | reuse | the client's own reply about a workflow synonym; the client has a second matter open |

### Hand-check sample

Twenty case ids drawn by `evals.generate.hand_check_sample` (`random.Random(20261029).sample` over the 110 case
ids, sorted). The owner reads each case against its tuple and marks agree or dispute. A disputed case leaves the
headline only if its tuple is wrong, never because of its result.

| Case id | Agree / dispute | Note |
|---|---|---|
| `v2-ambiguous-002` | | |
| `v2-ambiguous-003` | | |
| `v2-ambiguous-004` | | |
| `v2-ambiguous-007` | | |
| `v2-entity-005` | | |
| `v2-entity-010` | | |
| `v2-explicit-012` | | |
| `v2-near_duplicate-003` | | |
| `v2-near_duplicate-005` | | |
| `v2-near_duplicate-007` | | |
| `v2-near_duplicate-017` | | |
| `v2-parallel-005` | | |
| `v2-paraphrase-003` | | |
| `v2-paraphrase-004` | | |
| `v2-paraphrase-008` | | |
| `v2-paraphrase-009` | | |
| `v2-paraphrase-011` | | |
| `v2-pronoun-005` | | |
| `v2-should_create-012` | | |
| `v2-should_create-015` | | |
