"""Tests for the bridge client and the playtest tools.

A mock server speaks the same newline-delimited JSON protocol the GDScript
addon implements, so the client, the framing, the auth check and the tool layer
are all exercised without needing Godot running.
"""

from __future__ import annotations

import json
import shutil
import socket
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from godot_agent.bridge import (
    TOKEN_RELATIVE_PATH,
    BridgeClient,
    BridgeError,
    BridgeNotRunningError,
    read_token,
)
from godot_agent.config import Settings, reset_settings, set_settings
from godot_agent.tools.playtest import (
    godot_install_bridge,
    godot_playtest_input,
    godot_playtest_screenshot,
    godot_playtest_state,
    godot_playtest_status,
)

FIXTURES = Path(__file__).parent / "fixtures" / "scenes"
TOKEN = "test-token-abc123"


class MockBridge:
    """A stand-in for the GDScript addon, speaking the same wire protocol."""

    def __init__(self, handler: Callable[[str, dict], dict]) -> None:
        self._handler = handler
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(8)
        self.port = self._socket.getsockname()[1]
        self.requests: list[dict] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._running = True
        self._thread.start()

    def _serve(self) -> None:
        while self._running:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                return
            with connection:
                try:
                    data = b""
                    while b"\n" not in data:
                        chunk = connection.recv(4096)
                        if not chunk:
                            return
                        data += chunk
                    request = json.loads(data.split(b"\n", 1)[0])
                    self.requests.append(request)

                    if request.get("token") != TOKEN:
                        response = {"ok": False, "error": "invalid or missing token"}
                    else:
                        response = self._handler(
                            request.get("command", ""), request.get("args", {})
                        )
                    connection.sendall((json.dumps(response) + "\n").encode())
                except (OSError, json.JSONDecodeError):
                    return

    def close(self) -> None:
        self._running = False
        self._socket.close()


@pytest.fixture(autouse=True)
def _isolated_settings():
    set_settings(Settings(godot_bin=None, project_path=None))
    yield
    reset_settings()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "game"
    root.mkdir()
    shutil.copy(FIXTURES / "project.godot", root / "project.godot")
    token_path = root / TOKEN_RELATIVE_PATH
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(TOKEN, encoding="utf-8")
    return root


def _default_handler(command: str, args: dict) -> dict:
    responses = {
        "ping": {"ok": True, "half": "runtime", "frame": 42, "headless": False, "scene": "x.tscn"},
        "tap": {"ok": True, "tapped": args.get("action"), "frames": args.get("frames")},
        "press": {"ok": True, "pressed": args.get("action")},
        "release": {"ok": True, "released": args.get("action")},
        "state": {"ok": True, "frame": 42, "tree": {"name": "Main", "type": "Node2D"}},
        "get_property": {"ok": True, "node": args.get("node"), "value": 7},
        "screenshot": {"ok": True, "path": "/tmp/x.png", "width": 640, "blank": False},
    }
    return responses.get(command, {"ok": False, "error": f"unknown command {command}"})


@pytest.fixture
def bridge(project: Path):
    """A mock runtime bridge, with settings pointed at its port."""
    server = MockBridge(_default_handler)
    # The runtime probe sits one port above the configured base.
    set_settings(Settings(godot_bin=None, project_path=project, bridge_port=server.port - 1))
    yield server
    server.close()


# -- token ----------------------------------------------------------------


def test_reads_the_token(project: Path) -> None:
    assert read_token(project) == TOKEN


def test_missing_token_explains_how_to_create_it(tmp_path: Path) -> None:
    with pytest.raises(BridgeNotRunningError, match="Plugins"):
        read_token(tmp_path)


def test_empty_token_is_rejected(project: Path) -> None:
    (project / TOKEN_RELATIVE_PATH).write_text("   ", encoding="utf-8")
    with pytest.raises(BridgeNotRunningError, match="empty"):
        read_token(project)


# -- client ---------------------------------------------------------------


def test_call_round_trips(project: Path, bridge: MockBridge) -> None:
    result = BridgeClient.runtime(project).ping()
    assert result["half"] == "runtime"
    assert bridge.requests[0]["command"] == "ping"
    assert bridge.requests[0]["token"] == TOKEN


def test_call_sends_arguments(project: Path, bridge: MockBridge) -> None:
    BridgeClient.runtime(project).call("tap", action="jump", frames=3)
    assert bridge.requests[0]["args"] == {"action": "jump", "frames": 3}


def test_a_wrong_token_is_reported(project: Path, bridge: MockBridge) -> None:
    (project / TOKEN_RELATIVE_PATH).write_text("wrong", encoding="utf-8")
    with pytest.raises(BridgeError, match="invalid or missing token"):
        BridgeClient.runtime(project).ping()


