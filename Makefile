# Windows installs provide "python"; elsewhere it is usually "python3".
ifeq ($(OS),Windows_NT)
PYTHON ?= python
else
PYTHON ?= python3
endif
ARMS ?= baseline prompt_only fix
SIZES ?= 5,50,500
TRIALS ?= 1
# Spend cap for each arm's live run, in USD (three arms stay under the plan's $40).
BUDGET_PER_ARM ?= 13
# Extra flags for evals.run_routing, e.g. EVAL_ARGS="--model vendor/model --split all".
EVAL_ARGS ?=

.PHONY: test eval-replay eval-live injection compare check-numbers demo lifecycle

# Offline: no API key, no network.
test:
	$(PYTHON) -m pytest

# Offline: every arm from the committed cassettes; a call that was never recorded fails.
eval-replay:
	for arm in $(ARMS); do \
		$(PYTHON) -m evals.run_routing --arm $$arm --sizes $(SIZES) --trials $(TRIALS) --replay $(EVAL_ARGS) || exit 1; \
	done

# Live: needs OPENPOKE_LLM_API_KEY or OPENROUTER_API_KEY; records new calls into the cassettes.
eval-live:
	for arm in $(ARMS); do \
		$(PYTHON) -m evals.run_routing --arm $$arm --sizes $(SIZES) --trials $(TRIALS) --record \
			--budget-usd $(BUDGET_PER_ARM) $(EVAL_ARGS) || exit 1; \
	done

# Offline: replays the prompt-injection cases against the send gate.
injection:
	$(PYTHON) -m evals.run_injection --json evals/results/INJECTION.json

# Summarise the routing result files into a table.
compare:
	$(PYTHON) -m evals.compare

# Every marked number in the prose against its results file (v2: and continuity: keys read their own files).
check-numbers:
	$(PYTHON) -m evals.check_readme_numbers
	$(PYTHON) -m evals.check_readme_numbers --readme DESIGN.md
	$(PYTHON) -m evals.check_readme_numbers --readme evals/results/ROUND1.md
	$(PYTHON) -m evals.check_readme_numbers --readme evals/results/ROUND1-TABLES.md

# Offline: replays the scripted demo conversation (fix_v2, stub mode) into evals/results/TRANSCRIPT.md.
# Exits 0 on a complete replay and prints the cell verdict; DEMO_ARGS=--strict exits 1 when a turn misses its
# cell; a call missing from the cassette exits 2.
# DEMO_ARGS="--record --model vendor/model" records it live (needs a key).
DEMO_ARGS ?=
demo:
	$(PYTHON) -m evals.demo_conversation $(DEMO_ARGS)

# Offline: replays the 30-day lifecycle simulation for baseline and fix_v2 into evals/results/LIFECYCLE.md.
# LIFECYCLE_ARGS="--record --model vendor/model --days 15" records it live (needs a key).
LIFECYCLE_ARGS ?=
lifecycle:
	$(PYTHON) -m evals.run_lifecycle --arms baseline,fix_v2 $(LIFECYCLE_ARGS)
