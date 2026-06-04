"""启动所有 A2A Server。

运行：
    uv run python demo/scripts/a2a_servers.py

Ctrl+C 统一关闭。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_ROOT    = Path(__file__).parent.parent.parent
_A2A_DIR = Path(__file__).parent.parent / "mock_servers" / "a2a"

SERVERS = [
    {"name": "env_agency",         "script": _A2A_DIR / "en_server.py", "port": 9101, "cwd": str(_ROOT / "backend")},
    {"name": "medical_ems",        "script": _A2A_DIR / "me_server.py", "port": 9102, "cwd": str(_ROOT / "backend")},
    {"name": "traffic_control",    "script": _A2A_DIR / "tr_server.py", "port": 9103, "cwd": str(_ROOT / "backend")},
    {"name": "emergency_supplies", "script": _A2A_DIR / "lg_server.py", "port": 9104, "cwd": str(_ROOT / "backend")},
    {"name": "fire_brigade",       "script": _A2A_DIR / "ff_server.py", "port": 9105, "cwd": str(_ROOT / "backend")},
]

COLORS = ["\033[36m", "\033[32m", "\033[33m", "\033[35m", "\033[31m"]
RESET  = "\033[0m"
BOLD   = "\033[1m"


async def _probe_port(port: int) -> int | None:
    """尝试连接端口，成功返回 None，失败返回端口号。"""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=1.0
        )
        writer.close()
        await writer.wait_closed()
        return None
    except Exception:
        return port


async def _wait_for_mcp_ports(timeout: float = 120.0) -> None:
    """并行探测所有 MCP 端口，全部就绪后返回；超时后打印警告并继续启动。"""
    import time
    mcp_ports = [8102, 8103, 8104, 8105, 8106, 8107]
    print(f"{BOLD}等待 MCP Server 就绪 {mcp_ports} ...{RESET}")
    deadline = time.monotonic() + timeout
    while True:
        results = await asyncio.gather(*(_probe_port(p) for p in mcp_ports))
        missing = [p for p in results if p is not None]
        if not missing:
            print(f"{BOLD}\033[32m[OK] 所有 MCP Server 已就绪{RESET}\n")
            return
        remaining = max(0, int(deadline - time.monotonic()))
        if remaining == 0:
            break
        print(f"\033[33m  未就绪端口: {missing}  剩余 {remaining}s ...{RESET}")
        await asyncio.sleep(3.0)
        if time.monotonic() >= deadline:
            break
    print(f"{BOLD}\033[31m[错误] MCP Server 在 {timeout:.0f}s 内未全部就绪，继续启动 A2A（工具调用可能失败）{RESET}\n")


async def _stream(name: str, color: str, stream: asyncio.StreamReader) -> None:
    prefix = f"{color}[{name}]{RESET} "
    while True:
        line = await stream.readline()
        if not line:
            break
        print(prefix + line.decode(errors="replace").rstrip())


async def _launch(server: dict, color: str) -> asyncio.subprocess.Process:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(server["script"]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        cwd=server.get("cwd", str(_ROOT / "backend")),
    )
    asyncio.create_task(_stream(server["name"], color, proc.stdout))
    print(f"{BOLD}{color}[{server['name']}]{RESET} pid={proc.pid}  port={server['port']}")
    return proc


async def run(servers: list[dict] | None = None) -> None:
    """启动指定 server 列表（默认 SERVERS），等待 Ctrl+C 后统一关闭。"""
    from mcp_servers import _wait_and_shutdown
    targets = servers or SERVERS
    print(f"\n{BOLD}启动 {len(targets)} 个 A2A Server ...{RESET}")
    for s in targets:
        print(f"  {s['port']}  {s['name']}")
    print()

    await _wait_for_mcp_ports()

    procs: list[asyncio.subprocess.Process] = []
    for i, server in enumerate(targets):
        procs.append(await _launch(server, COLORS[i % len(COLORS)]))
        await asyncio.sleep(0.3)

    print(f"\n{BOLD}全部启动完成，Ctrl+C 统一关闭{RESET}\n")
    await _wait_and_shutdown(procs, "A2A")


if __name__ == "__main__":
    asyncio.run(run())
