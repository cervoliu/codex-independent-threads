#!/usr/bin/env python3
"""Independent Codex threads via the public app-server JSON-RPC protocol.

This is a local adapter, not the desktop's built-in codex_app tools.
Accept one JSON request on stdin. No model, safety, or feature overrides.
"""
import json
import queue
import subprocess
import sys
import threading
import time
from urllib.parse import quote, urlencode


# This adapter is installed on cervoll-pdc1. The desktop needs its remote host
# identity as well as a thread id to navigate to a newly created conversation.
APP_HOST_ID = "remote-ssh-discovered:pdc1"


def app_thread_url(thread_id):
    return "thread://" + quote(thread_id, safe="") + "?" + urlencode({"hostId": APP_HOST_ID})


class Client:
    def __init__(self):
        self.proc = subprocess.Popen(
            ["codex", "app-server", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
            text=True, bufsize=1,
        )
        self.inbox = queue.Queue()
        self.events = []
        self.request_id = 0
        threading.Thread(target=self._read, daemon=True).start()
        self.call("initialize", {"clientInfo": {
            "name": "independent_thread_client", "title": "Independent thread client",
            "version": "0.1.0",
        }})
        self.send({"method": "initialized", "params": {}})

    def _read(self):
        for line in self.proc.stdout:
            try:
                self.inbox.put(json.loads(line))
            except ValueError:
                continue
        self.inbox.put(None)

    def send(self, value):
        self.proc.stdin.write(json.dumps(value) + "\n")
        self.proc.stdin.flush()

    def receive(self, deadline):
        value = self.inbox.get(timeout=max(0, deadline - time.monotonic()))
        if value is None:
            raise RuntimeError("app-server closed")
        if "method" in value and "id" in value:
            # This adapter cannot present approval or dynamic-tool requests.
            self.send({"id": value["id"], "error": {
                "code": -32601, "message": "Interactive request unsupported; use the App",
            }})
        return value

    def call(self, method, params):
        self.request_id += 1
        request_id = self.request_id
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + 30
        while True:
            value = self.receive(deadline)
            if value.get("id") == request_id:
                if "error" in value:
                    raise RuntimeError(json.dumps(value["error"]))
                return value["result"]
            self.events.append(value)

    def wait_turn(self, thread_id, turn_id, timeout):
        deadline = time.monotonic() + timeout
        while True:
            value = self.events.pop(0) if self.events else self.receive(deadline)
            params = value.get("params", {})
            if (value.get("method") == "turn/completed"
                    and params.get("threadId") == thread_id
                    and params.get("turn", {}).get("id") == turn_id):
                return params["turn"]

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


def create_client():
    """Use the App daemon so thread lifecycle events reach connected UIs.

    Fail if the daemon is unavailable; a separate server would silently lose
    the sidebar notification and can conflict with the daemon writer.
    """
    from daemon_client import DaemonClient
    return DaemonClient()


def run(client, request):
    operation = request["operation"]
    if operation == "create_thread":
        result = client.call("thread/start", {"cwd": request["cwd"]})
        thread = result["thread"]
        # Emit the id immediately so a later failure cannot hide a created thread.
        app_link = {"app_host_id": APP_HOST_ID, "app_url": app_thread_url(thread["id"])}
        print(json.dumps({"created_thread_id": thread["id"], **app_link}), flush=True)
        if request.get("title"):
            client.call("thread/name/set", {"threadId": thread["id"], "name": request["title"]})
        return {**app_link, "thread": client.call("thread/read", {
            "threadId": thread["id"], "includeTurns": False,
        })["thread"]}
    thread_id = request["thread_id"]
    if operation == "read_thread":
        return client.call("thread/items/list", {"threadId": thread_id, "limit": 20})
    if operation != "send_message_to_thread":
        raise ValueError("Unknown operation")
    thread = client.call("thread/read", {"threadId": thread_id, "includeTurns": False})["thread"]
    if thread.get("status", {}).get("type") not in ("notLoaded", "idle"):
        raise RuntimeError("Thread is active; use the App queue to avoid concurrent writers")
    client.call("thread/resume", {"threadId": thread_id, "excludeTurns": True})
    result = client.call("turn/start", {
        "threadId": thread_id, "input": [{"type": "text", "text": request["message"]}],
    })
    turn_id = result["turn"]["id"]
    print(json.dumps({"accepted_turn_id": turn_id, "thread_id": thread_id}), flush=True)
    completed = client.wait_turn(thread_id, turn_id, request.get("timeout", 180))
    return {"turn": completed, "items": client.call("thread/items/list", {
        "threadId": thread_id, "turnId": turn_id, "limit": 30,
    })}


if __name__ == "__main__":
    client = None
    try:
        request = json.load(sys.stdin)
        client = create_client()
        print(json.dumps(run(client, request), ensure_ascii=False), flush=True)
    except Exception as error:
        print(json.dumps({"error": str(error), "note": "Do not retry a mutation blindly; inspect the thread first."}), flush=True)
        sys.exit(1)
    finally:
        if client:
            client.close()
