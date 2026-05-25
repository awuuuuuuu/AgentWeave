"""环保局 A2A Server — port 9101
运行：cd demo/mock_servers/a2a && uv run python en_server.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "backend"))

from agent.a2a.base import run_server  # noqa: E402

if __name__ == "__main__":
    run_server(dept_code="env_agency", port=9101)
