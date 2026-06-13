#!/usr/bin/env python3
"""Minimal Cat9kV MCP server.

This intentionally stays as a thin MCP/JSON-RPC wrapper over the existing
Cat9kV REST API. Deployment logic remains in webapp.app.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SERVER_NAME = "cat9kv-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-06-18"
DEFAULT_API_BASE = "http://127.0.0.1:8080"
DEFAULT_AUDIT_LOG = "/opt/cat9kv-playbook/logs/audit.jsonl"


class ToolError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cat9kV MCP server")
    parser.add_argument("--host", default=os.environ.get("CAT9KV_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CAT9KV_MCP_PORT", "8090")))
    parser.add_argument("--api-base", default=os.environ.get("CAT9KV_API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--audit-log", default=os.environ.get("CAT9KV_AUDIT_LOG", DEFAULT_AUDIT_LOG))
    return parser.parse_args()


def json_dumps(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def json_rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def json_rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def text_content(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, indent=2, sort_keys=True)
    return {"content": [{"type": "text", "text": text}]}


def api_request(api_base: str, method: str, path: str, payload: dict[str, Any] | None = None,
                timeout: int = 30) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(f"{api_base.rstrip('/')}{path}", data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ToolError(f"Cat9kV API returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise ToolError(f"Unable to reach Cat9kV API: {exc.reason}") from exc

    if not raw:
        return {}
    return json.loads(raw)


def wait_for_job(api_base: str, job_id: str, timeout_seconds: int = 600,
                 poll_interval_seconds: float = 1.5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_job: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        job = api_request(api_base, "GET", f"/api/jobs/{job_id}", timeout=30)
        last_job = job
        if job.get("status") in {"completed", "error"}:
            return job
        time.sleep(poll_interval_seconds)
    raise ToolError(f"Timed out waiting for job {job_id}. Last status: {last_job}")


def deploy_payload(arguments: dict[str, Any], mode: str) -> dict[str, Any]:
    try:
        vm_count = int(arguments.get("vm_count", 1))
    except (TypeError, ValueError) as exc:
        raise ToolError("vm_count must be an integer") from exc

    payload = {
        "esxi_host": str(arguments["esxi_host"]),
        "username": str(arguments["username"]),
        "password": str(arguments["password"]),
        "version": str(arguments["version"]),
        "vm_count": vm_count,
        "mode": mode,
    }
    return payload


def call_tool(api_base: str, audit_log: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "cat9kv_list_versions":
        return text_content(api_request(api_base, "GET", "/api/versions", timeout=30))

    if name == "cat9kv_dry_run":
        timeout_seconds = int(arguments.get("timeout_seconds", 300))
        started = api_request(api_base, "POST", "/api/deploy", deploy_payload(arguments, "dry_run"), timeout=30)
        job = wait_for_job(api_base, str(started["job_id"]), timeout_seconds=timeout_seconds)
        return text_content(job)

    if name == "cat9kv_start_deploy":
        started = api_request(api_base, "POST", "/api/deploy", deploy_payload(arguments, "deploy"), timeout=30)
        return text_content({
            **started,
            "message": "Deployment started. Use cat9kv_get_job or cat9kv_wait_for_job to monitor progress.",
        })

    if name == "cat9kv_get_job":
        job_id = str(arguments["job_id"])
        return text_content(api_request(api_base, "GET", f"/api/jobs/{job_id}", timeout=30))

    if name == "cat9kv_wait_for_job":
        job_id = str(arguments["job_id"])
        timeout_seconds = int(arguments.get("timeout_seconds", 900))
        return text_content(wait_for_job(api_base, job_id, timeout_seconds=timeout_seconds))

    if name == "cat9kv_audit_summary":
        repo_dir = Path(__file__).resolve().parents[1]
        script = repo_dir / "scripts" / "audit_summary.py"
        completed = subprocess.run(
            [sys.executable, str(script), audit_log],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            raise ToolError(completed.stdout.strip() or "audit summary failed")
        return text_content(completed.stdout.strip())

    raise ToolError(f"Unknown tool: {name}")


def tools() -> list[dict[str, Any]]:
    esxi_fields = {
        "esxi_host": {"type": "string", "description": "Target ESXi IP address or FQDN."},
        "username": {"type": "string", "description": "ESXi username."},
        "password": {"type": "string", "description": "ESXi password. It is forwarded to the Cat9kV API and is not logged."},
        "version": {"type": "string", "description": "Cat9kV version from cat9kv_list_versions, for example 17.15.04."},
        "vm_count": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Number of Cat9kV VMs to create."},
    }
    esxi_required = ["esxi_host", "username", "password", "version", "vm_count"]

    return [
        {
            "name": "cat9kv_list_versions",
            "description": "List Cat9kV versions available on the deployment server.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "cat9kv_dry_run",
            "description": "Run Cat9kV deployment planning only. This contacts ESXi, discovers placement, and returns planned VM names and serial ports without creating VMs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **esxi_fields,
                    "timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 900, "default": 300},
                },
                "required": esxi_required,
                "additionalProperties": False,
            },
        },
        {
            "name": "cat9kv_start_deploy",
            "description": "Start a Cat9kV deployment job. Run cat9kv_dry_run first unless the user explicitly asks to deploy immediately.",
            "inputSchema": {
                "type": "object",
                "properties": esxi_fields,
                "required": esxi_required,
                "additionalProperties": False,
            },
        },
        {
            "name": "cat9kv_get_job",
            "description": "Read current status, events, result, or error for a Cat9kV job.",
            "inputSchema": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "cat9kv_wait_for_job",
            "description": "Poll a Cat9kV job until it completes, errors, or times out.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 3600, "default": 900},
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "cat9kv_audit_summary",
            "description": "Return the current Cat9kV deployment audit summary from the Ubuntu server.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]


class Cat9kvMcpHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVER_NAME}/{SERVER_VERSION}"

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            self.send_json(200, {"status": "ok", "server": SERVER_NAME, "version": SERVER_VERSION})
            return
        self.send_json(405, {"error": "MCP uses POST for JSON-RPC requests"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/mcp":
            self.send_json(404, {"error": "not found"})
            return

        origin = self.headers.get("Origin")
        if origin and not self.server.origin_allowed(origin):  # type: ignore[attr-defined]
            self.send_json(403, {"error": "origin not allowed"})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self.send_json(400, json_rpc_error(None, -32700, "Parse error"))
            return

        responses = []
        batch = payload if isinstance(payload, list) else [payload]
        for request in batch:
            response = self.handle_rpc(request)
            if response is not None:
                responses.append(response)

        if not responses:
            self.send_response(202)
            self.end_headers()
            return
        self.send_json(200, responses if isinstance(payload, list) else responses[0])

    def handle_rpc(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if request_id is None and method and method.startswith("notifications/"):
            return None

        try:
            if method == "initialize":
                return json_rpc_result(request_id, {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                })

            if method == "ping":
                return json_rpc_result(request_id, {})

            if method == "tools/list":
                return json_rpc_result(request_id, {"tools": tools()})

            if method == "tools/call":
                name = str(params.get("name"))
                arguments = params.get("arguments") or {}
                result = call_tool(self.server.api_base, self.server.audit_log, name, arguments)  # type: ignore[attr-defined]
                return json_rpc_result(request_id, result)

            if method in {"resources/list", "prompts/list"}:
                key = "resources" if method == "resources/list" else "prompts"
                return json_rpc_result(request_id, {key: []})

            return json_rpc_error(request_id, -32601, f"Method not found: {method}")
        except KeyError as exc:
            return json_rpc_error(request_id, -32602, f"Missing required argument: {exc.args[0]}")
        except ToolError as exc:
            return json_rpc_result(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception as exc:  # noqa: BLE001 - keep MCP server failure visible to caller.
            return json_rpc_error(request_id, -32603, str(exc))

    def send_json(self, status: int, payload: dict[str, Any] | list[Any]) -> None:
        body = json_dumps(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("MCP-Protocol-Version", PROTOCOL_VERSION)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - BaseHTTPRequestHandler name.
        return


class Cat9kvMcpServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler],
                 api_base: str, audit_log: str, allowed_origins: set[str]):
        super().__init__(server_address, handler_class)
        self.api_base = api_base
        self.audit_log = audit_log
        self.allowed_origins = allowed_origins

    def origin_allowed(self, origin: str) -> bool:
        return "*" in self.allowed_origins or origin in self.allowed_origins


def main() -> int:
    args = parse_args()
    allowed_origins = {
        item.strip()
        for item in os.environ.get(
            "CAT9KV_MCP_ALLOWED_ORIGINS",
            "http://10.76.90.60,http://127.0.0.1,http://localhost",
        ).split(",")
        if item.strip()
    }
    server = Cat9kvMcpServer(
        (args.host, args.port),
        Cat9kvMcpHandler,
        api_base=args.api_base,
        audit_log=args.audit_log,
        allowed_origins=allowed_origins,
    )
    print(f"{SERVER_NAME} listening on http://{args.host}:{args.port}/mcp", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
