<!-- DRAFT-FOR-SINA: facts are checked; rewrite the prose in your own words before submitting -->

# Known gaps

Ranked, most serious first. One line each: the gap, then the fix.

Triage for v2 (kept 16, likely closed by v2 8, cut 6). Numbers are the round-1 numbers, so references from other files still resolve.

## Kept

1. **No auth on the HTTP API.** The server binds `0.0.0.0` by default (`OPENPOKE_HOST`), CORS is `*` by default, and `DELETE /chat/history` wipes everything with no check (`server/routes/chat.py`). Fix: an auth dependency on every route, default host `127.0.0.1`, a CORS allowlist.
3. **A draft send is bound to the draft id, not its contents.** `GMAIL_SEND_DRAFT` carries only `draft_id`, so the draft can be edited in Gmail after approval; the summary says "draft contents not shown" (`gmail.summarise_send`). Fix: fetch the draft when proposing, hash recipients, subject and body, re-fetch and compare before sending.
4. **The user sees a pending send through the model.** The summary and code are built in code, but reach the user inside the model's own reply; with two sends pending, which one a reply approves rests on the model relaying the right code next to the right summary. Fix: show pending actions in the UI straight from the approval store, one confirm control each.
5. **Single tenant by construction:** one registry, one conversation log, one Gmail user id; the `owner` column exists but nothing reads it. Fix: a tenant key on every store and route.
6. **No per-agent lock:** a trigger fire and a user follow-up can run the same agent at the same time. Fix: one asyncio lock per agent id in the batch manager.
7. **Retries outside email can repeat a side effect:** the single-use approval makes email at-most-once; any other tool that times out after acting and is retried acts twice. Fix: an idempotency key per tool call and an outbox.
8. **The summariser can carry email text into the user's "Preferences & Profile" memory** (`server/services/conversation/summarization/prompt_builder.py`). Fix: tag summary inputs by provenance and keep email and agent text out of profile facts.
9. **Every execution agent gets all 13 tools:** the same selection problem one level down. Fix: run the same selector over tool descriptions.
11. **One global batch:** a quick agent's result is held until every agent in the open batch has finished, each with a 90-second timeout (`server/agents/execution_agent/batch_manager.py`, `_complete_execution`). Fix: deliver each agent's result when it finishes.
12. **The Gmail watcher makes one LLM call per inbox email;** a broker inbox makes that a cost firehose. Fix: a rule prefilter and batched classification.
13. **Triggers re-fire after a crash and fire late after downtime.** Fix: persist the last fire time and define what happens to a missed window.
14. **Composio calls are synchronous on the event loop, and the 8-iteration cap ends a turn with an empty response** (`InteractionAgentRuntime.MAX_TOOL_ITERATIONS`). Fix: run the calls in a thread executor; tell the user when the cap is hit.
15. **Inherited third-party prompt text:** `prompts/interaction_full.md` (upstream, byte for byte), `prompts/interaction_shortlist.md` (a copy of it outside the rewritten paragraphs) and `server/agents/execution_agent/system_prompt.md` reproduce text from a leaked third-party assistant prompt. Fix: rewrite both prompts before any commercial use and re-run the eval.
16. **The duplicate check misses a duplicate that shares neither an id nor a similar name:** the name score caught none of the held-out duplicates at any threshold (`evals/results/DUPLICATE_THRESHOLD.md`); the shared-id check is what flags them (19 of the baseline's 27). Fix: also match the client's names and emails from the instructions, behind the same confirm step.
20. **The dataset is synthetic and template-clustered:** 130 cases from 28 templates (19 in the report split), phrased by hand-written templates, with no second-model phrasing pass and no hand review of the gold labels yet. Fix: a phrasing pass by another model family, a hand check of every label, then shadow mode on real traffic.
23. **The injection suite proves the gate, not the model:** a fake model makes exactly the attacker's calls. Fix: a live-model run over the same attacks.

## Likely closed by v2 (candidates; moved to FAILURES.md only if the fresh-set results show it)

2. **The fix still messages other clients' agents.** At N=500, 15 of 201 should-reuse case-trials also reached another client's agent, all as sprays (the right agent plus another), so pre-registered bar 3 fails (README). Fix: the multi-agent send check proposed in FAILURES.md (class 1), then a new recorded run.
   *Candidate: closed by C1 (shared-client send check).*
10. **The shortlist does not make a turn cheaper in prompt tokens.** At N=500 each model call carries 57.4% of the baseline's prompt, but the fix calls `search_agents` 1.18 times per turn (about 1.8 model calls), so a turn sends 102.5% of the baseline's prompt tokens and is slower; bar 5 fails. Fix: let the selector rank archived agents too and show a strong archived match on the shortlist, so fewer turns need a search round.
   *Candidate: closed by C3 (archived agents on the shortlist).*
17. **`cards._clip` drops a word it had room for:** when the cut lands exactly on the end of a word it still backs off to the previous space, so a card's purpose line can lose its last word, such as the city at the end of the first request. Left as is because the code is frozen for the recorded runs and the fix changes the fix-arm prompts. Fix: keep the cut when the next character is a space, then re-record.
   *Candidate: closed by C2 (card sentence clip).*
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
