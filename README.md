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
Each agent has a card built from its own log without an LLM. Each turn the interaction agent sees at most 8 cards
instead of the roster (up to 4 pinned agents, then the best by BM25 + entity overlap + recency, within a 1,200-token
budget). `search_agents(query)` reaches the rest; reuse passes `agent_id`; creating runs a duplicate check;
`clarify_agent_choice` asks the user. Reply, forward and send-draft become proposals sent once, only after the user
types the action's 4-character code. In round one, agents idle for 30 days left the shortlist.

Round two (profile v2, the default; `OPENPOKE_ROUTING_PROFILE=v1` gives round one back) adds four changes, each with
its own switch in `OPENPOKE_V2_DISABLE`: **c1** a check that refuses one turn's sends when the targets look like the
same client, **c2/c3** archived agents stay candidates on the shortlist with their status shown, and cards end at the
first sentence, **c4** a rewritten prompt rule (`interaction_shortlist_v2.md`). The upstream routing stays available as
`OPENPOKE_ROSTER_MODE=full` + `OPENPOKE_PROMPT_VARIANT=original`, the baseline arm.

| file | what it does | v2 |
|---|---|---|
| `server/services/execution/registry.py`, `roster.py`, `lifecycle.py` | SQLite registry: minted ids, unique slug, archive / revive; the old names-only API as a view | - |
| `server/services/execution/embeddings.py` | optional local embeddings fused with BM25 by RRF | - |
| `server/services/execution/cards.py`, `card_store.py` | pure card building from a log; stored cards rebuilt on dispatch | c2: purpose line ends at the first sentence |
| `server/services/execution/selector.py` | pure: BM25, entity overlap, recency, pins, `select`, `search` | evidence snippets (`OPENPOKE_SHORTLIST_EVIDENCE`) |
| `server/services/approvals.py`, `provenance.py`, `costs.py` | single-use pending sends with a typed code; turn source; per-call cost ledger | - |
| `server/agents/interaction_agent/agent.py` | roster modes, `<pending_actions>`, escaped current turn | c3: archived agents as candidates with status |
| `server/agents/interaction_agent/tools.py`, `runtime.py` | `agent_id` routing, duplicate check, `clarify_agent_choice`, `search_agents`, `confirm_action` | c1: `shared_client_conflict`, `check_turn_sends` |
| `server/agents/interaction_agent/prompts/` | `interaction_full.md` (upstream, byte for byte), `interaction_shortlist.md` | c4: `interaction_shortlist_v2.md` |
| `server/agents/execution_agent/` | reply / forward / send-draft register a proposal; history limit, stub mode | `OPENPOKE_EXECUTION_MEMORY=retrieved` |
| `server/config.py` | env overrides for models, endpoint, temperature, seed | `routing_profile`, `v2_change_enabled` |
| `evals/`, `tests/` | seeded generator, golden sets, evals, cassettes, stats, results | fresh set `routing_v2.jsonl`, lifecycle simulation, demo |

Design reasons: [DESIGN.md](DESIGN.md). Where it still fails: [FAILURES.md](FAILURES.md). Open problems: [KNOWN-GAPS.md](KNOWN-GAPS.md).

## Headline: baseline, fix v1, fix v2

Fresh set (`evals/golden/routing_v2.jsonl`), report split, N=500 agents, three trials, minimax-m3, temperature 0.
Every interval and the N=5 and N=50 rows are in [RESULTS_v2.md](evals/results/RESULTS_v2.md).

