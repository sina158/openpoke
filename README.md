<!-- DRAFT-FOR-SINA: facts are checked; rewrite the prose in your own words before submitting -->

# OpenPoke fork: bounded agent routing

This fork changes how OpenPoke's interaction agent picks an execution agent, and puts an approval gate in
front of outbound email. Everything above "Upstream README" is new; the upstream README is kept unchanged below.

TODO-AI-ASSISTANCE: one line, drafted by another party and rewritten by the owner. Nothing is written here yet.

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

| file | what it does | v2 |
|---|---|---|
| `server/services/execution/registry.py` | SQLite registry: minted ids, unique slug, archive / revive, one-time import of `roster.json` | <!-- v2-slot --> |
| `server/services/execution/roster.py` | the old names-only API, now a view over the registry | <!-- v2-slot --> |
| `server/services/execution/cards.py` | pure: build a card from log entries, extract entities, render a card | <!-- v2-slot --> |
| `server/services/execution/card_store.py` | keeps cards in the registry; rebuilt on dispatch and after each run | <!-- v2-slot --> |
| `server/services/execution/selector.py` | pure: BM25, entity overlap, recency, pins, `select`, `search`, shortlist rendering | <!-- v2-slot --> |
| `server/services/execution/embeddings.py` | optional local embeddings fused with BM25 by RRF (`OPENPOKE_SELECTOR=hybrid`) | <!-- v2-slot --> |
| `server/services/execution/lifecycle.py` | archive idle agents; `on_turn_start` hook | <!-- v2-slot --> |
| `server/services/approvals.py` | pending sends: canonical hash, confirmation code, single use, 24 h expiry | <!-- v2-slot --> |
| `server/services/provenance.py` | which source started the current turn: user, agent, email or automation | <!-- v2-slot --> |
| `server/services/costs.py` | one JSON line per LLM call (tokens, cost, latency); spend cap | <!-- v2-slot --> |
| `server/agents/interaction_agent/agent.py` | roster modes `full` / `full_cards` / `shortlist`, `<pending_actions>`, escaped current turn | <!-- v2-slot --> |
| `server/agents/interaction_agent/tools.py` | `agent_id` routing, duplicate check, `clarify_agent_choice`, `search_agents`, `confirm_action` | <!-- v2-slot --> |
| `server/agents/interaction_agent/prompts/interaction_full.md` | the upstream prompt, byte for byte | <!-- v2-slot --> |
| `server/agents/interaction_agent/prompts/interaction_shortlist.md` | the same prompt with the routing and confirmation paragraphs rewritten | <!-- v2-slot --> |
| `server/agents/execution_agent/tools/gmail.py` | reply / forward / send-draft register a proposal; `execute_approved_action` sends it | <!-- v2-slot --> |
| `server/agents/execution_agent/runtime.py, batch_manager.py` | history limit, stub mode, `agent` provenance, card refresh after a run | <!-- v2-slot --> |
| `server/config.py, server/openrouter_client/client.py` | env overrides for models, endpoint, temperature, seed; usage recorded per call | <!-- v2-slot --> |
| `server/utils/paths.py, clock.py` | `OPENPOKE_DATA_DIR`; a fixed clock for evals | <!-- v2-slot --> |
| `evals/` | seeded generator, golden set, routing / selector / injection evals, cassettes, stats, results | <!-- v2-slot --> |
| `tests/` | unit tests, the injection suite, and an offline replay of the baseline cassette | <!-- v2-slot --> |
 <!-- v2-slot --> |
| TODO-V2-FILES | one row per file v2 adds or changes, filled at merge | <!-- v2-slot --> |

Design reasons and rejected options: [DESIGN.md](DESIGN.md). Where it still fails: [FAILURES.md](FAILURES.md).
Open problems, ranked: [KNOWN-GAPS.md](KNOWN-GAPS.md).

## Headline: baseline, fix v1, fix v2

<!-- SLOT-HEADLINE: filled from the live v2 runs on the fresh set at N=500 (`evals/golden/routing_v2.jsonl`), three
trials on the headline arms. Zero-event cells are bounds. Do not put round-1 numbers in this table. -->

| row (fresh set, N=500) | baseline | fix v1 | fix v2 |
|---|---|---|---|
| accuracy, 95% Wilson and clustered intervals | SLOT | SLOT | SLOT |
| pass^3 (right on all three trials) | SLOT | SLOT | SLOT |
| wrong reuse | SLOT | SLOT | SLOT |
| cross-client | SLOT | SLOT | SLOT |
| hijack | SLOT | SLOT | SLOT |
| clarify recall | SLOT | SLOT | SLOT |
| duplicates | SLOT | SLOT | SLOT |
| prompt tokens per turn | SLOT | SLOT | SLOT |
| searches per turn | SLOT | SLOT | SLOT |
| p50 latency (ms) | SLOT | SLOT | SLOT |
| list $ per 100 turns | SLOT | SLOT | SLOT |

v2's changes were designed from v1's failures on the v1 report split, so v1's set is not blind for v2; every v2
number above is on templates no one had seen when the changes were written.

<!-- SLOT-HEADLINE-LABEL-CHECK: README headline waits for the label review of the 20 sampled fresh-set cases. -->

### v2 bars (`evals/BARS-v2.md`)

| bar | result | met |
|---|---|---|
| SLOT | SLOT | SLOT |

### Evidence-snippet arm

<!-- SLOT-EVIDENCE: arm result on the fresh set, or the sentence saying it was cut. -->

