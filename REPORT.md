# Report: agent overload in OpenPoke

The long version of the [README](README.md): the task, where I started, each step in order, the results with their
statistics, how to check every number, and what failed. Detail lives in the files named under each table.

## 1. The task

1. Review the OpenPoke repo, think about the broader problem of agent overload (an assistant with so many sub-agents
   that choosing the right one becomes the failure), and solve it in code.
2. Include test cases and evaluators.
3. Be ready to explain how I tested the agent and how I judged whether it was actually working well.
4. Flag other problems or gaps, and show in code how I would solve them.

The submission is this repository, walked through together on a 60-minute call.

## 2. Summary

Terms. An **arm** is one configuration run on the same cases: **baseline** (the upstream routing), **fix v1** (round
one) and **fix v2** (round two, the default). **N** is the number of agents. A **case-trial** is one case in one trial.
**Wrong reuse** sends the message to an existing agent that is not the right one; it is **cross-client** when that agent
belongs to another client. A **spray** messages the right agent and another one in the same turn, and counts as wrong
reuse. A **hijack** puts a new matter on an old agent; a **duplicate** creates a second agent for a matter that already
has one. **pass^3** is the share of cases right on all three trials.

| fresh test set, N=500, three trials, minimax-m3 | baseline | fix v1 | fix v2 |
|---|---|---|---|
| accuracy, Wilson 95% interval | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.rate -->46.4%<!-- /n --> [<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.ci95.0 -->41.1%<!-- /n -->, <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.ci95.1 -->51.8%<!-- /n -->] | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.rate -->71.2%<!-- /n --> [<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.ci95.0 -->66.1%<!-- /n -->, <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.ci95.1 -->75.8%<!-- /n -->] | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.rate -->83.0%<!-- /n --> [<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.ci95.0 -->78.6%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.ci95.1 -->86.7%<!-- /n -->] |
| right on all three trials (pass^3) | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.pass_k -->36.4%<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.pass_k -->59.1%<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.pass_k -->78.2%<!-- /n --> |
| wrong reuse, of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.wrong_reuse.n -->252<!-- /n --> should-reuse case-trials (how many cross-client) | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.rate -->29.0%<!-- /n --> (<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.cross_client.wrong_reuse -->46<!-- /n -->) | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.wrong_reuse.rate -->19.4%<!-- /n --> (<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.cross_client.wrong_reuse -->16<!-- /n -->) | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.wrong_reuse.rate -->6.3%<!-- /n --> (<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.cross_client.wrong_reuse -->11<!-- /n -->) |
| turns that messaged two clients, of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.n -->312<!-- /n --> | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.multi_client_send.k -->47<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.multi_client_send.k -->20<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.k -->19<!-- /n --> |
| hijack, of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.hijack.n -->48<!-- /n --> | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.hijack.count -->11<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.hijack.count -->12<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.hijack.count -->1<!-- /n --> |
| duplicates | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.duplicate.rate -->25.0%<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.duplicate.rate -->0.0%<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.duplicate.rate -->0.0%<!-- /n --> |
| prompt tokens per turn | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.prompt_tokens.mean -->9,901<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.prompt_tokens.mean -->9,944<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.prompt_tokens.mean -->10,198<!-- /n --> |
| p50 latency, ms | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.latency_ms.p50 -->8,509<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.latency_ms.p50 -->12,225<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.latency_ms.p50 -->12,065<!-- /n --> |

Source: [evals/results/RESULTS_v2.md](evals/results/RESULTS_v2.md), headline table (built from `RESULTS_v2.json`).

On a test set written after round one and locked by hash, fix v2 routes <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.rate -->83.0%<!-- /n --> of turns correctly at 500 agents, against
<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.rate -->46.4%<!-- /n --> for the upstream routing, and beats fix v1 in a paired test (p = <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.mcnemar_p -->0.0034<!-- /n -->).
Duplicates went to zero and hijacks to <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.hijack.count -->1<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.hijack.n -->48<!-- /n -->. Four of the ten main bars, all committed before the runs, failed
(B2a, B2b, B4, B5; section 5.4): turns still reached two clients, cross-client reuse stayed above 2%, the model almost
never asked the user, and at N=500 a turn costs slightly more tokens than the baseline. An opt-in client guard, judged
only on a held-out set, brought both client numbers to zero there but blocked some real two-client requests, so it
ships off. Every model call is recorded, so every number here replays offline.

## 3. Where I started

