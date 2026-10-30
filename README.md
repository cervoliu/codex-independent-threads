# Codex Independent Threads MCP

Create persistent, independent Codex sessions and exchange messages through an MCP server. Sessions use the running Codex App daemon, so connected App clients receive creation events and can show new sessions automatically.

## Requirements

- Python 3 with the standard library (tested with Python 3.12).
- Codex CLI and a running Codex App daemon (tested with version 0.164.0).
- Access to the daemon Unix socket at `~/.codex/app-server-control/app-server-control.sock`.

The transport uses a Unix socket; Windows transport is not implemented.

## Install

Clone the repository into a persistent directory outside your session workspace:

```sh
git clone https://github.com/OWNER/codex-independent-threads.git
```

Add the server to `~/.codex/config.toml`, using the absolute path to your clone:

```toml
[mcp_servers.independent_threads]
enabled = true
command = "/usr/bin/python3"
args = ["/absolute/path/codex-independent-threads/thread_mcp.py"]
tool_timeout_sec = 240
```

Reload MCP configuration or start a new Codex session to discover the tools.

## Tools

### `create_thread`

```json
{"cwd":"/absolute/existing/workspace","title":"New session"}
```

Creates a persistent independent session with the supplied default working directory. It returns the thread metadata, `app_host_id`, and `app_url`. No model turn starts until a message is sent. Use the returned `app_url` when linking to a remote session so the App routes to the correct host.

### `send_message_to_thread`

```json
{"thread_id":"EXISTING_THREAD_ID","message":"Run pwd and report the working directory.","timeout":180}
```

Starts a turn in an idle session and waits for its reply. The optional timeout accepts 1–180 seconds. Active sessions are rejected. Interactive approval and dynamic tool requests require the App; the synchronous adapter cannot present them. On a timeout or error, inspect the reported thread and turn before retrying: the mutation may already have succeeded.

## Command-line client

The client accepts one JSON request on standard input. Supported operations are `create_thread`, `send_message_to_thread`, and `read_thread`.

```sh
python3 thread_client.py <<'JSON'
{"operation":"create_thread","cwd":"/absolute/existing/workspace","title":"New session"}
JSON
```

## Implementation

- `thread_mcp.py`: MCP JSON-RPC over standard input/output, tool schemas, and argument validation.
- `thread_client.py`: session lifecycle operations, turn waiting, and host-aware App links.
- `daemon_client.py`: JSON-RPC over the running daemon's Unix WebSocket, using only the Python standard library.

Creation uses the running App daemon rather than launching a separate app-server process. In verification, a second connection received `thread/started` and `thread/name/updated` immediately, and the App displayed the new session without opening a link. A separate app-server process did not send those events to the App-connected daemon. The adapter fails when the daemon is unavailable instead of silently changing transport.
