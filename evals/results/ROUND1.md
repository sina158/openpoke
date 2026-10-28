# Round 1, as measured on 2026-10-28

Moved out of the README; the numbers are unchanged and are checked against `RESULTS.json` (`make check-numbers`).

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
