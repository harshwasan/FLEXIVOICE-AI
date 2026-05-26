"""
Launcher that forces the Windows ProactorEventLoopPolicy *before* uvicorn
imports asyncio. This is required because the Claude Agent SDK spawns the
Claude Code CLI as a subprocess, which is not supported by the
WindowsSelectorEventLoop (uvicorn's default `--loop auto` on Windows).
"""
from __future__ import annotations
import asyncio
import os
import sys


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8002"))
    # Reload is OFF by default: on Windows the uvicorn reloader frequently
    # orphans the multiprocessing worker, leaving zombie servers holding the
    # port with stale code. Set RELOAD=1 only when actively iterating.
    reload = os.getenv("RELOAD", "0") == "1"

    lan_urls: list[str] = []
    if host in ("0.0.0.0", "::"):
        try:
            import socket as _socket
            for entry in _socket.getaddrinfo(_socket.gethostname(), None):
                ip = entry[4][0]
                if "." in ip and not ip.startswith("127."):
                    lan_urls.append(f"http://{ip}:{port}/")
        except Exception:
            pass

    print(
        f"\n=== FlexiLoans voice agent ===\n"
        f"Local : http://127.0.0.1:{port}/  (emi-reminder | soft-collections)",
        flush=True,
    )
    if lan_urls:
        print("LAN   : " + "  ".join(sorted(set(lan_urls))), flush=True)
    print(
        f"reload={reload}  policy={type(asyncio.get_event_loop_policy()).__name__}\n",
        flush=True,
    )

    uvicorn.run(
        "server.main:app",
        host=host,
        port=port,
        reload=reload,
        # `--loop asyncio` so uvicorn uses the (Proactor) policy we just set,
        # instead of forcing its own WindowsSelectorEventLoop, which cannot
        # spawn subprocesses (required by the Claude Code SDK).
        loop="asyncio",
        log_level="info",
    )


if __name__ == "__main__":
    main()
