<!-- DRAFT-FOR-SINA: facts are checked; rewrite the prose in your own words before submitting -->

# OpenPoke fork: bounded agent routing

This fork changes how OpenPoke's interaction agent picks an execution agent, and puts an approval gate in
front of outbound email. Everything above "Upstream README" is new; the upstream README is kept unchanged below.

AI assistance: TBD-SINA (one line, plan decision 2a).

## The problem

Upstream, every turn shows the interaction agent every execution agent by name
(`server/agents/interaction_agent/agent.py`, `_render_active_agents`), and the model reuses an agent by retyping
its name in `send_message_to_agent(agent_name=...)`.

- **Size.** The roster block grows linearly with the number of agents: about <!-- n:selector.tokens.n5.full_mean -->TBD<!-- /n --> tokens at 5 agents, <!-- n:selector.tokens.n500.full_mean -->TBD<!-- /n --> at 500 and <!-- n:selector.tokens.n5000.full_mean -->TBD<!-- /n --> at 5,000 (chars / 4, `evals/results/SELECTOR.md`). In the baseline run the whole prompt at 500 agents averaged <!-- n:report.by_key.baseline_minimax-m3_k8_n500.prompt_tokens.mean -->TBD<!-- /n --> prompt tokens per turn.
- **Identity.** An agent was a string in `roster.json`. Reuse means retyping the name exactly; two names with the
  same slug shared one log file; two clients with similar names cannot be told apart from the name.
- **Decision.** The upstream prompt tells the model to prefer an existing agent and "Don't worry if the agent name
  is unrelated to the new task if it contains useful context" (`prompts/interaction_full.md`, line 19). There is no
  way to ask the user which agent is meant and no check before a second agent is made for the same matter.
  Baseline at 500 agents: wrong reuse <!-- n:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.rate -->TBD<!-- /n -->; <!-- n:report.by_key.baseline_minimax-m3_k8_n500.cross_client.wrong_reuse -->TBD<!-- /n --> of those went to another client's agent; duplicates <!-- n:report.by_key.baseline_minimax-m3_k8_n500.duplicate.rate -->TBD<!-- /n -->.
- **Over time.** Agents are never removed, so the roster only grows, and each execution agent's prompt carries its
  whole history (`ExecutionAgent` takes a `conversation_limit` that the runtime never passed).

## What changed

Agents live in a SQLite registry with a minted id (`a_0001`, ...) and a unique slug; the name is a display label.
Each agent has a card built from its own log without an LLM: a purpose line (first request plus the head of the last
response) and the identifiers and names it has handled. Each turn the interaction agent sees at most 8 cards instead
of the roster: up to 4 pinned agents (the one whose result is being handled, agents holding a thread, draft or
message id the message quotes, agents with a pending send, agents named in the last turns), then the best by BM25 +
entity overlap + recency, within a 1,200-token budget. A new `search_agents(query)` tool reaches the rest. Reuse
passes `agent_id`; creating by name runs a duplicate check that returns `possible_duplicates` unless `confirm_new`
is set; `clarify_agent_choice` asks the user. Agents idle for 30 days leave the shortlist (still searchable, revived
on reuse or when a trigger fires). Reply, forward and send-draft become proposals that are sent once, only after the
user types the action's 4-character confirmation code. The upstream routing stays available as
`OPENPOKE_ROSTER_MODE=full` + `OPENPOKE_PROMPT_VARIANT=original`, which is the eval's baseline arm.