| fresh set, N=500, 3 trials, minimax-m3 | baseline | fix v1 | fix v2 |
|---|---|---|---|
| accuracy, Wilson 95% | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.rate -->46.4%<!-- /n --> [<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.ci95.0 -->41.1%<!-- /n -->, <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.ci95.1 -->51.8%<!-- /n -->] (<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.k -->153<!-- /n -->/<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.n -->330<!-- /n -->) | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.rate -->71.2%<!-- /n --> [<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.ci95.0 -->66.1%<!-- /n -->, <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.ci95.1 -->75.8%<!-- /n -->] (<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.k -->235<!-- /n -->/<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.n -->330<!-- /n -->) | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.rate -->83.0%<!-- /n --> [<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.ci95.0 -->78.6%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.ci95.1 -->86.7%<!-- /n -->] (<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.k -->274<!-- /n -->/<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.n -->330<!-- /n -->) |
| accuracy, template-clustered 95% | [<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.clustered.ci95.0 -->25.5%<!-- /n -->, <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.clustered.ci95.1 -->64.6%<!-- /n -->] | [<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.clustered.ci95.0 -->55.8%<!-- /n -->, <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.clustered.ci95.1 -->84.9%<!-- /n -->] | [<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.clustered.ci95.0 -->68.1%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.clustered.ci95.1 -->94.7%<!-- /n -->] |
| pass^3 (right on all three trials) | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.pass_k -->36.4%<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.pass_k -->59.1%<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.pass_k -->78.2%<!-- /n --> |
| wrong reuse | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.rate -->29.0%<!-- /n --> (<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.k -->73<!-- /n -->/<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.n -->252<!-- /n -->) | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.wrong_reuse.rate -->19.4%<!-- /n --> (<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.wrong_reuse.k -->49<!-- /n -->/<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.wrong_reuse.n -->252<!-- /n -->) | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.wrong_reuse.rate -->6.3%<!-- /n --> (<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.wrong_reuse.k -->16<!-- /n -->/<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.wrong_reuse.n -->252<!-- /n -->) |
| cross-client wrong reuse | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.cross_client.wrong_reuse -->46<!-- /n --> of <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.cross_client.wrong_reuse_n -->252<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.cross_client.wrong_reuse -->16<!-- /n --> of <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.cross_client.wrong_reuse_n -->252<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.cross_client.wrong_reuse -->11<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.cross_client.wrong_reuse_n -->252<!-- /n --> |
| sends to two clients (B2a) | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.multi_client_send.k -->47<!-- /n --> of <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.multi_client_send.n -->312<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.multi_client_send.k -->20<!-- /n --> of <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.multi_client_send.n -->312<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.k -->19<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.n -->312<!-- /n --> |
| hijack | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.hijack.count -->11<!-- /n --> of <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.hijack.n -->48<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.hijack.count -->12<!-- /n --> of <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.hijack.n -->48<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.hijack.count -->1<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.hijack.n -->48<!-- /n --> |
| clarify recall | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.clarify_recall.rate -->0.0%<!-- /n --> (<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.clarify_recall.k -->0<!-- /n -->/<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.clarify_recall.n -->30<!-- /n -->) | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.clarify_recall.rate -->0.0%<!-- /n --> (<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.clarify_recall.k -->0<!-- /n -->/<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.clarify_recall.n -->30<!-- /n -->) | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.rate -->6.7%<!-- /n --> (<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.k -->2<!-- /n -->/<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.n -->30<!-- /n -->) |
| over-clarify | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.over_clarify.rate -->0.0%<!-- /n --> (<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.over_clarify.k -->0<!-- /n -->/<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.over_clarify.n -->300<!-- /n -->) | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.over_clarify.rate -->0.0%<!-- /n --> (<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.over_clarify.k -->0<!-- /n -->/<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.over_clarify.n -->300<!-- /n -->) | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.over_clarify.rate -->1.7%<!-- /n --> (<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.over_clarify.k -->5<!-- /n -->/<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.over_clarify.n -->300<!-- /n -->) |
| duplicates | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.duplicate.rate -->25.0%<!-- /n --> (<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.duplicate.k -->63<!-- /n -->/<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.duplicate.n -->252<!-- /n -->) | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.duplicate.rate -->0.0%<!-- /n --> (<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.duplicate.k -->0<!-- /n -->/<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.duplicate.n -->252<!-- /n -->) | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.duplicate.rate -->0.0%<!-- /n --> (<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.duplicate.k -->0<!-- /n -->/<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.duplicate.n -->252<!-- /n -->) |
| prompt tokens per turn | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.prompt_tokens.mean -->9,901<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.prompt_tokens.mean -->9,944<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.prompt_tokens.mean -->10,198<!-- /n --> |
| search_agents calls per turn | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.searches.mean -->0.00<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.searches.mean -->1.21<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.searches.mean -->1.08<!-- /n --> |
| model calls per turn | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.llm_calls.mean -->1.10<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.llm_calls.mean -->1.86<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.llm_calls.mean -->1.88<!-- /n --> |
| p50 latency, ms | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.latency_ms.p50 -->8,509<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.latency_ms.p50 -->12,225<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.latency_ms.p50 -->12,065<!-- /n --> |
| list $ per 100 turns | $<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.usd_per_100_turns -->0.2636<!-- /n --> | $<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.usd_per_100_turns -->0.2166<!-- /n --> | $<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.usd_per_100_turns -->0.2243<!-- /n --> |

Target mix: <!-- n:v2:set_mix.idle_target -->58<!-- /n --> of <!-- n:v2:set_mix.non_create -->94<!-- /n --> non-create fresh cases have a gold target idle more than 30 days, against
<!-- n:continuity:set_mix.idle_target -->35<!-- /n --> of <!-- n:continuity:set_mix.non_create -->73<!-- /n --> in round one's report split. Accuracy by target status:

