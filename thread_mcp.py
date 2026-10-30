#!/usr/bin/env python3
"""Custom MCP tools for independent Codex threads on this host.

Uses the public app-server interface through thread_client.py. This does not
unlock or impersonate the desktop's built-in codex_app tools. Only idle threads
are supported. Interactive approvals must be handled in the desktop App.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import sys

from thread_client import create_client, run


TOOLS = [
    {
        "name": "create_thread",
        "description": "Custom adapter: create a persistent independent Codex thread on pdc1, not a subagent. Returns its id and app_url with the required remote hostId. Present app_url as a Markdown link; an id-only App creation card can route to the wrong host and fail to resume. Uses the running App daemon to broadcast creation to connected App clients. No turn is started. Use only when the user requests a new chat.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {"cwd": {"type": "string"}, "title": {"type": "string"}},
            "required": ["cwd", "title"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "send_message_to_thread",
        "description": "Custom adapter: send an authorized message to an existing idle independent Codex thread on this host and wait for its reply. Not for active threads or subagents. The receiver can use its configured tools. A timeout is not proof that delivery failed: inspect history before retrying.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {"thread_id": {"type": "string"}, "message": {"type": "string"}, "timeout": {"type": "integer", "minimum": 1, "maximum": 180}},
            "required": ["thread_id", "message"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    },
]


def execute(name, arguments):
    schema = next((tool["inputSchema"] for tool in TOOLS if tool["name"] == name), None)
    if schema is None:
        raise ValueError("Unknown tool")
    if not isinstance(arguments, dict):
        raise ValueError("Arguments must be an object")
    if set(arguments) - set(schema["properties"]):
        raise ValueError("Unsupported argument")
    for key in schema["required"]:
        if not isinstance(arguments.get(key), str) or not arguments[key].strip():
            raise ValueError("Missing or invalid " + key)
    if "timeout" in arguments and (type(arguments["timeout"]) is not int or not 1 <= arguments["timeout"] <= 180):
        raise ValueError("timeout must be an integer between 1 and 180")
    if name == "create_thread" and (not os.path.isabs(arguments["cwd"]) or not Path(arguments["cwd"]).is_dir()):
        raise ValueError("cwd must be an existing absolute directory")
    client = None
    progress = io.StringIO()
    try:
        with contextlib.redirect_stdout(progress):
            client = create_client()
            result = run(client, {"operation": name, **arguments})
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
    except Exception as error:
        return {"isError": True, "content": [{"type": "text", "text": json.dumps({
            "error": str(error), "progress": progress.getvalue(),
            "note": "Inspect any reported thread/turn before retrying; mutations may have succeeded.",
        }, ensure_ascii=False)}]}
    finally:
        if client:
            client.close()


def dispatch(request):
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "independent-threads", "version": "0.2.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return execute(params["name"], params.get("arguments", {}))
    raise ValueError("Unsupported method")


def main():
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except ValueError:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}), flush=True)
            continue
        if "id" not in request:
            continue
        response = {"jsonrpc": "2.0", "id": request["id"]}
        try:
            response["result"] = dispatch(request)
        except (ValueError, KeyError, TypeError) as error:
            response["error"] = {"code": -32602, "message": str(error)}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
