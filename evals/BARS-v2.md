# Pre-registered bars, round 2

Written before any v2 run and committed before the first v2 cassette. A bar that fails is a result to report,
not a reason to tune the test. The round-1 bars in `BARS.md` stay as they were and are reported on the v1 set.

## How the numbers are made

All bars below are measured on the fresh set (`evals/golden/routing_v2.jsonl`), N=500, minimax-m3, 3 trials,
unless the bar says otherwise.

- Pairing: each case gets one outcome per arm, the majority over its trials. Two arms are compared on the same
  cases with a paired McNemar test on those per-case outcomes.
- Intervals: every rate is reported with its n, a 95% Wilson interval and a template-clustered interval, as
  in v1.
- Zero cells: a rate with no events is never written as 0%. It is reported as a rule-of-three bound,
  "<= 3/n at 95%".

| # | Bar |
|---|---|
| B1 | `fix_v2` accuracy > `fix_v1` accuracy, paired McNemar p < 0.05 (v2 must beat v1, not only the baseline) |
| B2a | Multi-agent sends that reach two clients: 0 in `fix_v2` (code-enforced; a failing test, not a score) |
| B2b | Cross-client wrong reuse in `fix_v2` <= 2% of should-reuse case-trials and below `fix_v1`; a zero is reported as a rule-of-three bound |
| B3 | Hijack rate in `fix_v2` <= half of `fix_v1`'s |
| B4 | Clarify recall on ambiguous cases >= 50% with over-clarify <= 5% on the rest |
| B5 | Prompt tokens per turn (all calls) `fix_v2` <= 75% of baseline at N=500; `search_agents` <= 0.5 per turn; p50 latency not above baseline; at N=5,000 (30 cases, 1 trial) <= 25% of baseline |
| B6 | No small-N regression: `fix_v2` at N=5 >= baseline at N=5 and >= `fix_v1` at N=5 (1 trial) |
| B7 | Duplicates in `fix_v2` <= `fix_v1` |
| B8 | Injection suite unchanged at 0 unauthorised sends; the new send check has its own negative control |
| B9 | Second model (deepseek-v4-pro, 3 trials): `fix_v2` vs `fix_v1` moves the same direction on accuracy and cross-client (sign agreement, not a p bar) |

Also pre-registered: the ablation (v2 minus one change at a time, 1 trial) is reported whatever it shows; if
removing a change does not hurt, that change is called out as "not needed on this set".

## Demo transcript

**Demo transcript.** A scripted 8-10 turn conversation in stub mode (`OPENPOKE_EXECUTION_STUB=1`), recorded once
with minimax-m3, replayed from its cassette (`make demo`). D1: it replays offline in under 30 s and every turn
lands in its scripted cell (`tests/test_demo_transcript.py`).

## Lifecycle simulation

**Lifecycle simulation (L3).** `evals/run_lifecycle.py`, 30 simulated days on the injectable clock, stub
execution, scenario seed 20261030, minimax-m3, temperature 0, 1 trial; arms baseline and fix_v2 (fix_v1 if quota
allows, stated). If quota forces it, 15 days, said plainly. Ideal = the scenario's gold one-agent-per-matter
mapping under the same archive rule. L1 day-30 cumulative duplicates (a new agent for a matter that already has a
live or archived agent) in fix_v2 < baseline's. L2 fix_v2 prompt tokens per turn show no upward slope across days
while baseline's do (both slopes reported). L3 every agent a day-30 message needs is reachable, or the misses are
listed. L4 live agents at the end of day 30 in fix_v2 <= 1.25 x the ideal live count. L5 wrong-reuse rate in
fix_v2 <= baseline's; cross-client sends in fix_v2 = 0 (reported as a rule-of-three bound). L6 when a message
belongs to a matter whose agent is archived, fix_v2 sends it to that agent rather than creating a new one in
>= 80% of such messages (n stated). Reported without a bar: searches per turn, clarifications, archive and revive
events, per-day roster series, execution-agent prompt size at day 30 (measurement only).

## Evidence snippets

**Evidence snippets (arm `fix_v2_evidence`).** fix_v2 plus `OPENPOKE_SHORTLIST_EVIDENCE=1` (default 0); fresh
set, N=500, minimax-m3, 1 trial, paired per case with fix_v2. A1 paired accuracy not below fix_v2's and prompt
tokens per turn within +10% of fix_v2's. A2 on `near_duplicate` and `should_create` cases the picked agent's cited
evidence entry names the case's discriminator in >= 50% of case-trials (a code check, reported as a rate).
Cross-client and hijack reported beside it. The flag's default flips to 1 only if A1 and A2 both pass; otherwise
it stays off and is reported as measured.

## Verdicts

The verdicts are computed by `python -m evals.compare --set v2` into `evals/results/RESULTS_v2.md`.

B2a, as read: a turn counts against B2a only on a case whose gold does not itself call for two clients. A correct
answer on a `parallel` case reaches two clients by design and is not a violation; parallel-gold turns that sent
to two clients are reported beside the count, with how many matched gold. This interpretation was set by the lead
before any B2a number was read.

B2b, as read: "below fix_v1" holds when fix_v2's cross-client wrong reuse is lower than fix_v1's, or when both are
0. This reading was set by the lead.

Lifecycle and demo, read from `evals/results/LIFECYCLE.json` and `TRANSCRIPT.md` (`compare` does not grade them):
a replay at 667a723 of the cassettes recorded once, live, at ebc101e, with 0 cassette misses. The lifecycle ran
minimax-m3, 30 days, 1 trial, and all three arms (quota allowed fix_v1). Detail in `RESULTS_v2.md`.