| target | baseline | fix v1 | fix v2 |
|---|---|---|---|
| idle more than 30 days | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.target_status.idle.rate -->27.6%<!-- /n --> (<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.target_status.idle.k -->48<!-- /n -->/<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.target_status.idle.n -->174<!-- /n -->) | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.target_status.idle.rate -->64.4%<!-- /n --> (<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.target_status.idle.k -->112<!-- /n -->/<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.target_status.idle.n -->174<!-- /n -->) | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.target_status.idle.rate -->73.0%<!-- /n --> (<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.target_status.idle.k -->127<!-- /n -->/<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.target_status.idle.n -->174<!-- /n -->) |
| active | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.target_status.active.rate -->63.0%<!-- /n --> (<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.target_status.active.k -->68<!-- /n -->/<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.target_status.active.n -->108<!-- /n -->) | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.target_status.active.rate -->80.6%<!-- /n --> (<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.target_status.active.k -->87<!-- /n -->/<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.target_status.active.n -->108<!-- /n -->) | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.target_status.active.rate -->92.6%<!-- /n --> (<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.target_status.active.k -->100<!-- /n -->/<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.target_status.active.n -->108<!-- /n -->) |

v2's changes were designed from v1's failures on the v1 report split, so v1's set is not blind for v2; every v2
number above is on templates no one had seen when the changes were written.

<!-- SLOT-HEADLINE-LABEL-CHECK: README headline waits for the label review of the 20 sampled fresh-set cases. -->

Tokens: a model call under fix v2 carries <!-- n:v2:bars.prompt_token_per_call_ratio_n500 -->58.5%<!-- /n --> of the baseline's prompt, but a turn makes
<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.llm_calls.mean -->1.88<!-- /n --> calls against <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.llm_calls.mean -->1.10<!-- /n --> (the shortlist needs a search round), so a turn sends <!-- n:v2:bars.prompt_token_ratio_n500 -->103.0%<!-- /n --> of the
baseline's. N=5,000 is where the baseline's roster should overload; that row is not run yet (<!-- SLOT-N5000 -->).

### v2 bars (`evals/BARS-v2.md`)

Written before any v2 run. Four bars fail, and are reported as they came out.

| bar | verdict | measured (fix v2 at N=500 unless stated) |
|---|---|---|
| B1 v2 beats v1, paired | pass | +<!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.delta_accuracy -->10.0%<!-- /n --> over <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.pairs -->110<!-- /n --> cases, McNemar p = <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.mcnemar_p -->0.0034<!-- /n --> |
| B2a no send reaches two clients | **fail** | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.k -->19<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.n -->312<!-- /n --> turns; parallel-gold turns that did so <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.parallel_gold.k -->13<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.parallel_gold.n -->18<!-- /n --> |
| B2b cross-client wrong reuse <= 2% | **fail** | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.cross_client.wrong_reuse -->11<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.cross_client.wrong_reuse_n -->252<!-- /n --> (fix v1: <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.cross_client.wrong_reuse -->16<!-- /n -->) |
| B3 hijack <= half of v1's | pass | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.hijack.count -->1<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.hijack.n -->48<!-- /n --> (fix v1: <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.hijack.count -->12<!-- /n -->) |
| B4 clarify recall >= 50%, over-clarify <= 5% | **fail** | recall <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.rate -->6.7%<!-- /n --> [<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.ci95.0 -->1.8%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.ci95.1 -->21.3%<!-- /n -->] (<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.k -->2<!-- /n -->/<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.n -->30<!-- /n -->); over-clarify <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.over_clarify.rate -->1.7%<!-- /n --> [<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.over_clarify.ci95.0 -->0.7%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.over_clarify.ci95.1 -->3.8%<!-- /n -->] (<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.over_clarify.k -->5<!-- /n -->/<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.over_clarify.n -->300<!-- /n -->) |
| B5 tokens per turn <= 75% of baseline; <= 0.5 searches; p50 not above baseline | **fail** | <!-- n:v2:bars.prompt_token_ratio_n500 -->103.0%<!-- /n --> of baseline per turn (<!-- n:v2:bars.prompt_token_per_call_ratio_n500 -->58.5%<!-- /n --> per call); <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.searches.mean -->1.08<!-- /n --> searches; p50 <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.latency_ms.p50 -->12,065<!-- /n --> ms vs <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.latency_ms.p50 -->8,509<!-- /n -->; N=5,000 row pending |
| B6 no small-N regression (N=5, 1 trial) | pass | fix v2 <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5.accuracy.rate -->87.3%<!-- /n --> (<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5.accuracy.k -->96<!-- /n -->/<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5.accuracy.n -->110<!-- /n -->), baseline <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n5.accuracy.rate -->34.5%<!-- /n --> (<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n5.accuracy.k -->38<!-- /n -->/<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n5.accuracy.n -->110<!-- /n -->), fix v1 <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n5.accuracy.rate -->74.5%<!-- /n --> (<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n5.accuracy.k -->82<!-- /n -->/<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n5.accuracy.n -->110<!-- /n -->) |
| B7 duplicates not above v1 | pass | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.duplicate.k -->0<!-- /n --> and <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.duplicate.k -->0<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.duplicate.n -->252<!-- /n --> |
| B8 injection suite unchanged | pass | <!-- n:v2:injection.unauthorised_sends -->0<!-- /n --> unauthorised sends in <!-- n:v2:injection.attacks -->46<!-- /n --> attacks |
| B9 second model, ablation | pending | the second-model runs and the one-change-off ablation are not run yet |

