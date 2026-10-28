# Client guard: offline what-if on the round-two recordings

The tables were computed before the guard existed in code and before any guard run, from the committed fix_v2
results (`20261028_v2_fix_v2_minimax-m3_k8_report_n500_t3_88f9fd2.json`: minimax-m3, N=500, 3 trials, 330
case-trials); the harness check further down came after. No model was called. The guard was designed after
reading these failures, so everything below is non-blind; only a held-out set can test it.

## What the 19 B2a turns and 11 B2b reuses have in common

All 19 turns that sent to two clients on a case whose gold does not call for two clients reused two existing
agents of two different clients in one assistant message, and in every one the incoming message names
neither client by anything a card holds:

- 11 are `paraphrase` cases ("the welder who asked about the certificate of insurance - has anything come
  back yet?"): the client is described, never named. Graded wrong_reuse.
- 8 are `ambiguous` cases ("Orrin called asking where his renewal is at"): a first name shared by two
  clients, which the entity reader does not take as a name. Gold is a clarifying question; graded
  wrong_guess.

The 11 cross-client wrong reuses (B2b) are 11 of those same 19 turns (the paraphrase sprays); fix_v2 has no
single-send cross-client reuse in these recordings.

Correct sends differ in what the message says, not in what the model did: a correct parallel turn's message
names each client by an entity on that client's cards (a full name, an email, a policy, claim or thread
number, a two-word town), and a correct single send either names its client the same way or names nobody
(a pronoun, a description).

## The decision function

1. A target's client is its card plus every card that shares an identifying entity with it (the entities on
   at most 2% of all cards, as the shared-client check reads them); the message names that client when it
   contains one of those entities. A target with no identifying entity is never judged.
2. A send that reuses an existing agent whose client the message does not name is refused when the message
   names some other client (wrong-client reuse), or when the turn's sends, earlier rounds included, reach
   two or more clients (multi-client spray).
3. New agents, single sends on a message that names nobody, and sends whose client is named all pass.

## What it would block

Applied to the turn's dispatched sends over every round, per recorded case-trial:

| recorded fix_v2 turns, minimax-m3, N=500, 3 trials | turns | guard would block |
|---|---|---|
| B2a: sends reached two clients, gold not parallel | 19 | 19 (11 paraphrase, 8 ambiguous) |
| B2b: cross-client wrong reuse | 11 | 11 (all inside the 19) |
| correct single-client send turns | 212 | 0 |
| parallel-gold correct multi-client sends | 13 | 1 (v2-parallel-002, trial 0) |
| other wrong send turns | 26 | 0 |
| parallel-gold turns that sent only the unnamed client (graded partial) | 2 | 2 |
| turns with no existing target (create, clarify, none) | 55 | not applicable |

The one parallel false block: "once the billing for the collision repair shop in Chatham is confirmed, nudge
Solveig Lindgren about the endorsement". Solveig Lindgren is named; the shop is only described, and a
one-word town is not an entity, so its send is refused. The same template with a two-word town (Port Hope,
Smiths Falls) names the business through the town and passes.

The same function over the other recordings (a check for false blocks, not part of any bar):

| recording | correct single-client blocked | parallel correct blocked | B2a blocked | B2b caught |
|---|---|---|---|---|
| fix_v2 minimax-m3 N=5 and N=50, 1 trial | 0/144 | 0/8 | 8/11 | 5/8 |
| fix_v2 deepseek-v4-pro N=500, 1 trial | 0/71 | 0/3 | 0/0 | 0/0 |
| all 13 round-two result files, every arm and size | 0/1,249 | 5/70 (all v2-parallel-002) | 148/191 | 108/159 |

At N=5 no entity is on at most 2% of five cards, so the guard never fires there (the shared-client check has
the same property); the 3 misses in the small-N row are the 3 N=5 turns. Below about 100 agents the 2% cut
also reads a client's own name as common once two of its matters hold it (at N=50 a same-client spray on
v2-explicit-008 was blocked as if it were two clients; it was wrong anyway). In the all-arms row every miss
but 4 (baseline sprays at N=500) is an N=5 turn or a single send to another client's agent on a message that
names nobody (8, all in baseline and fix_v1), which the function lets through by design (rule 3).

## Checked through the harness

The table above was computed by a scratch script before the guard was in code. The shipped function
(`unnamed_client_conflict`) then gave the same answer on all 1,890 recorded send turns of the 13 files. And the
guard arm replayed offline over the committed cassettes, which need no model call because the guard changes no
prompt, card or schema byte:

    python -m evals.run_routing --set v2 --arm fix_v2_guard --sizes 500 --trials 3 --replay --misses-as-errors

308 of 330 case-trials replay and grade exactly as fix_v2's recorded run; the other 22 are the turns the guard
refuses (the 19 B2a turns and the three v2-parallel-002 trials), each of which needs a follow-up call that was
never recorded and so misses. No live call was made; `tests/test_client_guard_replay.py` checks a subset of this.

## What this cannot say

It counts blocks, not outcomes. After a refusal the model gets another round, and what it does there (ask,
pick one, pick another wrong one, give up) is only known from a live run. The blocked B2a turns are all
already graded wrong, so blocking them cannot lower accuracy; the 8 ambiguous ones could turn into correct
clarifications. The parallel false block can cost at most that one case-trial (0.3 points of 330).