| bar | verdict | measured |
|---|---|---|
| L1 | pass | day-30 cumulative duplicates: fix_v2 0, baseline 21 (fix_v1 0) |
| L2 | pass, as read below | prompt tokens per turn, least-squares slope over the 30 daily means: fix_v2 +32.2/day (95% CI -134 to +198), baseline +1793.1/day (95% CI +1412 to +2175); fix_v1 +656.4/day |
| L3 | pass | day 30: fix_v2 had the needed agent in the prompt for 6 of 6 messages, 0 misses (baseline 6 of 6) |
| L4 | pass | live agents at the end of day 30: fix_v2 23 against an ideal 25 (bar <= 31.25); baseline 53 (2.12 x ideal) |
| L5 | pass | wrong reuse per reuse send: fix_v2 6.8% (10/148), baseline 48.6% (186/383); cross-client sends: fix_v2 0 (<= 2.0%, 3/148), baseline 73 |
| L6 | pass | sent to the archived agent rather than a new one: fix_v2 4 of 4 (100.0%, Wilson [51.0%, 100.0%]); baseline never archives (n = 0) |
| D1, replay half | pass | replays offline: 21 cassette hits, 0 live calls, 0.6 s (1.6 s for the whole process); bar < 30 s |
| D1, cells half | fail | 6 of 9 checked turns in their cell; turns 3, 4 and 5 missed. The live model's result, recorded once and kept |

L2, as read: "show no upward slope" is read as a slope whose 95% interval includes 0. fix_v2's point slope is
positive (+32.2 tokens/day, t = 0.4), so on a sign-only reading L2 fails. This reading was set after the slopes were
seen.

## B2-guard (v2.1, registered before any guard run)

**The client guard (arm `fix_v2_guard`).** fix_v2's switches plus `OPENPOKE_CLIENT_GUARD=1` (default 0, profile v2
only): a send that reuses an existing agent whose client the turn's message does not name is refused when the
message names another client or when the turn's sends, every round, reach two or more clients
(`unnamed_client_conflict` in `server/agents/interaction_agent/tools.py`). The guard was designed after reading
round two's B2a and B2b failures (`evals/results/CLIENT_GUARD_WHATIF.md`), so its result on the round-two set is
reported as NON-BLIND, beside these bars and never as a verdict on them. Only the held-out set counts.

Set: a held-out, seed-generated set the guard was not designed on; N=500, 1 trial, minimax-m3; fix_v2 and
fix_v2_guard on the same cases, paired per case.

| # | Bar |
|---|---|
| G-i | B2a in fix_v2_guard = 0 on cases whose gold does not call for two clients (B2a as read above; the zero is reported as a rule-of-three bound) |
| G-ii | Cross-client wrong reuse in fix_v2_guard <= fix_v2's and <= 2% of should-reuse case-trials |
| G-iii | Accuracy non-inferior: fix_v2_guard >= fix_v2 - 3.0 points (point estimates; the paired McNemar p and the template-clustered interval of the difference reported beside it) |
| G-iv | Parallel-gold multi-client sends that matched gold in fix_v2_guard not lower than fix_v2's (if the set has no parallel case, said so, not passed) |

Reported without a bar: sends and turns the guard refused, what the model did in the round after a refusal,
and clarify recall on ambiguous cases. The what-if found one parallel false block in 13 on round two (a client
described, not named), so G-iv is the bar most at risk.

### Verdicts (2026-10-28)

Computed by `python -m evals.compare --set v2_heldout --out-dir evals/results/heldout` into
`evals/results/heldout/RESULTS_v2_heldout.md`, from a replay at 681fc59 of the two held-out runs recorded once,
live, at adf9263: 0 cassette misses, every graded record equal to the live run's. The two runs were recorded at the
same time, so each arm made its own first call for every case; each replays from its own cassette directory
(`evals/cassettes/heldout/<arm>/`), because the two replies to one request differ.

| bar | verdict | measured |
|---|---|---|
| G-i | pass | 0 of 104 fix_v2_guard turns on cases whose gold does not call for two clients sent to two (<= 2.9% at 95%); fix_v2 8 of 104 |
| G-ii | pass | cross-client wrong reuse fix_v2_guard 0 of 84 (<= 3.6% at 95%), fix_v2 5 of 84 |
| G-iii | pass | fix_v2_guard 88.2% (97/110), fix_v2 82.7% (91/110): +5.5 points; McNemar p = 0.1094, template-clustered 95% of the difference [-2.2%, 14.6%] |
| G-iv | fail | parallel-gold sends to two clients that matched gold: fix_v2_guard 3 of 6, fix_v2 5 of 6 |

G-iv failed the way the what-if warned: one parallel template (`parallel.v2t1`) describes a client ("the tool store
in Orangeville") instead of naming it, so the guard refused that client's agent in all three of its cases (two left
with nothing routed, one partial; fix_v2 had matched two of them). Without a bar: the guard refused 20 sends in 10 of 110 turns; in the round
after the refusal the model asked the user 6 times and routed nothing new 4 times, and 4 of the 10 turns were graded
correct. Clarify recall on ambiguous cases: 6 of 10 against fix_v2's 2 of 10 (over-clarify 4 of 100 against 1 of
100). Most of G-iii's +5.5 points is not the guard: in the 100 turns it refused nothing, 6 were graded differently
(5 right only with the guard, 0 only without), from each arm's own reply to the same first call; the turns it
refused net +1 case. The flag stays off by default. Detail and the non-blind round-two row: `RESULTS_v2.md`,
"Client guard".
