@tool
extends RefCounted
class_name AgentBridgeServer
## Newline-delimited JSON over loopback TCP.
##
## Shared by both halves of the bridge: the editor plugin, which inspects and
## edits scenes, and the runtime probe, which drives a running game. Each
## supplies its own command table; everything about framing, authentication and
## error reporting lives here.
##
## The socket binds to 127.0.0.1 only and every request must carry the token
## from the project's `.godot-agent/bridge_token` file. This process can open
## files, edit scenes and inject input -- it is not something to expose beyond
## the local machine.

## Where the shared secret lives, relative to the project root.
const TOKEN_PATH := "res://.godot-agent/bridge_token"

## Refuse a request line longer than this. A client that never sends a newline
## would otherwise grow the buffer without bound.
const MAX_REQUEST_BYTES := 1 << 20

signal client_error(message: String)

var _server := TCPServer.new()
var _peers: Array[StreamPeerTCP] = []
var _buffers: Array[String] = []
var _commands: Dictionary = {}
var _token: String = ""
var _port: int = 0


## Start listening. `commands` maps a command name to a Callable taking a
## Dictionary of arguments and returning a Dictionary result.
func start(port: int, commands: Dictionary) -> Error:
	_commands = commands
	_token = _read_or_create_token()
	_port = port

	var error := _server.listen(port, "127.0.0.1")
	if error != OK:
		push_error("[godot-agent] could not listen on 127.0.0.1:%d (%d)" % [port, error])
	return error


func stop() -> void:
	for peer in _peers:
		peer.disconnect_from_host()
	_peers.clear()
	_buffers.clear()
	_server.stop()


func is_listening() -> bool:
	return _server.is_listening()


func port() -> int:
	return _port


## Accept new connections and handle any complete request lines. Call this
## every frame from the owning node's `_process`.
func poll() -> void:
	while _server.is_connection_available():
		var peer := _server.take_connection()
		peer.set_no_delay(true)
		_peers.append(peer)
		_buffers.append("")

	for index in range(_peers.size() - 1, -1, -1):
		var peer: StreamPeerTCP = _peers[index]
		peer.poll()

		if peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
			_peers.remove_at(index)
			_buffers.remove_at(index)
			continue

		var available := peer.get_available_bytes()
		if available > 0:
			_buffers[index] += peer.get_utf8_string(available)

		if _buffers[index].length() > MAX_REQUEST_BYTES:
			_send(peer, {"ok": false, "error": "request too large"})
			peer.disconnect_from_host()
			_peers.remove_at(index)
			_buffers.remove_at(index)
			continue

		while "\n" in _buffers[index]:
			var split := _buffers[index].split("\n", true, 1)
			_buffers[index] = split[1] if split.size() > 1 else ""
			var line: String = split[0].strip_edges()
			if line.is_empty():
				continue
			_send(peer, _handle(line))


func _handle(line: String) -> Dictionary:
	var parsed: Variant = JSON.parse_string(line)
	if typeof(parsed) != TYPE_DICTIONARY:
		return {"ok": false, "error": "request must be a JSON object"}

	var request: Dictionary = parsed
	if String(request.get("token", "")) != _token:
		return {"ok": false, "error": "invalid or missing token"}

	var command := String(request.get("command", ""))
	if not _commands.has(command):
		var known: Array = _commands.keys()
		known.sort()
		return {
			"ok": false,
			"error": "unknown command %s; known: %s" % [command, ", ".join(known)],
		}

	var args: Dictionary = request.get("args", {})
	if typeof(args) != TYPE_DICTIONARY:
		return {"ok": false, "error": "args must be a JSON object"}

	var callable: Callable = _commands[command]
	var result: Variant = callable.call(args)
	if typeof(result) != TYPE_DICTIONARY:
		return {"ok": true, "result": result}
	var response: Dictionary = result
	if not response.has("ok"):
		response["ok"] = true
	return response


func _send(peer: StreamPeerTCP, payload: Dictionary) -> void:
	var encoded := JSON.stringify(payload) + "\n"
	peer.put_data(encoded.to_utf8_buffer())


## Read the shared token, generating one on first run.
func _read_or_create_token() -> String:
	if FileAccess.file_exists(TOKEN_PATH):
		var existing := FileAccess.open(TOKEN_PATH, FileAccess.READ)
		if existing != null:
			var value := existing.get_as_text().strip_edges()
			existing.close()
			if not value.is_empty():
				return value

	DirAccess.make_dir_recursive_absolute(TOKEN_PATH.get_base_dir())
	var generated := _random_token()
	var file := FileAccess.open(TOKEN_PATH, FileAccess.WRITE)
	if file == null:
		push_error("[godot-agent] could not write %s" % TOKEN_PATH)
		return generated
	file.store_string(generated)
	file.close()
	return generated


func _random_token() -> String:
	var crypto := Crypto.new()
	return crypto.generate_random_bytes(32).hex_encode()