| file | what it does |
|---|---|
| `server/services/execution/registry.py` | SQLite registry: minted ids, unique slug, archive / revive, one-time import of `roster.json` |
| `server/services/execution/roster.py` | the old names-only API, now a view over the registry |
| `server/services/execution/cards.py` | pure: build a card from log entries, extract entities, render a card |
| `server/services/execution/card_store.py` | keeps cards in the registry; rebuilt on dispatch and after each run |
| `server/services/execution/selector.py` | pure: BM25, entity overlap, recency, pins, `select`, `search`, shortlist rendering |
| `server/services/execution/embeddings.py` | optional local embeddings fused with BM25 by RRF (`OPENPOKE_SELECTOR=hybrid`) |
| `server/services/execution/lifecycle.py` | archive idle agents; `on_turn_start` hook |
| `server/services/approvals.py` | pending sends: canonical hash, confirmation code, single use, 24 h expiry |
| `server/services/provenance.py` | which source started the current turn: user, agent, email or automation |
| `server/services/costs.py` | one JSON line per LLM call (tokens, cost, latency); spend cap |
| `server/agents/interaction_agent/agent.py` | roster modes `full` / `full_cards` / `shortlist`, `<pending_actions>`, escaped current turn |
| `server/agents/interaction_agent/tools.py` | `agent_id` routing, duplicate check, `clarify_agent_choice`, `search_agents`, `confirm_action` |
| `server/agents/interaction_agent/prompts/interaction_full.md` | the upstream prompt, byte for byte |
| `server/agents/interaction_agent/prompts/interaction_shortlist.md` | the same prompt with the routing and confirmation paragraphs rewritten |
| `server/agents/execution_agent/tools/gmail.py` | reply / forward / send-draft register a proposal; `execute_approved_action` sends it |
| `server/agents/execution_agent/runtime.py, batch_manager.py` | history limit, stub mode, `agent` provenance, card refresh after a run |
| `server/config.py, server/openrouter_client/client.py` | env overrides for models, endpoint, temperature, seed; usage recorded per call |
| `server/utils/paths.py, clock.py` | `OPENPOKE_DATA_DIR`; a fixed clock for evals |
| `evals/` | seeded generator, golden set, routing / selector / injection evals, cassettes, stats, results |
| `tests/` | unit tests, the injection suite, and an offline replay of the baseline cassette |

Design reasons and rejected options: [DESIGN.md](DESIGN.md). Where it still fails: [FAILURES.md](FAILURES.md).
Open problems, ranked: [KNOWN-GAPS.md](KNOWN-GAPS.md).

## How to run

