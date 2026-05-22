"""
一键启动所有部门 A2A Server

用法（从项目根目录）：
    cd backend && uv run python ../demo/start_a2a_servers.py

Ctrl+C 统一关闭所有进程。

部门端口：
  9001  env_agency         环保局
  9002  medical_ems        医疗急救
  9003  traffic_control    交通管控
  9004  emergency_supplies 应急物资
  9005  enterprise_safety  企业安全
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

ROOT   = Path(__file__).parent.parent
A2A_DIR = Path(__file__).parent / "mock_servers" / "a2a"

SERVERS = [
    {"name": "env_agency",         "script": A2A_DIR / "en_server.py", "port": 9001},
    {"name": "medical_ems",        "script": A2A_DIR / "me_server.py", "port": 9002},
    {"name": "traffic_control",    "script": A2A_DIR / "tr_server.py", "port": 9003},
    {"name": "emergency_supplies", "script": A2A_DIR / "lg_server.py", "port": 9004},
    {"name": "enterprise_safety",  "script": A2A_DIR / "sf_server.py", "port": 9005},
]

COLORS = ["\033[36m", "\033[32m", "\033[33m", "\033[35m", "\033[31m"]
RESET  = "\033[0m"
BOLD   = "\033[1m"


async def stream_output(name: str, color: str, stream: asyncio.StreamReader) -> None:
    prefix = f"{color}[{name}]{RESET} "
    while True:
        line = await stream.readline()
        if not line:
            break
        print(prefix + line.decode("utf-8", errors="replace").rstrip())


async def launch(server: dict, color: str, env: dict) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(server["script"]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        cwd=str(ROOT / "backend"),   # A2A server 从 backend/ 目录启动，import 路径一致
    )
    asyncio.create_task(stream_output(server["name"], color, proc.stdout))
    print(f"{BOLD}{color}[{server['name']}]{RESET} 已启动 pid={proc.pid}  port={server['port']}")
    return proc


async def main() -> None:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"

    print(f"\n{BOLD}启动 {len(SERVERS)} 个 A2A Server ...{RESET}\n")

    procs: list[asyncio.subprocess.Process] = []
    for i, server in enumerate(SERVERS):
        proc = await launch(server, COLORS[i % len(COLORS)], env)
        procs.append(proc)
        await asyncio.sleep(0.3)

    print(f"\n{BOLD}全部启动完成，Ctrl+C 统一关闭{RESET}\n")
    print(f"  {COLORS[0]}9001{RESET}  env_agency         环保局")
    print(f"  {COLORS[1]}9002{RESET}  medical_ems        医疗急救")
    print(f"  {COLORS[2]}9003{RESET}  traffic_control    交通管控")
    print(f"  {COLORS[3]}9004{RESET}  emergency_supplies 应急物资")
    print(f"  {COLORS[4]}9005{RESET}  enterprise_safety  企业安全\n")

    stop = asyncio.Event()

    def _handle_sigint(*_):
        stop.set()

    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGINT, _handle_sigint)
        loop.add_signal_handler(signal.SIGTERM, _handle_sigint)

    try:
        if sys.platform != "win32":
            await stop.wait()
        else:
            await asyncio.gather(*[proc.wait() for proc in procs])
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n{BOLD}正在关闭所有 A2A Server ...{RESET}")
        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass
        await asyncio.gather(*[proc.wait() for proc in procs], return_exceptions=True)
        print("已全部关闭。")


if __name__ == "__main__":
    asyncio.run(main())
