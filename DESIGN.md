<!-- DRAFT-FOR-SINA: facts are checked; rewrite the prose in your own words before submitting -->

# Design

Numbers here are marked like the README's, so `python -m evals.check_readme_numbers --readme DESIGN.md` checks
them. Selector numbers come from the offline selector eval (`evals/results/SELECTOR.md`, all 130 cases, 110 with a
target agent). Live routing numbers come from `evals/results/RESULTS.json`: report split, model minimax-m3, three
trials for baseline and fix at N=500 and one for every other run.

## 1. A turn, in order

Lookup first, retrieval second, the model last.

1. `lifecycle.on_turn_start`: archive agents idle for 30 days (shortlist mode only, at most once an hour).
2. `selector.pins`: agents that must be shown whatever their score (section 6).
3. `selector.select`: score the rest by BM25 + entity overlap + recency, add by score until k or the token budget.
4. `agent._render_shortlist`: render the cards as `<active_agents mode="shortlist" shown=".." total="..">`, plus a
   line saying how many agents are hidden and that `search_agents(query)` reaches them.
5. The model decides: reuse by `agent_id`, create by `agent_name` (duplicate check first), `clarify_agent_choice`,
   or `search_agents` and then one of those.

```
score(a) = 1.0 * bm25(q, card_text(a)) + 2.0 * |entities(q) & entities(a)| + 0.5 * exp(-age_days(a) / 14)
q = the current message + the last two user turns
order = pins (max 4), then score desc, then id   -> stop at k=8 or 1,200 tokens (chars / 4); first card always kept
```

## 2. Why k=8 and a 1,200-token budget

- k is the shortlist size (`OPENPOKE_SHORTLIST_K`, default 8). Up to 4 of the 8 can be pins (`max_pins=4`), so at
  least 4 slots always go to the best scores.
- Recall does not need 8 on this set. At N=500 without pins, recall@k is
  <!-- n:selector.recall.bm25_entities_recency.n500.k3 -->0.973<!-- /n --> at k=3,
  <!-- n:selector.recall.bm25_entities_recency.n500.k5 -->0.991<!-- /n --> at k=5,
  <!-- n:selector.recall.bm25_entities_recency.n500.k8 -->0.991<!-- /n --> at k=8 and
  <!-- n:selector.recall.bm25_entities_recency.n500.k12 -->1.000<!-- /n --> at k=12; with pins it is
  <!-- n:selector.recall.plus_pins.n500.k3 -->1.000<!-- /n --> already at k=3.
- k=8 is for what the offline eval does not see: several pins at once (a pronoun follow-up while a send is pending
  and another agent just reported) can take half the list. The plan rejected 3 candidates as too few for pronoun
  follow-ups. Whether a longer list lowers the model's choice accuracy is what the live k-sweep measures: fix arm
  accuracy at N=500 is <!-- n:report.by_key.fix_minimax-m3_k3_n500.accuracy.rate -->70.9%<!-- /n --> at k=3,
  <!-- n:report.by_key.fix_minimax-m3_k8_n500.accuracy.rate -->73.3%<!-- /n --> at k=8 and
  <!-- n:report.by_key.fix_minimax-m3_k16_n500.accuracy.rate -->73.3%<!-- /n --> at k=16. k=3 and k=16 are one trial
  each and k=8 three, and the three lie inside each other's Wilson intervals: on this set the size of the list
  makes little difference either way.
- The budget: the plan said 600 tokens. A rendered card is about 65-130 tokens (`server/config.py` comment), so 600
  would cut k=8 to about 5-6 cards. 1,200 lets 8 cards fit and only trims outliers. The shortlist block averages
  <!-- n:selector.tokens.n500.shortlist_mean -->769<!-- /n --> tokens at N=500 against
  <!-- n:selector.tokens.n500.full_mean -->5,499<!-- /n --> for every name. The whole prompt per turn is not smaller
  for it: the fix makes <!-- n:report.by_key.fix_minimax-m3_k8_n500.llm_calls.mean -->1.83<!-- /n --> model calls per turn
  at N=500, mostly to read `search_agents` results, so a turn sends
  <!-- n:bars.prompt_token_ratio_n500 -->102.5%<!-- /n --> of the baseline's prompt tokens (README, bar 5, which fails).