def test_a_closed_port_says_how_to_start_the_probe(project: Path) -> None:
    set_settings(Settings(godot_bin=None, project_path=project, bridge_port=1))
    with pytest.raises(BridgeNotRunningError, match="GODOT_AGENT_PROBE=1"):
        BridgeClient.runtime(project).ping()


def test_a_closed_editor_port_says_to_open_the_editor(project: Path) -> None:
    set_settings(Settings(godot_bin=None, project_path=project, bridge_port=1))
    with pytest.raises(BridgeNotRunningError, match="Godot editor"):
        BridgeClient.editor(project).ping()


def test_a_reported_failure_becomes_an_error(project: Path) -> None:
    server = MockBridge(lambda command, args: {"ok": False, "error": "no scene is running"})
    set_settings(Settings(godot_bin=None, project_path=project, bridge_port=server.port - 1))
    try:
        with pytest.raises(BridgeError, match="no scene is running"):
            BridgeClient.runtime(project).call("state")
    finally:
        server.close()


def test_an_unparseable_reply_is_reported(project: Path) -> None:
    """A malformed reply must be named as such, not silently treated as success."""

    class BrokenBridge(MockBridge):
        def _serve(self) -> None:
            while self._running:
                try:
                    connection, _ = self._socket.accept()
                except OSError:
                    return
                with connection:
                    connection.recv(4096)
                    connection.sendall(b"this is not json\n")

    server = BrokenBridge(_default_handler)
    set_settings(Settings(godot_bin=None, project_path=project, bridge_port=server.port - 1))
    try:
        with pytest.raises(BridgeError, match="invalid JSON"):
            BridgeClient.runtime(project).ping()
    finally:
        server.close()


def test_a_non_object_reply_is_reported(project: Path) -> None:
    class ListBridge(MockBridge):
        def _serve(self) -> None:
            while self._running:
                try:
                    connection, _ = self._socket.accept()
                except OSError:
                    return
                with connection:
                    connection.recv(4096)
                    connection.sendall(b"[1, 2, 3]\n")

    server = ListBridge(_default_handler)
    set_settings(Settings(godot_bin=None, project_path=project, bridge_port=server.port - 1))
    try:
        with pytest.raises(BridgeError, match="not an object"):
            BridgeClient.runtime(project).ping()
    finally:
        server.close()


def test_is_available_does_not_raise(project: Path) -> None:
    set_settings(Settings(godot_bin=None, project_path=project, bridge_port=1))
    assert BridgeClient.runtime(project).is_available() is False


# -- tools ----------------------------------------------------------------


def test_status_reports_a_live_probe(project: Path, bridge: MockBridge) -> None:
    result = godot_playtest_status.invoke({"project": str(project)})
    assert result["ok"]
    assert result["runtime_available"] is True
    assert result["frame"] == 42


def test_status_reports_a_missing_probe(project: Path) -> None:
    set_settings(Settings(godot_bin=None, project_path=project, bridge_port=1))
    result = godot_playtest_status.invoke({"project": str(project)})
    assert not result["ok"]
    assert result["runtime_available"] is False


def test_status_surfaces_the_headless_warning(project: Path) -> None:
    """Headless input tests pass vacuously; the agent has to be told."""
    server = MockBridge(
        lambda command, args: {
            "ok": True,
            "headless": True,
            "warning": "Running headless: Godot does not deliver InputEvents",
        }
    )
    set_settings(Settings(godot_bin=None, project_path=project, bridge_port=server.port - 1))
    try:
        result = godot_playtest_status.invoke({"project": str(project)})
        assert result["headless"] is True
        assert "InputEvents" in result["warning"]
    finally:
        server.close()


def test_input_tap(project: Path, bridge: MockBridge) -> None:
    result = godot_playtest_input.invoke(
        {"action": "jump", "mode": "tap", "frames": 3, "project": str(project)}
    )
    assert result["ok"]
    assert result["tapped"] == "jump"


def test_input_rejects_an_unknown_mode(project: Path, bridge: MockBridge) -> None:
    result = godot_playtest_input.invoke(
        {"action": "jump", "mode": "wiggle", "project": str(project)}
    )
    assert not result["ok"]
    assert "tap, press or release" in result["error"]


def test_state_dumps_the_tree(project: Path, bridge: MockBridge) -> None:
    result = godot_playtest_state.invoke({"project": str(project)})
    assert result["ok"]
    assert result["tree"]["name"] == "Main"


def test_state_reads_one_property(project: Path, bridge: MockBridge) -> None:
    result = godot_playtest_state.invoke(
        {"node": "HUD/Score", "prop": "text", "project": str(project)}
    )
    assert result["value"] == 7


