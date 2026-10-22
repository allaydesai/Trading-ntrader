"""A raw JSON-RPC client over the server's stdio, recording every stdout line.

The SDK client tolerates stray stdout; this one does not interpret anything,
so tests can assert that stdout carried protocol messages and nothing else.
"""

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from mcp.types import LATEST_PROTOCOL_VERSION

REPO_ROOT = Path(__file__).resolve().parents[3]


class RawStdioServer:
    def __init__(self, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "src.mcp_server"],
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.lines: list[str] = []
        self._responses: dict[int, dict[str, Any]] = {}
        self._cond = threading.Condition()
        self._next_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            with self._cond:
                self.lines.append(line)
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict) and "id" in message:
                    self._responses[message["id"]] = message
                self._cond.notify_all()

    def _send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        with self._cond:
            if not self._cond.wait_for(lambda: request_id in self._responses, timeout=timeout):
                raise TimeoutError(f"no response to {method}")
            return self._responses.pop(request_id)

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        response = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        return response["result"]["structuredContent"]

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "raw-test", "version": "0"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def close(self) -> list[str]:
        """Close stdin, wait for exit, and return every line the server wrote to stdout."""
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        return list(self.lines)


def non_protocol_lines(lines: list[str]) -> list[str]:
    """Every stdout line that is not a JSON-RPC 2.0 message."""
    bad = []
    for line in lines:
        try:
            message = json.loads(line)
        except ValueError:
            bad.append(line)
            continue
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            bad.append(line)
    return bad
