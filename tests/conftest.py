"""Shared test setup: every test session writes to a throwaway data dir and never needs an API key."""

import os
import tempfile

# Must happen before anything under server/ is imported: the stores resolve their paths at import time.
_SESSION_DATA_DIR = tempfile.mkdtemp(prefix="openpoke-test-")
os.environ["OPENPOKE_DATA_DIR"] = _SESSION_DATA_DIR
os.environ.pop("OPENROUTER_API_KEY", None)
os.environ.pop("OPENPOKE_LLM_API_KEY", None)
