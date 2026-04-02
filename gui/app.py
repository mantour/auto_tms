"""Desktop app entry point: GUI (no args) or CLI (with args)."""

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


def _launch_gui() -> None:
    """Launch the desktop GUI window."""
    import webview

    port = _find_free_port()

    server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
    server_thread.start()

    webview.create_window(
        "auto_tms",
        f"http://127.0.0.1:{port}",
        width=900,
        height=700,
        min_size=(700, 500),
    )
    webview.start()


def _attach_console() -> None:
    """On Windows, reattach to the parent console for CLI output.

    PyInstaller builds with console=False have no console attached,
    so stdout/stderr are lost. This restores them when running in CLI mode.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import os

        ATTACH_PARENT_PROCESS = -1
        kernel32 = ctypes.windll.kernel32
        if kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            sys.stdin = open("CONIN$", "r", encoding="utf-8")
            sys.stdout = open("CONOUT$", "w", encoding="utf-8")
            sys.stderr = open("CONOUT$", "w", encoding="utf-8")
    except Exception:
        pass


def main() -> None:
    """Entry point: no args → GUI, with args → CLI."""
    if len(sys.argv) > 1:
        _attach_console()
        from auto_tms.cli import cli
        cli()
    else:
        _launch_gui()


if __name__ == "__main__":
    main()
