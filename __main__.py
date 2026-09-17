"""One-command launcher: fetches the connectome and the flybody mesh if
needed, starts the engine and the frontend's static server, opens the
browser.

Run from the repo root with: python __main__.py
Ctrl+C stops both servers.
"""
from __future__ import annotations

import asyncio
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from engine import fetch_connectome, fetch_flybody, network
from engine import server as engine_server

FRONTEND_DIR = Path(__file__).parent / "frontend"
FRONTEND_PORT = 8080
ENGINE_PORT = 8765


def _start_frontend_server() -> ThreadingHTTPServer:
    def handler_factory(*args, **kwargs):
        return SimpleHTTPRequestHandler(*args, directory=str(FRONTEND_DIR), **kwargs)

    httpd = ThreadingHTTPServer(("127.0.0.1", FRONTEND_PORT), handler_factory)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _start_engine_thread() -> None:
    def run():
        asyncio.run(engine_server.main(port=ENGINE_PORT))

    threading.Thread(target=run, daemon=True).start()


def main() -> None:
    if not network.data_available():
        print("First run: fetching the real connectome data (~50 MB)...")
        fetch_connectome.fetch()

    if not fetch_flybody.flybody_available():
        print("First run: fetching the real flybody anatomical mesh (~81 MB)...")
        fetch_flybody.fetch()

    print("Starting the engine (loads the real connectome, ~10-15s)...")
    _start_engine_thread()

    httpd = _start_frontend_server()
    url = f"http://localhost:{FRONTEND_PORT}"
    print(f"Frontend serving at {url}")
    # give the engine a moment to at least start listening before opening
    # the tab -- not required (the frontend reconnects on its own either
    # way), just avoids landing on a "reconnecting..." flash.
    time.sleep(1)
    webbrowser.open(url)

    print("Running -- press Ctrl+C to stop both servers.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
        httpd.shutdown()


if __name__ == "__main__":
    main()
