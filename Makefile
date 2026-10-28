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

.PHONY: test eval-replay eval-live injection compare

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
	$(PYTHON) -m evals.run_injection

# Summarise the routing result files into a table.
compare:
	$(PYTHON) -m evals.compare
