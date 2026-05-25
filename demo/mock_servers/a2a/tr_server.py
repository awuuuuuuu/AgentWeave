"""交通管控 A2A Server — port 9103
运行：cd demo/mock_servers/a2a && uv run python tr_server.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "backend"))

from agent.a2a.base import run_server  # noqa: E402

if __name__ == "__main__":
    run_server(dept_code="traffic_control", port=9103)