- Cards cost more than names at small N: <!-- n:selector.tokens.n5.shortlist_mean -->512<!-- /n --> vs
  <!-- n:selector.tokens.n5.full_mean -->55<!-- /n --> tokens at N=5, and
  <!-- n:selector.tokens.n50.shortlist_mean -->838<!-- /n --> vs <!-- n:selector.tokens.n50.full_mean -->552<!-- /n -->
  at N=50. The block gets smaller than the name list somewhere between 50 and 500 agents.
- No "no match" score threshold: on should-create cases the best score any agent reaches rises with N (mean
  <!-- n:selector.create_best_score.plus_pins.n5.mean -->6.51<!-- /n --> at N=5,
  <!-- n:selector.create_best_score.plus_pins.n5000.mean -->29.89<!-- /n --> at N=5,000), so one cut-off cannot work
  across roster sizes. The create decision stays with the model plus the duplicate check.

## 3. Why BM25 first, and what the embeddings ablation showed

- Broker messages carry identifiers: emails, policy numbers, thread ids, business names. BM25 matches the exact token.
  The `BM25` class in `selector.py` is about 40 lines, has no dependency, is deterministic and runs offline.
- The hybrid was built anyway, behind `OPENPOKE_SELECTOR=hybrid`: `BAAI/bge-small-en-v1.5` through `fastembed` on the
  CPU (no API key; `requirements-embeddings.txt`), an int8 vector cache committed at
  `evals/fixtures/embeddings_cache.bin`, and reciprocal rank fusion (k=60) of the full lexical ranking with the cosine
  ranking (`embeddings.hybrid_scores`). The plan said OpenRouter's embeddings endpoint; a local model was used so the
  offline eval needs no key.
- Result: the hybrid is worse than its own lexical input from N=50 up. recall@8:
  <!-- n:selector.recall.hybrid.n50.k8 -->0.964<!-- /n --> vs
  <!-- n:selector.recall.bm25_entities_recency.n50.k8 -->1.000<!-- /n --> at N=50,
  <!-- n:selector.recall.hybrid.n500.k8 -->0.918<!-- /n --> vs
  <!-- n:selector.recall.bm25_entities_recency.n500.k8 -->0.991<!-- /n --> at N=500,
  <!-- n:selector.recall.hybrid.n5000.k8 -->0.873<!-- /n --> vs
  <!-- n:selector.recall.bm25_entities_recency.n5000.k8 -->0.955<!-- /n --> at N=5,000. MRR is lower at every N,
  N=5 included (<!-- n:selector.recall.hybrid.n5.mrr -->0.923<!-- /n --> vs
  <!-- n:selector.recall.bm25_entities_recency.n5.mrr -->0.953<!-- /n -->).
- Where it loses: entity-only queries. recall@8
  <!-- n:selector.recall.hybrid.n500.by_category.entity.k8 -->0.533<!-- /n --> vs
  <!-- n:selector.recall.bm25_entities_recency.n500.by_category.entity.k8 -->0.933<!-- /n --> at N=500, and
  <!-- n:selector.recall.hybrid.n5000.by_category.entity.k8 -->0.467<!-- /n --> vs
  <!-- n:selector.recall.bm25_entities_recency.n5000.by_category.entity.k8 -->0.667<!-- /n --> at N=5,000. On
  paraphrase, the category embeddings were meant to help, both score
  <!-- n:selector.recall.hybrid.n500.by_category.paraphrase.k8 -->1.000<!-- /n --> at N=500.
- Caveat: paraphrase cases are template-built (business type + city + a workflow synonym) and the card carries the
  type and the city, so this set does not test hard paraphrase. Real traffic could change the answer.
- Decision: `bm25` is the default; `hybrid` stays a flag, with its tests skipping when the model is not installed.

## Retrieval and the audit surface

<!-- SLOT-RETRIEVAL: one paragraph on retrieval (what ranks the shortlist and why archived agents now compete in it), filled after the v2 runs. -->

<!-- SLOT-AUDIT: one sentence on the audit surface (what is recorded per send so a reviewer can reconstruct it). Retrieval and audit together stay under 200 words. -->

