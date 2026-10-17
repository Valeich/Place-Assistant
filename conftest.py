"""pytest options for the whole project. The tests live in place_assistant/tests/.

    pytest                   offline tests only: free, no network, a few seconds
    pytest --live            also tests that call Vertex AI (Gemini): small cost
    pytest --live --billed   also tests that call the Maps Platform: billed per place

This file sits at the project root, not in the tests folder, because .env must
be loaded before anything imports place_assistant (it reads settings on import),
and command-line options can only be added from a root conftest.
"""

import os
import sys
from pathlib import Path

import pytest

_ENV_FILE = Path(__file__).resolve().parent / ".env"
_LIVE_FLAGS = ("--live", "--billed")

# pytest imports the place_assistant package (through its tests' conftest)
# before any configure hook runs, and the package reads settings such as the
# Maps key on import. So .env is loaded here, as soon as this file is read.
if any(arg in _LIVE_FLAGS for arg in sys.argv[1:] + os.environ.get("PYTEST_ADDOPTS", "").split()):
    from dotenv import load_dotenv

    load_dotenv(_ENV_FILE)


def pytest_addoption(parser):
    """Adds --live and --billed."""
    parser.addoption("--live", action="store_true", help="also run tests that call Vertex AI (small cost)")
    parser.addoption("--billed", action="store_true", help="also run tests that call the Maps Platform (billed)")


def pytest_collection_modifyitems(config, items):
    """Skips live and billed tests unless their option was given."""
    live = config.getoption("--live") or config.getoption("--billed")
    billed = config.getoption("--billed")
    for item in items:
        if "billed" in item.keywords and not billed:
            item.add_marker(pytest.mark.skip(reason="spends Maps Platform budget; run with --billed"))
        elif "live" in item.keywords and not live:
            item.add_marker(pytest.mark.skip(reason="calls Vertex AI; run with --live"))
