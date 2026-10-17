"""Start the Place Assistant web UI locally.

Usage:
    python run_ui.py                 # http://127.0.0.1:8080
    python run_ui.py --port 9000
    python run_ui.py --reload        # restart on code changes while developing

Loads .env from the project root first, like run.py, because the package
reads its settings when it is imported. In a container, set the same
variables in the environment instead and run:
    uvicorn place_assistant.ui.server:app --host 0.0.0.0 --port $PORT
"""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import uvicorn  # noqa: E402 (must follow load_dotenv)


def main() -> None:
    """Reads host, port and reload options, then serves the UI with uvicorn."""
    parser = argparse.ArgumentParser(description="Run the Place Assistant web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow other machines")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    parser.add_argument("--reload", action="store_true", help="restart when code changes")
    args = parser.parse_args()

    print(f"Place Assistant UI: http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}")
    uvicorn.run("place_assistant.ui.server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