## 4. Why names are not identity

- Upstream an agent is a string in `roster.json` (`list[str]`). Reuse means the model retypes the name. The log file
  is the slug of the name, so two names with one slug share a log. The save truncated the file before taking the
  lock, and `fcntl` made the module Unix-only.
- Now `registry.AgentRegistry` (SQLite under `OPENPOKE_DATA_DIR`): ids `a_0001`, ... minted from a counter that
  `clear()` keeps, so a stale id never lands on a new agent; `slug` is `UNIQUE`; `get_or_create` matches by slug;
  `create` adds " (2)", " (3)" to the name, not only the slug, because logs and triggers are keyed by name.
- The tool takes `agent_id` to reuse; `agent_name` creates (a name whose slug matches an existing agent reuses it). An
  unknown id returns an error that says to use an id from `<active_agents>` or `search_agents`.
- The plan's "names mean nothing" option was not taken: the id was added and the name kept as the display label and
  the create path, so `send_message_to_agent` keeps its upstream shape and the baseline arm can use the upstream schema.
- Grading follows: the fix is graded by id; the baseline's reuse by name is resolved through the slug
  (`evals/run_routing.py`, `resolve_call`).

## 5. Why wrong reuse outranks duplicates

- A duplicate is a second thread for one matter: wasteful, recoverable. A wrong reuse puts one client's thread ids,
  drafts and details into another client's work: a disclosure (a recordable breach under PIPEDA), not a quality bug.
- Where the design shows it:
  - The prompt rule (`prompts/interaction_shortlist.md`, replacing upstream line 19): reuse only when the card clearly
    matches the same person, thread or matter; "a similar name or topic is not enough".
  - `clarify_agent_choice(candidate_ids, question)`: asking is a real, gradable outcome.
  - The duplicate check (`tools.possible_duplicates`, shortlist prompt only) only advises: nothing is sent, the model
    gets `possible_duplicates` with a reason each, then reuses by id or passes `confirm_new=true`. It never reuses on
    its own. A card matches on name similarity (Jaccard of word sets >= `OPENPOKE_DUPLICATE_THRESHOLD`, default 0.85)
    or on a shared thread, draft, message or policy id; a shared email does not count, since a client's address is on
    all of its matters. Replayed offline on the baseline's results it flagged 19 of 27 real duplicates and 0 of 35
    correct creates (commit 424189f message; not in a results file).
  - The name threshold is not what catches duplicates. A sweep on the held-out split
    (`evals/results/DUPLICATE_THRESHOLD.md`) gives the same counts at every threshold from 0.55 to 0.95: the closest a
    duplicate's name came to its gold agent's name was 0.20. The shared-id check is what catches them. The server
    default stays 0.85.
  - Grading: wrong reuse is its own cell, the cross-client part is counted apart, and a zero is reported as a
    rule-of-three upper bound, never as "0%" (`evals/stats.py`, `rule_of_three`).
- Baseline at N=500: wrong reuse <!-- n:report.by_key.baseline_minimax-m3_k8_n500.wrong_reuse.rate -->36.3%<!-- /n -->,
  duplicates <!-- n:report.by_key.baseline_minimax-m3_k8_n500.duplicate.rate -->13.4%<!-- /n -->; fix:
  <!-- n:report.by_key.fix_minimax-m3_k8_n500.wrong_reuse.rate -->20.4%<!-- /n --> and
  <!-- n:report.by_key.fix_minimax-m3_k8_n500.duplicate.rate -->0.0%<!-- /n --> (three trials each).
- The prompt rule did not bring cross-client wrong reuse to zero: the fix still sent
  <!-- n:report.by_key.fix_minimax-m3_k8_n500.cross_client.wrong_reuse -->15<!-- /n --> of
  <!-- n:report.by_key.fix_minimax-m3_k8_n500.cross_client.wrong_reuse_n -->201<!-- /n --> should-reuse case-trials to
  another client's agent as well as the right one, so bar 3 fails. The new rule says to clarify when two agents
  could fit, but the upstream paragraph kept above it tells the model to call `send_message_to_agent` in parallel
  as much as possible (`interaction_shortlist.md`, line 15), and nothing stops a turn from messaging two clients'
  agents (FAILURES.md, class 1).

