"""
Test MCP Server stdio communication.
This script simulates an MCP client sending requests to the server.
"""
# ruff: noqa: T201

import asyncio
import json
import subprocess
import sys
from pathlib import Path


async def send_request(process: subprocess.Popen, request: dict) -> dict:
    """Send a JSON-RPC request and read response."""
    request_json = json.dumps(request) + "\n"
    process.stdin.write(request_json.encode())
    process.stdin.flush()

    response_line = process.stdout.readline().decode().strip()
    return json.loads(response_line)


async def test_mcp_server():
    """Test MCP server with basic requests."""
    server_dir = Path(__file__).parent
    target_repo = str(server_dir)

    print("Starting MCP server...")
    print(f"Target repo: {target_repo}")

    process = subprocess.Popen(
        [sys.executable, "-m", "codebase_rag.cli", "mcp-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(server_dir),
        env={
            "TARGET_REPO_PATH": target_repo,
            "CYPHER_PROVIDER": "ollama",
            "CYPHER_MODEL": "codellama",
        },
    )

    try:
        await asyncio.sleep(2)

        print("\n1. Testing initialize request...")
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        }

        response = await send_request(process, init_request)
        print(f"Response: {json.dumps(response, indent=2)}")

        print("\n2. Testing tools/list request...")
        list_tools_request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}

        response = await send_request(process, list_tools_request)
        print(f"Available tools: {len(response.get('result', {}).get('tools', []))}")
        for tool in response.get("result", {}).get("tools", []):
            print(f"  - {tool['name']}: {tool['description'][:50]}...")

        print("\n3. Testing list_projects tool...")
        call_tool_request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_projects", "arguments": {}},
        }

        response = await send_request(process, call_tool_request)
        print(f"Response: {json.dumps(response, indent=2)}")

        print("\n✅ MCP server is working correctly!")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        stderr = process.stderr.read().decode()
        if stderr:
            print(f"Server errors:\n{stderr}")

    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    asyncio.run(test_mcp_server())