def test_state_requires_both_node_and_prop(project: Path, bridge: MockBridge) -> None:
    result = godot_playtest_state.invoke({"node": "HUD", "project": str(project)})
    assert not result["ok"]
    assert "both node and prop" in result["error"]


def test_screenshot_warns_about_a_blank_frame(project: Path) -> None:
    """A blank screenshot reading as success is worse than an error."""
    server = MockBridge(
        lambda command, args: {"ok": True, "path": "/tmp/x.png", "blank": True}
    )
    set_settings(Settings(godot_bin=None, project_path=project, bridge_port=server.port - 1))
    try:
        result = godot_playtest_screenshot.invoke({"project": str(project)})
        assert "did not render" in result["warning"]
    finally:
        server.close()


def test_screenshot_is_quiet_when_the_frame_is_fine(project: Path, bridge: MockBridge) -> None:
    result = godot_playtest_screenshot.invoke({"project": str(project)})
    assert result["ok"]
    assert "warning" not in result


# -- addon install --------------------------------------------------------


def test_install_bridge_copies_the_addon(project: Path) -> None:
    result = godot_install_bridge.invoke({"project": str(project)})
    assert result["ok"]
    addon = project / "addons" / "godot_agent_bridge"
    assert (addon / "plugin.cfg").is_file()
    assert (addon / "bridge_server.gd").is_file()
    assert (addon / "agent_probe.gd").is_file()
    assert (addon / "editor_plugin.gd").is_file()


def test_install_bridge_gitignores_the_token(project: Path) -> None:
    """The token is a local secret; committing it would share control of the game."""
    godot_install_bridge.invoke({"project": str(project)})
    assert ".godot-agent/" in (project / ".gitignore").read_text(encoding="utf-8")


def test_install_bridge_does_not_duplicate_the_gitignore_entry(project: Path) -> None:
    godot_install_bridge.invoke({"project": str(project)})
    godot_install_bridge.invoke({"project": str(project)})
    content = (project / ".gitignore").read_text(encoding="utf-8")
    assert content.count(".godot-agent/") == 1


def test_install_bridge_explains_the_manual_steps(project: Path) -> None:
    """Enabling a plugin and adding an autoload cannot be done from outside the editor."""
    result = godot_install_bridge.invoke({"project": str(project)})
    steps = " ".join(result["next_steps"])
    assert "Plugins" in steps
    assert "autoload" in steps
    assert "GODOT_AGENT_PROBE=1" in steps


# -- addon / client consistency -------------------------------------------

ADDON = Path(__file__).resolve().parent.parent / "godot-addon" / "addons" / "godot_agent_bridge"


def _gdscript_commands(script: Path) -> set[str]:
    """The command names a half of the addon registers in its command table."""
    import re

    text = script.read_text(encoding="utf-8")
    table = text.split("_server.start(", 1)[-1].split("})", 1)[0]
    return set(re.findall(r'"(\w+)":\s*_cmd_', table))


def test_addon_ships_all_four_files() -> None:
    for name in ("plugin.cfg", "bridge_server.gd", "editor_plugin.gd", "agent_probe.gd"):
        assert (ADDON / name).is_file(), name


def test_runtime_commands_cover_what_the_client_calls() -> None:
    """A command the client sends but the addon lacks fails only at runtime."""
    registered = _gdscript_commands(ADDON / "agent_probe.gd")
    used = {"ping", "press", "release", "tap", "state", "get_property", "screenshot"}
    assert used <= registered, f"missing from the probe: {sorted(used - registered)}"


def test_editor_commands_are_registered() -> None:
    registered = _gdscript_commands(ADDON / "editor_plugin.gd")
    assert {"ping", "describe_scene", "open_scene", "save_scene", "screenshot"} <= registered


def test_addon_and_client_agree_on_the_token_path() -> None:
    """A mismatch here makes every call fail with a confusing auth error."""
    server_source = (ADDON / "bridge_server.gd").read_text(encoding="utf-8")
    expected = "res://" + TOKEN_RELATIVE_PATH.as_posix()
    assert f'const TOKEN_PATH := "{expected}"' in server_source


def test_addon_and_client_agree_on_the_port_offset() -> None:
    from godot_agent.bridge import RUNTIME_PORT_OFFSET

    probe_source = (ADDON / "agent_probe.gd").read_text(encoding="utf-8")
    assert f"const PORT_OFFSET := {RUNTIME_PORT_OFFSET}" in probe_source


def test_addon_and_client_agree_on_the_default_port() -> None:
    from godot_agent.config import DEFAULT_BRIDGE_PORT

    for script in ("agent_probe.gd", "editor_plugin.gd"):
        source = (ADDON / script).read_text(encoding="utf-8")
        assert f"const DEFAULT_PORT := {DEFAULT_BRIDGE_PORT}" in source, script
