"""Desktop GUI entry point: FastAPI server + pywebview window."""

import asyncio
import socket
import sys
import threading

import uvicorn


def _find_free_port() -> int:
    """Find a random available port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server(port: int) -> None:
    """Run the FastAPI server in a background thread."""
    from gui.api import app

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    loop.run_until_complete(server.serve())


def main() -> None:
    """Launch the desktop app."""
    import webview

    port = _find_free_port()

    # Start FastAPI server in background thread
    server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
    server_thread.start()

    # Open native window
    webview.create_window(
        "auto_tms",
        f"http://127.0.0.1:{port}",
        width=900,
        height=700,
        min_size=(700, 500),
    )
    webview.start()


if __name__ == "__main__":
    main()
