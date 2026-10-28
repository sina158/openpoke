<!-- DRAFT-FOR-SINA: facts are checked; rewrite the prose in your own words before submitting -->

# Where it still fails

Real transcripts from the fix arm, one per failure class. Each is taken from a committed results file and replays
offline from the committed cassettes:

```bash
python -m evals.run_routing --arm fix --sizes <N> --cases <case_id> --replay
```

Every slot below is TBD until the final runs; fill it from the results file named in its header.

## 1. Pronoun follow-up after summarisation

- Case: TBD (`case_id`), category TBD, N = TBD, trial TBD. Results file: TBD.
- User message: TBD
- What the history held: TBD (note: the eval runs with summarisation off, so this class may need a hand-built case;
  say so if no committed case fits)
- Shortlist shown (ids, names, pins): TBD
- Tool call made: TBD
- Gold: TBD
- Why it failed: TBD
- What would fix it: TBD

## 2. Near-duplicate business name

- Case: TBD, N = TBD, trial TBD. Results file: TBD.
- User message: TBD
- The two agents (names, cities or policy lines, ids): TBD
- Shortlist shown: TBD
- Tool call made: TBD
- Gold: TBD
- Cross-client: TBD (yes / no)
- Why it failed: TBD
- What would fix it: TBD

## 3. Entity-only query the card missed

- Case: TBD, N = TBD, trial TBD. Results file: TBD.
- User message (the identifier only): TBD
- Was the target on the shortlist? TBD (rank TBD; pinned TBD)
- The target's card entities: TBD
- Tool call made: TBD
- Gold: TBD
- Why it failed: TBD
- What would fix it: TBD

## 4. Clarify was right, the model guessed

- Case: TBD, N = TBD, trial TBD. Results file: TBD.
- User message: TBD
- The candidate agents (both targets): TBD
- Shortlist shown: TBD
- Tool call made: TBD
- Gold: clarify between TBD and TBD
- Why it failed: TBD
- What would fix it: TBD

## 5. Archived target, not searched

- Case: TBD, N = TBD, trial TBD. Results file: TBD.
- User message: TBD
- Target: TBD, last active TBD days before the eval clock (archived after 30 idle days)
- `search_agents` called? TBD (query TBD, results TBD)
- Tool call made: TBD
- Gold: TBD
- Why it failed: TBD
- What would fix it: TBD

## Where the baseline was as good as the fix

- Categories and sizes where the two tie: TBD (from the per-category table in `evals/results/RESULTS.md`).
- One example case where both are right: TBD.

## The embeddings result

- The hybrid scorer was worse than BM25 from N=50 up on this set, mostly on entity-only queries; numbers in
  `DESIGN.md` section 3 and `evals/results/SELECTOR.md`.
- One example query where the hybrid ranked the target out of the top 8 and BM25 did not: TBD.