Python 3.11. No API key is needed for anything except `make eval-live`.

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
make test           # offline: unit tests + a replay of the baseline cassette
make eval-replay    # offline: every arm from the committed cassettes; an unrecorded call fails
make injection      # offline: prompt-injection cases against the send gate
python -m evals.run_selector          # offline: recall@k, MRR and roster size per scorer
make compare        # result files -> evals/results/RESULTS.md and RESULTS.json
python -m evals.check_readme_numbers  # every marked number in this README against RESULTS.json
make eval-live      # live: needs OPENPOKE_LLM_API_KEY or OPENROUTER_API_KEY; spend cap BUDGET_PER_ARM (default $13) per arm
```

The committed cassettes were recorded with `minimax-m3`. On OpenRouter, `make eval-live` defaults to
`openai/gpt-4.1-mini`; pass `EVAL_ARGS="--model <model>"` to choose another.

Settings (env): `OPENPOKE_ROSTER_MODE` (`shortlist` default, `full`, `full_cards`), `OPENPOKE_PROMPT_VARIANT`
(`shortlist` default, `original`), `OPENPOKE_SHORTLIST_K` (8), `OPENPOKE_SHORTLIST_TOKEN_BUDGET` (1200),
`OPENPOKE_SELECTOR` (`bm25` default, `hybrid`), `OPENPOKE_DUPLICATE_THRESHOLD` (0.85), `OPENPOKE_ARCHIVE_IDLE_DAYS`
(30), `OPENPOKE_EXECUTION_HISTORY_LIMIT` (unset = upstream behaviour), `OPENPOKE_DATA_DIR`, `OPENPOKE_LLM_BASE_URL`,
`OPENPOKE_*_MODEL` per role. The hybrid scorer needs `pip install -r requirements-embeddings.txt`.

## Results

Model <!-- n:report.by_key.baseline_minimax-m3_k8_n500.model -->TBD<!-- /n --> through an OpenAI-compatible endpoint (host in each results file header),
temperature 0, <!-- n:report.by_key.baseline_minimax-m3_k8_n500.trials -->TBD<!-- /n --> trial(s). Headline numbers use the report split: <!-- n:report.by_key.baseline_minimax-m3_k8_n500.n -->TBD<!-- /n --> cases
per arm and size (the held-out split was kept for tuning; all 130 cases are a secondary table in
`evals/results/RESULTS.md`). Baseline run <!-- n:report.by_key.baseline_minimax-m3_k8_n500.date -->TBD<!-- /n --> at commit <!-- n:report.by_key.baseline_minimax-m3_k8_n500.commit -->TBD<!-- /n -->; fix run
<!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.date -->TBD<!-- /n --> at commit <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.commit -->TBD<!-- /n -->. Rates carry 95% Wilson intervals; because cases share
templates, a template-clustered bootstrap interval is given too. Every number below is copied from
`evals/results/RESULTS.json` and checked by
`evals/check_readme_numbers.py`. Arms: **baseline** = upstream prompt, upstream four tools, every agent by name
(the current turn is escaped, and email sends only propose); **prompt_only** = the new prompt and tools over the
full roster with ids; **fix** = the new prompt and tools over the shortlist. Execution is not run: the eval stops
at the first routing call and grades its arguments (`evals/run_routing.py`).

### Pre-registered bars (`evals/BARS.md`)

| bar | result | met |
|---|---|---|
| selector recall@8 at N=500, non-ambiguous cases >= 0.95 | <!-- n:selector.recall.plus_pins.n500.non_ambiguous.k8 -->TBD<!-- /n --> with pins, <!-- n:selector.recall.bm25_entities_recency.n500.non_ambiguous.k8 -->TBD<!-- /n --> without | TBD |
| fix wrong reuse not worse than baseline at every N | table below | TBD |
| cross-client wrong reuse = 0 in the fix arm (as a bound) | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.cross_client.wrong_reuse -->TBD<!-- /n --> of <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.cross_client.wrong_reuse_n -->TBD<!-- /n --> at N=500; 95% upper bound <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.cross_client.wrong_reuse_upper95 -->TBD<!-- /n --> | TBD |
| duplicates lower than baseline at N=500 | fix <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.duplicate.rate -->TBD<!-- /n --> vs baseline <!-- n:report.by_key.baseline_minimax-m3_k8_n500.duplicate.rate -->TBD<!-- /n --> | TBD |
| prompt tokens at N=500 in the fix <= 40% of baseline | fix <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.prompt_tokens.mean -->TBD<!-- /n --> vs baseline <!-- n:report.by_key.baseline_minimax-m3_k8_n500.prompt_tokens.mean -->TBD<!-- /n --> (ratio <!-- n:TODO.bars.prompt_token_ratio_n500 -->TBD<!-- /n -->) | TBD |
| injection suite: 0 sends without a consumed approval | <!-- n:TODO.injection.unauthorised_sends -->TBD<!-- /n --> of <!-- n:TODO.injection.attacks -->TBD<!-- /n --> attacks | TBD |

### Routing: baseline vs prompt_only vs fix

| arm | N | n | accuracy | 95% CI | wrong reuse | cross-client: wrong reuse / hijack / wrong guess | duplicate | correct create | hijack |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 5 | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.n -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy.rate -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy.ci95.1 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.wrong_reuse.rate -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.cross_client.wrong_reuse -->TBD<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n5.cross_client.hijack -->TBD<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n5.cross_client.wrong_guess -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.duplicate.rate -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.correct_create.rate -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.hijack.count -->TBD<!-- /n --> of <!-- n:report.by_key.baseline_minimax-m3_k8_n5.hijack.n -->TBD<!-- /n --> |
| baseline | 50 | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.n -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy.rate -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy.ci95.1 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.wrong_reuse.rate -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.cross_client.wrong_reuse -->TBD<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n50.cross_client.hijack -->TBD<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n50.cross_client.wrong_guess -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.duplicate.rate -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.correct_create.rate -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.hijack.count -->TBD<!-- /n --> of <!-- n:report.by_key.baseline_minimax-m3_k8_n50.hijack.n -->TBD<!-- /n --> |
| baseline | 500 | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.n -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.rate -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.ci95.1 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.rate -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.cross_client.wrong_reuse -->TBD<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n500.cross_client.hijack -->TBD<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n500.cross_client.wrong_guess -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.duplicate.rate -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.correct_create.rate -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.hijack.count -->TBD<!-- /n --> of <!-- n:report.by_key.baseline_minimax-m3_k8_n500.hijack.n -->TBD<!-- /n --> |
| prompt_only | 5 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.accuracy.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.accuracy.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.wrong_reuse.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.cross_client.wrong_reuse -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.cross_client.hijack -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.cross_client.wrong_guess -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.duplicate.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.correct_create.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.hijack.count -->TBD<!-- /n --> of <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.hijack.n -->TBD<!-- /n --> |
| prompt_only | 50 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.accuracy.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.accuracy.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.wrong_reuse.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.cross_client.wrong_reuse -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.cross_client.hijack -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.cross_client.wrong_guess -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.duplicate.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.correct_create.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.hijack.count -->TBD<!-- /n --> of <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.hijack.n -->TBD<!-- /n --> |
| prompt_only | 500 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.accuracy.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.accuracy.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.wrong_reuse.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.cross_client.wrong_reuse -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.cross_client.hijack -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.cross_client.wrong_guess -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.duplicate.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.correct_create.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.hijack.count -->TBD<!-- /n --> of <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.hijack.n -->TBD<!-- /n --> |
| fix | 5 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.accuracy.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.accuracy.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.wrong_reuse.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.cross_client.wrong_reuse -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.cross_client.hijack -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.cross_client.wrong_guess -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.duplicate.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.correct_create.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.hijack.count -->TBD<!-- /n --> of <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.hijack.n -->TBD<!-- /n --> |
| fix | 50 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.accuracy.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.accuracy.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.wrong_reuse.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.cross_client.wrong_reuse -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.cross_client.hijack -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.cross_client.wrong_guess -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.duplicate.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.correct_create.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.hijack.count -->TBD<!-- /n --> of <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.hijack.n -->TBD<!-- /n --> |
| fix | 500 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.accuracy.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.accuracy.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.wrong_reuse.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.cross_client.wrong_reuse -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.cross_client.hijack -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.cross_client.wrong_guess -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.duplicate.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.correct_create.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.hijack.count -->TBD<!-- /n --> of <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.hijack.n -->TBD<!-- /n --> |
| cards_full | 5 | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n5.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n5.accuracy.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n5.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n5.accuracy.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n5.wrong_reuse.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n5.cross_client.wrong_reuse -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n5.cross_client.hijack -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n5.cross_client.wrong_guess -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n5.duplicate.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n5.correct_create.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n5.hijack.count -->TBD<!-- /n --> of <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n5.hijack.n -->TBD<!-- /n --> |
| cards_full | 50 | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n50.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n50.accuracy.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n50.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n50.accuracy.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n50.wrong_reuse.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n50.cross_client.wrong_reuse -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n50.cross_client.hijack -->TBD<!-- /n --> / <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n50.cross_client.wrong_guess -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n50.duplicate.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n50.correct_create.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n50.hijack.count -->TBD<!-- /n --> of <!-- n:TODO.report.by_key.cards_full_minimax-m3_k8_n50.hijack.n -->TBD<!-- /n --> |

Wrong reuse and duplicate are over cases whose gold is an existing agent; correct create and hijack over
should-create cases. Cross-client: how many of those wrong answers reached another client's agent.
`cards_full` (every card, no selection, roster mode `full_cards`; N <= 50 only) separates what the cards bought from what the selection bought.

Cases share templates (28 templates across the 130 cases, 19 of them in the report split), so the Wilson intervals
above are too narrow. Intervals clustered by template:

| arm | N | accuracy, 95% CI by template bootstrap | templates (effective n) |
|---|---|---|---|
| baseline | 5 | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy.clustered.ci95.0 -->TBD<!-- /n --> to <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy.clustered.ci95.1 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy.clustered.templates -->TBD<!-- /n --> |
| baseline | 50 | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy.clustered.ci95.0 -->TBD<!-- /n --> to <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy.clustered.ci95.1 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy.clustered.templates -->TBD<!-- /n --> |
| baseline | 500 | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.clustered.ci95.0 -->TBD<!-- /n --> to <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.clustered.ci95.1 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.clustered.templates -->TBD<!-- /n --> |
| prompt_only | 5 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.accuracy.clustered.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.accuracy.clustered.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.accuracy.clustered.templates -->TBD<!-- /n --> |
| prompt_only | 50 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.accuracy.clustered.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.accuracy.clustered.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.accuracy.clustered.templates -->TBD<!-- /n --> |
| prompt_only | 500 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.accuracy.clustered.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.accuracy.clustered.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.accuracy.clustered.templates -->TBD<!-- /n --> |
| fix | 5 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.accuracy.clustered.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.accuracy.clustered.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.accuracy.clustered.templates -->TBD<!-- /n --> |
| fix | 50 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.accuracy.clustered.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.accuracy.clustered.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.accuracy.clustered.templates -->TBD<!-- /n --> |
| fix | 500 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.accuracy.clustered.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.accuracy.clustered.ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.accuracy.clustered.templates -->TBD<!-- /n --> |

### Paired against baseline (same cases)

Each case counts once, by its majority outcome over the trials.

| arm | N | pairs | change in accuracy | 95% CI (template bootstrap) | only baseline right | only arm right | McNemar p |
|---|---|---|---|---|---|---|---|
| prompt_only | 5 | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.pairs -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.delta_accuracy -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.delta_clustered_ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.delta_clustered_ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.b -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.c -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.mcnemar_p -->TBD<!-- /n --> |
| prompt_only | 50 | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.pairs -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.delta_accuracy -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.delta_clustered_ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.delta_clustered_ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.b -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.c -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.mcnemar_p -->TBD<!-- /n --> |
| prompt_only | 500 | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.pairs -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.delta_accuracy -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.b -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.c -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.mcnemar_p -->TBD<!-- /n --> |
| fix | 5 | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.pairs -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.delta_accuracy -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.delta_clustered_ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.delta_clustered_ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.b -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.c -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.mcnemar_p -->TBD<!-- /n --> |
| fix | 50 | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.pairs -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.delta_accuracy -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.delta_clustered_ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.delta_clustered_ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.b -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.c -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.mcnemar_p -->TBD<!-- /n --> |
| fix | 500 | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.pairs -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.delta_accuracy -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.1 -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.b -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.c -->TBD<!-- /n --> | <!-- n:TODO.report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.mcnemar_p -->TBD<!-- /n --> |

### Accuracy without the categories the baseline cannot win

The baseline has no clarify tool (ambiguous cases) and only names to go on (paraphrase cases are built so the name
lacks the discriminator), so it loses those by construction. Accuracy with both categories left out:

| arm | N | n | accuracy |
|---|---|---|---|
| baseline | 5 | <!-- n:TODO.report.by_key.baseline_minimax-m3_k8_n5.accuracy_excl_ambiguous_paraphrase.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.baseline_minimax-m3_k8_n5.accuracy_excl_ambiguous_paraphrase.rate -->TBD<!-- /n --> |
| baseline | 50 | <!-- n:TODO.report.by_key.baseline_minimax-m3_k8_n50.accuracy_excl_ambiguous_paraphrase.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.baseline_minimax-m3_k8_n50.accuracy_excl_ambiguous_paraphrase.rate -->TBD<!-- /n --> |
| baseline | 500 | <!-- n:TODO.report.by_key.baseline_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.baseline_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.rate -->TBD<!-- /n --> |
| prompt_only | 5 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.accuracy_excl_ambiguous_paraphrase.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.accuracy_excl_ambiguous_paraphrase.rate -->TBD<!-- /n --> |
| prompt_only | 50 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.accuracy_excl_ambiguous_paraphrase.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.accuracy_excl_ambiguous_paraphrase.rate -->TBD<!-- /n --> |
| prompt_only | 500 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.rate -->TBD<!-- /n --> |
| fix | 5 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.accuracy_excl_ambiguous_paraphrase.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.accuracy_excl_ambiguous_paraphrase.rate -->TBD<!-- /n --> |
| fix | 50 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.accuracy_excl_ambiguous_paraphrase.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.accuracy_excl_ambiguous_paraphrase.rate -->TBD<!-- /n --> |
| fix | 500 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.rate -->TBD<!-- /n --> |

### Clarify (ambiguous cases) and over-clarify (all other cases)

| arm | N | clarify recall | clarify precision | over-clarify |
|---|---|---|---|---|
| baseline | all | no clarify tool | - | - |
| prompt_only | 5 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.clarify_recall.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.clarify_precision.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.over_clarify.rate -->TBD<!-- /n --> |
| prompt_only | 50 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.clarify_recall.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.clarify_precision.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.over_clarify.rate -->TBD<!-- /n --> |
| prompt_only | 500 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.clarify_recall.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.clarify_precision.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.over_clarify.rate -->TBD<!-- /n --> |
| fix | 5 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.clarify_recall.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.clarify_precision.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.over_clarify.rate -->TBD<!-- /n --> |
| fix | 50 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.clarify_recall.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.clarify_precision.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.over_clarify.rate -->TBD<!-- /n --> |
| fix | 500 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.clarify_recall.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.clarify_precision.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.over_clarify.rate -->TBD<!-- /n --> |

A clarify counts as correct only on cases labelled ambiguous, and only when its candidates include every target.

### Consistency across trials

| arm | trials | pass^k N=5 | pass^k N=50 | pass^k N=500 | agreement N=500 |
|---|---|---|---|---|---|
| baseline | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.trials -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.baseline_minimax-m3_k8_n5.pass_k -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.baseline_minimax-m3_k8_n50.pass_k -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.baseline_minimax-m3_k8_n500.pass_k -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.baseline_minimax-m3_k8_n500.agreement -->TBD<!-- /n --> |
| prompt_only | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.trials -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.pass_k -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.pass_k -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.pass_k -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.agreement -->TBD<!-- /n --> |
| fix | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.trials -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.pass_k -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.pass_k -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.pass_k -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.agreement -->TBD<!-- /n --> |

pass^k: share of cases right on every trial. Agreement: share of cases whose trials all agree.

### Cost and latency

| arm | N | prompt tokens mean | p95 | latency ms p50 | p95 | $ per 100 turns |
|---|---|---|---|---|---|---|
| baseline | 5 | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.prompt_tokens.mean -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.prompt_tokens.p95 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.latency_ms.p50 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.latency_ms.p95 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.usd_per_100_turns -->TBD<!-- /n --> |
| baseline | 50 | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.prompt_tokens.mean -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.prompt_tokens.p95 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.latency_ms.p50 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.latency_ms.p95 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.usd_per_100_turns -->TBD<!-- /n --> |
| baseline | 500 | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.prompt_tokens.mean -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.prompt_tokens.p95 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.latency_ms.p50 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.latency_ms.p95 -->TBD<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.usd_per_100_turns -->TBD<!-- /n --> |
| prompt_only | 5 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.prompt_tokens.mean -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.prompt_tokens.p95 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.latency_ms.p50 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.latency_ms.p95 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n5.usd_per_100_turns -->TBD<!-- /n --> |
| prompt_only | 50 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.prompt_tokens.mean -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.prompt_tokens.p95 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.latency_ms.p50 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.latency_ms.p95 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n50.usd_per_100_turns -->TBD<!-- /n --> |
| prompt_only | 500 | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.prompt_tokens.mean -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.prompt_tokens.p95 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.latency_ms.p50 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.latency_ms.p95 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.prompt_only_minimax-m3_k8_n500.usd_per_100_turns -->TBD<!-- /n --> |
| fix | 5 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.prompt_tokens.mean -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.prompt_tokens.p95 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.latency_ms.p50 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.latency_ms.p95 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.usd_per_100_turns -->TBD<!-- /n --> |
| fix | 50 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.prompt_tokens.mean -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.prompt_tokens.p95 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.latency_ms.p50 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.latency_ms.p95 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.usd_per_100_turns -->TBD<!-- /n --> |
| fix | 500 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.prompt_tokens.mean -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.prompt_tokens.p95 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.latency_ms.p50 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.latency_ms.p95 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.usd_per_100_turns -->TBD<!-- /n --> |

Dollars are list-price equivalents from the provider's usage block, per routing decision (all LLM calls of the
turn). Total live spend for the eval: <!-- n:TODO.spend.total_usd -->TBD<!-- /n -->.

### What the fix arm did with the shortlist

| N | duplicate check fired | right after it fired | cases whose target was archived | accuracy on those | search found the archived target | accuracy, target active |
|---|---|---|---|---|---|---|
| 5 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.guard.fired -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.guard.correct_after -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.archived_target.archived.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.archived_target.archived.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.archived_target.search_found_target -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n5.archived_target.active.rate -->TBD<!-- /n --> |
| 50 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.guard.fired -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.guard.correct_after -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.archived_target.archived.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.archived_target.archived.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.archived_target.search_found_target -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n50.archived_target.active.rate -->TBD<!-- /n --> |
| 500 | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.guard.fired -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.guard.correct_after -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.archived_target.archived.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.archived_target.archived.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.archived_target.search_found_target -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.archived_target.active.rate -->TBD<!-- /n --> |

The eval runs the shipped 30-day archive default, so old matters are archived and only reachable through
`search_agents`.

### Selector, offline (all 130 cases, 110 with a target)

recall@8: share of cases whose target agents all rank in the top 8. `hybrid` is the lexical score fused with
embeddings by reciprocal rank fusion. `plus_pins` is what ships.

| scorer | N=5 | N=50 | N=500 | N=5,000 | MRR N=500 |
|---|---|---|---|---|---|
| recency | <!-- n:selector.recall.recency.n5.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.recency.n50.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.recency.n500.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.recency.n5000.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.recency.n500.mrr -->TBD<!-- /n --> |
| bm25 | <!-- n:selector.recall.bm25.n5.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25.n50.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25.n500.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25.n5000.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25.n500.mrr -->TBD<!-- /n --> |
| bm25_entities | <!-- n:selector.recall.bm25_entities.n5.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25_entities.n50.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25_entities.n500.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25_entities.n5000.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25_entities.n500.mrr -->TBD<!-- /n --> |
| bm25_entities_recency | <!-- n:selector.recall.bm25_entities_recency.n5.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n50.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n500.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n5000.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n500.mrr -->TBD<!-- /n --> |
| plus_pins | <!-- n:selector.recall.plus_pins.n5.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.plus_pins.n50.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.plus_pins.n5000.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.mrr -->TBD<!-- /n --> |
| hybrid | <!-- n:selector.recall.hybrid.n5.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.hybrid.n50.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.hybrid.n500.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.hybrid.n5000.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.hybrid.n500.mrr -->TBD<!-- /n --> |

Where hybrid loses, recall@8 on entity-only queries (an email, policy number or thread id):

| scorer | entity N=500 | entity N=5,000 | paraphrase N=500 | paraphrase N=5,000 |
|---|---|---|---|---|
| bm25_entities_recency | <!-- n:selector.recall.bm25_entities_recency.n500.by_category.entity.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n5000.by_category.entity.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n500.by_category.paraphrase.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n5000.by_category.paraphrase.k8 -->TBD<!-- /n --> |
| hybrid | <!-- n:selector.recall.hybrid.n500.by_category.entity.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.hybrid.n5000.by_category.entity.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.hybrid.n500.by_category.paraphrase.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.hybrid.n5000.by_category.paraphrase.k8 -->TBD<!-- /n --> |
| plus_pins | <!-- n:selector.recall.plus_pins.n500.by_category.entity.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.plus_pins.n5000.by_category.entity.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.by_category.paraphrase.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.plus_pins.n5000.by_category.paraphrase.k8 -->TBD<!-- /n --> |

### k-sweep at N=500

| k | recall@k, no pins | recall@k, with pins | fix arm accuracy (live) |
|---|---|---|---|
| 3 | <!-- n:selector.recall.bm25_entities_recency.n500.k3 -->TBD<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.k3 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k3_n500.accuracy.rate -->TBD<!-- /n --> |
| 5 | <!-- n:selector.recall.bm25_entities_recency.n500.k5 -->TBD<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.k5 -->TBD<!-- /n --> | not run |
| 8 | <!-- n:selector.recall.bm25_entities_recency.n500.k8 -->TBD<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.k8 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k8_n500.accuracy.rate -->TBD<!-- /n --> |
| 12 | <!-- n:selector.recall.bm25_entities_recency.n500.k12 -->TBD<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.k12 -->TBD<!-- /n --> | not run |
| 16 | <!-- n:selector.recall.bm25_entities_recency.n500.k16 -->TBD<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.k16 -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_minimax-m3_k16_n500.accuracy.rate -->TBD<!-- /n --> |

### Roster block size (chars / 4, mean per case)

| N | every name (baseline) | k=8 shortlist | shortlist / full |
|---|---|---|---|
| 5 | <!-- n:selector.tokens.n5.full_mean -->TBD<!-- /n --> | <!-- n:selector.tokens.n5.shortlist_mean -->TBD<!-- /n --> | <!-- n:selector.tokens.n5.ratio -->TBD<!-- /n --> |
| 50 | <!-- n:selector.tokens.n50.full_mean -->TBD<!-- /n --> | <!-- n:selector.tokens.n50.shortlist_mean -->TBD<!-- /n --> | <!-- n:selector.tokens.n50.ratio -->TBD<!-- /n --> |
| 500 | <!-- n:selector.tokens.n500.full_mean -->TBD<!-- /n --> | <!-- n:selector.tokens.n500.shortlist_mean -->TBD<!-- /n --> | <!-- n:selector.tokens.n500.ratio -->TBD<!-- /n --> |
| 5000 | <!-- n:selector.tokens.n5000.full_mean -->TBD<!-- /n --> | <!-- n:selector.tokens.n5000.shortlist_mean -->TBD<!-- /n --> | <!-- n:selector.tokens.n5000.ratio -->TBD<!-- /n --> |

Up to 50 agents the shortlist is larger than the list of names, because a card carries more text than a name.

### Injection suite (`make injection`)

<!-- n:TODO.injection.attacks -->TBD<!-- /n --> attacks, <!-- n:TODO.injection.unauthorised_sends -->TBD<!-- /n --> sends without a consumed approval
(95% upper bound <!-- n:TODO.injection.upper95 -->TBD<!-- /n -->); <!-- n:TODO.injection.positive_controls_sent -->TBD<!-- /n --> of <!-- n:TODO.injection.positive_controls -->TBD<!-- /n -->
positive controls (the user really confirms) sent exactly once. The fake model makes exactly the calls each attack
wants, so this proves the gate, not the model's judgement.

### Second model family (N=500, 1 trial)

| arm | model | n | accuracy | 95% CI |
|---|---|---|---|---|
| baseline | <!-- n:TODO.report.by_key.baseline_MODEL2_k8_n500.model -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.baseline_MODEL2_k8_n500.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.baseline_MODEL2_k8_n500.accuracy.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.baseline_MODEL2_k8_n500.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.baseline_MODEL2_k8_n500.accuracy.ci95.1 -->TBD<!-- /n --> |
| fix | <!-- n:TODO.report.by_key.fix_MODEL2_k8_n500.model -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_MODEL2_k8_n500.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_MODEL2_k8_n500.accuracy.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_MODEL2_k8_n500.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.fix_MODEL2_k8_n500.accuracy.ci95.1 -->TBD<!-- /n --> |

### Not measured

- The 30-day lifecycle simulation (plan L3): not built, not run.
- A run on the repo's default model (`anthropic/claude-sonnet-4`): no OpenRouter key was available; every live run
  used an OpenAI-compatible endpoint instead.

## Limitations

- The golden set is synthetic and built from templates: 130 cases from 28 templates, phrased by hand-written
  templates, with no second-model phrasing pass and no hand review yet.
- The baseline cannot win ambiguous or paraphrase cases by construction; both views are reported above.
- The eval grades one routing decision per case with execution stubbed; it says nothing about what the execution
  agent then does.
- The injection suite proves the gate, not the model.
- The baseline arm is upstream routing plus an escaped current turn, and its email sends only propose; the
  committed baseline cassette replays offline in `tests/test_baseline_replay.py`.
- More in [KNOWN-GAPS.md](KNOWN-GAPS.md).

## What this means for an assistant at a brokerage

TBD-SINA (one paragraph, plan section 1 item 5).

## License

Fork of [OpenPoke](https://github.com/shlokkhemani/OpenPoke) by its upstream author (github.com/shlokkhemani),
MIT License. The `LICENSE` file is unchanged.

---

## Upstream README

# OpenPoke 🌴

OpenPoke is a simplified, open-source take on [Interaction Company’s](https://interaction.co/about) [Poke](https://poke.com/) assistant—built to show how a multi-agent orchestration stack can feel genuinely useful. It keeps the handful of things Poke is great at (email triage, reminders, and persistent agents) while staying easy to spin up locally.

- Multi-agent FastAPI backend that mirrors Poke's interaction/execution split, powered by [OpenRouter](https://openrouter.ai/).
- Gmail tooling via [Composio](https://composio.dev/) for drafting/replying/forwarding without leaving chat.
- Trigger scheduler and background watchers for reminders and "important email" alerts.
- Next.js web UI that proxies everything through the shared `.env`, so plugging in API keys is the only setup.

## Requirements
- Python 3.10+
- Node.js 18+
- npm 9+

## Quickstart
1. **Clone and enter the repo.**
   ```bash
   git clone https://github.com/shlokkhemani/OpenPoke
   cd OpenPoke
   ```
2. **Create a shared env file.** Copy the template and open it in your editor:
   ```bash
   cp .env.example .env
   ```
3. **Get your API keys and add them to `.env`:**
   
   **OpenRouter (Required)**
   - Create an account at [openrouter.ai](https://openrouter.ai/)
   - Generate an API key
   - Replace `your_openrouter_api_key_here` with your actual key in `.env`
   
   **Composio (Required for Gmail)**
   - Sign in at [composio.dev](https://composio.dev/)
   - Create an API key
   - Set up Gmail integration and get your auth config ID
   - Replace `your_composio_api_key_here` and `your_gmail_auth_config_id_here` in `.env`
4. **(Required) Create and activate a Python 3.10+ virtualenv:**
   ```bash
   # Ensure you're using Python 3.10+
   python3.10 -m venv .venv
   source .venv/bin/activate
   
   # Verify Python version (should show 3.10+)
   python --version
   ```
   On Windows (PowerShell):
   ```powershell
   # Use Python 3.10+ (adjust path as needed)
   python3.10 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   
   # Verify Python version
   python --version
   ```

5. **Install backend dependencies:**
   ```bash
   pip install -r server/requirements.txt
   ```
6. **Install frontend dependencies:**
   ```bash
   npm install --prefix web
   ```
7. **Start the FastAPI server:**
   ```bash
   python -m server.server --reload
   ```
8. **Start the Next.js app (new terminal):**
   ```bash
   npm run dev --prefix web
   ```
9. **Connect Gmail for email workflows.** With both services running, open [http://localhost:3000](http://localhost:3000), head to *Settings → Gmail*, and complete the Composio OAuth flow. This step is required for email drafting, replies, and the important-email monitor.

The web app proxies API calls to the Python server using the values in `.env`, so keeping both processes running is required for end-to-end flows.

## Project Layout
- `server/` – FastAPI application and agents
- `web/` – Next.js app
- `server/data/` – runtime data (ignored by git)

## License
MIT — see [LICENSE](LICENSE).
