<!-- DRAFT-FOR-SINA: facts are checked; rewrite the prose in your own words before submitting -->

# Where it still fails

Real transcripts from the fix arm, one per failure class, all from
`evals/results/20261028_fix_minimax-m3_k8_all_773a8ac.json` (model minimax-m3, k=8, temperature 0, one trial
per case), all on the report split at N=500. Each replays offline from the committed cassettes and gives the
same cell:

```bash
python -m evals.run_routing --arm fix --sizes <N> --cases <case_id> --split all --model minimax-m3 --replay
```

The replay writes a `*_partial_<sha>.json` file under `evals/results/` (`--out-dir` puts it elsewhere).
Cassettes store only the response, keyed by a hash of the full request, so the shortlists below are the prompt
as the replay renders it; a cassette hit means it is the prompt that was recorded. `a_0499`-style ids are minted
per case and are what the model saw; the `g_0022`-style ids in brackets are the golden file's.

At N=500 the fix arm got 23 of the 86 report cases wrong, and 15 of those 23 turns sent the request to two or
more agents at once. Classes 1, 2 and 4 are that one behaviour from different sides.

## 1. Messaged the right agent and a second one (spray)

This replaces "pronoun follow-up after summarisation": the eval runs with summarisation off, and the fix arm got
every pronoun case right (10/10 on the report split at N=5, 50 and 500), so no committed case shows it. Spraying
is the most common fix-arm failure instead: 11 of the 14 wrong reuses at N=500.

- Case: `explicit-008` (template `explicit.t1`), N = 500, trial 0. Cell `wrong_reuse`, spray, not cross-client.
- User message: "what's the latest on the Lars Lachance auto quote?"
- Target: "Lars Lachance auto quote" [g_0022], last active 350 days before the eval clock, so archived and not on
  the shortlist.
- Shortlist shown (8 of 257 active, no pins): `a_0500` "Lars Lachance auto COI" first, then seven other
  clients' quote agents (`a_0214` "Hal Dunmore auto quote", `a_0410` "Tess Hartwood auto quote", ...).
