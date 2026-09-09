extends Node
## The runtime half of the agent bridge: an autoload inside the running game.
##
## This is what makes real QA possible. Compiling a project proves very little
## about a game; what matters is whether pressing "jump" makes the player jump.
## The probe injects input, steps frames deterministically, captures the screen
## and reports live node state, so the agent can play the game and check.
##
## Add it as an autoload named `AgentProbe`. It does nothing unless
## `GODOT_AGENT_PROBE=1` is set, so a shipped build carries no open socket.
##
## Two engine limits are worth knowing, because they silently invalidate
## results rather than erroring:
##
## * Godot does not deliver `InputEvent`s under `--headless`, so input
##   injection needs a real display. `ping` reports `headless` for this reason.
## * `--write-movie` forces fixed-step timing, which is what makes a recorded
##   playtest reproducible frame for frame.

const AgentBridgeServer := preload("res://addons/godot_agent_bridge/bridge_server.gd")

## The editor half uses the base port; the runtime probe uses the next one, so
## both can run at once.
const PORT_OFFSET := 1
const DEFAULT_PORT := 45719

var _server: AgentBridgeServer
var _held: Dictionary = {}
var _frame: int = 0


func _ready() -> void:
	if OS.get_environment("GODOT_AGENT_PROBE") != "1":
		set_process(false)
		return

	var port := DEFAULT_PORT
	if OS.has_environment("GODOT_AGENT_BRIDGE_PORT"):
		port = int(OS.get_environment("GODOT_AGENT_BRIDGE_PORT"))
	port += PORT_OFFSET

	_server = AgentBridgeServer.new()
	var error := _server.start(port, {
		"ping": _cmd_ping,
		"press": _cmd_press,
		"release": _cmd_release,
		"tap": _cmd_tap,
		"step": _cmd_step,
		"screenshot": _cmd_screenshot,
		"state": _cmd_state,
		"get_property": _cmd_get_property,
		"seed": _cmd_seed,
		"quit": _cmd_quit,
	})
	if error == OK:
		print("[godot-agent] probe listening on 127.0.0.1:%d" % port)
	set_process(error == OK)


func _exit_tree() -> void:
	_release_all()
	if _server != null:
		_server.stop()
		_server = null


func _process(_delta: float) -> void:
	_frame += 1
	if _server != null:
		_server.poll()


# -- commands -----------------------------------------------------------------


func _cmd_ping(_args: Dictionary) -> Dictionary:
	var headless := DisplayServer.get_name() == "headless"
	var response := {
		"half": "runtime",
		"frame": _frame,
		"headless": headless,
		"scene": get_tree().current_scene.scene_file_path if get_tree().current_scene else "",
	}
	if headless:
		response["warning"] = (
			"Running headless: Godot does not deliver InputEvents in this mode, "
			"so press/release/tap will not reach the game. Run with a display "
			"for input tests."
		)
	return response


func _cmd_press(args: Dictionary) -> Dictionary:
	var action := String(args.get("action", ""))
	var problem := _validate_action(action)
	if not problem.is_empty():
		return {"ok": false, "error": problem}

	Input.action_press(action, float(args.get("strength", 1.0)))
	_held[action] = true
	return {"pressed": action}


func _cmd_release(args: Dictionary) -> Dictionary:
	var action := String(args.get("action", ""))
	var problem := _validate_action(action)
	if not problem.is_empty():
		return {"ok": false, "error": problem}

	Input.action_release(action)
	_held.erase(action)
	return {"released": action}


func _cmd_tap(args: Dictionary) -> Dictionary:
	## Press, hold for a number of frames, release. This is the honest way to
	## simulate a button press: a press and release in the same frame is often
	## missed entirely by `is_action_just_pressed`.
	var action := String(args.get("action", ""))
	var problem := _validate_action(action)
	if not problem.is_empty():
		return {"ok": false, "error": problem}

	var frames := maxi(1, int(args.get("frames", 2)))
	Input.action_press(action, float(args.get("strength", 1.0)))
	for _i in range(frames):
		await get_tree().process_frame
	Input.action_release(action)
	return {"tapped": action, "frames": frames}


