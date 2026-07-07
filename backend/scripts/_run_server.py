"""Start uvicorn server with Windows SelectorEventLoop (required for asyncpg/psycopg).

Uvicorn's default loop="auto" on Windows hardcodes ProactorEventLoop.
Using loop="none" makes uvicorn respect asyncio event loop policy.
"""
import asyncio
import sys
from pathlib import Path

# Ensure backend/ is importable
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--log-level", default="warning")
    args = parser.parse_args()

    # Force SelectorEventLoop on Windows BEFORE importing uvicorn
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    import uvicorn
    # loop="none" → get_loop_factory() returns None → Runner uses asyncio.new_event_loop
    # which respects our WindowsSelectorEventLoopPolicy
    uvicorn.run("app:app", host=args.host, port=args.port, log_level=args.log_level, loop="none")