- Searches: `search_agents("Lars Lachance auto quote")` returned `a_0499` "Lars Lachance auto quote" (archived)
  first and `a_0500` second; `search_agents("AU-6497-13")` (the COI's policy number) returned `a_0500`.
- The model's reasoning: "that's a_0499, but it's archived. ... I could message both in parallel to get the
  full picture."
- Tool call made: `send_message_to_agent(agent_id="a_0499")` with "Give me a status update on the Lars Lachance
  auto quote (policy AU-6381-54, Fieldstone Underwriters). ..." and, in the same turn,
  `send_message_to_agent(agent_id="a_0500")` with "Give me a status update on the Lars Lachance certificate of
  insurance under auto policy AU-6497-13 (Granite Bay Assurance). ..."
- Gold: reuse `g_0022` (`a_0499` here) only.
- Why it failed: nothing was ambiguous (the user typed the agent's name) and the search found it, yet the model
  sent a second request for context. The prompt says to clarify when two agents could fit, and a line kept from
  the upstream prompt (`interaction_shortlist.md`, line 15) tells it to message agents in parallel as much as
  possible, which invites exactly this. At N=5 and N=50 the same case fails another way: no search, and the request goes to
  the COI agent alone. The baseline, which lists every name and archives nothing, got it right at all three sizes.
- What would fix it: a check in `send_message_to_agent`, shaped like the duplicate guard: when one assistant turn
  sends to two or more agents whose logs share an email address or a person or business name, send nothing and
  return both ids with "reuse one, or call clarify_agent_choice". Carrier names must not count: the two targets of
  `parallel-005` share only "fieldstone underwriters". Measured with `cards.extract_entities` over each agent's
  first request and last response, carrier names removed, the agents messaged together share such an entity in
  all 15 multi-agent wrong turns at N=500 and in none of the 3 correct ones (the parallel cases). The turns after
  the check need a new recorded run.

- v2 on this case: `correct_reuse`: one send, to `a_0499` (the right agent, found by `search_agents`); no second send. From `evals/results/continuity/20261028_fix_v2_minimax-m3_k8_report_n500_t1_accf492.json` (fix v2 on the v1 set, one trial, N=500). Replay: `python -m evals.run_routing --set v1 --arm fix_v2 --sizes 500 --cases explicit-008 --split all --model minimax-m3 --replay`

## 2. Near-duplicate business name

- Case: `near_duplicate-006` (`near_duplicate.t1`), N = 500, trial 0. Cell `wrong_reuse`, spray.
- User message: "the Maplestone in Barrie needs their certificate resent, the site manager is asking"
- The two agents, both last active 7 days ago, different clients:
  - target "Maplestone Plumbing & Heating COI" [g_0222], `a_0500` here: furnace and pipe contractor in Barrie,
    CGL-9652-23 with Lighthouse Point Insurance, contact Emerson Beaulieu;
  - "Maplestone Plumbing COI" [g_0221], `a_0499`: pipe and drain contractor in Sarnia, CGL-9485-83 with Northern
    Shield Insurance, contact Emerson Ferreira.
- Shortlist shown (8 of 257, no pins): `a_0499` and `a_0500` first and second, then six other clients' agents
  (`a_0247` "Delta Landscaping COI", `a_0489` "Gwen Marshley auto COI", ...). Both purposes stop before the
  city: "Issue a certificate of insurance under the CGL policy for Maplestone Plumbing, a pipe and drain
  contractor in..." and "... for Maplestone Plumbing & Heating, a furnace and pipe...". Neither card lists a city.
- Searches: `search_agents("Maplestone Barrie")` and `search_agents("Maplestone Plumbing certificate of
  insurance")` returned the same two cards with the same cut purposes. The model: "There are two Maplestone
  agents, and I don't know which one is in Barrie. The purpose strings are truncated."
- Tool call made: the same request to `a_0499` and to `a_0500`: "Please look up your file and report back (no
  emails, no drafts, read-only): the insured's full legal name and city/location, ..."
- Gold: reuse `g_0222` only.
- Cross-client: yes. The request was read-only, but it still reached another client's agent. In the one-trial run over
  all 130 cases, all 4 cross-client wrong reuses of the fix arm at N=500 are sprays on this template (`near_duplicate-002`, `-006`, `-010`, `-014`).
  The baseline also messaged both, at all three sizes.
- Why it failed: the city is the only thing that tells the two apart, and the card drops it. `_purpose_from_log`
  in `server/services/execution/cards.py` clips the first request with `_clip(..., 120)`, which keeps at most
  117 characters. "Barrie" starts at offset 122, past the cut. "Sarnia." ends at offset 117, exactly at the cut,
  and `_clip` still cuts back to the previous space, so it drops a word that was already complete. The name
  pattern needs two capitalised words, so one-word cities never become entities (two-word ones such as "parry
  sound" do). Search returns the same card, so it could not help.
- What would fix it: make `_clip` keep the last word when the cut falls on a word boundary, and clip the first
  request at the end of its first sentence instead of at 120 characters (every request `World.matter` writes
  ends that sentence with the city; the longest first sentence in the pool is 149 characters). Add a card test
  with this pair asserting "Barrie" and "Sarnia" in the purposes. A card change changes the prompt, so the arm
  needs re-recording.

- v2 on this case: `correct_reuse`: one send, to `a_0500`; no spray. From `evals/results/continuity/20261028_fix_v2_minimax-m3_k8_report_n500_t1_accf492.json` (fix v2 on the v1 set, one trial, N=500). Replay: `python -m evals.run_routing --set v1 --arm fix_v2 --sizes 500 --cases near_duplicate-006 --split all --model minimax-m3 --replay`

## 3. A new matter sent to an old agent (hijack)

This replaces "entity-only query the card missed": no entity case failed in the fix arm at any size, on either
split, so there is no committed case. Hijacks are a frequent failure the outline did not list: 4 of the 13
should-create cases at N=500 (6 at N=5, 5 at N=50).

- Case: `should_create-002` (`should_create.t1`), N = 500, trial 0. Cell `hijack`.
- User message: "Mara Fairweather had a pipe burst in the basement, start a claim"
- The client's only agent: "Mara Fairweather home renewal" [g_0253], accountant in Huntsville, home policy
  HO-7515-91 with Fieldstone Underwriters, last active 45 days ago, so archived.
- Shortlist shown (8 of 245, no pins): eight other clients' claim agents (`a_0145` "Uma Brookmore auto claim",
  `a_0102` "Alder Electrical claim", ...). Nothing for Fairweather.
- Searches: `search_agents("Mara Fairweather")` and `search_agents("Fairweather")` each returned only `a_0250`
  "Mara Fairweather home renewal" (archived).
- The model: "That's the same person. I should reuse that agent since it's the same matter (same person, same
  policy)."