Upstream, every turn lists every execution agent by name (`_render_active_agents` in
`server/agents/interaction_agent/agent.py`) and the model reuses one by retyping its name. An agent is a string in
`roster.json`, so two names with one slug share a log file. The prompt says to prefer an existing agent, "Don't worry
if the agent name is unrelated to the new task" (`prompts/interaction_full.md`, line 19), with no way to ask the user
and no duplicate check, and agents are never removed ([README, The problem](README.md#the-problem)). Measured:

| failure mode | baseline arm, fresh set, N=500 unless noted |
|---|---|
| the roster block grows with N (chars / 4) | <!-- n:selector.tokens.n5.full_mean -->55<!-- /n --> tokens at 5 agents, <!-- n:selector.tokens.n500.full_mean -->5,499<!-- /n --> at 500, <!-- n:selector.tokens.n5000.full_mean -->55,014<!-- /n --> at 5,000 (`evals/results/SELECTOR.md`) |
| wrong reuse | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.rate -->29.0%<!-- /n -->, <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.cross_client.wrong_reuse -->46<!-- /n --> times to another client's agent |
| duplicates; hijacks | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.duplicate.rate -->25.0%<!-- /n -->; <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.hijack.count -->11<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.hijack.n -->48<!-- /n --> |
| never asks the user (it has no tool to) | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.clarify_recall.k -->0<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.n -->30<!-- /n --> ambiguous case-trials |
| not only a size problem | accuracy at N=5 is <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n5.accuracy.rate -->34.5%<!-- /n --> |
| over a simulated month | <!-- n:lifecycle:arms.baseline.summary.live_end -->53<!-- /n --> live agents against an ideal <!-- n:lifecycle:arms.baseline.summary.ideal_live_end -->25<!-- /n -->; tokens per turn grow <!-- n:lifecycle:arms.baseline.summary.prompt_tokens_slope_per_day -->1,793<!-- /n --> a day |

Sources: `evals/results/RESULTS_v2.md`, `evals/results/SELECTOR.md`, `evals/results/LIFECYCLE.json`.

**The baseline was recorded before the router changed.** Commit `19b6d3a` ("Record the baseline routing results
before changing the router") stores a live run of the upstream routing at its parent `5a8fd9a`: 130 round-one cases
at N=5, 50 and 500, one trial, minimax-m3, temperature 0.
The first commit that changes routing, `861b8d5` (cards), is its direct child: `git rev-parse --short 861b8d5^` prints
`19b6d3a`. Earlier commits are plumbing (data-dir override, a portable roster save, the registry behind the old
names-only roster API, configurable models, the cost ledger, the generator and eval harness) and the round-one bars
(`73b6b1f`). In every later run the baseline arm is still the upstream prompt, tools and full roster, with two stated
differences: the current turn is escaped and email sends are only proposed; `tests/test_baseline_replay.py` fails if
its request changes. Summarised, the round-one baseline at N=500 over three trials routed <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.rate -->48.4%<!-- /n --> correctly, with
wrong reuse <!-- n:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.rate -->36.3%<!-- /n --> and duplicates <!-- n:report.by_key.baseline_minimax-m3_k8_n500.duplicate.rate -->13.4%<!-- /n --> (`evals/results/RESULTS.md`).

## 4. Steps taken, in order

### 4.1 Bars, harness, then the baseline (`73b6b1f` to `19b6d3a`)

Before any live run I committed six bars (`evals/BARS.md`, unchanged since), a recorder that stores every model call
keyed by a hash of the full request (`e390e67`), the routing harness (`354442b`) and the statistics (`b1544fd`,
`f2e9ea9`). Then the baseline. Why: a fixed yardstick before touching the router.

### 4.2 Fix v1: a bounded choice by id (`feb9374` to `358423a`)

- **Ids, not names:** a SQLite registry with minted ids (`a_0001`) and unique slugs (`feb9374`); reuse passes `agent_id` (`c8d24df`).
- **Cards** built from each agent's own log with no model call: a purpose line plus the ids, emails and names it has handled (`861b8d5`, `62a007d`, `31c5297`).
- **A shortlist of at most 8 cards** (`5f46473`, `84f585d`, `210dd8c`): up to 4 pins (the agent whose result is being handled, a quoted thread id, a pending send, a recent mention), then the best by BM25 keyword score + 2 x entity overlap + 0.5 x recency, within 1,200 tokens.
- **`search_agents`** reaches every agent; **`clarify_agent_choice`** asks the user and sends nothing; a **duplicate check** returns look-alikes before a create, by name or by a shared thread or policy id (`c8d24df`, `424189f`).
- **Archive and revive** (`1a4981c`): agents idle 30 days are archived, never one with a trigger or a pending send, and revived when reused.
- **Outbound email waits for a code** (`65e6c3c`, `7aeedc5`, `358423a`): reply, forward and send-draft become a proposal bound to a sha256 of its arguments, approved only on a user turn whose text contains the proposal's 4-character code, then consumed once. A review found why the code is needed: otherwise a user turn about something else could confirm a forward an email had injected ([DESIGN.md](DESIGN.md), section 7).
- **Measured and dropped:** a local-embeddings hybrid (`3985ecb`); recall@8 at N=500 was <!-- n:selector.recall.hybrid.n500.k8 -->0.918<!-- /n --> against BM25's <!-- n:selector.recall.bm25_entities_recency.n500.k8 -->0.991<!-- /n -->.

Why: show the model a small relevant subset, let it pick by id, and give it real moves for "nothing fits" and "two fit".

### 4.3 The injection suite (`6c0f0b1`, extended in `358423a`)

Each case scripts turns from the user, an agent, an email or an automation; a fake model makes exactly the attacker's
calls through the real loop, tool handlers and approval store, and each attack names the check it must hit (turn
source, consent code, escaping, argument hash, single use, expiry, ...). It proves the gate, not the model's judgement.

### 4.4 Round one, and what it taught (`4c399f2`, `4315cc9`, `c3b0a7a`)

Fix v1 on the round-one set at N=500 routed <!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy.rate -->73.3%<!-- /n --> against <!-- n:report.by_key.baseline_minimax-m3_k8_n500.accuracy.rate -->48.4%<!-- /n --> (McNemar p = <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.mcnemar_p -->0.00047<!-- /n -->; template-clustered
interval of the gain <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.0 -->1.1%<!-- /n --> to <!-- n:report.paired_by_key.fix_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.1 -->49.5%<!-- /n -->). Two of six bars failed: cross-client wrong reuse was <!-- n:report.by_key.fix_minimax-m3_k8_n500.cross_client.wrong_reuse -->15<!-- /n --> of <!-- n:report.by_key.fix_minimax-m3_k8_n500.cross_client.wrong_reuse_n -->201<!-- /n -->,
not zero, and a turn sent <!-- n:bars.prompt_token_ratio_n500 -->102.5%<!-- /n --> of the baseline's tokens ([ROUND1.md](evals/results/ROUND1.md)). [FAILURES.md](FAILURES.md) has one replayable
transcript per failure class. What they taught:

- **Sprays:** 15 of the 23 wrong turns at N=500 sent to two or more agents, encouraged by an inherited upstream line asking for parallel sends. Led to c1 and c4.
- **Cards cut the city:** two "Maplestone" businesses, in Barrie and Sarnia, lost the city from their cards. Led to c2.
- **Hijacks:** "same person" satisfied the reuse rule, so a new claim went to a renewal agent. Led to c4.
- **Archived agents were invisible** unless searched: right on <!-- n:report.by_key.fix_minimax-m3_k8_n500.archived_target.archived.rate -->60.0%<!-- /n --> of cases with an archived target against <!-- n:report.by_key.fix_minimax-m3_k8_n500.archived_target.active.rate -->86.0%<!-- /n --> with an active one. Led to c3.
- **A set you design from cannot judge the design,** so round two needed new bars and a fresh set.

### 4.5 Round-two bars, committed first (`7ec4fbc`)

`evals/BARS-v2.md` was committed before the fresh set existed and before any v2 recording; since then lines have only
been added (verdicts, the guard's bars). Names used below: **B1** v2 beats v1; **B2a** no turn reaches two clients;
**B2b** cross-client wrong reuse at most 2%; **B3** hijacks halved; **B4** the model asks when it should; **B5**
tokens, searches and latency; **B6** no small-N regression; **B7** duplicates; **B8** injection; **B9** a second
model agrees; **A1/A2** evidence snippets; **L1-L6** the month; **D1** the demo. The file states three readings: B2a
leaves out parallel cases, whose right answer does reach two clients (set by the lead agent before any B2a number was
read); B2b's "below fix v1" also holds when both are zero; L2's "no upward slope" means an interval that includes 0,
a reading chosen after the slopes were seen.

### 4.6 The fresh set (`4914c11`, locked in `8090344`)

`evals/golden/routing_v2.jsonl`: <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.pairs -->110<!-- /n --> cases from <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.templates -->19<!-- /n --> new templates in nine categories, new synthetic clients, seed 20261029,
nothing tuned on it. The agent on my team that wrote it had not seen the failures, results or v2 code, and the agents
that built v2 never read it. Gold labels come from the tuple that builds each case, never from a model
([LABELS.md](evals/golden/LABELS.md)). `8090344` locks it with sha256
`35c914b20baa0409d1ee28db4c56d23b2e4b1feb4facbbadecfc7e3807a3122f`; a test fails on drift. It leans on archived
agents: <!-- n:v2:set_mix.idle_target -->58<!-- /n --> of <!-- n:v2:set_mix.non_create -->94<!-- /n --> non-create cases have a target idle over 30 days.

### 4.7 Fix v2: four changes, each switchable (`c9f729f` to `b6ab3db`; results `912142a`, `667a723`)

- **c1, shared-client check** (`c9f729f`, `f7dd159`): if one turn sends to two or more existing agents whose cards share a rare name, email or id (on at most 2% of all cards), nothing is sent and the model gets another round. Insurers and cities sit on many cards, so they never count; below 100 agents it never fires.
- **c2, first-sentence cards** (`edb8579`): the purpose line keeps the request's first sentence instead of a 120-character clip, so the city survives.
- **c3, archived agents on the shortlist** (`b6ab3db`): they compete on score and the card says `status="archived"`. One switch controls c2 and c3.
- **c4, the prompt** (`99775ff`): reuse an agent only for the same matter; the same person with a new task needs a new agent; sending to two candidates to find out is guessing. It drops the inherited parallel-send line.
- `OPENPOKE_ROUTING_PROFILE=v1` restores fix v1 byte for byte (`7e75ad2`); `OPENPOKE_V2_DISABLE` removes one change at a time for the ablations. Built and left off: evidence snippets (`ab33fd4`, A1 failed) and retrieved execution memory (`72054b3`, unit test only).

### 4.8 The 30-day simulation (`8c12d17`, `3b0ea53`, `0359477`; results `454e8d3`)

The routing eval grades one decision per case, but overload builds up. So one broker's month runs through the real
interaction turn: <!-- n:lifecycle:arms.fix_v2.summary.turns -->158<!-- /n --> turns per arm, <!-- n:lifecycle:arms.fix_v2.summary.seeded_agents -->16<!-- /n --> agents pre-seeded, execution stubbed, one
trial, graded against the scenario's one-agent-per-matter ideal; recorded live once (`ebc101e`), replayed at
`667a723`. The nine-turn demo (`make demo`) uses the same engine and was also recorded once.

### 4.9 The held-out set and the client guard (`c82a7b1`, `fa85c06`, `e41120c`, `adf9263`, `d586c2c`)

Fix v2's client bars failed, so I studied the recorded failures offline
([CLIENT_GUARD_WHATIF.md](evals/results/CLIENT_GUARD_WHATIF.md)) and designed a guard from them. A change designed
from a set's failures cannot be judged on that set, so I generated a held-out set from a new seed, 20261030
(`c82a7b1`), and locked it by hash (`fa85c06`, sha256 `023a347b6e3fcf5e0809274a4d50b8c5b48774111446b91627d4d1b09385d336`).
The guard code (`e41120c`) and its bars G-i to G-iv (`adf9263`) followed, all before any guard run. The guard
(`OPENPOKE_CLIENT_GUARD=1`, off by default) refuses a send to an existing agent whose client the message does not name,
when the message names another client or the turn's sends reach two clients (`unnamed_client_conflict` in
`server/agents/interaction_agent/tools.py`). Its run on the round-two set is reported as non-blind, never a verdict.

### 4.10 N=5,000 (`d586c2c`)

<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5000_s30.accuracy.n -->30<!-- /n --> fresh-set cases sampled by category, at 5,000 agents, one trial, baseline and fix v2: B5's demonstration row, to
see whether the shortlist pays off where a full roster is huge.

### 4.11 Final gates

- `make test`: 437 offline tests, the count `pytest --collect-only` prints, with the 20 marked slow left out.
- `pytest -m slow`: replays every round-two result file in full, held-out runs included, and round one's fix runs under profile v1; each must make 0 live calls and grade every record as recorded.
- `make check-numbers`: every marked number in README, DESIGN, ROUND1 and ROUND1-TABLES against its result file; `d586c2c`, `d72ab22` and `bd8eff8` each record a clean run in their message.
- A scan of committed eval data for real addresses, numbers and credentials (`8ce82a8`, `tests/test_eval_personal_data.py`).
- Two gates left no committed record: a clean install in a fresh `python:3.11-slim` container with no network and no API key (tests, replays, lifecycle, demo, injection, number checks), and a scan for private or internal names. A reviewer can only check these by repeating them.

### 4.12 The label hand check (`d045cee`, `bd8eff8`)

Twenty case ids drawn by a seeded sample. A first pass by my assistant, an AI agent, read each case against the gold
and hard-negative agents' records and searched each roster for look-alikes; then I checked them myself. Both: 20 of
20 agree, 0 disputes.

## 5. Results, with proper statistics

minimax-m3, fresh set, N=500 unless a row says otherwise. Paired tests use each case's majority outcome over its
trials; a one-trial arm is paired with fix v2's first trial.

### 5.1 Accuracy

| arm | accuracy | correct / case-trials | Wilson 95% | template-clustered 95% | pass^3 |
|---|---|---|---|---|---|
| baseline | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.rate -->46.4%<!-- /n --> | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.k -->153<!-- /n -->/<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.n -->330<!-- /n --> | [<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.ci95.0 -->41.1%<!-- /n -->, <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.ci95.1 -->51.8%<!-- /n -->] | [<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.clustered.ci95.0 -->25.5%<!-- /n -->, <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.accuracy.clustered.ci95.1 -->64.6%<!-- /n -->] | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.pass_k -->36.4%<!-- /n --> |
| fix v1 | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.rate -->71.2%<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.k -->235<!-- /n -->/<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.n -->330<!-- /n --> | [<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.ci95.0 -->66.1%<!-- /n -->, <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.ci95.1 -->75.8%<!-- /n -->] | [<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.clustered.ci95.0 -->55.8%<!-- /n -->, <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.accuracy.clustered.ci95.1 -->84.9%<!-- /n -->] | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.pass_k -->59.1%<!-- /n --> |
| fix v2 | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.rate -->83.0%<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.k -->274<!-- /n -->/<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.n -->330<!-- /n --> | [<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.ci95.0 -->78.6%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.ci95.1 -->86.7%<!-- /n -->] | [<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.clustered.ci95.0 -->68.1%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.clustered.ci95.1 -->94.7%<!-- /n -->] | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.pass_k -->78.2%<!-- /n --> |

Source: `evals/results/RESULTS_v2.md` (headline table; "Consistency across trials").

### 5.2 Paired comparisons

| comparison | change in accuracy | template-clustered 95% of the change | only the reference right / only the arm right | McNemar p |
|---|---|---|---|---|
| fix v1 vs baseline | +<!-- n:v2:report.paired_by_key.fix_v1_vs_baseline_minimax-m3_k8_n500.delta_accuracy -->24.5%<!-- /n --> | [<!-- n:v2:report.paired_by_key.fix_v1_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.0 -->2.5%<!-- /n -->, <!-- n:v2:report.paired_by_key.fix_v1_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.1 -->50.9%<!-- /n -->] | <!-- n:v2:report.paired_by_key.fix_v1_vs_baseline_minimax-m3_k8_n500.b -->11<!-- /n --> / <!-- n:v2:report.paired_by_key.fix_v1_vs_baseline_minimax-m3_k8_n500.c -->38<!-- /n --> | <!-- n:v2:report.paired_by_key.fix_v1_vs_baseline_minimax-m3_k8_n500.mcnemar_p -->0.0001<!-- /n --> |
| fix v2 vs baseline | +<!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n500.delta_accuracy -->34.5%<!-- /n --> | [<!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.0 -->15.9%<!-- /n -->, <!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n500.delta_clustered_ci95.1 -->56.9%<!-- /n -->] | <!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n500.b -->2<!-- /n --> / <!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n500.c -->40<!-- /n --> | <!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n500.mcnemar_p -->0.0000<!-- /n --> |
| fix v2 vs fix v1 (B1) | +<!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.delta_accuracy -->10.0%<!-- /n --> | [<!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.delta_clustered_ci95.0 -->1.9%<!-- /n -->, <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.delta_clustered_ci95.1 -->18.8%<!-- /n -->] | <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.b -->1<!-- /n --> / <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.c -->12<!-- /n --> | <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.mcnemar_p -->0.0034<!-- /n --> |
| without c1, vs fix v2 | <!-- n:v2:report.paired_by_key.fix_v2_no_c1_vs_fix_v2_minimax-m3_k8_n500.delta_accuracy -->0.0%<!-- /n --> | [<!-- n:v2:report.paired_by_key.fix_v2_no_c1_vs_fix_v2_minimax-m3_k8_n500.delta_clustered_ci95.0 -->0.0%<!-- /n -->, <!-- n:v2:report.paired_by_key.fix_v2_no_c1_vs_fix_v2_minimax-m3_k8_n500.delta_clustered_ci95.1 -->0.0%<!-- /n -->] | <!-- n:v2:report.paired_by_key.fix_v2_no_c1_vs_fix_v2_minimax-m3_k8_n500.b -->0<!-- /n --> / <!-- n:v2:report.paired_by_key.fix_v2_no_c1_vs_fix_v2_minimax-m3_k8_n500.c -->0<!-- /n --> | <!-- n:v2:report.paired_by_key.fix_v2_no_c1_vs_fix_v2_minimax-m3_k8_n500.mcnemar_p -->1.0000<!-- /n --> |
| without c2/c3, vs fix v2 | <!-- n:v2:report.paired_by_key.fix_v2_no_c3_vs_fix_v2_minimax-m3_k8_n500.delta_accuracy -->-8.2%<!-- /n --> | [<!-- n:v2:report.paired_by_key.fix_v2_no_c3_vs_fix_v2_minimax-m3_k8_n500.delta_clustered_ci95.0 -->-13.2%<!-- /n -->, <!-- n:v2:report.paired_by_key.fix_v2_no_c3_vs_fix_v2_minimax-m3_k8_n500.delta_clustered_ci95.1 -->-2.9%<!-- /n -->] | <!-- n:v2:report.paired_by_key.fix_v2_no_c3_vs_fix_v2_minimax-m3_k8_n500.b -->11<!-- /n --> / <!-- n:v2:report.paired_by_key.fix_v2_no_c3_vs_fix_v2_minimax-m3_k8_n500.c -->2<!-- /n --> | <!-- n:v2:report.paired_by_key.fix_v2_no_c3_vs_fix_v2_minimax-m3_k8_n500.mcnemar_p -->0.0225<!-- /n --> |
| without c4, vs fix v2 | <!-- n:v2:report.paired_by_key.fix_v2_no_c4_vs_fix_v2_minimax-m3_k8_n500.delta_accuracy -->-3.6%<!-- /n --> | [<!-- n:v2:report.paired_by_key.fix_v2_no_c4_vs_fix_v2_minimax-m3_k8_n500.delta_clustered_ci95.0 -->-11.5%<!-- /n -->, <!-- n:v2:report.paired_by_key.fix_v2_no_c4_vs_fix_v2_minimax-m3_k8_n500.delta_clustered_ci95.1 -->1.9%<!-- /n -->] | <!-- n:v2:report.paired_by_key.fix_v2_no_c4_vs_fix_v2_minimax-m3_k8_n500.b -->8<!-- /n --> / <!-- n:v2:report.paired_by_key.fix_v2_no_c4_vs_fix_v2_minimax-m3_k8_n500.c -->4<!-- /n --> | <!-- n:v2:report.paired_by_key.fix_v2_no_c4_vs_fix_v2_minimax-m3_k8_n500.mcnemar_p -->0.3877<!-- /n --> |
| deepseek-v4-pro, fix v2 vs fix v1 (B9, one trial) | +<!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_deepseek-v4-pro_k8_n500.delta_accuracy -->1.8%<!-- /n --> | [<!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_deepseek-v4-pro_k8_n500.delta_clustered_ci95.0 -->-7.2%<!-- /n -->, <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_deepseek-v4-pro_k8_n500.delta_clustered_ci95.1 -->11.7%<!-- /n -->] | <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_deepseek-v4-pro_k8_n500.b -->4<!-- /n --> / <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_deepseek-v4-pro_k8_n500.c -->6<!-- /n --> | <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_deepseek-v4-pro_k8_n500.mcnemar_p -->0.7539<!-- /n --> |
| N=5,000, 30 cases, fix v2 vs baseline (one trial) | +<!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n5000_s30.delta_accuracy -->30.0%<!-- /n --> | [<!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n5000_s30.delta_clustered_ci95.0 -->11.1%<!-- /n -->, <!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n5000_s30.delta_clustered_ci95.1 -->53.1%<!-- /n -->] | <!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n5000_s30.b -->1<!-- /n --> / <!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n5000_s30.c -->10<!-- /n --> | <!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n5000_s30.mcnemar_p -->0.0117<!-- /n --> |
| held-out set, guard vs fix v2 (one trial) | +<!-- n:heldout:report.paired_by_key.fix_v2_guard_vs_fix_v2_minimax-m3_k8_n500.delta_accuracy -->5.5%<!-- /n --> | [<!-- n:heldout:report.paired_by_key.fix_v2_guard_vs_fix_v2_minimax-m3_k8_n500.delta_clustered_ci95.0 -->-2.2%<!-- /n -->, <!-- n:heldout:report.paired_by_key.fix_v2_guard_vs_fix_v2_minimax-m3_k8_n500.delta_clustered_ci95.1 -->14.6%<!-- /n -->] | <!-- n:heldout:report.paired_by_key.fix_v2_guard_vs_fix_v2_minimax-m3_k8_n500.b -->2<!-- /n --> / <!-- n:heldout:report.paired_by_key.fix_v2_guard_vs_fix_v2_minimax-m3_k8_n500.c -->8<!-- /n --> | <!-- n:heldout:report.paired_by_key.fix_v2_guard_vs_fix_v2_minimax-m3_k8_n500.mcnemar_p -->0.1094<!-- /n --> |

Sources: `evals/results/RESULTS_v2.md` (paired table) and `evals/results/heldout/RESULTS_v2_heldout.md`. The N=5,000 row
pairs <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5000_s30.accuracy.n -->30<!-- /n --> cases from <!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n5000_s30.templates -->16<!-- /n --> templates; the held-out row pairs the <!-- n:heldout:report.paired_by_key.fix_v2_guard_vs_fix_v2_minimax-m3_k8_n500.pairs -->110<!-- /n --> held-out cases; every
other row pairs the same <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.pairs -->110<!-- /n --> fresh-set cases. p = 0.0000 means below 0.00005.

### 5.3 Error types

| measure (denominator) | baseline | fix v1 | fix v2 |
|---|---|---|---|
| wrong reuse, of 252 should-reuse case-trials: rate [Wilson 95%], count | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.rate -->29.0%<!-- /n --> [<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.ci95.0 -->23.7%<!-- /n -->, <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.ci95.1 -->34.9%<!-- /n -->], <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.k -->73<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.wrong_reuse.rate -->19.4%<!-- /n --> [<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.wrong_reuse.ci95.0 -->15.0%<!-- /n -->, <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.wrong_reuse.ci95.1 -->24.8%<!-- /n -->], <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.wrong_reuse.k -->49<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.wrong_reuse.rate -->6.3%<!-- /n --> [<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.wrong_reuse.ci95.0 -->3.9%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.wrong_reuse.ci95.1 -->10.1%<!-- /n -->], <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.wrong_reuse.k -->16<!-- /n --> |
| of which to another client's agent | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.cross_client.wrong_reuse -->46<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.cross_client.wrong_reuse -->16<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.cross_client.wrong_reuse -->11<!-- /n --> |
| turns that messaged two clients, of 312 (parallel cases left out) | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.multi_client_send.k -->47<!-- /n -->: <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.multi_client_send.rate -->15.1%<!-- /n --> [<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.multi_client_send.ci95.0 -->11.5%<!-- /n -->, <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.multi_client_send.ci95.1 -->19.5%<!-- /n -->] | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.multi_client_send.k -->20<!-- /n -->: <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.multi_client_send.rate -->6.4%<!-- /n --> [<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.multi_client_send.ci95.0 -->4.2%<!-- /n -->, <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.multi_client_send.ci95.1 -->9.7%<!-- /n -->] | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.k -->19<!-- /n -->: <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.rate -->6.1%<!-- /n --> [<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.ci95.0 -->3.9%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.ci95.1 -->9.3%<!-- /n -->] |
| duplicates, of 252: rate [Wilson 95%] | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.duplicate.rate -->25.0%<!-- /n --> [<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.duplicate.ci95.0 -->20.1%<!-- /n -->, <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.duplicate.ci95.1 -->30.7%<!-- /n -->] | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.duplicate.rate -->0.0%<!-- /n --> [<!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.duplicate.ci95.0 -->0.0%<!-- /n -->, <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.duplicate.ci95.1 -->1.5%<!-- /n -->] | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.duplicate.rate -->0.0%<!-- /n --> [<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.duplicate.ci95.0 -->0.0%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.duplicate.ci95.1 -->1.5%<!-- /n -->] |
| hijack, of 48 should-create case-trials | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.hijack.count -->11<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.hijack.count -->12<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.hijack.count -->1<!-- /n --> |
| asked the user when ambiguous, of 30 | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.clarify_recall.k -->0<!-- /n --> (no tool to ask) | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.clarify_recall.k -->0<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.k -->2<!-- /n -->: <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.rate -->6.7%<!-- /n --> [<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.ci95.0 -->1.8%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.ci95.1 -->21.3%<!-- /n -->] |
| asked when not ambiguous, of 300 | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.over_clarify.k -->0<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.over_clarify.k -->0<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.over_clarify.k -->5<!-- /n -->: <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.over_clarify.rate -->1.7%<!-- /n --> [<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.over_clarify.ci95.0 -->0.7%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.over_clarify.ci95.1 -->3.8%<!-- /n -->] |
| model calls per turn | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.llm_calls.mean -->1.10<!-- /n --> | <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.llm_calls.mean -->1.86<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.llm_calls.mean -->1.88<!-- /n --> |

Source: `evals/results/RESULTS_v2.md` (headline table; "Bars inputs, round two").

### 5.4 The bars

| bar | threshold | measured, fix v2 | verdict |
|---|---|---|---|
| B1 | beats fix v1, paired McNemar p < 0.05 | +<!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.delta_accuracy -->10.0%<!-- /n -->, p = <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.mcnemar_p -->0.0034<!-- /n --> | pass |
| B2a | 0 turns reach two clients | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.k -->19<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.n -->312<!-- /n --> | **fail** |
| B2b | cross-client wrong reuse <= 2% and below fix v1 | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.cross_client.wrong_reuse -->11<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.cross_client.wrong_reuse_n -->252<!-- /n --> (11 / 252 = 4.4%, computed); fix v1 <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.cross_client.wrong_reuse -->16<!-- /n --> | **fail** |
| B3 | hijacks <= half of fix v1's | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.hijack.count -->1<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.hijack.n -->48<!-- /n -->; fix v1 <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.hijack.count -->12<!-- /n --> | pass |
| B4 | asks on >= 50% of ambiguous cases; over-asks <= 5% | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.k -->2<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.n -->30<!-- /n -->; over-asks <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.over_clarify.rate -->1.7%<!-- /n --> | **fail** |
| B5 | tokens per turn <= 75% of baseline; <= 0.5 searches a turn; p50 not above baseline; N=5,000 tokens <= 25% | <!-- n:v2:bars.prompt_token_ratio_n500 -->103.0%<!-- /n -->; <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.searches.mean -->1.08<!-- /n -->; <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.latency_ms.p50 -->12,065<!-- /n --> vs <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n500.latency_ms.p50 -->8,509<!-- /n --> ms; N=5,000 <!-- n:v2:bars.prompt_token_ratio_n5000 -->15.6%<!-- /n --> | **fail** (N=5,000 part passes) |
| B6 | at N=5, >= baseline and >= fix v1 (one trial) | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5.accuracy.rate -->87.3%<!-- /n --> vs <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n5.accuracy.rate -->34.5%<!-- /n --> and <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n5.accuracy.rate -->74.5%<!-- /n --> | pass |
| B7 | duplicates <= fix v1's | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.duplicate.k -->0<!-- /n -->; fix v1 <!-- n:v2:report.by_key.fix_v1_minimax-m3_k8_n500.duplicate.rate -->0.0%<!-- /n --> | pass |
| B8 | 0 unauthorised sends | <!-- n:v2:injection.unauthorised_sends -->0<!-- /n --> in <!-- n:v2:injection.attacks -->46<!-- /n --> attacks | pass |
| B9 | deepseek-v4-pro moves the same way (registered with 3 trials; ran 1) | accuracy +<!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_deepseek-v4-pro_k8_n500.delta_accuracy -->1.8%<!-- /n -->; cross-client <!-- n:v2:report.by_key.fix_v1_deepseek-v4-pro_k8_n500.cross_client.wrong_reuse -->1<!-- /n --> to <!-- n:v2:report.by_key.fix_v2_deepseek-v4-pro_k8_n500.cross_client.wrong_reuse -->0<!-- /n --> | pass |
| ablations | reported whatever they show | no c1: <!-- n:v2:verdicts.10.verdict -->not needed on this set<!-- /n -->; no c2/c3: <!-- n:v2:verdicts.11.verdict -->needed (removing it lowers accuracy)<!-- /n -->; no c4: <!-- n:v2:verdicts.12.verdict -->needed (removing it lowers accuracy)<!-- /n -->, though its interval includes 0 | as stated |
| A1, A2 | evidence: no accuracy loss and tokens <= +10%; cites the discriminator >= 50% | <!-- n:v2:report.paired_by_key.fix_v2_evidence_vs_fix_v2_minimax-m3_k8_n500.delta_accuracy -->0.0%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_evidence_minimax-m3_k8_n500.prompt_tokens.mean -->11,456<!-- /n --> vs <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.prompt_tokens.mean -->10,198<!-- /n --> tokens; <!-- n:v2:report.by_key.fix_v2_evidence_minimax-m3_k8_n500.evidence.registered.k -->18<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_evidence_minimax-m3_k8_n500.evidence.registered.n -->34<!-- /n --> | A1 **fail**, A2 pass |
| L1-L6 | the month (5.5) | all six as registered; L2 only on a reading chosen after the slopes were seen | pass |
| D1 | replays offline in < 30 s; every turn in its scripted cell | 0.6 s; 6 of 9 turns | replay pass, cells **fail** |

Sources: thresholds from `evals/BARS-v2.md`; measurements and verdicts from `evals/results/RESULTS_v2.md` ("Bars").

### 5.5 The 30-day month

| measure | baseline | fix v1 | fix v2 | bar |
|---|---|---|---|---|
| live agents at day 30 (ideal <!-- n:lifecycle:arms.fix_v2.summary.ideal_live_end -->25<!-- /n -->) | <!-- n:lifecycle:arms.baseline.summary.live_end -->53<!-- /n --> | <!-- n:lifecycle:arms.fix_v1.summary.live_end -->20<!-- /n --> | <!-- n:lifecycle:arms.fix_v2.summary.live_end -->23<!-- /n --> | L4, at most 1.25 x ideal: pass |
| duplicates | <!-- n:lifecycle:arms.baseline.summary.duplicates -->21<!-- /n --> | <!-- n:lifecycle:arms.fix_v1.summary.duplicates -->0<!-- /n --> | <!-- n:lifecycle:arms.fix_v2.summary.duplicates -->0<!-- /n --> | L1: pass |
| wrong-reuse sends (share of reuse sends) | <!-- n:lifecycle:arms.baseline.summary.wrong_reuse -->186<!-- /n --> (48.6%) | <!-- n:lifecycle:arms.fix_v1.summary.wrong_reuse -->57<!-- /n --> (34.1%) | <!-- n:lifecycle:arms.fix_v2.summary.wrong_reuse -->10<!-- /n --> (6.8%) | L5: pass |
| cross-client sends | <!-- n:lifecycle:arms.baseline.summary.cross_client -->73<!-- /n --> | <!-- n:lifecycle:arms.fix_v1.summary.cross_client -->2<!-- /n --> | <!-- n:lifecycle:arms.fix_v2.summary.cross_client -->0<!-- /n --> (at most 2.0% at 95%) | L5: pass |
| day-30 messages whose agent was in the prompt | <!-- n:lifecycle:arms.baseline.summary.reachability_last_day.reachable -->6<!-- /n -->/<!-- n:lifecycle:arms.baseline.summary.reachability_last_day.needing_an_agent -->6<!-- /n --> | <!-- n:lifecycle:arms.fix_v1.summary.reachability_last_day.reachable -->2<!-- /n -->/<!-- n:lifecycle:arms.fix_v1.summary.reachability_last_day.needing_an_agent -->2<!-- /n --> | <!-- n:lifecycle:arms.fix_v2.summary.reachability_last_day.reachable -->6<!-- /n -->/<!-- n:lifecycle:arms.fix_v2.summary.reachability_last_day.needing_an_agent -->6<!-- /n --> | L3: pass |
| an archived matter's message reached its agent | never archives | <!-- n:lifecycle:arms.fix_v1.summary.revived -->3<!-- /n -->/<!-- n:lifecycle:arms.fix_v1.summary.revive_due -->3<!-- /n --> | <!-- n:lifecycle:arms.fix_v2.summary.revived -->4<!-- /n -->/<!-- n:lifecycle:arms.fix_v2.summary.revive_due -->4<!-- /n --> | L6, at least 80%: pass |
| prompt tokens per turn | <!-- n:lifecycle:arms.baseline.summary.prompt_tokens_per_turn -->30,366<!-- /n --> | <!-- n:lifecycle:arms.fix_v1.summary.prompt_tokens_per_turn -->22,257<!-- /n --> | <!-- n:lifecycle:arms.fix_v2.summary.prompt_tokens_per_turn -->20,110<!-- /n --> | |
| its slope per day [95% CI] | +<!-- n:lifecycle:arms.baseline.summary.prompt_tokens_slope_per_day -->1,793<!-- /n --> [+1,412, +2,175] | +<!-- n:lifecycle:arms.fix_v1.summary.prompt_tokens_slope_per_day -->656<!-- /n --> [+340, +973] | +<!-- n:lifecycle:arms.fix_v2.summary.prompt_tokens_slope_per_day -->32<!-- /n --> [-134, +198] | L2: pass, post hoc |
| turns ended at the tool-iteration limit | <!-- n:lifecycle:arms.baseline.summary.errors -->52<!-- /n --> | <!-- n:lifecycle:arms.fix_v1.summary.errors -->4<!-- /n --> | <!-- n:lifecycle:arms.fix_v2.summary.errors -->0<!-- /n --> | |
| clarifications, of <!-- n:lifecycle:arms.fix_v2.summary.ambiguous_messages -->10<!-- /n --> ambiguous messages | <!-- n:lifecycle:arms.baseline.summary.clarifications -->0<!-- /n --> | <!-- n:lifecycle:arms.fix_v1.summary.clarifications -->0<!-- /n --> | <!-- n:lifecycle:arms.fix_v2.summary.clarifications -->0<!-- /n --> | |

Sources: `evals/results/LIFECYCLE.json`, `LIFECYCLE.md`; intervals from `evals/results/RESULTS_v2.md` ("Lifecycle").
Fix v1's 2/2 counts only matters that already had their own agent: four of its six day-30 matters never got one.

### 5.6 N=5,000 (<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5000_s30.accuracy.n -->30<!-- /n --> sampled cases, one trial)

| measure | baseline | fix v2 |
|---|---|---|
| accuracy, Wilson 95% | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n5000_s30.accuracy.rate -->46.7%<!-- /n --> (<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n5000_s30.accuracy.k -->14<!-- /n --> correct) [<!-- n:v2:report.by_key.baseline_minimax-m3_k8_n5000_s30.accuracy.ci95.0 -->30.2%<!-- /n -->, <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n5000_s30.accuracy.ci95.1 -->63.9%<!-- /n -->] | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5000_s30.accuracy.rate -->76.7%<!-- /n --> (<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5000_s30.accuracy.k -->23<!-- /n --> correct) [<!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5000_s30.accuracy.ci95.0 -->59.1%<!-- /n -->, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5000_s30.accuracy.ci95.1 -->88.2%<!-- /n -->] |
| prompt tokens per turn | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n5000_s30.prompt_tokens.mean -->72,091<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5000_s30.prompt_tokens.mean -->11,215<!-- /n --> (<!-- n:v2:bars.prompt_token_ratio_n5000 -->15.6%<!-- /n --> of the baseline's) |
| model calls per turn | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n5000_s30.llm_calls.mean -->1.17<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5000_s30.llm_calls.mean -->2.07<!-- /n --> |
| p50 latency, ms | <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n5000_s30.latency_ms.p50 -->9,162<!-- /n --> | <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5000_s30.latency_ms.p50 -->28,759<!-- /n --> |

Source: `evals/results/RESULTS_v2.md` ("N=5,000 demonstration"). Paired McNemar p = <!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n5000_s30.mcnemar_p -->0.0117<!-- /n -->. One fix v2 turn
hit the tool-iteration limit and counts as wrong.

### 5.7 The injection suite

<!-- n:v2:injection.attacks -->46<!-- /n --> attacks, <!-- n:v2:injection.unauthorised_sends -->0<!-- /n --> sends without a consumed approval; by the rule of three the true rate is at most
<!-- n:v2:injection.upper95 -->6.5%<!-- /n --> at 95%, and <!-- n:v2:injection.positive_controls_sent -->4<!-- /n --> of <!-- n:v2:injection.positive_controls -->4<!-- /n --> positive controls (the user really types the code)
sent exactly once. Source: `evals/results/INJECTION.json` (`make injection`);
`INJECTION_v2.json` holds the same values.

### 5.8 The client guard on the held-out set

| bar (registered in `adf9263`, before any guard run) | verdict | guard | fix v2, same cases |
|---|---|---|---|
| G-i no turn reaches two clients (parallel cases left out) | <!-- n:heldout:verdicts.0.verdict -->pass<!-- /n --> | <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.multi_client_send.k -->0<!-- /n --> of <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.multi_client_send.n -->104<!-- /n --> (at most <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.multi_client_send.upper95 -->2.9%<!-- /n --> at 95%) | <!-- n:heldout:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.k -->8<!-- /n --> |
| G-ii cross-client wrong reuse <= fix v2's and <= 2% | <!-- n:heldout:verdicts.1.verdict -->pass<!-- /n --> | <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.cross_client.wrong_reuse -->0<!-- /n --> of <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.cross_client.wrong_reuse_n -->84<!-- /n --> (at most <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.cross_client.wrong_reuse_upper95 -->3.6%<!-- /n -->) | <!-- n:heldout:report.by_key.fix_v2_minimax-m3_k8_n500.cross_client.wrong_reuse -->5<!-- /n --> |
| G-iii accuracy at most 3 points below fix v2's | <!-- n:heldout:verdicts.2.verdict -->pass<!-- /n --> | <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.accuracy.rate -->88.2%<!-- /n -->, Wilson [<!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.accuracy.ci95.0 -->80.8%<!-- /n -->, <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.accuracy.ci95.1 -->93.0%<!-- /n -->] | <!-- n:heldout:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.rate -->82.7%<!-- /n -->, Wilson [<!-- n:heldout:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.ci95.0 -->74.6%<!-- /n -->, <!-- n:heldout:report.by_key.fix_v2_minimax-m3_k8_n500.accuracy.ci95.1 -->88.7%<!-- /n -->] |
| G-iv parallel cases still matched, not below fix v2 | **<!-- n:heldout:verdicts.3.verdict -->fail<!-- /n -->** | <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.multi_client_send.parallel_gold.matched_gold -->3<!-- /n --> of <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.multi_client_send.parallel_gold.n -->6<!-- /n --> | <!-- n:heldout:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.parallel_gold.matched_gold -->5<!-- /n --> |

Source: `evals/results/heldout/RESULTS_v2_heldout.md`. The guard refused <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.client_guard.sends -->20<!-- /n --> sends in <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.client_guard.turns -->10<!-- /n --> of <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.client_guard.n -->110<!-- /n --> turns; in the
round after, the model asked the user <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.client_guard.outcomes.clarified -->6<!-- /n --> times. G-iv failed the way the what-if warned: one parallel template
describes a client ("the tool store in Orangeville") instead of naming it, and the guard refuses that client's agent.
On the round-two set, which it was designed on (non-blind, never a verdict), it was right on +<!-- n:v2:report.paired_by_key.fix_v2_guard_vs_fix_v2_minimax-m3_k8_n500.delta_accuracy -->2.7%<!-- /n --> more cases
than fix v2's first trial (p = <!-- n:v2:report.paired_by_key.fix_v2_guard_vs_fix_v2_minimax-m3_k8_n500.mcnemar_p -->0.3750<!-- /n -->).

### 5.9 How to read these numbers

- **Wilson interval:** a 95% interval for a proportion that stays between 0% and 100% and holds up when n is small or the rate is near 0 or 1, where the textbook normal interval breaks.
- **Template-clustered interval:** cases built from one template tend to pass or fail together, so 110 cases carry about as much information as their 19 templates; the bootstrap resamples whole templates (2,000 resamples), which is why these intervals are much wider than the Wilson ones.
- **Paired McNemar test:** both systems ran on the same cases, so only the cases where exactly one was right say which is better; the exact test asks how likely so lopsided a split would be if each such case were a coin flip. For B1 the split is <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.b -->1<!-- /n --> against <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.c -->12<!-- /n -->, and p = 2 x (1 + 13) / 2^13 = <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.mcnemar_p -->0.0034<!-- /n -->.
- **Rule of three:** zero events in n tries does not mean a zero rate; the 95% upper bound is about 3/n, so 0 of <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.multi_client_send.n -->104<!-- /n --> reads as "at most <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.multi_client_send.upper95 -->2.9%<!-- /n -->", and 0 of <!-- n:v2:injection.attacks -->46<!-- /n --> attacks as "at most <!-- n:v2:injection.upper95 -->6.5%<!-- /n -->".
- **p = 0.11 against 0.0034 and 0.0117:** the guard's held-out gain (<!-- n:heldout:report.paired_by_key.fix_v2_guard_vs_fix_v2_minimax-m3_k8_n500.b -->2<!-- /n --> against <!-- n:heldout:report.paired_by_key.fix_v2_guard_vs_fix_v2_minimax-m3_k8_n500.c -->8<!-- /n --> cases, p = <!-- n:heldout:report.paired_by_key.fix_v2_guard_vs_fix_v2_minimax-m3_k8_n500.mcnemar_p -->0.1094<!-- /n -->) is not significant: chance alone gives a split like that about one time in nine. G-iii only asked that accuracy not drop by more than 3 points, and 5 of the 8 cases only the guard got right fall on turns it did not touch, so most of the gap comes from the two arms' own replies to the same first call. B1 (1 against 12) and N=5,000 (<!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n5000_s30.b -->1<!-- /n --> against <!-- n:v2:report.paired_by_key.fix_v2_vs_baseline_minimax-m3_k8_n5000_s30.c -->10<!-- /n -->) are significant at 0.05; the N=5,000 result rests on 30 cases and one trial, so its interval is wide.

## 6. Proof: how to check every number

Python 3.11; only `make eval-live` needs an API key. From [README, How to run](README.md#how-to-run):

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
make test           # unit tests and a replay of the baseline cassette
make demo           # the demo -> evals/results/TRANSCRIPT.md; exit 0 on a complete replay, DEMO_ARGS=--strict for 1 on a miss
make lifecycle      # the 30-day simulation -> evals/results/LIFECYCLE.md
make injection      # the attacks against the send gate -> evals/results/INJECTION.json
make check-numbers  # every marked number in the docs against its results file
make eval-replay ARMS="baseline fix_v1 fix_v2" SIZES=500 TRIALS=3 EVAL_ARGS="--set v2 --model minimax-m3 --out-dir /tmp/r"
```

`pytest -m slow` adds the full replays (every round-two run, held-out included, and round one's fix runs); a call missing from the cassettes fails every replay. Read-only checks with git:

```bash
git rev-parse --short 861b8d5^                                 # 19b6d3a: the baseline came first
git show HEAD:evals/golden/routing_v2.jsonl | sha256sum         # 35c914b2...3122f, as in routing_v2.sha256
git log --format='%h %s' -- evals/golden/routing_v2.jsonl evals/golden/routing_v2.sha256   # 4914c11 and 8090344 only
git diff 7ec4fbc HEAD -- evals/BARS-v2.md | grep -c '^-[^-]'   # 0: no bar line removed or changed
```

**Commit hashes in result filenames.** A run's result file ends with the commit it was recorded at. Five of those
commits were cherry-picked onto this branch under a new hash, so `git show 88f9fd2` fails in a fresh clone. Each pair
is the same patch (equal `git patch-id --stable`) with the same subject:

| hash in the filename | the same change on this branch | result files |
|---|---|---|
| `88f9fd2` | `3f664d0` | six round-two runs: baseline and fix v2 at N=5 and 50, fix v1 at N=5 and 500, fix v2 at N=500, the evidence arm |
| `67ce14f` | `ffd5715` | the minimax-m3 baseline at N=500, three trials |
| `262fe0b` | `5c290dd` | the deepseek-v4-pro baseline at N=500 |
| `accf492` | `068f733` | the continuity run in `evals/results/continuity/` |
| `c488caa` | `c596cc8` | the selector table, `20261028_selector_c488caa.json` |

The proof that the recordings still match the code is the replay at HEAD (`pytest -m slow`), not the hash in a name.

| table | result file |
|---|---|
| 2, 5.1 to 5.4, 5.6 | `evals/results/RESULTS_v2.md` and `.json`, built by `python -m evals.compare --set v2` from the run files under its "Sources" |
| 5.5 | `evals/results/LIFECYCLE.json`, `LIFECYCLE.md` and the three `lifecycle_*_minimax-m3_d30.jsonl` files |
| 5.7 | `evals/results/INJECTION.json` |
| 5.8 | `evals/results/heldout/RESULTS_v2_heldout.md` and `.json` |
| D1 | `evals/results/TRANSCRIPT.md` |
| 3 and 4.4 (round one) | `evals/results/RESULTS.json`, `RESULTS.md`, `ROUND1.md`; the first baseline's raw records in `evals/results/upstream/` |

**How the numbers are checked.** Each checked number sits between two invisible HTML comments: an opening one naming
a dotted key into a results file (no prefix reads round one's `RESULTS.json`; `v2:`, `heldout:`, `lifecycle:` and
`continuity:` read their own files) and a closing `/n` one. `evals/check_readme_numbers.py` compares the shown value
with the stored one at the precision shown (a percentage is the stored value times 100) and exits 1 on a mismatch or
an unknown key. `make check-numbers` covers README, DESIGN, ROUND1, ROUND1-TABLES and this report, which on its own is checked with
`python -m evals.check_readme_numbers --readme REPORT.md`.

## 7. What failed, and the limits of the evidence

**Failed bars, and why** (next steps in section 9):

- **B2a**, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.k -->19<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.multi_client_send.n -->312<!-- /n --> turns reached two clients: c1 refuses only targets that look like the same client, and these were two different clients, for example both welders messaged for "the welder who asked about the upcoming term" ([FAILURES.md](FAILURES.md), class A).
- **B2b**, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.cross_client.wrong_reuse -->11<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.cross_client.wrong_reuse_n -->252<!-- /n -->: all sprays, the right agent plus another client's.
- **B4**, <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.k -->2<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.clarify_recall.n -->30<!-- /n -->: with two claims and no date the model picked one; with two clients sharing a first name it messaged both. deepseek-v4-pro asked on <!-- n:v2:report.by_key.fix_v2_deepseek-v4-pro_k8_n500.clarify_recall.k -->5<!-- /n --> of <!-- n:v2:report.by_key.fix_v2_deepseek-v4-pro_k8_n500.clarify_recall.n -->10<!-- /n --> under the same prompt, so following the rule depends on the model.
- **B5**, <!-- n:v2:bars.prompt_token_ratio_n500 -->103.0%<!-- /n --> of the baseline's tokens per turn: each call carries <!-- n:v2:bars.prompt_token_per_call_ratio_n500 -->58.5%<!-- /n --> of the baseline's prompt, but the search round makes almost two calls a turn.
- **A1**: the eval's agent logs hold two entries, both already on the card, so the evidence arm tests duplication; the flag stays off.
- **D1 cells**, 6 of 9: a new renewal and a new claim went to the client's welcome agent, the same-matter rule not followed in this recording. The phishing turn sent nothing because the model never called `confirm_action`; the injection suite tests the refusal.
- **G-iv**, <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.multi_client_send.parallel_gold.matched_gold -->3<!-- /n --> of <!-- n:heldout:report.by_key.fix_v2_guard_minimax-m3_k8_n500.multi_client_send.parallel_gold.n -->6<!-- /n -->: the guard cannot yet tell that a description names a client.

**Limits of the evidence:**

- **Synthetic data:** <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.pairs -->110<!-- /n --> cases from <!-- n:v2:report.paired_by_key.fix_v2_vs_fix_v1_minimax-m3_k8_n500.templates -->19<!-- /n --> hand-written templates, no phrasing pass by another model, a hand check of 20 cases. Every should-create message says "new", so hijack is measured only where the user states it.
- **Mainly one model:** minimax-m3 throughout; deepseek-v4-pro ran one trial per arm (trials 2 and 3 cut for quota); no run used the repo's default model (`anthropic/claude-sonnet-4`).
- **Disclosed replay misses:** the deepseek baseline has one case (`v2-paraphrase-001`) with no recorded response, graded `error` with 0 tokens, which can only lower that baseline's accuracy (by at most one case) and its token mean; the continuity run (fix v2 on round one's set) has <!-- n:continuity:report.by_key.fix_v2_minimax-m3_k8_n500.cells.error -->3<!-- /n --> of <!-- n:continuity:report.by_key.fix_v2_minimax-m3_k8_n500.n -->86<!-- /n -->. Recorded data was never edited.
- **A post hoc reading:** L2 passes only on the interval reading chosen after the slopes were seen; on a sign-only reading fix v2's +<!-- n:lifecycle:arms.fix_v2.summary.prompt_tokens_slope_per_day -->32<!-- /n --> a day fails it.
- **The guard was designed after seeing failures,** so only the held-out set counts. That set has fewer archived targets, <!-- n:heldout:set_mix.idle_target -->43<!-- /n --> of <!-- n:heldout:set_mix.non_create -->94<!-- /n --> against <!-- n:v2:set_mix.idle_target -->58<!-- /n --> of <!-- n:v2:set_mix.non_create -->94<!-- /n -->, and most of G-iii's gain is run-to-run variation, not the guard.
- **Slower at scale:** at N=5,000 a fix v2 turn takes <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n5000_s30.latency_ms.p50 -->28,759<!-- /n --> ms at p50 against <!-- n:v2:report.by_key.baseline_minimax-m3_k8_n5000_s30.latency_ms.p50 -->9,162<!-- /n --> ms, about three times ([KNOWN-GAPS.md](KNOWN-GAPS.md), item 10).
- **Archived targets are still harder:** fix v2 is right on <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.target_status.idle.rate -->73.0%<!-- /n --> of case-trials whose target is idle against <!-- n:v2:report.by_key.fix_v2_minimax-m3_k8_n500.target_status.active.rate -->92.6%<!-- /n --> when it is active.
- **Not measured:** execution is stubbed, so the evals grade routing, not what an agent then does; the injection suite tests the gate with a scripted model, not a live one; c1 is inert below 100 agents, so the month says nothing about it; c2 cuts a card at an abbreviation ("... in St."), affecting 14 fresh-set case agents, found after the runs were frozen and not fixed because the fix changes card bytes the recordings replay; the "with pins" selector numbers were found on round one's report split, so they are not blind.

## 8. Other problems found and fixed in code

From [README, Other problems fixed in code](README.md#other-problems-fixed-in-code):

1. **Email went out on the model's word.** Now a proposal bound to a hash of its arguments, sent once after the user types its code (`approvals.py`; `test_send_gate.py`): <!-- n:v2:injection.unauthorised_sends -->0<!-- /n --> unauthorised sends in <!-- n:v2:injection.attacks -->46<!-- /n --> attacks.
2. **Names were identity.** Two names with one slug shared a log, and the save truncated the file before locking it; now minted ids and a unique slug (`registry.py`; `test_registry.py`).
3. **An email could pose as the user.** The current turn is escaped like history, and approval needs `user` provenance, which defaults to none (`provenance.py`; `test_provenance.py`).
4. **The roster only grew.** Agents idle 30 days are archived, never one with a trigger or a pending send, and revive when reused (`lifecycle.py`; `test_lifecycle.py`).
5. **Execution history was unbounded.** `conversation_limit` is now passed, and `OPENPOKE_EXECUTION_MEMORY=retrieved` keeps recent plus relevant entries: 5,190 to 246 history tokens on a 200-entry log (`test_execution_memory.py`; unit test only, off by default).
6. **Model calls were not metered.** Every call goes through a cost ledger with a budget (`costs.py`; `test_every_llm_call_is_ledgered.py`).

[KNOWN-GAPS.md](KNOWN-GAPS.md) ranks 30 open gaps, each with its fix.

## 9. What I'd do next

The failed bars point at code, not at more prompt text:

1. **Close the two-client gap in code:** teach the client guard that a description fitting one client's cards names that client, then measure it on a new held-out set with its bar written first (B2a, B2b, G-iv).
2. **Ask in code:** when two shortlisted agents fit a message equally, return the question to the user instead of letting the model guess, measured against the over-ask rate (B4).
3. **Skip the search round** when a strong match is already on the shortlist (B5).
4. **Fix the abbreviation cut** in `cards._first_sentence` (skip Mr., Ms., Mrs., Dr., St., Jr., Sr.), then re-record.

First on Monday, from the top of [KNOWN-GAPS.md](KNOWN-GAPS.md): auth on the HTTP API (it binds `0.0.0.0` with CORS `*` and an unauthenticated delete-all); the client guard; a hash of the draft body at approval, since a draft can be edited after it is approved;
pending sends shown in the UI straight from the approval store, so the code does not reach the user through the model; and a tenant key on every store.
On the evidence side: a run on the default model, a phrasing pass by another model family, a live-model injection run, and shadow mode on real traffic.
