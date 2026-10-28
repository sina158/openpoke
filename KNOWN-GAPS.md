<!-- DRAFT-FOR-SINA: facts are checked; rewrite the prose in your own words before submitting -->

# Known gaps

Ranked, most serious first. One line each: the gap, then the fix.

## In the product

1. **No auth on the HTTP API.** The server binds `0.0.0.0` by default (`OPENPOKE_HOST`), CORS is `*` by default, and `DELETE /chat/history` wipes everything with no check (`server/routes/chat.py`). Fix: an auth dependency on every route, default host `127.0.0.1`, a CORS allowlist.
2. **A draft send is bound to the draft id, not its contents.** `GMAIL_SEND_DRAFT` carries only `draft_id`, so the draft can be edited in Gmail after approval; the summary says "draft contents not shown" (`gmail.summarise_send`). Fix: fetch the draft when proposing, hash recipients, subject and body, re-fetch and compare before sending.
3. **The user sees a pending send through the model.** The summary and code are built in code, but reach the user inside the model's own reply; with two sends pending, which one a reply approves rests on the model relaying the right code next to the right summary. Fix: show pending actions in the UI straight from the approval store, one confirm control each.
4. **Single tenant by construction:** one registry, one conversation log, one Gmail user id; the `owner` column exists but nothing reads it. Fix: a tenant key on every store and route.
5. **No per-agent lock:** a trigger fire and a user follow-up can run the same agent at the same time. Fix: one asyncio lock per agent id in the batch manager.
6. **Retries outside email can repeat a side effect:** the single-use approval makes email at-most-once; any other tool that times out after acting and is retried acts twice. Fix: an idempotency key per tool call and an outbox.
7. **The summariser can carry email text into the user's "Preferences & Profile" memory** (`server/services/conversation/summarization/prompt_builder.py`). Fix: tag summary inputs by provenance and keep email and agent text out of profile facts.
8. **Every execution agent gets all 13 tools:** the same selection problem one level down. Fix: run the same selector over tool descriptions.
9. **One global batch:** a quick agent's result is held until every agent in the open batch has finished, each with a 90-second timeout (`server/agents/execution_agent/batch_manager.py`, `_complete_execution`). Fix: deliver each agent's result when it finishes.
10. **The Gmail watcher makes one LLM call per inbox email;** a broker inbox makes that a cost firehose. Fix: a rule prefilter and batched classification.
11. **Triggers re-fire after a crash and fire late after downtime.** Fix: persist the last fire time and define what happens to a missed window.
12. **Composio calls are synchronous on the event loop, and the 8-iteration cap ends a turn with an empty response** (`InteractionAgentRuntime.MAX_TOOL_ITERATIONS`). Fix: run the calls in a thread executor; tell the user when the cap is hit.
13. **Inherited third-party prompt text:** `prompts/interaction_full.md` (upstream, byte for byte), `prompts/interaction_shortlist.md` (a copy of it outside the rewritten paragraphs) and `server/agents/execution_agent/system_prompt.md` reproduce text from a leaked third-party assistant prompt. Fix: rewrite both prompts before any commercial use and re-run the eval.
14. **The duplicate check misses a duplicate that shares neither an id nor a similar name:** the name score caught none of the held-out duplicates at any threshold (`evals/results/DUPLICATE_THRESHOLD.md`); the shared-id check is what flags them (19 of the baseline's 27). Fix: also match the client's names and emails from the instructions, behind the same confirm step.
15. **`conversation_limit` is wired but unset by default,** so an execution agent still carries its whole history. Fix: choose a limit by measuring execution quality against history length.
16. **The pending action's hash sits in the same row as its arguments,** so it catches code that changes the arguments, not a write to the database. Fix: sign the hash with a server key, or keep it in a separate store.

## In the evaluation

17. **The dataset is synthetic and template-clustered:** 130 cases from 28 templates (19 in the report split), phrased by hand-written templates, with no second-model phrasing pass and no hand review of the gold labels yet. Fix: a phrasing pass by another model family, a hand check of every label, then shadow mode on real traffic.
18. **The baseline cannot win ambiguous cases (no clarify tool) or paraphrase cases (the name lacks the discriminator) by construction.** Fix: none in code; accuracy without those two categories is reported beside the headline.
19. **The injection suite proves the gate, not the model:** a fake model makes exactly the attacker's calls. Fix: a live-model run over the same attacks.
20. **Not measured:** the 30-day lifecycle simulation (plan L3) was not built, and no run used the repo's default model (`anthropic/claude-sonnet-4`). Fix: build `evals/run_lifecycle.py` on the stub mode; run `make eval-live` with an OpenRouter key.
21. **Live runs used an OpenAI-compatible endpoint with `minimax-m3`, not OpenRouter,** because no OpenRouter key was available; provider pinning (`OPENPOKE_LLM_PROVIDER`) only applies on OpenRouter, so it was not exercised. Fix: re-run on OpenRouter with the provider pinned.
22. **The thread-id lookup pin was found while reading report-split selector results,** so the "with pins" selector numbers are not blind. Fix: confirm on a freshly generated set.
23. **One routing decision per case, execution stubbed:** nothing measures what the execution agent then does, or drift over many turns. Fix: the lifecycle simulation, then shadow mode.
24. **The baseline arm is upstream routing plus an escaped current turn, and its email sends only propose;** the escaping changes the prompt of a few cases (4 of 130 at the time of writing). Fix: none; disclosed, and the committed baseline cassette replays in `tests/test_baseline_replay.py`.
25. **Trials:** TBD-LEAD trial(s) per case; consistency numbers need three. Fix: run trials 2 and 3 from the same cassettes' settings.
26. **The diff is larger than planned:** about 2,700 lines added under `server/` against a 1,200-1,500 budget, and TBD-LEAD commits against a planned 15-25. Fix: none; disclosed.