- Tool call made: `send_message_to_agent(agent_id="a_0250")` with "Mara Fairweather (accountant in Huntsville,
  ...) has had a pipe burst in her basement. Please open a new home claim for this loss, following the same
  process you've used on the renewal matter for this policyholder. ..."
- Gold: create a new agent.
- Why it failed: search did its job and found the client's agent; the reuse rule then allowed it.
  `interaction_shortlist.md` says "Reuse an agent only when its card clearly matches the same person, thread or
  matter", so a same-person match satisfies the wording. The duplicate guard runs only on creates, so nothing
  checks a reuse. The baseline also reused at all three sizes; prompt_only created a new agent at all three.
- What would fix it: change that line to require the same matter ("the same person with a different task, such
  as a claim where the agent handled a renewal, needs a new agent"), re-record the arm, and check that the
  duplicate column does not rise.

- v2 on this case: `correct_create`: a new agent "Mara Fairweather home claim"; no hijack. From `evals/results/continuity/20261028_fix_v2_minimax-m3_k8_report_n500_t1_accf492.json` (fix v2 on the v1 set, one trial, N=500). Replay: `python -m evals.run_routing --set v1 --arm fix_v2 --sizes 500 --cases should_create-002 --split all --model minimax-m3 --replay`

## 4. Clarify was right, the model messaged both

- Case: `ambiguous-002` (`ambiguous.t1`), N = 500, trial 0. Cell `wrong_guess`.
- History: the user said "I'm going through Larkspur Florist's files this morning"; the assistant replied "Sounds
  good, tell me what you need."
- User message: "resend their COI to the general contractor"
- The candidate agents (both targets), same contact (Emerson Lund, office@larkspurflorist.example.com):
  "Larkspur Florist COI - Bay St lobby" [g_0295], `a_0250` here, CGL-9527-57 with Maplecrest Mutual, last active
  0h ago; "Larkspur Florist COI - Mill Rd warehouse" [g_0294], `a_0251`, CP-4961-19 with Granite Bay Assurance,
  7 days ago.
- Shortlist shown (8 of 254, no pins): `a_0250` and `a_0251` first and second, then `a_0135` "Slate Florist
  COI", `a_0249` "Oxbow Plumbing COI" and four other clients' agents.
- Tool call made: first turn, `send_message_to_user` with "On it — digging up the Larkspur COI details and who
  the general contractor was. Two Larkspur COIs are on file (Bay St lobby and Mill Rd warehouse), so I'm checking
  both." and `search_agents("Larkspur Florist")`, which returned the same two. Second turn,
  `send_message_to_agent` to `a_0250` and to `a_0251`, each "Retrieve the details of the certificate of insurance
  you issued for Larkspur Florist (...). ... Do not send any email yet - just report back."
- Gold: clarify between `g_0294` and `g_0295`.
- Why it failed: not retrieval. The model saw both candidates and told the user there were two. The prompt says
  "If two or more agents could fit and the message does not settle which one, call
  `clarify_agent_choice(candidate_ids, question)` to ask the user instead of guessing", and the model did not
  treat asking both agents as guessing. 4 of the 5 wrong ambiguous cases at N=500 sent to both agents; the fifth
  (`ambiguous-005`) saw only one candidate, because the other was archived (class 5).
- What would fix it: the check from class 1 catches this turn (both cards carry office@larkspurflorist.example.com
  and "emerson lund"); its message should name `clarify_agent_choice` and both ids. The eval already records what
  the model does after a guard (`guard_outcome`), so the same columns would show whether it then asks.

- v2 on this case: `correct_clarify`: `clarify_agent_choice` with both candidates (`g_0295`, `g_0294`); nothing sent. From `evals/results/continuity/20261028_fix_v2_minimax-m3_k8_report_n500_t1_accf492.json` (fix v2 on the v1 set, one trial, N=500). Replay: `python -m evals.run_routing --set v1 --arm fix_v2 --sizes 500 --cases ambiguous-002 --split all --model minimax-m3 --replay`

## 5. Archived target, not searched

- Case: `near_duplicate-016` (`near_duplicate.t3`), N = 500, trial 0. Cell `wrong_reuse`, wrong agent only.
- User message: "pull the terms we agreed for Hollowtree Dental Clinic last year so we can compare"
- Target: "Hollowtree Dental Clinic renewal 2025" [g_0241], last active 350 days before the eval clock (archived
  after 30 idle days). The other: "Hollowtree Dental Clinic renewal 2026" [g_0242], last active 3 days ago.
- Shortlist shown (8 of 251 active, no pins): `a_0250` "Hollowtree Dental Clinic renewal 2026" first, then
  `a_0407` "Yarrow Dental Clinic COI", `a_0168` "Topaz Dental Clinic billing", `a_0357` "Kelp Dental Clinic
  billing" and four unrelated agents. Last line: "243 more agents are not shown. To find one, call
  search_agents(query) with a name, email, id or topic."
- `search_agents` called? No.
- Tool call made: `send_message_to_agent(agent_id="a_0250")` with "Please pull the full record for Hollowtree
  Dental Clinic (policy cp-6650-98, ...) from last year. I need the terms we originally agreed to, ..."
- Gold: reuse `g_0241`.
- Why it failed: a shown card matched the client's name, so the model did not search. The shortlist is built from
  `registry.list("active")` and its last line counts only hidden active agents, so nothing in the prompt says an
  archived agent exists; "last year" against "renewal 2026" was the only hint. Same at N=5 and N=50. Across the
  report split at N=500 the fix arm is right on 21 of 35 cases whose target is archived and 33 of 38 whose
  target is active (`RESULTS.md`, "Archived targets").
- What would fix it: in `_render_shortlist` (`server/agents/interaction_agent/agent.py`), also score the archived
  cards and append the best one, rendered with `status="archived"`, when it outscores the last card shown. A
  selector test on this pair would show whether the 2025 card makes the cut.

- v2 on this case: `correct_reuse`: one send, to `a_0251`; the archived target was found. From `evals/results/continuity/20261028_fix_v2_minimax-m3_k8_report_n500_t1_accf492.json` (fix v2 on the v1 set, one trial, N=500). Replay: `python -m evals.run_routing --set v1 --arm fix_v2 --sizes 500 --cases near_duplicate-016 --split all --model minimax-m3 --replay`

## Where the baseline was as good as the fix

From "Accuracy by category (report split)" in `evals/results/RESULTS.md`:

- Ties at every N: pronoun (10/10 both) and parallel (3/3 both).
- Ties at one N: explicit at N=5 (10/13 both), near_duplicate at N=500 (6/15 both), ambiguous at N=5 and N=50
  (0/6 both).
- The baseline was ahead on near_duplicate at N=5 (5/15 vs 3/15) and N=50 (8/15 vs 5/15), and on explicit at
  N=500 (9/13 vs 8/13). Each category has 2 or 3 templates, so gaps of one to three cases are within noise.
- One case where both are right: `pronoun-002` (`pronoun.t1`), N = 500, trial 0. The history ends with the agent
  message "[SUCCESS] Email to Ruben Lindqvist: Ruben replied on thread 32ceea55dc08896a: "Should I add the new
  car from the 1st?"" and the reply "Ruben asked: ... Want me to say yes?"; the user then says "ok, let him know
  that works". The baseline reused "Email to Ruben Lindqvist" [g_0169] by retyping its name from a list of 500;
  the fix reused it by id (`a_0250`, first on the shortlist with `pinned="recent"`). Both `correct_reuse`;
  prompt tokens 9,052 (baseline) and 4,990 (fix). The recent turns name the agent, so neither arm needs the
  roster. It replays with `--arm baseline` as well as `--arm fix`.
- One case where the baseline beat the fix: `explicit-008` (class 1), right in the baseline at all three sizes
  and wrong in the fix at all three, because archiving took the named agent off the shortlist.

## The embeddings result

- The hybrid scorer was worse than BM25 from N=50 up on this set, mostly on entity-only queries; numbers in
  `DESIGN.md` section 3 and `evals/results/SELECTOR.md`.
- Example: `entity-006` (`entity.t2`, report split), N = 500: "the carrier wrote back on thread b9b73287feb0f060,
  can you pick that up again?" The target, "Cedarvale Veterinary endorsement" [g_0076], has that thread id in its
  log. Its rank in `evals/results/20261028_selector_c488caa.json`: hybrid 21, the hybrid's own lexical input
  (`bm25_entities_recency`) 5, `plus_pins` 1. The hex id is the message's only distinctive token: the lexical
  scorer matches it exactly, and fusing in the cosine ranking moved the target from 5 to 21. Pins are off in the
  hybrid row; in the live shortlist the thread-id pin puts this agent first under either scorer.

## A caveat on the golden data

Found while pulling these cases, not fixed: `World.matter` in `evals/generate.py` picks the policy line at random
even when the caller passes a name that says "auto" or "home", so some agents are named for one line while their
log describes the other. 8 golden agents have this, in `near_duplicate-003`, `-007`, `-011`, `-015` and
`should_create-005`, `-011`, `-017`, `-020`; for example "Ines Pemberton auto endorsement" [g_0239] says "Process a
home endorsement ... Home policy HO-3817-65". None of the cases above is affected. Fixing it changes the golden
file and invalidates every cassette.

## Closed in v2

Closed means: the case above is right under fix v2 AND the class rate in the continuity run is zero. The continuity run
is round one's report split, one trial, so it is not blind for v2 (design changes were made from these failures).

- **Class 1 and 2 (spray), on the v1 set only.** `explicit-008` and `near_duplicate-006` pass, and the continuity run
  has 0 sprays among its should-reuse turns (`evals/results/continuity/RESULTS.md`). **Not closed on the fresh set:**
  fix v2 sprayed across clients in 11 of 252 case-trials (B2b, `evals/results/RESULTS_v2.md`), so the check misses
  the shape the fresh set contains (see the new classes below).
- **Class 4 (clarify was right, the model messaged both), on this case only.** `ambiguous-002` clarifies. On the fresh
  set clarification is rare (new class B below), so this is a case fixed, not a behaviour fixed.

Replay each with the command printed under its transcript. Classes 3 (hijack: the continuity run still has 1 hijack among
13 should-create turns) and 5 (archived target) stay open above, with this case's v2 outcome filled in.

## New failure classes from the fresh set

From `evals/results/RESULTS_v2.md` only, fix v2, N=500, report split, three trials (`evals/golden/routing_v2.jsonl`).
Per-case transcripts are not pulled yet (each class needs one, written as above, once the label review is done).

- **A. Sends that reach two clients.** 19 of 312 turns on cases whose gold does not call for two (B2a), and 11 of 252
  should-reuse case-trials reached another client's agent, all of them sprays (the right agent plus another). The
  check refuses only targets that look like the same client; two different clients are not refused.
- **B. Clarification almost never happens.** 2 of 30 ambiguous case-trials clarify (6.7%); the other 28 were graded as something else (the cell counts are in `report.by_key.fix_v2_minimax-m3_k8_n500.cells`, not split by category).
- **C. Archived targets are still harder.** Accuracy 73.0% (127/174) when the gold target has been idle more than 30
  days against 92.6% (100/108) when it is active, a gap of 19.6 points; fix v1 had 64.4% (112/174) against 80.6% (87/108), a gap of 16.4 points. v2 raised both rows and the gap grew.
- **D. Tokens.** A turn sends 103.0% of the baseline's prompt tokens (B5): the cost of the search round is not paid for
  by the smaller roster at N=500.
