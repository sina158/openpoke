<!-- DRAFT-FOR-SINA: facts are checked; rewrite the prose in your own words before submitting -->

# Known gaps

Ranked, most serious first. One line each: the gap, then the fix.

Triage for v2, updated with the tier-1 results (items 2, 10 and 17 are kept: reduced or changed by v2, not closed; 31-35 are new). Numbers are the round-1 numbers, so references from other files still resolve.

## Kept

1. **No auth on the HTTP API.** The server binds `0.0.0.0` by default (`OPENPOKE_HOST`), CORS is `*` by default, and `DELETE /chat/history` wipes everything with no check (`server/routes/chat.py`). Fix: an auth dependency on every route, default host `127.0.0.1`, a CORS allowlist.
2. **Cross-client messaging is reduced, not closed.** The v2 shared-client check (c1) refuses a turn's sends only when the targets look like the SAME client (shared multi-word name, email or id), and it is inert below 100 agents (fixed 2% document-frequency cut-off). On the fresh set at N=500 fix v2 still sent to two clients in 19 of 312 turns (B2a, fail) and reached another client's agent in 11 of 252 should-reuse case-trials (B2b, fail); `evals/results/RESULTS_v2.md`. Fix: refuse any turn whose sends reach agents of two clients unless the user named both, then a new recorded run.
3. **A draft send is bound to the draft id, not its contents.** `GMAIL_SEND_DRAFT` carries only `draft_id`, so the draft can be edited in Gmail after approval; the summary says "draft contents not shown" (`gmail.summarise_send`). Fix: fetch the draft when proposing, hash recipients, subject and body, re-fetch and compare before sending.
4. **The user sees a pending send through the model.** The summary and code are built in code, but reach the user inside the model's own reply; with two sends pending, which one a reply approves rests on the model relaying the right code next to the right summary. Fix: show pending actions in the UI straight from the approval store, one confirm control each.
5. **Single tenant by construction:** one registry, one conversation log, one Gmail user id; the `owner` column exists but nothing reads it. Fix: a tenant key on every store and route.
6. **No per-agent lock:** a trigger fire and a user follow-up can run the same agent at the same time. Fix: one asyncio lock per agent id in the batch manager.
7. **Retries outside email can repeat a side effect:** the single-use approval makes email at-most-once; any other tool that times out after acting and is retried acts twice. Fix: an idempotency key per tool call and an outbox.
8. **The summariser can carry email text into the user's "Preferences & Profile" memory** (`server/services/conversation/summarization/prompt_builder.py`). Fix: tag summary inputs by provenance and keep email and agent text out of profile facts.
9. **Every execution agent gets all 13 tools:** the same selection problem one level down. Fix: run the same selector over tool descriptions.
10. **The shortlist does not make a turn cheaper in prompt tokens.** At N=500 fix v2 carries 58.5% of the baseline's prompt per model call but makes about 1.9 calls a turn, so a turn sends 103.0% of the baseline's (B5, fail); the N=5,000 row, where the baseline's roster overloads, is not run yet. Fix: answer from the shortlist without a search round when a strong match is on it.
11. **One global batch:** a quick agent's result is held until every agent in the open batch has finished, each with a 90-second timeout (`server/agents/execution_agent/batch_manager.py`, `_complete_execution`). Fix: deliver each agent's result when it finishes.
12. **The Gmail watcher makes one LLM call per inbox email;** a broker inbox makes that a cost firehose. Fix: a rule prefilter and batched classification.
13. **Triggers re-fire after a crash and fire late after downtime.** Fix: persist the last fire time and define what happens to a missed window.
14. **Composio calls are synchronous on the event loop, and the 8-iteration cap ends a turn with an empty response** (`InteractionAgentRuntime.MAX_TOOL_ITERATIONS`). Fix: run the calls in a thread executor; tell the user when the cap is hit.
15. **Inherited third-party prompt text:** `prompts/interaction_full.md` (upstream, byte for byte), `prompts/interaction_shortlist.md` (a copy of it outside the rewritten paragraphs) and `server/agents/execution_agent/system_prompt.md` reproduce text from a leaked third-party assistant prompt. Fix: rewrite both prompts before any commercial use and re-run the eval.
16. **The duplicate check misses a duplicate that shares neither an id nor a similar name:** the name score caught none of the held-out duplicates at any threshold (`evals/results/DUPLICATE_THRESHOLD.md`); the shared-id check is what flags them (19 of the baseline's 27). Fix: also match the client's names and emails from the instructions, behind the same confirm step.
17. **`cards._first_sentence` stops at an abbreviation** (`server/services/execution/cards.py:68` `_SENTENCE_END`, `:181` `_first_sentence`): "Send Ms. Halloran the renewal forms..." becomes "Send Ms.". Found by the blind review after the measurement was frozen; not fixed because it changes card bytes the recorded runs replay against. Fix: skip a match that ends in Mr., Ms., Mrs., Dr., St., Jr. or Sr., then re-record.
20. **The dataset is synthetic and template-clustered:** 130 cases from 28 templates (19 in the report split), phrased by hand-written templates, with no second-model phrasing pass and no hand review of the gold labels yet. Fix: a phrasing pass by another model family, a hand check of every label, then shadow mode on real traffic.
23. **The injection suite proves the gate, not the model:** a fake model makes exactly the attacker's calls. Fix: a live-model run over the same attacks.
31. **Clarification almost never happens.** On the fresh set fix v2 asked the user in 2 of 30 ambiguous case-trials (clarify recall 6.7%, B4 fail), with 5 of 300 over-clarifications; `evals/results/RESULTS_v2.md`. Fix: a prompt or tool-side rule that clarifies when two agents of different clients both match, measured against the over-clarify rate.
32. **Every `should_create` fresh message says "new",** so hijack (B3) is measured only on cases where the user states it. Fix: add should_create cases that leave it implicit.
33. **The evidence arm cannot show retrieval here:** eval agent logs hold two entries, both already on the card, so it tests duplication; evidence is added outside the shortlist token budget; the v2 prompt explains neither `status="archived"` nor `evidence=`. Fix: longer logs in the fresh set and a prompt line for both attributes.
34. **Bounded execution memory (`OPENPOKE_EXECUTION_MEMORY=retrieved`) is covered by a unit test only** (`tests/test_execution_memory.py`: a 200-entry log goes from 5,190 to 246 history tokens); the routing eval stubs execution. Fix: score execution quality against history length.
35. **Cut for quota:** deepseek-v4-pro trials 2 and 3 (one trial only), the `cards_full` N=500 demo, the model-phrased set.

## Likely closed by v2 (candidates; moved to FAILURES.md only if the fresh-set results show it)

18. **`conversation_limit` is wired but unset by default,** so an execution agent still carries its whole history. Fix: choose a limit by measuring execution quality against history length.
   *Candidate: closed by the lifecycle simulation (measures execution prompt size with and without the history limit).*
21. **The generator picks the policy line at random even when the agent's name hard-codes it** (`evals/generate.py`, `World.matter` with `name=` given): 8 golden agents, and 1,147 of the 2,433 agents in `evals/fixtures/agents_pool.jsonl` whose name says "auto" or "home", have a log that names the other line. Left as is because the fix changes the golden file and every cassette. Fix: take the line from the name when one is given, regenerate, re-record.
   *Candidate: closed by the fresh set (generator reads the policy line from the name; confirm in the evals seat's notes).*
24. **Not measured:** the 30-day lifecycle simulation (plan L3) was not built, and no run used the repo's default model (`anthropic/claude-sonnet-4`). Fix: build `evals/run_lifecycle.py` on the stub mode; run `make eval-live` with an OpenRouter key.
   *Candidate: closed by the lifecycle simulation (the default-model half stays open).*
26. **The thread-id lookup pin was found while reading report-split selector results,** so the "with pins" selector numbers are not blind. Fix: confirm on a freshly generated set.
   *Candidate: closed by the fresh set (a new set no one tuned on).*
27. **One routing decision per case, execution stubbed:** nothing measures what the execution agent then does, or drift over many turns. Fix: the lifecycle simulation, then shadow mode.
   *Candidate: closed by the lifecycle simulation (drift over many turns; shadow mode stays in item 20).*

## Cut (and why)

- 19: low value next to items 3 and 4; covered by the approval-gate section of DESIGN.md.
- 22: stated in the README (How it was tested and judged); not a gap in the code.
- 25: stated in the README (not measured); provider pinning only matters on OpenRouter.
- 28: stated in the README (the baseline arm); replay is pinned by tests/test_baseline_replay.py.
- 29: stated in the README (trials per arm).
- 30: history-size disclosure; the squash changes it.
