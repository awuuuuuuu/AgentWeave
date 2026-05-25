"""
快速验证 A2A Server 可用性

用法（确保对应 server 已启动）：
    cd demo/mock_servers/a2a && uv run python test_a2a.py [port]
    默认 port=9101（环保局）
"""
import asyncio
import json
import sys
import uuid

import httpx


async def test(port: int = 9101) -> None:
    base = f"http://localhost:{port}"

    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.get(f"{base}/.well-known/agent.json")
        print("=== agent.json ===")
        print(json.dumps(r.json(), ensure_ascii=False, indent=2))

        task_id = str(uuid.uuid4())[:8]
        payload = {
            "task_id": task_id,
            "task": "当前事故为港城大道388号液氨泄漏，风速4.2m/s，风向东南，请评估大气扩散范围并给出疏散建议。",
            "timeout_sec": 90,
        }
        print(f"\n=== POST /a2a/tasks/send (task_id={task_id}) ===")
        r = await c.post(f"{base}/a2a/tasks/send", json=payload)
        resp = r.json()
        print(f"status:     {resp['status']}")
        print(f"dept_code:  {resp['dept_code']}")
        print(f"duration:   {resp['duration_ms']}ms")
        print(f"map_events: {len(resp['map_events'])} 条")
        print("key_facts:")
        for f in resp["key_facts"]:
            print(f"  · {f}")
        print(f"\nsummary (前500字):\n{resp['summary'][:500]}")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9101
    asyncio.run(test(port))
