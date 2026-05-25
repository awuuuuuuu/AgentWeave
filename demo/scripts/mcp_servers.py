"""启动所有 MCP Server。

运行：
    uv run python demo/scripts/mcp_servers.py

Ctrl+C 统一关闭。
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

_ROOT    = Path(__file__).parent.parent.parent
_MCP_DIR = Path(__file__).parent.parent / "mock_servers" / "mcp"

# AMAP key 映射：加载 .env 并将 AMAP_SERVICE_KEY → AMAP_API_KEY
def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for f in [_ROOT / "backend" / ".env", _ROOT / "frontend" / ".env.local"]:
        if f.exists():
            load_dotenv(f, override=False)
    if not os.environ.get("AMAP_API_KEY") and os.environ.get("AMAP_SERVICE_KEY"):
        os.environ["AMAP_API_KEY"] = os.environ["AMAP_SERVICE_KEY"]

_load_env()

SERVERS = [
    {"name": "ambulance_dispatch", "script": _MCP_DIR / "ambulance_dispatch.py", "port": 8102, "cwd": str(_ROOT)},
    {"name": "signal_control",     "script": _MCP_DIR / "signal_control.py",     "port": 8103, "cwd": str(_ROOT)},
    {"name": "warehouse",          "script": _MCP_DIR / "warehouse.py",           "port": 8104, "cwd": str(_ROOT)},
    {"name": "environment_sensor", "script": _MCP_DIR / "environment_sensor.py",  "port": 8105, "cwd": str(_ROOT)},
    {"name": "amap",               "script": _MCP_DIR / "amap.py",                "port": 8106, "cwd": str(_ROOT)},
    {"name": "fire_station",       "script": _MCP_DIR / "fire_station.py",        "port": 8107, "cwd": str(_ROOT)},
]

COLORS = ["\033[36m", "\033[32m", "\033[33m", "\033[35m", "\033[34m", "\033[31m"]
RESET  = "\033[0m"
BOLD   = "\033[1m"


async def _wait_for_port(port: int, timeout: float = 15.0) -> bool:
    """Poll until the port accepts a TCP connection or timeout elapses."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=1.0
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            await asyncio.sleep(0.5)
    return False


async def _stream(name: str, color: str, stream: asyncio.StreamReader) -> None:
    prefix = f"{color}[{name}]{RESET} "
    while True:
        line = await stream.readline()
        if not line:
            break
        print(prefix + line.decode(errors="replace").rstrip())


async def _launch(server: dict, color: str) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(server["script"]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=os.environ.copy(),
        cwd=server.get("cwd", str(_ROOT)),
    )
    asyncio.create_task(_stream(server["name"], color, proc.stdout))
    print(f"{BOLD}{color}[{server['name']}]{RESET} pid={proc.pid}  port={server['port']}")
    return proc


async def run(servers: list[dict] | None = None) -> None:
    """启动指定 server 列表（默认 SERVERS），等待 Ctrl+C 后统一关闭。"""
    targets = servers or SERVERS
    print(f"\n{BOLD}启动 {len(targets)} 个 MCP Server ...{RESET}")
    for s in targets:
        print(f"  {s['port']}  {s['name']}")
    print()

    procs: list[asyncio.subprocess.Process] = []
    for i, server in enumerate(targets):
        procs.append(await _launch(server, COLORS[i % len(COLORS)]))
        ready = await _wait_for_port(server["port"])
        if not ready:
            print(f"{BOLD}\033[33m[警告] {server['name']} 端口 {server['port']} 在 15s 内未就绪{RESET}")

    print(f"\n{BOLD}全部启动完成，Ctrl+C 统一关闭{RESET}\n")
    await _wait_and_shutdown(procs, "MCP")


async def _wait_and_shutdown(procs: list, label: str) -> None:
    stop = asyncio.Event()

    def _sig(*_):
        stop.set()

    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGINT, _sig)
        loop.add_signal_handler(signal.SIGTERM, _sig)

    try:
        if sys.platform != "win32":
            await stop.wait()
        else:
            await asyncio.gather(*[p.wait() for p in procs])
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n{BOLD}正在关闭 {label} Server ...{RESET}")
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        await asyncio.gather(*[p.wait() for p in procs], return_exceptions=True)
        print("已全部关闭。")


if __name__ == "__main__":
    asyncio.run(run())