## 6. Lookup-first pins

- `selector.pins` order: `current` (the agent whose result is being handled, read from the `[SUCCESS] name:` or
  `[FAILED] name:` line of an agent message), `lookup` (agents whose card holds a thread, draft or message id the message quotes), `pending`
  (agents with a pending send), `recent` (agents whose id or multi-word name appears in the last three turns, latest
  mention first). At most 4; each agent once. A one-word name never pins ("Email" is too generic).
- Only thread, draft and message ids pin: they name one conversation. Emails and policy numbers are shared by every
  matter of a client (`cards.MAIL_ID` comment), so they add to the score (entity weight 2.0) but do not pin.
- Why it was needed: at N=5,000, entity-only recall@8 without pins is
  <!-- n:selector.recall.bm25_entities_recency.n5000.by_category.entity.k8 -->0.667<!-- /n -->; short cards with
  common words outscored the target in BM25. With pins it is
  <!-- n:selector.recall.plus_pins.n5000.by_category.entity.k8 -->1.000<!-- /n -->, and overall recall@8 at N=5,000
  goes from <!-- n:selector.recall.bm25_entities_recency.n5000.k8 -->0.955<!-- /n --> to
  <!-- n:selector.recall.plus_pins.n5000.k8 -->1.000<!-- /n -->. This fix was found by looking at report-split
  results, so the "with pins" number is not blind.
- Cards keep identifiers ahead of names: at most 8 ids (the most recently mentioned) and 4 names, so a log full of
  names cannot push out the thread id a user will quote back. Tool responses are left out of entity extraction: they
  hold raw search results, and every address in an inbox would become the agent's entity.

## 7. The approval gate and the consent code

- Propose: `gmail_reply_to_thread`, `gmail_forward_email` and `gmail_execute_draft` call `_propose_send`, which calls
  `ApprovalStore.propose(kind, args, agent_id, summary)` (`server/services/approvals.py`). Stored: state `proposed`,
  `args_hash` = sha256 of the sorted-key compact JSON of `[kind, args]`, a 4-character code from a 20-character
  alphabet with no look-alike characters (20^4 = 160,000 codes; unique among live actions), and a summary built in
  code from the arguments (`gmail.summarise_send`), never from the agent's description.
- Show: the shortlist variant renders `<pending_actions>` (`agent._render_pending_actions`): the newest 5 with id,
  code, kind, age and summary. The prompt tells the model to show the user the summary and the code.
- Approve: `confirm_action(action_id)` checks Gmail is connected, then `approve(action_id, provenance, turn_id,
  user_text)` refuses unless the turn's provenance is `user` and the text the user typed on that turn
  (`provenance.current_turn_text`) contains the code as a whole token, any case; the action must be `proposed` and
  younger than 24 h.
- Execute: `execute_approved_action` calls `consume`, which moves `approved` to `executed` in one UPDATE guarded by the
  state (of two racing callers only one wins) and recomputes the hash. It runs before the Composio call, so a retry
  after a timeout cannot send twice: at most once, not at least once.
- Provenance (`server/services/provenance.py`): a context variable that defaults to `None`, so a path that forgets to
  set it can never approve. User turns are `user`; execution-agent runs and their results are `agent`
  (`batch_manager`); the Gmail watcher is `email`. The current turn is escaped like history
  (`agent._render_current_turn`), so an email cannot close the tag and forge a user turn.
- Why a code and not just "a user turn": with turn-level approval, a user turn about something else (a calendar
  question) could be used to confirm a forward an email had injected. The code ties consent to one action the user
  was shown. A review found that attack; the injection suite now has it and it is refused.
- Clearing the chat history expires every pending send (`routes/chat.py`, `expire_pending`).
- The baseline arm (upstream prompt and tools) has no `confirm_action`, so a send there is proposed and never approved.
- What it does not cover is in KNOWN-GAPS.md: the draft body is not hashed, the summary reaches the user through the
  model, the HTTP API has no auth.

## 8. Lifecycle

- `lifecycle.archive_idle_agents`: runs only when `OPENPOKE_ROSTER_MODE=shortlist`, at most once an hour per data dir;
  archives agents idle longer than `OPENPOKE_ARCHIVE_IDLE_DAYS` (30); never an agent that owns an active trigger or a
  pending send (`protected_agent_ids`).
