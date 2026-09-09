"""Client for the ``godot_agent_bridge`` addon.

Speaks newline-delimited JSON over loopback TCP to whichever half of the addon
is running: the editor plugin (scene inspection, reimport, editor screenshots)
or the runtime probe inside a running game (input injection, frame stepping,
screenshots, live state).

Connections are short-lived by design -- one per call. A long-lived socket
would have to survive the game restarting, the editor reloading the plugin, and
a scene change, and reconnecting costs about a millisecond on loopback.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from godot_agent.config import get_settings

__all__ = [
    "DEFAULT_CONNECT_TIMEOUT",
    "EDITOR_PORT_OFFSET",
    "RUNTIME_PORT_OFFSET",
    "BridgeClient",
    "BridgeError",
    "BridgeNotRunningError",
    "read_token",
]

#: Where the addon writes the shared secret, relative to the project root.
TOKEN_RELATIVE_PATH = Path(".godot-agent") / "bridge_token"

#: The editor half listens on the base port; the runtime probe on the next.
EDITOR_PORT_OFFSET = 0
RUNTIME_PORT_OFFSET = 1

#: Seconds to wait for a reply once connected. Commands such as a frame step
#: legitimately take a while.
DEFAULT_TIMEOUT = 15.0

#: Seconds to wait for the connection itself. Kept short on purpose: the bridge
#: is on loopback, so it either answers at once or is not running, and Windows
#: takes about two seconds to refuse a closed port. Waiting the full reply
#: timeout to discover "the addon is not enabled" is pure latency.
DEFAULT_CONNECT_TIMEOUT = 1.0

#: Even shorter, for a liveness check whose whole job is to answer quickly.
PROBE_CONNECT_TIMEOUT = 0.25


class BridgeError(RuntimeError):
    """The bridge was reachable but rejected or failed the request."""


class BridgeNotRunningError(BridgeError):
    """Nothing is listening on the bridge port.

    Almost always means the addon is not enabled, or the game is not running
    with ``GODOT_AGENT_PROBE=1``.
    """


def read_token(project_root: Path) -> str:
    """Read the bridge token the addon generated for this project.

    Raises:
        BridgeNotRunningError: if the token file does not exist, which means
            the addon has never started in this project.
    """
    path = project_root / TOKEN_RELATIVE_PATH
    if not path.is_file():
        raise BridgeNotRunningError(
            f"No bridge token at {path}. Enable the 'Godot Agent Bridge' addon in "
            "Project Settings > Plugins and open the project once; the addon "
            "writes the token on first run."
        )
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise BridgeNotRunningError(f"The bridge token at {path} is empty.")
    return token


@dataclass(slots=True)
class BridgeClient:
    """A client for one half of the bridge."""

    project_root: Path
    port: int
    timeout: float = DEFAULT_TIMEOUT
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT

    @classmethod
    def editor(cls, project_root: Path, timeout: float = DEFAULT_TIMEOUT) -> BridgeClient:
        """A client for the editor half."""
        base = get_settings().bridge_port
        return cls(project_root, base + EDITOR_PORT_OFFSET, timeout)

    @classmethod
    def runtime(cls, project_root: Path, timeout: float = DEFAULT_TIMEOUT) -> BridgeClient:
        """A client for the probe inside a running game."""
        base = get_settings().bridge_port
        return cls(project_root, base + RUNTIME_PORT_OFFSET, timeout)

    def call(self, command: str, **args: Any) -> dict[str, Any]:
        """Send one command and return its result.

        Raises:
            BridgeNotRunningError: if nothing is listening.
            BridgeError: if the bridge reported a failure or replied with
                something unparseable.
        """
        token = read_token(self.project_root)
        request = json.dumps({"token": token, "command": command, "args": args}) + "\n"

        # Connect and exchange are separate phases with different meanings: a
        # failure to connect means the bridge is not running, while a failure
        # after connecting means it is up but wedged. Reporting the first as
        # the second sends the reader looking in entirely the wrong place.
        try:
            connection = socket.create_connection(
                ("127.0.0.1", self.port), self.connect_timeout
            )
        except OSError as error:
            raise BridgeNotRunningError(self._not_running_message()) from error

        try:
            with connection:
                connection.settimeout(self.timeout)
                connection.sendall(request.encode("utf-8"))
                payload = _read_line(connection)
        except TimeoutError as error:
            raise BridgeError(
                f"The bridge on port {self.port} accepted the connection but did not "
                f"reply to {command!r} within {self.timeout:.0f}s."
            ) from error
        except OSError as error:
            raise BridgeError(
                f"The connection to the bridge on port {self.port} failed "
                f"during {command!r}: {error}"
            ) from error

        try:
            response = json.loads(payload)
        except json.JSONDecodeError as error:
            raise BridgeError(f"the bridge replied with invalid JSON: {payload[:200]!r}") from error

        if not isinstance(response, dict):
            raise BridgeError(f"the bridge replied with a {type(response).__name__}, not an object")
        if not response.get("ok", False):
            raise BridgeError(str(response.get("error", "the bridge reported an unnamed failure")))
        return response

    def _not_running_message(self) -> str:
        """Explain what to start, based on which half was addressed."""
        is_editor_half = self.port == get_settings().bridge_port + EDITOR_PORT_OFFSET
        remedy = (
            "Open the project in the Godot editor with the bridge addon enabled."
            if is_editor_half
            else "Start the game with GODOT_AGENT_PROBE=1 so the runtime probe runs."
        )
        return f"Nothing is listening on 127.0.0.1:{self.port}. {remedy}"

    def ping(self) -> dict[str, Any]:
        """Check the bridge is up and report which half answered."""
        return self.call("ping")

    def is_available(self) -> bool:
        """True when this half of the bridge is reachable right now.

        Uses a very short connect timeout: the only useful answers here are
        "yes, immediately" and "no".
        """
        probe = BridgeClient(
            self.project_root,
            self.port,
            timeout=self.timeout,
            connect_timeout=PROBE_CONNECT_TIMEOUT,
        )
        try:
            probe.ping()
        except BridgeError:
            return False
        return True


def _read_line(connection: socket.socket) -> str:
    """Read until the first newline. Replies are one line by protocol."""
    chunks: list[bytes] = []
    while True:
        chunk = connection.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    return b"".join(chunks).split(b"\n", 1)[0].decode("utf-8", errors="replace")