### Evidence snippets

Arm `fix_v2_evidence` (`OPENPOKE_SHORTLIST_EVIDENCE=1`, one trial, <!-- n:v2:report.by_key.fix_v2_evidence_minimax-m3_k8_n500.n -->110<!-- /n --> case-trials): paired with fix v2 it moves accuracy by
<!-- n:v2:report.paired_by_key.fix_v2_evidence_vs_fix_v2_minimax-m3_k8_n500.delta_accuracy -->0.0%<!-- /n --> (McNemar p = <!-- n:v2:report.paired_by_key.fix_v2_evidence_vs_fix_v2_minimax-m3_k8_n500.mcnemar_p -->1.0000<!-- /n -->) and costs
<!-- n:v2:report.by_key.fix_v2_evidence_minimax-m3_k8_n500.prompt_tokens.mean -->11,456<!-- /n --> prompt tokens per turn against <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.prompt_tokens.mean -->10,198<!-- /n -->, above the +10% allowance: **A1 fails**, so the flag stays off.
A2 (the cited entry names the discriminator), as registered: <!-- n:v2:report.by_key.fix_v2_evidence_minimax-m3_k8_n500.evidence.registered.rate -->52.9%<!-- /n --> (<!-- n:v2:report.by_key.fix_v2_evidence_minimax-m3_k8_n500.evidence.registered.k -->18<!-- /n -->/<!-- n:v2:report.by_key.fix_v2_evidence_minimax-m3_k8_n500.evidence.registered.n -->34<!-- /n -->), pass;
corrected to correct near-duplicate picks only: <!-- n:v2:report.by_key.fix_v2_evidence_minimax-m3_k8_n500.evidence.corrected.rate -->100.0%<!-- /n --> (<!-- n:v2:report.by_key.fix_v2_evidence_minimax-m3_k8_n500.evidence.corrected.k -->18<!-- /n -->/<!-- n:v2:report.by_key.fix_v2_evidence_minimax-m3_k8_n500.evidence.corrected.n -->18<!-- /n -->), pass. Read this with the limits below: the eval's agent logs hold
two entries, both already on the card, so here the arm tests duplication, not retrieval.

### 30-day lifecycle simulation

<!-- SLOT-LIFECYCLE: result of the 30 simulated days, from `evals/results/LIFECYCLE.md` (execution-agent prompt size at day 30 with and without the history limit, reported as a measurement, not a bar). -->

### Demo transcript

<!-- SLOT-DEMO: `make demo` writes `evals/results/TRANSCRIPT.md`; link it and give the one-line command here. -->

## How it was tested and judged

1. **Bars before runs.** Written and committed before any run: `evals/BARS.md` (round one) and `evals/BARS-v2.md`
   (round two). A failed bar is a result, not a reason to change the test.
2. **Baseline first.** The baseline is the upstream prompt, upstream four tools and every agent by name (the current
   turn is escaped, and email sends only propose). It ran on the same cases, with the same settings, as the fixes.
3. **A blind fresh set.** `routing_v2.jsonl` was written by a seat that did not see the failures, the results or the
   v2 code, and the builders of the v2 changes did not read it. <!-- SLOT-BLIND: sha of the set and the label-check outcome. -->
4. **Replay.** Every live call is stored in a cassette keyed by a hash of the full request; the committed cassettes
   replay offline and an unrecorded call fails (`make eval-replay`).
