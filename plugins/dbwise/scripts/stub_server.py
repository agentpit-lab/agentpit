"""The MCP server that runs until the dependencies exist.

Claude Code gives a server a few tens of seconds to answer initialize; building the environment takes
longer. This stub answers at once with one tool, dbwise_status, starts the build as a detached child
and tells the model what to relay to the user. It speaks newline-delimited JSON-RPC 2.0 over stdio,
with the standard library only, and never writes anything but protocol messages to stdout.
"""
import json
import os
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import bootstrap
from errors import RELOAD

# Newest first: an unknown client version is answered with the first entry.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SPAWN_GRACE_SECONDS = 20
# Another session's status probe holds the lock for an instant; the start probe waits that out.
START_PROBE_GRACE_SECONDS = 0.5
LOG_TAIL_LINES = 20
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

TEXT_BUILDING = ("dbwise is installing its dependencies (about a minute on the first start). "
                 f"When it finishes, reconnect {RELOAD}")
TEXT_ANOTHER_SESSION = ("Another session is installing the dbwise dependencies (about a minute on the first "
                        f"start). When it finishes, reconnect {RELOAD}")
TEXT_READY = f"The dependencies are installed. Reconnect {RELOAD}"

STATUS_TOOL = {
    "name": "dbwise_status",
    "description": ("Reports whether dbwise has finished setting itself up. Call it when the user asks about "
                    "databases or dbwise, and relay the answer to the user as written."),
    "inputSchema": {"type": "object", "properties": {}},
}


def plugin_version(root: Path) -> str:
    try:
        data = json.loads((Path(root) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        return str(data["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return "0.0.0"


class Stub:
    def __init__(self, paths, *, os_name: str = os.name, build_platform=None, popen=subprocess.Popen,
                 clock=time.monotonic):
        self.paths = paths
        self.os_name = os_name
        self.build_platform = build_platform if build_platform is not None else sysconfig.get_platform()
        self.popen = popen
        self.clock = clock
        self.spawned_at = None

    def log(self, message: str) -> None:
        try:
            self.paths.home.mkdir(parents=True, exist_ok=True)
            with open(self.paths.log, "a", encoding="utf-8") as out:
                out.write(f"stub: {message}\n")
        except OSError:
            pass

    def build_command(self) -> list:
        override = os.environ.get("DBWISE_BUILD_COMMAND")
        if override and Path(override).is_file():
            return [sys.executable, override]
        # -I makes the child ignore PYTHON* variables and the user site-packages.
        return [sys.executable, "-I", str(Path(bootstrap.__file__)), "--build"]

    def start_build(self) -> str:
        if bootstrap.arm64_refusal(self.os_name, self.build_platform):
            return "refused"
        if bootstrap.is_current(self.paths):
            return "ready"
        try:
            self.paths.home.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not bootstrap.lock_is_free(self.paths.lock, START_PROBE_GRACE_SECONDS):
            return "held"
        flags = ({"creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP} if self.os_name == "nt"
                 else {"start_new_session": True})
        try:
            with open(self.paths.log, "wb") as out:
                self.popen(self.build_command(), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                           close_fds=True, **flags)
        except OSError as error:
            self.log(f"cannot start the build: {error}")
            return "failed"
        self.spawned_at = self.clock()
        return "spawned"

    def log_tail(self) -> str:
        try:
            lines = self.paths.log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return "(no log was written)"
        return "\n".join(lines[-LOG_TAIL_LINES:])

    def state(self) -> tuple:
        refusal = bootstrap.arm64_refusal(self.os_name, self.build_platform)
        if refusal:
            return "refused", refusal
        if bootstrap.is_current(self.paths):
            return "ready", TEXT_READY
        recently_started = self.spawned_at is not None and self.clock() - self.spawned_at <= SPAWN_GRACE_SECONDS
        if recently_started:
            return "building", TEXT_BUILDING
        if not bootstrap.lock_is_free(self.paths.lock):
            # Only a build this stub started is "building"; any other holder is a second session's build.
            return ("building", TEXT_BUILDING) if self.spawned_at is not None else ("elsewhere", TEXT_ANOTHER_SESSION)
        return "failed", ("dbwise could not finish installing its dependencies. Last lines of the build log:\n"
                          f"{self.log_tail()}\nFull log: {self.paths.log}\n"
                          "Show the user this text; they can fix the cause and restart Claude Code.")

    def handle(self, message: dict):
        method = message.get("method")
        params = message.get("params") or {}
        if method == "initialize":
            asked = params.get("protocolVersion")
            return {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dbwise", "version": plugin_version(self.paths.root)}}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [STATUS_TOOL]}
        if method == "tools/call":
            if params.get("name") != STATUS_TOOL["name"]:
                raise ProtocolError(-32602, f"unknown tool: {params.get('name')}")
            return {"content": [{"type": "text", "text": self.state()[1]}]}
        raise ProtocolError(-32601, f"method not found: {method}")

    def respond(self, raw: bytes):
        try:
            message = json.loads(raw.decode("utf-8"))
        except ValueError:
            return error_reply(None, -32700, "parse error")
        if not isinstance(message, dict):
            return error_reply(None, -32600, "invalid request")
        if "id" not in message or "method" not in message:
            return None
        try:
            return {"jsonrpc": "2.0", "id": message["id"], "result": self.handle(message)}
        except ProtocolError as error:
            return error_reply(message["id"], error.code, str(error))
        except Exception as error:
            self.log(f"handler failed: {error!r}")
            return error_reply(message["id"], -32603, "internal error")

    def serve(self, stdin, stdout) -> int:
        try:
            self.start_build()
        except Exception as error:
            self.log(f"cannot start the build: {error!r}")
        while True:
            try:
                line = stdin.readline()
            except (OSError, ValueError):
                return 0
            if not line:
                return 0
            if not line.strip():
                continue
            reply = self.respond(line)
            if reply is None:
                continue
            try:
                stdout.write(json.dumps(reply, separators=(",", ":")).encode("utf-8") + b"\n")
                stdout.flush()
            except OSError:
                return 0


class ProtocolError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def error_reply(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def main(paths) -> int:
    return Stub(paths).serve(sys.stdin.buffer, sys.stdout.buffer)