- `on_turn_start` runs before every user and agent turn (`InteractionAgentRuntime._start_turn`) and before each case in
  the routing eval, so the eval measures what ships.
- An archived agent leaves the shortlist (`registry.list("active")`), stays in `search_agents` (`list(status=None)`),
  and is revived when `send_message_to_agent` reuses it or the batch manager runs it, e.g. on a trigger
  (`batch_manager._mark_active`).
- Cards are rebuilt on dispatch and after each run (`card_store.refresh_card`) and stored in the registry, so a turn
  reads no logs: about 131 ms per turn at 500 agents before, about 10 ms after (commit 31c5297 message).
- Execution-agent history: `conversation_limit` is now passed (`OPENPOKE_EXECUTION_HISTORY_LIMIT`); unset keeps the
  upstream behaviour (all history). `OPENPOKE_EXECUTION_STUB=1` returns canned results and still writes the log, for
  simulations.
- In the eval, pool agents were last active 0, 3, 7, 45 or 350 days ago; about half are older than 30 days, so the fix
  arm sees them only through `search_agents`. At N=500 it was right on
  <!-- n:report.by_key.fix_minimax-m3_k8_n500.archived_target.archived.rate -->60.0%<!-- /n --> of cases whose target was
  archived against <!-- n:report.by_key.fix_minimax-m3_k8_n500.archived_target.active.rate -->86.0%<!-- /n --> when it was active.

## 9. What was rejected, and why

- Vector database, LangChain, LangGraph, any new framework: before cards there was nothing to embed; heavy
  dependencies in a small repo; the diff has to be reviewable; and the measured hybrid lost (section 3).
- A router model on the hot path: one more call per turn and evals that do not replay; cards are needed anyway. It is
  the next step only if a measured miss rate asks for it. The extra-call argument is weaker than it looked: the fix
  already makes <!-- n:report.by_key.fix_minimax-m3_k8_n500.llm_calls.mean -->1.83<!-- /n --> calls per turn at N=500
  through `search_agents`.
- Merging agents: a wrong merge corrupts memory, and merges cannot be evaluated in the time available.
- A cap of 6 active agents and at most 3 candidates: the cap changes product behaviour; 3 is too few for pronoun
  follow-ups. k is measured instead.
- Rolling per-agent LLM summaries: not now; the card plus `conversation_limit` is the bounded version.
- A score threshold for "nothing matches": scores grow with N (section 2).
- Real Gmail or Composio in the eval: execution is stubbed; real mail buys nothing for the routing question.
- Multi-tenancy: flagged, with an `owner` column already in the `agents` table.
- A general outbox with idempotency keys: only the email case is covered (single-use approval); the rest is flagged.
- Frontend, personality, MCP: out of scope.

## 10. How the routing eval works, briefly

- `evals/run_routing.py` seeds a fresh data dir per case through the repo's own APIs, renders the real prompt, and runs
  the real `InteractionAgentRuntime._run_interaction_loop` with tool calls sent to a recorder. The first routing call
  ends the case; it is graded from its arguments into one cell: correct reuse, wrong reuse, duplicate, over-clarify,
  hijack, correct create, correct clarify, wrong guess, partial, no route, error.
- For the new-prompt arms a create goes through the server's own duplicate check; if it refuses, the model gets the
  refusal and one more turn, and that turn is graded. The baseline has no check, as upstream. `on_turn_start` runs
  before each case, so archiving is measured too.
- A clarify is correct only on cases labelled ambiguous, and only when its candidates include every target.
- Arms: `baseline` (full roster, upstream prompt), `prompt_only` (full roster with ids, new prompt and tools), `fix`
  (shortlist, new prompt and tools), and `cards_full` (roster mode `full_cards`: every card, no selection) for N <= 50.
- Cassettes key every call by sha256 of model, messages, tools, temperature and seed (`evals/cassette.py`); replay
  never touches the network and an unrecorded call fails.
- Gold labels come from the generator's tuples, never from a model (`evals/golden/LABELS.md`); template `t0` of each
  category is the held-out split for tuning, the rest is reported.