5. **Failures.** Real transcripts, one per class, each with its replay command and what fix v2 did on the same case (`evals/results/continuity/`): [FAILURES.md](FAILURES.md).
6. **What was not measured, and what the numbers do not say:**
   - Not run: second-model runs for v2, the one-change-off ablation, N=5,000, the default model
     (`anthropic/claude-sonnet-4`, no key). Cut for quota: second-model trials 2 and 3 (one trial), the `cards_full`
     N=500 demo, a model-phrased set.
   - The routing eval stubs execution: it grades one routing decision per case, not what the agent then does. The
     injection suite proves the gate, not the model. The sets are synthetic and built from templates.
   - **C2 abbreviation clip** (`server/services/execution/cards.py:68` `_SENTENCE_END`, `:181` `_first_sentence`):
     "Send Ms. Halloran the renewal forms..." becomes "Send Ms.". Found by the blind review after the measurement
     was frozen; not fixed because it changes card bytes the recorded runs replay against. Fix: skip a match that
     ends in Mr., Ms., Mrs., Dr., St., Jr. or Sr.
   - **The shared-client check refuses only targets that look like the same client** (a shared multi-word name,
     email or id). Two different clients' agents in one turn are not refused (B2a above), and the check is inert
     below 100 agents (a fixed 2% document-frequency cut-off), so its zeros mean something at N=500 only.
   - **Every `should_create` fresh message says "new"**, so hijack (B3) is measured only where the user states it.
   - **Evidence arm:** the agent logs have two entries, both already on the card, so it tests duplication, not
     retrieval; evidence is added outside the shortlist token budget; the v2 prompt explains neither
     `status="archived"` nor `evidence=`.
   - **Bounded execution memory** (`OPENPOKE_EXECUTION_MEMORY=retrieved`) is covered by a unit test only
     (`tests/test_execution_memory.py`: a 200-entry log goes from 5,190 to 246 history tokens); the routing eval
     stubs execution and cannot score it.
   - Round one's own limits are in [evals/results/ROUND1.md](evals/results/ROUND1.md). More in [KNOWN-GAPS.md](KNOWN-GAPS.md).

## Round 1, as measured on 2026-10-28

Round one (shortlist router, v1 set, minimax-m3) is kept as reported in [evals/results/ROUND1.md](evals/results/ROUND1.md)
(summary, the six pre-registered bars, the injection suite, the second-model table) and
[evals/results/ROUND1-TABLES.md](evals/results/ROUND1-TABLES.md); those numbers are checked against `RESULTS.json`
and never share a table with the fresh-set numbers above. In short: at N=500 the fix routed
<!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy.rate -->73.3%<!-- /n --> of turns correctly against
<!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.rate -->48.4%<!-- /n --> for the baseline, and two of the six round-one bars failed:
cross-client wrong reuse was not zero, and prompt tokens per turn were
<!-- n:bars.prompt_token_ratio_n500 -->102.5%<!-- /n --> of the baseline's.

## How to run

Python 3.11. No API key is needed for anything except `make eval-live`.

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
make test           # offline: unit tests + a replay of the baseline cassette
make injection      # offline: prompt-injection cases against the send gate -> evals/results/INJECTION.json
make compare        # committed result files + INJECTION.json -> evals/results/RESULTS.md and RESULTS.json
make check-numbers  # every marked number in README.md, DESIGN.md and ROUND1.md against its results file

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

Settings (env):

| variable | default | what it does |
|---|---|---|
| `OPENPOKE_ROUTING_PROFILE` | `v2` | `v1` restores round one's behaviour |
| `OPENPOKE_V2_DISABLE` | empty | comma list of `c1`, `c3`, `c4` to switch single v2 changes off |
| `OPENPOKE_SHORTLIST_EVIDENCE` | `0` | `1` adds an evidence snippet to shortlisted cards (profile v2 only) |
| `OPENPOKE_EXECUTION_MEMORY` | `recent` | `retrieved` gives an execution agent recent plus relevant log entries |
| `OPENPOKE_ROSTER_MODE`, `OPENPOKE_PROMPT_VARIANT` | `shortlist` | `full`, `full_cards`, `original` give the baseline arms |
| `OPENPOKE_SHORTLIST_K`, `_TOKEN_BUDGET` | 8, 1200 | shortlist size and card budget |
| `OPENPOKE_SELECTOR` | `bm25` | `hybrid` needs `pip install -r requirements-embeddings.txt` |
| `OPENPOKE_DUPLICATE_THRESHOLD`, `OPENPOKE_ARCHIVE_IDLE_DAYS` | 0.85, 30 | duplicate name score, idle days before archiving |
| `OPENPOKE_EXECUTION_HISTORY_LIMIT`, `OPENPOKE_DATA_DIR`, `OPENPOKE_LLM_BASE_URL`, `OPENPOKE_*_MODEL` | unset | history cap, data dir, endpoint, per-role models |


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
