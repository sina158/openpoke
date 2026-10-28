<!-- DRAFT-FOR-SINA: facts are checked; rewrite the prose in your own words before submitting -->

# OpenPoke fork: bounded agent routing

This fork changes how OpenPoke's interaction agent picks an execution agent, and puts an approval gate in
front of outbound email. Everything above "Upstream README" is new; the upstream README is kept unchanged below.

AI assistance: TBD-SINA (one line, plan decision 2a).

## The problem

Upstream, every turn shows the interaction agent every execution agent by name
(`server/agents/interaction_agent/agent.py`, `_render_active_agents`), and the model reuses an agent by retyping
its name in `send_message_to_agent(agent_name=...)`.

- **Size.** The roster block grows linearly with the number of agents: about <!-- n:selector.tokens.n5.full_mean -->55<!-- /n --> tokens at 5 agents, <!-- n:selector.tokens.n500.full_mean -->5,499<!-- /n --> at 500 and <!-- n:selector.tokens.n5000.full_mean -->55,014<!-- /n --> at 5,000 (chars / 4, `evals/results/SELECTOR.md`). In the baseline run the whole prompt at 500 agents averaged <!-- n:report.by_key.baseline_minimax-m3_k8_n500.prompt_tokens.mean -->9,499<!-- /n --> prompt tokens per turn.
- **Identity.** An agent was a string in `roster.json`. Reuse means retyping the name exactly; two names with the
  same slug shared one log file; two clients with similar names cannot be told apart from the name.
- **Decision.** The upstream prompt tells the model to prefer an existing agent and "Don't worry if the agent name
  is unrelated to the new task if it contains useful context" (`prompts/interaction_full.md`, line 19). There is no
  way to ask the user which agent is meant and no check before a second agent is made for the same matter.
  Baseline at 500 agents (three trials): wrong reuse <!-- n:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.rate -->36.3%<!-- /n --> (<!-- n:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.k -->73<!-- /n --> of <!-- n:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.n -->201<!-- /n -->); <!-- n:report.by_key.baseline_minimax-m3_k8_n500.cross_client.wrong_reuse -->49<!-- /n --> of those reached another client's agent; duplicates <!-- n:report.by_key.baseline_minimax-m3_k8_n500.duplicate.rate -->13.4%<!-- /n -->.
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
make injection      # offline: prompt-injection cases against the send gate -> evals/results/INJECTION.json
make compare        # committed result files + INJECTION.json -> evals/results/RESULTS.md and RESULTS.json
python -m evals.check_readme_numbers                     # every marked number in this README against RESULTS.json
python -m evals.check_readme_numbers --readme DESIGN.md  # the same for DESIGN.md

# offline: rerun the routing eval from the committed cassettes; an unrecorded call fails
make eval-replay EVAL_ARGS="--model minimax-m3 --split all --out-dir /tmp/openpoke-replay"
make eval-replay ARMS="baseline fix" SIZES=500 TRIALS=3 EVAL_ARGS="--model minimax-m3 --out-dir /tmp/openpoke-replay"
python -m evals.run_selector --out-dir /tmp/openpoke-replay  # offline: recall@k, MRR and roster size per scorer

