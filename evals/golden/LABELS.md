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
