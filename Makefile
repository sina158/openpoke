PYTHON ?= python

.PHONY: test

# Offline: no API key, no network.
test:
	$(PYTHON) -m pytest