make eval-live      # live: needs OPENPOKE_LLM_API_KEY or OPENROUTER_API_KEY; spend cap BUDGET_PER_ARM (default $13) per arm
```

`make compare` reads `evals/results/INJECTION.json`, so run `make injection` first. The replays and the selector
eval write new result files; `--out-dir` keeps them apart from the committed ones, because `make compare` refuses two
result files with the same configuration. The committed cassettes were recorded with `minimax-m3`. On OpenRouter,
`make eval-live` defaults to `openai/gpt-4.1-mini`; pass `EVAL_ARGS="--model <model>"` to choose another.

Settings (env): `OPENPOKE_ROSTER_MODE` (`shortlist` default, `full`, `full_cards`), `OPENPOKE_PROMPT_VARIANT`
(`shortlist` default, `original`), `OPENPOKE_SHORTLIST_K` (8), `OPENPOKE_SHORTLIST_TOKEN_BUDGET` (1200),
`OPENPOKE_SELECTOR` (`bm25` default, `hybrid`), `OPENPOKE_DUPLICATE_THRESHOLD` (0.85), `OPENPOKE_ARCHIVE_IDLE_DAYS`
(30), `OPENPOKE_EXECUTION_HISTORY_LIMIT` (unset = upstream behaviour), `OPENPOKE_DATA_DIR`, `OPENPOKE_LLM_BASE_URL`,
`OPENPOKE_*_MODEL` per role. The hybrid scorer needs `pip install -r requirements-embeddings.txt`.

## Results

Model <!-- n:report.by_key.baseline_minimax-m3_k8_n500.model -->minimax-m3<!-- /n --> through an OpenAI-compatible endpoint (host in each results file header),
temperature 0. Headline numbers use the report split: <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.pairs -->86<!-- /n --> cases
from <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.templates -->19<!-- /n --> templates (the held-out split was kept for tuning; all 130 cases are a secondary table in
`evals/results/RESULTS.md`). Baseline and fix at N=500 ran <!-- n:report.by_key.fix_minimax-m3_k8_n500.trials -->3<!-- /n --> trials at commit
<!-- n:report.by_key.fix_minimax-m3_k8_n500.commit -->4c399f2<!-- /n --> (<!-- n:report.by_key.fix_minimax-m3_k8_n500.date -->2026-10-28T07:21:03Z<!-- /n -->), so their rows count each case once per trial
(n = <!-- n:report.by_key.fix_minimax-m3_k8_n500.n -->258<!-- /n -->); the k=3 and k=16 runs are one trial at the same commit. Every other row is one trial
of all 130 cases at commit <!-- n:report.by_key.baseline_minimax-m3_k8_n5.commit -->773a8ac<!-- /n -->, of which the report cases are shown.
Rates carry 95% Wilson intervals; because cases share
templates, a template-clustered bootstrap interval is given too. Every number below is copied from
`evals/results/RESULTS.json` and checked by
`evals/check_readme_numbers.py`. Arms: **baseline** = upstream prompt, upstream four tools, every agent by name
(the current turn is escaped, and email sends only propose); **prompt_only** = the new prompt and tools over the
full roster with ids; **fix** = the new prompt and tools over the shortlist. Execution is not run: the eval stops
at the first routing call and grades its arguments (`evals/run_routing.py`).

### Summary

- At N=500 the fix routed <!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy.rate -->73.3%<!-- /n --> of turns correctly against
  <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.rate -->48.4%<!-- /n --> for the baseline, and
  <!-- n:report.by_key.fix_minimax-m3_k8_n500.pass_k -->64.0%<!-- /n --> vs
  <!-- n:report.by_key.baseline_minimax-m3_k8_n500.pass_k -->40.7%<!-- /n --> of cases were right on all three trials. Paired by
  case, only the fix was right on <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.c -->30<!-- /n --> cases and only the
  baseline on <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.b -->8<!-- /n --> (McNemar p =
  <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.mcnemar_p -->0.00047<!-- /n -->).
- The size of the gain is uncertain. The template-clustered 95% interval on the paired difference is
  <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.0 -->1.1%<!-- /n --> to
  <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.1 -->49.5%<!-- /n -->: the 86 cases come from
  only 19 templates, and cases from one template tend to pass or fail together.
- Much of the gain is on paraphrase and entity queries, which the cards and `search_agents` make answerable and a
  list of names does not. At N=500, paraphrase: fix
  <!-- n:report.by_key.fix_minimax-m3_k8_n500.by_category.paraphrase.rate -->97.4%<!-- /n -->, baseline
  <!-- n:report.by_key.baseline_minimax-m3_k8_n500.by_category.paraphrase.rate -->0.0%<!-- /n -->; entity:
  <!-- n:report.by_key.fix_minimax-m3_k8_n500.by_category.entity.rate -->100.0%<!-- /n --> vs
  <!-- n:report.by_key.baseline_minimax-m3_k8_n500.by_category.entity.rate -->46.7%<!-- /n -->; explicit name:
  <!-- n:report.by_key.fix_minimax-m3_k8_n500.by_category.explicit.rate -->69.2%<!-- /n --> vs
  <!-- n:report.by_key.baseline_minimax-m3_k8_n500.by_category.explicit.rate -->71.8%<!-- /n -->. Without the ambiguous and
  paraphrase categories the gap shrinks to
  <!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.rate -->74.1%<!-- /n --> vs
  <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.rate -->62.2%<!-- /n -->, and prompt_only
  (one trial) scores <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.rate -->76.1%<!-- /n -->
  there. Outside those two categories, a full roster that shows ids does about as well as the shortlist; it sends <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.prompt_tokens.mean -->15,153<!-- /n --> prompt tokens per turn to do it.
- The fix does not make the prompt smaller per turn. Each model call carries
  <!-- n:bars.prompt_token_per_call_ratio_n500 -->57.4%<!-- /n --> of the baseline's prompt tokens at N=500, but the fix calls
  `search_agents` <!-- n:report.by_key.fix_minimax-m3_k8_n500.searches.mean -->1.18<!-- /n --> times per turn and needs another
  model call to read the results: <!-- n:report.by_key.fix_minimax-m3_k8_n500.llm_calls.mean -->1.83<!-- /n --> calls per turn
  against <!-- n:report.by_key.baseline_minimax-m3_k8_n500.llm_calls.mean -->1.06<!-- /n -->, so per turn it sends
  <!-- n:bars.prompt_token_ratio_n500 -->102.5%<!-- /n --> of the baseline's prompt tokens. It is slower at every N (p50
  <!-- n:report.by_key.fix_minimax-m3_k8_n500.latency_ms.p50 -->11,515<!-- /n --> ms vs
  <!-- n:report.by_key.baseline_minimax-m3_k8_n500.latency_ms.p50 -->8,297<!-- /n --> ms at N=500) and costs about twice as
  much as the baseline at N=5 and N=50. At N=500 it cost less in dollars
  ($<!-- n:report.by_key.fix_minimax-m3_k8_n500.usd_per_100_turns -->0.2128<!-- /n --> vs
  $<!-- n:report.by_key.baseline_minimax-m3_k8_n500.usd_per_100_turns -->0.2493<!-- /n --> per 100 turns) despite the extra tokens:
  the raw result files show a larger share of its prompt tokens served from the provider's cache, since a second call
  in a turn repeats the first call's prefix. RESULTS.json does not carry that split.
- k=3, 8 and 16 give about the same accuracy at N=500 (k-sweep below).
- Two of the six pre-registered bars fail: cross-client wrong reuse in the fix arm is not zero, and the
  prompt-token bar.

### Pre-registered bars (`evals/BARS.md`)

| bar | result | met |
|---|---|---|
| selector recall@8 at N=500, non-ambiguous cases >= 0.95 | <!-- n:selector.recall.plus_pins.n500.non_ambiguous.k8 -->1.000<!-- /n --> with pins, <!-- n:selector.recall.bm25_entities_recency.n500.non_ambiguous.k8 -->0.990<!-- /n --> without | yes |
| fix wrong reuse not worse than baseline at every N | fix <!-- n:report.by_key.fix_minimax-m3_k8_n5.wrong_reuse.rate -->25.4%<!-- /n --> / <!-- n:report.by_key.fix_minimax-m3_k8_n50.wrong_reuse.rate -->17.9%<!-- /n --> / <!-- n:report.by_key.fix_minimax-m3_k8_n500.wrong_reuse.rate -->20.4%<!-- /n --> vs baseline <!-- n:report.by_key.baseline_minimax-m3_k8_n5.wrong_reuse.rate -->47.8%<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n50.wrong_reuse.rate -->38.8%<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.rate -->36.3%<!-- /n --> at N=5 / 50 / 500 | yes |
| cross-client wrong reuse = 0 in the fix arm (as a bound) | <!-- n:report.by_key.fix_minimax-m3_k8_n5.cross_client.wrong_reuse -->8<!-- /n --> of <!-- n:report.by_key.fix_minimax-m3_k8_n5.cross_client.wrong_reuse_n -->67<!-- /n --> at N=5, <!-- n:report.by_key.fix_minimax-m3_k8_n50.cross_client.wrong_reuse -->5<!-- /n --> of <!-- n:report.by_key.fix_minimax-m3_k8_n50.cross_client.wrong_reuse_n -->67<!-- /n --> at N=50, <!-- n:report.by_key.fix_minimax-m3_k8_n500.cross_client.wrong_reuse -->15<!-- /n --> of <!-- n:report.by_key.fix_minimax-m3_k8_n500.cross_client.wrong_reuse_n -->201<!-- /n --> at N=500 (three trials) | **no** |
| duplicates lower than baseline at N=500 | fix <!-- n:report.by_key.fix_minimax-m3_k8_n500.duplicate.k -->0<!-- /n --> of <!-- n:report.by_key.fix_minimax-m3_k8_n500.duplicate.n -->201<!-- /n --> vs baseline <!-- n:report.by_key.baseline_minimax-m3_k8_n500.duplicate.k -->27<!-- /n --> of <!-- n:report.by_key.baseline_minimax-m3_k8_n500.duplicate.n -->201<!-- /n --> (<!-- n:report.by_key.baseline_minimax-m3_k8_n500.duplicate.rate -->13.4%<!-- /n -->) | yes |
| prompt tokens per turn at N=500 in the fix <= 40% of baseline | fix <!-- n:report.by_key.fix_minimax-m3_k8_n500.prompt_tokens.mean -->9,738<!-- /n --> vs baseline <!-- n:report.by_key.baseline_minimax-m3_k8_n500.prompt_tokens.mean -->9,499<!-- /n --> (<!-- n:bars.prompt_token_ratio_n500 -->102.5%<!-- /n -->); per model call <!-- n:report.by_key.fix_minimax-m3_k8_n500.prompt_tokens_per_call.mean -->5,142<!-- /n --> vs <!-- n:report.by_key.baseline_minimax-m3_k8_n500.prompt_tokens_per_call.mean -->8,962<!-- /n --> (<!-- n:bars.prompt_token_per_call_ratio_n500 -->57.4%<!-- /n -->) | **no** |
| injection suite: 0 sends without a consumed approval | <!-- n:injection.unauthorised_sends -->0<!-- /n --> of <!-- n:injection.attacks -->46<!-- /n --> attacks | yes |

Bar 3 fails at every N. At N=500 all of the fix's cross-client wrong reuses were sprays: the right agent was
messaged and so was another client's (<!-- n:report.by_key.fix_minimax-m3_k8_n500.spray.cross_client -->15<!-- /n --> sprays,
<!-- n:report.by_key.fix_minimax-m3_k8_n500.wrong_only.cross_client -->0<!-- /n --> wrong-only). A spray still puts one client's
details in front of another client's agent, so it counts. Bar 5 fails on both readings: per turn because the fix makes more
model calls, and per call because its prompt is still <!-- n:bars.prompt_token_per_call_ratio_n500 -->57.4%<!-- /n --> of the
baseline's. The roster block itself shrinks to <!-- n:selector.tokens.n500.ratio -->0.140<!-- /n --> of the name list at N=500, but
the rest of the prompt (instructions, tool schemas, conversation) does not shrink. Bar 6 tests the gate with a scripted model, not the model's
judgement.

### Routing: baseline vs prompt_only vs fix

| arm | N | n | accuracy | 95% CI | wrong reuse | cross-client: wrong reuse / hijack / wrong guess | duplicate | correct create | hijack |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 5 | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.n -->86<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy.rate -->45.3%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy.ci95.0 -->35.3%<!-- /n --> to <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy.ci95.1 -->55.8%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.wrong_reuse.rate -->47.8%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.cross_client.wrong_reuse -->25<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n5.cross_client.hijack -->1<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n5.cross_client.wrong_guess -->1<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.duplicate.rate -->1.5%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.correct_create.rate -->38.5%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.hijack.count -->8<!-- /n --> of <!-- n:report.by_key.baseline_minimax-m3_k8_n5.hijack.n -->13<!-- /n --> |
| baseline | 50 | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.n -->86<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy.rate -->48.8%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy.ci95.0 -->38.6%<!-- /n --> to <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy.ci95.1 -->59.2%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.wrong_reuse.rate -->38.8%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.cross_client.wrong_reuse -->18<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n50.cross_client.hijack -->0<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n50.cross_client.wrong_guess -->0<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.duplicate.rate -->9.0%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.correct_create.rate -->53.8%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.hijack.count -->6<!-- /n --> of <!-- n:report.by_key.baseline_minimax-m3_k8_n50.hijack.n -->13<!-- /n --> |
| baseline | 500 | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.n -->258<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.rate -->48.4%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.ci95.0 -->42.4%<!-- /n --> to <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.ci95.1 -->54.5%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.rate -->36.3%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.cross_client.wrong_reuse -->49<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n500.cross_client.hijack -->0<!-- /n --> / <!-- n:report.by_key.baseline_minimax-m3_k8_n500.cross_client.wrong_guess -->0<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.duplicate.rate -->13.4%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.correct_create.rate -->61.5%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.hijack.count -->15<!-- /n --> of <!-- n:report.by_key.baseline_minimax-m3_k8_n500.hijack.n -->39<!-- /n --> |
| prompt_only | 5 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.n -->86<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.accuracy.rate -->58.1%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.accuracy.ci95.0 -->47.6%<!-- /n --> to <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.accuracy.ci95.1 -->68.0%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.wrong_reuse.rate -->34.3%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.cross_client.wrong_reuse -->16<!-- /n --> / <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.cross_client.hijack -->0<!-- /n --> / <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.cross_client.wrong_guess -->0<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.duplicate.rate -->0.0%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.correct_create.rate -->76.9%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.hijack.count -->3<!-- /n --> of <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.hijack.n -->13<!-- /n --> |
| prompt_only | 50 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.n -->86<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.accuracy.rate -->54.7%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.accuracy.ci95.0 -->44.2%<!-- /n --> to <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.accuracy.ci95.1 -->64.7%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.wrong_reuse.rate -->40.3%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.cross_client.wrong_reuse -->19<!-- /n --> / <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.cross_client.hijack -->0<!-- /n --> / <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.cross_client.wrong_guess -->0<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.duplicate.rate -->3.0%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.correct_create.rate -->69.2%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.hijack.count -->4<!-- /n --> of <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.hijack.n -->13<!-- /n --> |
| prompt_only | 500 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.n -->86<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.accuracy.rate -->60.5%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.accuracy.ci95.0 -->49.9%<!-- /n --> to <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.accuracy.ci95.1 -->70.1%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.wrong_reuse.rate -->35.8%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.cross_client.wrong_reuse -->15<!-- /n --> / <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.cross_client.hijack -->0<!-- /n --> / <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.cross_client.wrong_guess -->0<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.duplicate.rate -->1.5%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.correct_create.rate -->92.3%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.hijack.count -->1<!-- /n --> of <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.hijack.n -->13<!-- /n --> |
| fix | 5 | <!-- n:report.by_key.fix_minimax-m3_k8_n5.n -->86<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.accuracy.rate -->66.3%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.accuracy.ci95.0 -->55.8%<!-- /n --> to <!-- n:report.by_key.fix_minimax-m3_k8_n5.accuracy.ci95.1 -->75.4%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.wrong_reuse.rate -->25.4%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.cross_client.wrong_reuse -->8<!-- /n --> / <!-- n:report.by_key.fix_minimax-m3_k8_n5.cross_client.hijack -->0<!-- /n --> / <!-- n:report.by_key.fix_minimax-m3_k8_n5.cross_client.wrong_guess -->0<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.duplicate.rate -->0.0%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.correct_create.rate -->53.8%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.hijack.count -->6<!-- /n --> of <!-- n:report.by_key.fix_minimax-m3_k8_n5.hijack.n -->13<!-- /n --> |
| fix | 50 | <!-- n:report.by_key.fix_minimax-m3_k8_n50.n -->86<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.accuracy.rate -->72.1%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.accuracy.ci95.0 -->61.8%<!-- /n --> to <!-- n:report.by_key.fix_minimax-m3_k8_n50.accuracy.ci95.1 -->80.5%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.wrong_reuse.rate -->17.9%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.cross_client.wrong_reuse -->5<!-- /n --> / <!-- n:report.by_key.fix_minimax-m3_k8_n50.cross_client.hijack -->0<!-- /n --> / <!-- n:report.by_key.fix_minimax-m3_k8_n50.cross_client.wrong_guess -->0<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.duplicate.rate -->0.0%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.correct_create.rate -->61.5%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.hijack.count -->5<!-- /n --> of <!-- n:report.by_key.fix_minimax-m3_k8_n50.hijack.n -->13<!-- /n --> |
| fix | 500 | <!-- n:report.by_key.fix_minimax-m3_k8_n500.n -->258<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy.rate -->73.3%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy.ci95.0 -->67.5%<!-- /n --> to <!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy.ci95.1 -->78.3%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.wrong_reuse.rate -->20.4%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.cross_client.wrong_reuse -->15<!-- /n --> / <!-- n:report.by_key.fix_minimax-m3_k8_n500.cross_client.hijack -->0<!-- /n --> / <!-- n:report.by_key.fix_minimax-m3_k8_n500.cross_client.wrong_guess -->0<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.duplicate.rate -->0.0%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.correct_create.rate -->71.8%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.hijack.count -->11<!-- /n --> of <!-- n:report.by_key.fix_minimax-m3_k8_n500.hijack.n -->39<!-- /n --> |
| cards_full | 5 | <!-- n:report.by_key.cards_full_minimax-m3_k8_n5.n -->86<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n5.accuracy.rate -->72.1%<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n5.accuracy.ci95.0 -->61.8%<!-- /n --> to <!-- n:report.by_key.cards_full_minimax-m3_k8_n5.accuracy.ci95.1 -->80.5%<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n5.wrong_reuse.rate -->17.9%<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n5.cross_client.wrong_reuse -->6<!-- /n --> / <!-- n:report.by_key.cards_full_minimax-m3_k8_n5.cross_client.hijack -->0<!-- /n --> / <!-- n:report.by_key.cards_full_minimax-m3_k8_n5.cross_client.wrong_guess -->0<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n5.duplicate.rate -->0.0%<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n5.correct_create.rate -->38.5%<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n5.hijack.count -->8<!-- /n --> of <!-- n:report.by_key.cards_full_minimax-m3_k8_n5.hijack.n -->13<!-- /n --> |
| cards_full | 50 | <!-- n:report.by_key.cards_full_minimax-m3_k8_n50.n -->86<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n50.accuracy.rate -->69.8%<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n50.accuracy.ci95.0 -->59.4%<!-- /n --> to <!-- n:report.by_key.cards_full_minimax-m3_k8_n50.accuracy.ci95.1 -->78.5%<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n50.wrong_reuse.rate -->20.9%<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n50.cross_client.wrong_reuse -->7<!-- /n --> / <!-- n:report.by_key.cards_full_minimax-m3_k8_n50.cross_client.hijack -->0<!-- /n --> / <!-- n:report.by_key.cards_full_minimax-m3_k8_n50.cross_client.wrong_guess -->0<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n50.duplicate.rate -->0.0%<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n50.correct_create.rate -->38.5%<!-- /n --> | <!-- n:report.by_key.cards_full_minimax-m3_k8_n50.hijack.count -->8<!-- /n --> of <!-- n:report.by_key.cards_full_minimax-m3_k8_n50.hijack.n -->13<!-- /n --> |

Wrong reuse and duplicate are over cases whose gold is an existing agent; correct create and hijack over
should-create cases. Cross-client: how many of those wrong answers reached another client's agent.
`cards_full` (every card, no selection, roster mode `full_cards`; N <= 50 only) separates what the cards bought
from what the selection bought. At N=5 and N=50 most of the gain over prompt_only comes from the cards: prompt_only
<!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.accuracy.rate -->58.1%<!-- /n --> / <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.accuracy.rate -->54.7%<!-- /n -->, cards_full <!-- n:report.by_key.cards_full_minimax-m3_k8_n5.accuracy.rate -->72.1%<!-- /n --> / <!-- n:report.by_key.cards_full_minimax-m3_k8_n50.accuracy.rate -->69.8%<!-- /n -->, fix <!-- n:report.by_key.fix_minimax-m3_k8_n5.accuracy.rate -->66.3%<!-- /n --> / <!-- n:report.by_key.fix_minimax-m3_k8_n50.accuracy.rate -->72.1%<!-- /n -->.
cards_full was not run at N=500, so what the selection adds there is not measured directly.

Cases share templates (28 templates across the 130 cases, 19 of them in the report split), so the Wilson intervals
above are too narrow. Intervals clustered by template:

| arm | N | accuracy, 95% CI by template bootstrap | templates (effective n) |
|---|---|---|---|
| baseline | 5 | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy.clustered.ci95.0 -->23.9%<!-- /n --> to <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy.clustered.ci95.1 -->66.7%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy.clustered.templates -->19<!-- /n --> |
| baseline | 50 | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy.clustered.ci95.0 -->31.0%<!-- /n --> to <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy.clustered.ci95.1 -->65.2%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy.clustered.templates -->19<!-- /n --> |
| baseline | 500 | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.clustered.ci95.0 -->28.2%<!-- /n --> to <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.clustered.ci95.1 -->68.5%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.clustered.templates -->19<!-- /n --> |
| prompt_only | 5 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.accuracy.clustered.ci95.0 -->40.0%<!-- /n --> to <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.accuracy.clustered.ci95.1 -->75.6%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.accuracy.clustered.templates -->19<!-- /n --> |
| prompt_only | 50 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.accuracy.clustered.ci95.0 -->35.9%<!-- /n --> to <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.accuracy.clustered.ci95.1 -->72.5%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.accuracy.clustered.templates -->19<!-- /n --> |
| prompt_only | 500 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.accuracy.clustered.ci95.0 -->39.8%<!-- /n --> to <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.accuracy.clustered.ci95.1 -->80.2%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.accuracy.clustered.templates -->19<!-- /n --> |
| fix | 5 | <!-- n:report.by_key.fix_minimax-m3_k8_n5.accuracy.clustered.ci95.0 -->48.8%<!-- /n --> to <!-- n:report.by_key.fix_minimax-m3_k8_n5.accuracy.clustered.ci95.1 -->82.6%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.accuracy.clustered.templates -->19<!-- /n --> |
| fix | 50 | <!-- n:report.by_key.fix_minimax-m3_k8_n50.accuracy.clustered.ci95.0 -->56.4%<!-- /n --> to <!-- n:report.by_key.fix_minimax-m3_k8_n50.accuracy.clustered.ci95.1 -->86.1%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.accuracy.clustered.templates -->19<!-- /n --> |
| fix | 500 | <!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy.clustered.ci95.0 -->59.6%<!-- /n --> to <!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy.clustered.ci95.1 -->85.6%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy.clustered.templates -->19<!-- /n --> |

### Paired against baseline (same cases)

Each case counts once, by its majority outcome over the trials.

| arm | N | pairs | change in accuracy | 95% CI (template bootstrap) | only baseline right | only arm right | McNemar p |
|---|---|---|---|---|---|---|---|
| prompt_only | 5 | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.pairs -->86<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.delta_accuracy -->12.8%<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.delta_clustered_ci95.0 -->3.2%<!-- /n --> to <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.delta_clustered_ci95.1 -->24.7%<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.b -->5<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.c -->16<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n5.mcnemar_p -->0.027<!-- /n --> |
| prompt_only | 50 | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.pairs -->86<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.delta_accuracy -->5.8%<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.delta_clustered_ci95.0 -->-4.5%<!-- /n --> to <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.delta_clustered_ci95.1 -->16.7%<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.b -->7<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.c -->12<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n50.mcnemar_p -->0.36<!-- /n --> |
| prompt_only | 500 | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.pairs -->86<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.delta_accuracy -->12.8%<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.0 -->1.1%<!-- /n --> to <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.1 -->27.7%<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.b -->2<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.c -->13<!-- /n --> | <!-- n:report.paired_by_key.prompt_only_vs_baseline_minimax-m3_k8_n500.mcnemar_p -->0.0074<!-- /n --> |
| fix | 5 | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.pairs -->86<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.delta_accuracy -->20.9%<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.delta_clustered_ci95.0 -->1.1%<!-- /n --> to <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.delta_clustered_ci95.1 -->42.0%<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.b -->7<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.c -->25<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n5.mcnemar_p -->0.0021<!-- /n --> |
| fix | 50 | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.pairs -->86<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.delta_accuracy -->23.3%<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.delta_clustered_ci95.0 -->3.0%<!-- /n --> to <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.delta_clustered_ci95.1 -->44.3%<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.b -->11<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.c -->31<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n50.mcnemar_p -->0.0029<!-- /n --> |
| fix | 500 | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.pairs -->86<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.delta_accuracy -->25.6%<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.0 -->1.1%<!-- /n --> to <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.1 -->49.5%<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.b -->8<!-- /n --> | <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.c -->30<!-- /n --> | < 0.001 |

### Accuracy without the categories the baseline cannot win

The baseline has no clarify tool (ambiguous cases) and only names to go on (paraphrase cases are built so the name
lacks the discriminator), so it loses those by construction. Accuracy with both categories left out:

| arm | N | n | accuracy |
|---|---|---|---|
| baseline | 5 | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy_excl_ambiguous_paraphrase.n -->67<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.accuracy_excl_ambiguous_paraphrase.rate -->58.2%<!-- /n --> |
| baseline | 50 | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy_excl_ambiguous_paraphrase.n -->67<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.accuracy_excl_ambiguous_paraphrase.rate -->61.2%<!-- /n --> |
| baseline | 500 | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.n -->201<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.rate -->62.2%<!-- /n --> |
| prompt_only | 5 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.accuracy_excl_ambiguous_paraphrase.n -->67<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.accuracy_excl_ambiguous_paraphrase.rate -->70.1%<!-- /n --> |
| prompt_only | 50 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.accuracy_excl_ambiguous_paraphrase.n -->67<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.accuracy_excl_ambiguous_paraphrase.rate -->67.2%<!-- /n --> |
| prompt_only | 500 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.n -->67<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.rate -->76.1%<!-- /n --> |
| fix | 5 | <!-- n:report.by_key.fix_minimax-m3_k8_n5.accuracy_excl_ambiguous_paraphrase.n -->67<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.accuracy_excl_ambiguous_paraphrase.rate -->68.7%<!-- /n --> |
| fix | 50 | <!-- n:report.by_key.fix_minimax-m3_k8_n50.accuracy_excl_ambiguous_paraphrase.n -->67<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.accuracy_excl_ambiguous_paraphrase.rate -->73.1%<!-- /n --> |
| fix | 500 | <!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.n -->201<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy_excl_ambiguous_paraphrase.rate -->74.1%<!-- /n --> |

On these cases prompt_only at N=500 (one trial) is level with the fix: the ids and the new prompt do most of the
work.

### Clarify (ambiguous cases) and over-clarify (all other cases)

| arm | N | clarify recall | clarify precision | over-clarify |
|---|---|---|---|---|
| baseline | all | no clarify tool | - | - |
| prompt_only | 5 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.clarify_recall.rate -->50.0%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.clarify_precision.rate -->30.0%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.over_clarify.rate -->8.8%<!-- /n --> |
| prompt_only | 50 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.clarify_recall.rate -->33.3%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.clarify_precision.rate -->40.0%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.over_clarify.rate -->2.5%<!-- /n --> |
| prompt_only | 500 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.clarify_recall.rate -->16.7%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.clarify_precision.rate -->33.3%<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.over_clarify.rate -->2.5%<!-- /n --> |
| fix | 5 | <!-- n:report.by_key.fix_minimax-m3_k8_n5.clarify_recall.rate -->0.0%<!-- /n --> | - (no clarify) | <!-- n:report.by_key.fix_minimax-m3_k8_n5.over_clarify.rate -->0.0%<!-- /n --> |
| fix | 50 | <!-- n:report.by_key.fix_minimax-m3_k8_n50.clarify_recall.rate -->0.0%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.clarify_precision.rate -->0.0%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.over_clarify.rate -->1.2%<!-- /n --> |
| fix | 500 | <!-- n:report.by_key.fix_minimax-m3_k8_n500.clarify_recall.rate -->11.1%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.clarify_precision.rate -->100.0%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.over_clarify.rate -->0.0%<!-- /n --> |

A clarify counts as correct only on cases labelled ambiguous, and only when its candidates include every target.
The fix seldom asks: at N=500 it clarified on <!-- n:report.by_key.fix_minimax-m3_k8_n500.clarify_recall.k -->2<!-- /n --> of
<!-- n:report.by_key.fix_minimax-m3_k8_n500.clarify_recall.n -->18<!-- /n --> ambiguous case-trials; more often it messaged two
agents at once (FAILURES.md, class 4).

### Consistency across trials

| arm | N | trials | pass^3 | agreement |
|---|---|---|---|---|
| baseline | 500 | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.trials -->3<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.pass_k -->40.7%<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.agreement -->83.7%<!-- /n --> |
| fix | 500 | <!-- n:report.by_key.fix_minimax-m3_k8_n500.trials -->3<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.pass_k -->64.0%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.agreement -->81.4%<!-- /n --> |

pass^3: share of cases right on all three trials. Agreement: share of cases right on all three trials or wrong on
all three. Only these two runs had more than one trial, so the other rows have no consistency figure.

### Cost and latency

| arm | N | prompt tokens per turn, mean | p95 | prompt tokens per call | LLM calls per turn | `search_agents` per turn | latency ms p50 | p95 | USD per 100 turns |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 5 | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.prompt_tokens.mean -->3,214<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.prompt_tokens.p95 -->3,270<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.prompt_tokens_per_call.mean -->3,136<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.llm_calls.mean -->1.02<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.searches.mean -->0.00<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.latency_ms.p50 -->6,332<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n5.latency_ms.p95 -->15,490<!-- /n --> | $<!-- n:report.by_key.baseline_minimax-m3_k8_n5.usd_per_100_turns -->0.0967<!-- /n --> |
| baseline | 50 | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.prompt_tokens.mean -->3,853<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.prompt_tokens.p95 -->3,812<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.prompt_tokens_per_call.mean -->3,673<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.llm_calls.mean -->1.05<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.searches.mean -->0.00<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.latency_ms.p50 -->6,156<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n50.latency_ms.p95 -->14,312<!-- /n --> | $<!-- n:report.by_key.baseline_minimax-m3_k8_n50.usd_per_100_turns -->0.1044<!-- /n --> |
| baseline | 500 | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.prompt_tokens.mean -->9,499<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.prompt_tokens.p95 -->18,223<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.prompt_tokens_per_call.mean -->8,962<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.llm_calls.mean -->1.06<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.searches.mean -->0.00<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.latency_ms.p50 -->8,297<!-- /n --> | <!-- n:report.by_key.baseline_minimax-m3_k8_n500.latency_ms.p95 -->20,077<!-- /n --> | $<!-- n:report.by_key.baseline_minimax-m3_k8_n500.usd_per_100_turns -->0.2493<!-- /n --> |
| prompt_only | 5 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.prompt_tokens.mean -->4,564<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.prompt_tokens.p95 -->8,398<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.prompt_tokens_per_call.mean -->3,895<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.llm_calls.mean -->1.16<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.searches.mean -->0.00<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.latency_ms.p50 -->7,957<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.latency_ms.p95 -->16,805<!-- /n --> | $<!-- n:report.by_key.prompt_only_minimax-m3_k8_n5.usd_per_100_turns -->0.1522<!-- /n --> |
| prompt_only | 50 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.prompt_tokens.mean -->5,285<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.prompt_tokens.p95 -->10,149<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.prompt_tokens_per_call.mean -->4,744<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.llm_calls.mean -->1.10<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.searches.mean -->0.00<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.latency_ms.p50 -->8,239<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.latency_ms.p95 -->19,991<!-- /n --> | $<!-- n:report.by_key.prompt_only_minimax-m3_k8_n50.usd_per_100_turns -->0.1830<!-- /n --> |
| prompt_only | 500 | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.prompt_tokens.mean -->15,153<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.prompt_tokens.p95 -->27,477<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.prompt_tokens_per_call.mean -->13,225<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.llm_calls.mean -->1.14<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.searches.mean -->0.00<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.latency_ms.p50 -->8,025<!-- /n --> | <!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.latency_ms.p95 -->28,168<!-- /n --> | $<!-- n:report.by_key.prompt_only_minimax-m3_k8_n500.usd_per_100_turns -->0.4605<!-- /n --> |
| fix | 5 | <!-- n:report.by_key.fix_minimax-m3_k8_n5.prompt_tokens.mean -->6,949<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.prompt_tokens.p95 -->14,516<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.prompt_tokens_per_call.mean -->4,416<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.llm_calls.mean -->1.55<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.searches.mean -->0.88<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.latency_ms.p50 -->9,196<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.latency_ms.p95 -->24,210<!-- /n --> | $<!-- n:report.by_key.fix_minimax-m3_k8_n5.usd_per_100_turns -->0.1892<!-- /n --> |
| fix | 50 | <!-- n:report.by_key.fix_minimax-m3_k8_n50.prompt_tokens.mean -->9,216<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.prompt_tokens.p95 -->18,342<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.prompt_tokens_per_call.mean -->5,167<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.llm_calls.mean -->1.73<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.searches.mean -->0.99<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.latency_ms.p50 -->9,894<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.latency_ms.p95 -->21,623<!-- /n --> | $<!-- n:report.by_key.fix_minimax-m3_k8_n50.usd_per_100_turns -->0.2158<!-- /n --> |
| fix | 500 | <!-- n:report.by_key.fix_minimax-m3_k8_n500.prompt_tokens.mean -->9,738<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.prompt_tokens.p95 -->20,472<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.prompt_tokens_per_call.mean -->5,142<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.llm_calls.mean -->1.83<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.searches.mean -->1.18<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.latency_ms.p50 -->11,515<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.latency_ms.p95 -->28,659<!-- /n --> | $<!-- n:report.by_key.fix_minimax-m3_k8_n500.usd_per_100_turns -->0.2128<!-- /n --> |

Dollars are list-price equivalents from the provider's usage block, per routing decision (all LLM calls of the
turn). In the fix arm the extra calls are `search_agents` rounds (the model needs another call to read the
results) and second tries after the duplicate check; one response can hold several searches. Total spend behind the <!-- n:spend.files -->8<!-- /n --> result files:
$<!-- n:spend.total_usd -->4.65<!-- /n -->.

### What the fix arm did with the shortlist

| N | duplicate check fired | right after it fired | cases whose target was archived | accuracy on those | search found the archived target | accuracy, target active |
|---|---|---|---|---|---|---|
| 5 | <!-- n:report.by_key.fix_minimax-m3_k8_n5.guard.fired -->1<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.guard.correct_after -->1<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.archived_target.archived.n -->35<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.archived_target.archived.rate -->51.4%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.archived_target.search_found_target -->28<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n5.archived_target.active.rate -->84.2%<!-- /n --> |
| 50 | <!-- n:report.by_key.fix_minimax-m3_k8_n50.guard.fired -->4<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.guard.correct_after -->4<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.archived_target.archived.n -->35<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.archived_target.archived.rate -->60.0%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.archived_target.search_found_target -->30<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n50.archived_target.active.rate -->86.8%<!-- /n --> |
| 500 | <!-- n:report.by_key.fix_minimax-m3_k8_n500.guard.fired -->13<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.guard.correct_after -->11<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.archived_target.archived.n -->105<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.archived_target.archived.rate -->60.0%<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.archived_target.search_found_target -->95<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.archived_target.active.rate -->86.0%<!-- /n --> |

The eval runs the shipped 30-day archive default, so old matters are archived and only reachable through
`search_agents`. At N=500 a search returned the archived target in
<!-- n:report.by_key.fix_minimax-m3_k8_n500.archived_target.search_found_target -->95<!-- /n --> of those
<!-- n:report.by_key.fix_minimax-m3_k8_n500.archived_target.archived.n -->105<!-- /n --> case-trials, but only
<!-- n:report.by_key.fix_minimax-m3_k8_n500.archived_target.archived.k -->63<!-- /n --> were routed right, so most of the loss on
archived targets comes after the search has found the agent.

### Selector, offline (all 130 cases, 110 with a target)

recall@8: share of cases whose target agents all rank in the top 8. `hybrid` is the lexical score fused with
embeddings by reciprocal rank fusion. `plus_pins` is what ships.

| scorer | N=5 | N=50 | N=500 | N=5,000 | MRR N=500 |
|---|---|---|---|---|---|
| recency | <!-- n:selector.recall.recency.n5.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.recency.n50.k8 -->0.018<!-- /n --> | <!-- n:selector.recall.recency.n500.k8 -->0.000<!-- /n --> | <!-- n:selector.recall.recency.n5000.k8 -->0.000<!-- /n --> | <!-- n:selector.recall.recency.n500.mrr -->0.005<!-- /n --> |
| bm25 | <!-- n:selector.recall.bm25.n5.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.bm25.n50.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.bm25.n500.k8 -->0.991<!-- /n --> | <!-- n:selector.recall.bm25.n5000.k8 -->0.955<!-- /n --> | <!-- n:selector.recall.bm25.n500.mrr -->0.926<!-- /n --> |
| bm25_entities | <!-- n:selector.recall.bm25_entities.n5.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.bm25_entities.n50.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.bm25_entities.n500.k8 -->0.991<!-- /n --> | <!-- n:selector.recall.bm25_entities.n5000.k8 -->0.955<!-- /n --> | <!-- n:selector.recall.bm25_entities.n500.mrr -->0.926<!-- /n --> |
| bm25_entities_recency | <!-- n:selector.recall.bm25_entities_recency.n5.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n50.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n500.k8 -->0.991<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n5000.k8 -->0.955<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n500.mrr -->0.924<!-- /n --> |
| plus_pins | <!-- n:selector.recall.plus_pins.n5.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.plus_pins.n50.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.plus_pins.n5000.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.mrr -->0.958<!-- /n --> |
| hybrid | <!-- n:selector.recall.hybrid.n5.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.hybrid.n50.k8 -->0.964<!-- /n --> | <!-- n:selector.recall.hybrid.n500.k8 -->0.918<!-- /n --> | <!-- n:selector.recall.hybrid.n5000.k8 -->0.873<!-- /n --> | <!-- n:selector.recall.hybrid.n500.mrr -->0.843<!-- /n --> |

Where hybrid loses, recall@8 on entity-only queries (an email, policy number or thread id):

| scorer | entity N=500 | entity N=5,000 | paraphrase N=500 | paraphrase N=5,000 |
|---|---|---|---|---|
| bm25_entities_recency | <!-- n:selector.recall.bm25_entities_recency.n500.by_category.entity.k8 -->0.933<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n5000.by_category.entity.k8 -->0.667<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n500.by_category.paraphrase.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.bm25_entities_recency.n5000.by_category.paraphrase.k8 -->1.000<!-- /n --> |
| hybrid | <!-- n:selector.recall.hybrid.n500.by_category.entity.k8 -->0.533<!-- /n --> | <!-- n:selector.recall.hybrid.n5000.by_category.entity.k8 -->0.467<!-- /n --> | <!-- n:selector.recall.hybrid.n500.by_category.paraphrase.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.hybrid.n5000.by_category.paraphrase.k8 -->1.000<!-- /n --> |
| plus_pins | <!-- n:selector.recall.plus_pins.n500.by_category.entity.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.plus_pins.n5000.by_category.entity.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.by_category.paraphrase.k8 -->1.000<!-- /n --> | <!-- n:selector.recall.plus_pins.n5000.by_category.paraphrase.k8 -->1.000<!-- /n --> |

### k-sweep at N=500

| k | recall@k, no pins | recall@k, with pins | fix arm accuracy (live) |
|---|---|---|---|
| 3 | <!-- n:selector.recall.bm25_entities_recency.n500.k3 -->0.973<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.k3 -->1.000<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k3_n500.accuracy.rate -->70.9%<!-- /n --> |
| 5 | <!-- n:selector.recall.bm25_entities_recency.n500.k5 -->0.991<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.k5 -->1.000<!-- /n --> | not run |
| 8 | <!-- n:selector.recall.bm25_entities_recency.n500.k8 -->0.991<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.k8 -->1.000<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy.rate -->73.3%<!-- /n --> |
| 12 | <!-- n:selector.recall.bm25_entities_recency.n500.k12 -->1.000<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.k12 -->1.000<!-- /n --> | not run |
| 16 | <!-- n:selector.recall.bm25_entities_recency.n500.k16 -->1.000<!-- /n --> | <!-- n:selector.recall.plus_pins.n500.k16 -->1.000<!-- /n --> | <!-- n:report.by_key.fix_minimax-m3_k16_n500.accuracy.rate -->73.3%<!-- /n --> |

k=3 and k=16 ran one trial of the 86 cases, k=8 three. Each of the three accuracies lies inside the others' Wilson
intervals, so on this set a shortlist of 3 to 16 cards makes little difference.

### Roster block size (chars / 4, mean per case)

| N | every name (baseline) | k=8 shortlist | shortlist / full |
|---|---|---|---|
| 5 | <!-- n:selector.tokens.n5.full_mean -->55<!-- /n --> | <!-- n:selector.tokens.n5.shortlist_mean -->512<!-- /n --> | <!-- n:selector.tokens.n5.ratio -->9.255<!-- /n --> |
| 50 | <!-- n:selector.tokens.n50.full_mean -->552<!-- /n --> | <!-- n:selector.tokens.n50.shortlist_mean -->838<!-- /n --> | <!-- n:selector.tokens.n50.ratio -->1.518<!-- /n --> |
| 500 | <!-- n:selector.tokens.n500.full_mean -->5,499<!-- /n --> | <!-- n:selector.tokens.n500.shortlist_mean -->769<!-- /n --> | <!-- n:selector.tokens.n500.ratio -->0.140<!-- /n --> |
| 5000 | <!-- n:selector.tokens.n5000.full_mean -->55,014<!-- /n --> | <!-- n:selector.tokens.n5000.shortlist_mean -->732<!-- /n --> | <!-- n:selector.tokens.n5000.ratio -->0.013<!-- /n --> |

Up to 50 agents the shortlist is larger than the list of names, because a card carries more text than a name. From
500 agents up the block is much smaller, but the prompt per turn is not (bar 5): the fix makes more model calls.

### Injection suite (`make injection`)

<!-- n:injection.attacks -->46<!-- /n --> attacks, <!-- n:injection.unauthorised_sends -->0<!-- /n --> sends without a consumed approval
(95% upper bound <!-- n:injection.upper95 -->6.5%<!-- /n -->); <!-- n:injection.positive_controls_sent -->4<!-- /n --> of <!-- n:injection.positive_controls -->4<!-- /n -->
positive controls (the user really confirms) sent exactly once. The fake model makes exactly the calls each attack
wants, so this proves the gate, not the model's judgement.

### Second model family (N=500, 1 trial)

| arm | model | n | accuracy | 95% CI |
|---|---|---|---|---|
| baseline | <!-- n:TODO.report.by_key.baseline_deepseek-v4-pro_k8_n500.model -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.baseline_deepseek-v4-pro_k8_n500.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.baseline_deepseek-v4-pro_k8_n500.accuracy.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.baseline_deepseek-v4-pro_k8_n500.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.baseline_deepseek-v4-pro_k8_n500.accuracy.ci95.1 -->TBD<!-- /n --> |
| fix | <!-- n:TODO.report.by_key.fix_deepseek-v4-pro_k8_n500.model -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_deepseek-v4-pro_k8_n500.n -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_deepseek-v4-pro_k8_n500.accuracy.rate -->TBD<!-- /n --> | <!-- n:TODO.report.by_key.fix_deepseek-v4-pro_k8_n500.accuracy.ci95.0 -->TBD<!-- /n --> to <!-- n:TODO.report.by_key.fix_deepseek-v4-pro_k8_n500.accuracy.ci95.1 -->TBD<!-- /n --> |

### Not measured

- The 30-day lifecycle simulation (plan L3): not built, not run.
- A run on the repo's default model (`anthropic/claude-sonnet-4`): no OpenRouter key was available; every live run
  used an OpenAI-compatible endpoint instead.

## Limitations

- The golden set is synthetic and built from templates: 130 cases from 28 templates, phrased by hand-written
  templates, with no second-model phrasing pass and no hand review yet.
- The report split is 86 cases from 19 templates, so the template-clustered intervals are wide; only baseline and
  fix at N=500 ran three trials, everything else one.
- One model family so far; the second-model table is filled in after that run.
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
