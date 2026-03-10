"""
Smoke test for BIG-IP MCP Server.
Usage: python test_mcp.py <hostname> [port]
"""

import sys
import json
import asyncio
import websockets
import httpx


async def test_websocket(host: str, port: int):
    uri = f"ws://{host}:{port}/ws"
    print(f"\n── WebSocket: {uri}")
    async with websockets.connect(uri) as ws:
        # Initialize
        await ws.send(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05",
                       "clientInfo": {"name": "test", "version": "1.0"}},
        }))
        resp = json.loads(await ws.recv())
        info = resp["result"]["serverInfo"]
        print(f"   Server: {info['name']} v{info['version']}")

        # List tools
        await ws.send(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        }))
        resp = json.loads(await ws.recv())
        tools = resp["result"]["tools"]
        print(f"   Tools ({len(tools)}):")
        for t in tools:
            print(f"     - {t['name']}: {t['description'][:70]}")
    print("   OK")


async def test_sse(host: str, port: int):
    base = f"http://{host}:{port}"
    print(f"\n── SSE: {base}/sse")
    async with httpx.AsyncClient(timeout=15) as client:
        async with client.stream("GET", f"{base}/sse") as stream:
            session_id = None
            async for line in stream.aiter_lines():
                if line.startswith("data: /messages?session_id="):
                    session_id = line.split("session_id=")[1]
                    break
            if not session_id:
                print("   FAIL: no session_id")
                return

            # Initialize
            r = await client.post(
                f"{base}/messages?session_id={session_id}",
                json={
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05",
                               "clientInfo": {"name": "test", "version": "1.0"}},
                },
            )
            print(f"   Init POST: {r.status_code}")

            # List tools
            r = await client.post(
                f"{base}/messages?session_id={session_id}",
                json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
                },
            )
            print(f"   Tools POST: {r.status_code}")
    print("   OK")


async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    print(f"Testing BIG-IP MCP Server at {host}:{port}")

    await test_websocket(host, port)
    await test_sse(host, port)
    print("\nAll tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
