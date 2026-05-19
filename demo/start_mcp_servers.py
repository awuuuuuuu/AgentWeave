"""
一键启动所有 MCP Server

用法：
    uv run python demo/start_mcp_servers.py

可选：指定高德 API Key（amap server 需要）
    AMAP_API_KEY=xxx uv run python demo/start_mcp_servers.py

Ctrl+C 统一关闭所有进程。
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
MCP_DIR = Path(__file__).parent / "mock_servers" / "mcp"


def _load_env() -> None:
    """
    按优先级加载环境变量：
      1. backend/.env          （主配置，不覆盖已有 shell 变量）
      2. frontend/.env.local   （补充 AMAP key）
    AMAP_SERVICE_KEY → AMAP_API_KEY 自动映射。
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return  # python-dotenv 未安装时跳过，依赖 shell 变量

    for env_file in [ROOT / "backend" / ".env", ROOT / "frontend" / ".env.local"]:
        if env_file.exists():
            load_dotenv(env_file, override=False)

    # 前端用 AMAP_SERVICE_KEY，MCP server 读 AMAP_API_KEY，自动映射
    if not os.environ.get("AMAP_API_KEY") and os.environ.get("AMAP_SERVICE_KEY"):
        os.environ["AMAP_API_KEY"] = os.environ["AMAP_SERVICE_KEY"]


_load_env()

SERVERS = [
    {"name": "atmospheric_dispersion", "script": MCP_DIR / "atmospheric_dispersion.py", "port": 8101},
    {"name": "ambulance_dispatch",      "script": MCP_DIR / "ambulance_dispatch.py",      "port": 8102},
    {"name": "signal_control",          "script": MCP_DIR / "signal_control.py",          "port": 8103},
    {"name": "warehouse",               "script": MCP_DIR / "warehouse.py",               "port": 8104},
    {"name": "enterprise_sensor",       "script": MCP_DIR / "enterprise_sensor.py",       "port": 8105},
    {"name": "amap",                    "script": MCP_DIR / "amap.py",                    "port": 8106},
]

# ANSI 颜色（每个 server 一种，方便区分日志）
COLORS = ["\033[36m", "\033[32m", "\033[33m", "\033[35m", "\033[34m", "\033[31m"]
RESET = "\033[0m"
BOLD = "\033[1m"


async def stream_output(name: str, color: str, stream: asyncio.StreamReader) -> None:
    """把子进程的 stdout/stderr 转发到主进程，带颜色前缀。"""
    prefix = f"{color}[{name}]{RESET} "
    while True:
        line = await stream.readline()
        if not line:
            break
        print(prefix + line.decode(errors="replace").rstrip())


async def launch(server: dict, color: str, env: dict) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(server["script"]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        cwd=str(ROOT),
    )
    asyncio.create_task(stream_output(server["name"], color, proc.stdout))
    print(f"{BOLD}{color}[{server['name']}]{RESET} 已启动 pid={proc.pid}  port={server['port']}")
    return proc


async def main() -> None:
    env = os.environ.copy()
    # Windows 下 asyncio 子进程需要 ProactorEventLoop（Python 3.8+ 默认已是）

    print(f"\n{BOLD}启动 {len(SERVERS)} 个 MCP Server ...{RESET}\n")

    procs: list[asyncio.subprocess.Process] = []
    for i, server in enumerate(SERVERS):
        proc = await launch(server, COLORS[i % len(COLORS)], env)
        procs.append(proc)
        await asyncio.sleep(0.3)  # 错开启动，避免端口竞争日志混乱

    print(f"\n{BOLD}全部启动完成，Ctrl+C 统一关闭{RESET}\n")

    # 等待所有进程，直到 Ctrl+C
    stop = asyncio.Event()

    def _handle_sigint(*_):
        stop.set()

    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGINT, _handle_sigint)
        loop.add_signal_handler(signal.SIGTERM, _handle_sigint)
    else:
        # Windows 不支持 add_signal_handler，用 KeyboardInterrupt 捕获
        pass

    try:
        if sys.platform != "win32":
            await stop.wait()
        else:
            await asyncio.gather(*[proc.wait() for proc in procs])
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n{BOLD}正在关闭所有 MCP Server ...{RESET}")
        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass
        await asyncio.gather(*[proc.wait() for proc in procs], return_exceptions=True)
        print("已全部关闭。")


if __name__ == "__main__":
    asyncio.run(main())
