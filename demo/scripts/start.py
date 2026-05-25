"""统一入口 — 启动 MCP / A2A / 全部 Server。

各脚本也可单独运行：
    uv run python demo/scripts/mcp_servers.py
    uv run python demo/scripts/a2a_servers.py

统一用法：
    uv run python demo/scripts/start.py mcp
    uv run python demo/scripts/start.py a2a
    uv run python demo/scripts/start.py all    # 自动分屏：左 MCP / 右 A2A
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import mcp_servers
import a2a_servers

_ROOT = Path(__file__).parent.parent.parent


def _launch_split() -> None:
    """在 Windows Terminal 分屏启动，或退回到两个独立 cmd 窗口。"""
    root = str(_ROOT)
    mcp_cmd = f'cd /d "{root}" && uv run python demo/scripts/mcp_servers.py'
    a2a_cmd = f'cd /d "{root}" && uv run python demo/scripts/a2a_servers.py'

    if shutil.which("wt"):
        # Windows Terminal: 新 tab 左右分屏
        subprocess.Popen([
            "wt",
            "new-tab", "--title", "MCP Servers", "--", "cmd", "/k", mcp_cmd,
            ";",
            "split-pane", "--title", "A2A Servers", "-H", "--", "cmd", "/k", a2a_cmd,
        ])
    else:
        # Fallback: 两个独立的 cmd 窗口
        subprocess.Popen(f'start "MCP Servers" cmd /k "{mcp_cmd}"', shell=True)
        subprocess.Popen(f'start "A2A Servers" cmd /k "{a2a_cmd}"', shell=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AgentWeave Demo Server 启动入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("mcp",  help="启动所有 MCP Server（端口 8102-8107）")
    sub.add_parser("a2a",  help="启动所有 A2A Server（端口 9101-9105）")
    sub.add_parser("all",  help="分屏启动 MCP + A2A Server")

    args = parser.parse_args()

    if args.cmd == "mcp":
        asyncio.run(mcp_servers.run())

    elif args.cmd == "a2a":
        asyncio.run(a2a_servers.run())

    elif args.cmd == "all":
        _launch_split()


if __name__ == "__main__":
    main()