### 30-day lifecycle simulation

<!-- SLOT-LIFECYCLE: result of the 30 simulated days, from `evals/results/LIFECYCLE.md` (execution-agent prompt size at day 30 with and without the history limit, reported as a measurement, not a bar). -->

### Demo transcript

<!-- SLOT-DEMO: `make demo` writes `evals/results/TRANSCRIPT.md`; link it and give the one-line command here. -->

## How it was tested and judged

1. **Bars before runs.** The pass/fail bars were written and committed before any live run (`evals/BARS.md` for
   round 1, `evals/BARS-v2.md` for v2; <!-- SLOT-BARS-SHA: commit ids -->).
2. **Baseline first.** The baseline is the upstream prompt, upstream four tools and every agent by name (the current
   turn is escaped, and email sends only propose). It ran before the fix, on the same cases, with the same settings.
3. **A blind fresh set.** <!-- SLOT-BLIND: how `routing_v2.jsonl` was written and kept unread by the builders of the v2 changes, from the evals seat's notes. -->
4. **Replay.** Every live call is recorded in a cassette keyed by a hash of the full request; the committed
   cassettes replay offline and an unrecorded call fails (`make eval-replay`, below). The baseline cassette is also
   replayed by `tests/test_baseline_replay.py`.
5. **Failures.** Real transcripts of what still goes wrong, one per class, each with its replay command:
   [FAILURES.md](FAILURES.md). Open problems, ranked: [KNOWN-GAPS.md](KNOWN-GAPS.md).
6. **What was not measured** (round 1 text, moved here from Results and Limitations):
   - The 30-day lifecycle simulation (plan L3): not built, not run in round 1 (v2 slot above).
   - A run on the repo's default model (`anthropic/claude-sonnet-4`): no OpenRouter key was available; every live run
     used an OpenAI-compatible endpoint instead.


   - The golden set is synthetic and built from templates: 130 cases from 28 templates, phrased by hand-written
     templates, with no second-model phrasing pass and no hand review yet.
   - The report split is 86 cases from 19 templates, so the template-clustered intervals are wide; only baseline and
     fix at N=500 ran three trials, everything else one.
   - The second model family (deepseek-v4-pro) ran one trial per arm, at N=500 only.
   - The baseline cannot win ambiguous or paraphrase cases by construction; both views are reported above.
   - The eval grades one routing decision per case with execution stubbed; it says nothing about what the execution
     agent then does.
   - The injection suite proves the gate, not the model.
   - The baseline arm is upstream routing plus an escaped current turn, and its email sends only propose; the
     committed baseline cassette replays offline in `tests/test_baseline_replay.py`.
   - More in [KNOWN-GAPS.md](KNOWN-GAPS.md).

## Round 1, as measured on 2026-10-28

Numbers in this section are the round-1 report, kept as reported. They are on the v1 set and are never placed in the
same table as the fresh-set numbers above. Tables not repeated here (routing by arm, paired, without the
ambiguous and paraphrase categories, clarify, consistency, cost and latency, shortlist behaviour, selector, k-sweep,
roster block size) are in [evals/results/ROUND1-TABLES.md](evals/results/ROUND1-TABLES.md); the generated tables are
in [evals/results/RESULTS.md](evals/results/RESULTS.md).


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

### Injection suite (`make injection`)

<!-- n:injection.attacks -->46<!-- /n --> attacks, <!-- n:injection.unauthorised_sends -->0<!-- /n --> sends without a consumed approval
(95% upper bound <!-- n:injection.upper95 -->6.5%<!-- /n -->); <!-- n:injection.positive_controls_sent -->4<!-- /n --> of <!-- n:injection.positive_controls -->4<!-- /n -->
positive controls (the user really confirms) sent exactly once. The fake model makes exactly the calls each attack
wants, so this proves the gate, not the model's judgement.

### Second model family (N=500, 1 trial)

| arm | model | n | accuracy | 95% CI |
|---|---|---|---|---|
| baseline | <!-- n:report.by_key.baseline_deepseek-v4-pro_k8_n500.model -->deepseek-v4-pro<!-- /n --> | <!-- n:report.by_key.baseline_deepseek-v4-pro_k8_n500.n -->86<!-- /n --> | <!-- n:report.by_key.baseline_deepseek-v4-pro_k8_n500.accuracy.rate -->59.3%<!-- /n --> | <!-- n:report.by_key.baseline_deepseek-v4-pro_k8_n500.accuracy.ci95.0 -->48.7%<!-- /n --> to <!-- n:report.by_key.baseline_deepseek-v4-pro_k8_n500.accuracy.ci95.1 -->69.1%<!-- /n --> |
| fix | <!-- n:report.by_key.fix_deepseek-v4-pro_k8_n500.model -->deepseek-v4-pro<!-- /n --> | <!-- n:report.by_key.fix_deepseek-v4-pro_k8_n500.n -->86<!-- /n --> | <!-- n:report.by_key.fix_deepseek-v4-pro_k8_n500.accuracy.rate -->79.1%<!-- /n --> | <!-- n:report.by_key.fix_deepseek-v4-pro_k8_n500.accuracy.ci95.0 -->69.3%<!-- /n --> to <!-- n:report.by_key.fix_deepseek-v4-pro_k8_n500.accuracy.ci95.1 -->86.3%<!-- /n --> |

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

## What this means for an assistant at a brokerage

TODO-BROKERAGE: another party drafts this paragraph; the owner rewrites it. Nothing is written here yet.

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