func _cmd_step(args: Dictionary) -> Dictionary:
	var frames := maxi(1, int(args.get("frames", 1)))
	for _i in range(frames):
		await get_tree().process_frame
	return {"frame": _frame}


func _cmd_screenshot(args: Dictionary) -> Dictionary:
	var path := String(args.get("path", "user://agent_screenshot.png"))
	# Wait a frame so the capture reflects any input applied just before it.
	await RenderingServer.frame_post_draw

	var image := get_viewport().get_texture().get_image()
	if image == null:
		return {"ok": false, "error": "the viewport produced no image"}

	DirAccess.make_dir_recursive_absolute(path.get_base_dir())
	var error := image.save_png(path)
	if error != OK:
		return {"ok": false, "error": "could not write %s (%d)" % [path, error]}

	return {
		"path": ProjectSettings.globalize_path(path),
		"width": image.get_width(),
		"height": image.get_height(),
		"blank": _is_blank(image),
	}


func _is_blank(image: Image) -> bool:
	## A uniformly coloured frame almost always means the scene never rendered.
	## Reporting it beats handing back a screenshot that looks like a success.
	if image.get_width() == 0 or image.get_height() == 0:
		return true
	var first := image.get_pixel(0, 0)
	var step := maxi(1, image.get_width() / 16)
	for x in range(0, image.get_width(), step):
		for y in range(0, image.get_height(), step):
			if not image.get_pixel(x, y).is_equal_approx(first):
				return false
	return true


func _cmd_state(args: Dictionary) -> Dictionary:
	var scene := get_tree().current_scene
	if scene == null:
		return {"ok": false, "error": "no scene is running"}
	var depth := int(args.get("depth", 3))
	return {"frame": _frame, "tree": _dump(scene, scene, depth)}


func _dump(node: Node, root: Node, depth: int) -> Dictionary:
	var entry := {
		"name": node.name,
		"type": node.get_class(),
		"path": "." if node == root else String(root.get_path_to(node)),
	}
	if node is Node2D:
		entry["position"] = [node.global_position.x, node.global_position.y]
		entry["visible"] = node.visible
	if node is CanvasItem:
		entry["visible"] = node.visible

	if depth > 0:
		var children: Array = []
		for child in node.get_children():
			children.append(_dump(child, root, depth - 1))
		if not children.is_empty():
			entry["children"] = children
	return entry


func _cmd_get_property(args: Dictionary) -> Dictionary:
	## Read one property off a node. This is how a playtest asserts on game
	## state -- score, health, whether the player is alive -- rather than
	## guessing from a screenshot.
	var scene := get_tree().current_scene
	if scene == null:
		return {"ok": false, "error": "no scene is running"}

	var node_path := String(args.get("node", "."))
	var node: Node = scene if node_path == "." else scene.get_node_or_null(node_path)
	if node == null:
		return {"ok": false, "error": "node %s does not exist in the running scene" % node_path}

	var property := String(args.get("property", ""))
	if not property in node:
		return {"ok": false, "error": "%s has no property %s" % [node_path, property]}

	return {"node": node_path, "property": property, "value": node.get(property)}


func _cmd_seed(args: Dictionary) -> Dictionary:
	## Deterministic randomness, so a failing playtest can be replayed.
	var value := int(args.get("seed", 0))
	seed(value)
	return {"seed": value}


func _cmd_quit(args: Dictionary) -> Dictionary:
	var code := int(args.get("code", 0))
	_release_all()
	get_tree().quit(code)
	return {"quitting": code}


func _validate_action(action: String) -> String:
	if action.is_empty():
		return "no action name given"
	if not InputMap.has_action(action):
		var known: Array = []
		for existing in InputMap.get_actions():
			if not String(existing).begins_with("ui_"):
				known.append(String(existing))
		known.sort()
		return (
			"input action %s is not in the input map. Godot silently ignores "
			"unknown actions rather than erroring. Project actions: %s"
			% [action, ", ".join(known)]
		)
	return ""


func _release_all() -> void:
	for action in _held.keys():
		if InputMap.has_action(action):
			Input.action_release(action)
	_held.clear()
