@tool
extends EditorPlugin
## The editor half of the agent bridge.
##
## Exposes the parts of the editor that text-file editing cannot reach:
## the live scene tree, resource inspection, reimport, and a screenshot of the
## editor viewport. Everything here is read-mostly; scene *editing* goes through
## the agent's file-level scene tools, which produce reviewable diffs.

const AgentBridgeServer := preload("res://addons/godot_agent_bridge/bridge_server.gd")

## Editor half listens here; the runtime probe uses this port plus one.
const DEFAULT_PORT := 45719

var _server: AgentBridgeServer


func _enter_tree() -> void:
	_server = AgentBridgeServer.new()
	var port := DEFAULT_PORT
	if OS.has_environment("GODOT_AGENT_BRIDGE_PORT"):
		port = int(OS.get_environment("GODOT_AGENT_BRIDGE_PORT"))

	var error := _server.start(port, {
		"ping": _cmd_ping,
		"describe_scene": _cmd_describe_scene,
		"open_scene": _cmd_open_scene,
		"save_scene": _cmd_save_scene,
		"reimport": _cmd_reimport,
		"list_resources": _cmd_list_resources,
		"screenshot": _cmd_screenshot,
	})
	if error == OK:
		print("[godot-agent] bridge listening on 127.0.0.1:%d" % port)
	set_process(error == OK)


func _exit_tree() -> void:
	if _server != null:
		_server.stop()
		_server = null


func _process(_delta: float) -> void:
	if _server != null:
		_server.poll()


# -- commands -----------------------------------------------------------------


func _cmd_ping(_args: Dictionary) -> Dictionary:
	return {
		"half": "editor",
		"godot_version": Engine.get_version_info(),
		"project": ProjectSettings.get_setting("application/config/name", ""),
	}


func _cmd_describe_scene(_args: Dictionary) -> Dictionary:
	var root := get_editor_interface().get_edited_scene_root()
	if root == null:
		return {"ok": false, "error": "no scene is currently open in the editor"}
	return {
		"scene": root.scene_file_path,
		"tree": _describe_node(root, root),
	}


func _describe_node(node: Node, root: Node) -> Dictionary:
	var children: Array = []
	for child in node.get_children():
		children.append(_describe_node(child, root))
	return {
		"name": node.name,
		"type": node.get_class(),
		"path": "." if node == root else String(root.get_path_to(node)),
		"script": node.get_script().resource_path if node.get_script() != null else "",
		"children": children,
	}


func _cmd_open_scene(args: Dictionary) -> Dictionary:
	var path := String(args.get("path", ""))
	if not ResourceLoader.exists(path):
		return {"ok": false, "error": "%s does not exist" % path}
	get_editor_interface().open_scene_from_path(path)
	return {"opened": path}


func _cmd_save_scene(_args: Dictionary) -> Dictionary:
	var root := get_editor_interface().get_edited_scene_root()
	if root == null:
		return {"ok": false, "error": "no scene is currently open in the editor"}
	get_editor_interface().save_scene()
	return {"saved": root.scene_file_path}


func _cmd_reimport(args: Dictionary) -> Dictionary:
	var paths: Array = args.get("paths", [])
	var filesystem := get_editor_interface().get_resource_filesystem()
	if paths.is_empty():
		filesystem.scan()
		return {"scanned": true}

	var as_strings := PackedStringArray()
	for path in paths:
		as_strings.append(String(path))
	filesystem.reimport_files(as_strings)
	return {"reimported": Array(as_strings)}


func _cmd_list_resources(args: Dictionary) -> Dictionary:
	var directory := String(args.get("dir", "res://"))
	var found: Array = []
	_walk(directory, found)
	return {"dir": directory, "resources": found}


func _walk(path: String, out: Array) -> void:
	var dir := DirAccess.open(path)
	if dir == null:
		return
	dir.list_dir_begin()
	var entry := dir.get_next()
	while entry != "":
		if entry.begins_with("."):
			entry = dir.get_next()
			continue
		var full := path.path_join(entry)
		if dir.current_is_dir():
			_walk(full, out)
		elif not entry.ends_with(".import") and not entry.ends_with(".uid"):
			out.append(full)
		entry = dir.get_next()
	dir.list_dir_end()


func _cmd_screenshot(args: Dictionary) -> Dictionary:
	var path := String(args.get("path", "res://.godot-agent/editor.png"))
	var viewport := get_editor_interface().get_editor_viewport_2d()
	if viewport == null:
		return {"ok": false, "error": "no editor viewport is available"}

	var image := viewport.get_texture().get_image()
	if image == null:
		return {"ok": false, "error": "the viewport produced no image"}

	DirAccess.make_dir_recursive_absolute(path.get_base_dir())
	var error := image.save_png(path)
	if error != OK:
		return {"ok": false, "error": "could not write %s (%d)" % [path, error]}
	return {"path": path, "width": image.get_width(), "height": image.get_height()}
