---
name: godot-scene-format
description: How Godot .tscn and .tres files are structured, and the rules that decide whether the engine can load one. Use when creating or editing scenes, debugging a scene that will not open, reading a scene diff, or deciding between editing scene text and using the scene tools.
---

# Godot scene and resource file format

A `.tscn` is a flat list of bracketed sections. The tree is implied by each
node's `parent` attribute, not by nesting.

```
[gd_scene load_steps=3 format=3 uid="uid://bwhlkliwp13p4"]

[ext_resource type="Script" path="res://player.gd" id="1"]
[ext_resource type="Texture2D" path="res://art/player.png" id="2"]

[sub_resource type="RectangleShape2D" id="Rect_1"]
size = Vector2(24, 32)

[node name="Player" type="CharacterBody2D"]
script = ExtResource("1")

[node name="Sprite2D" type="Sprite2D" parent="."]
texture = ExtResource("2")

[node name="Shape" type="CollisionShape2D" parent="."]
shape = SubResource("Rect_1")

[connection signal="body_entered" from="." to="." method="_on_body_entered"]
```

## Rules the engine enforces

**Parents come first.** A node's section must appear before any of its
children. Inserting a child immediately after its parent is not enough when the
parent already has children -- the new node goes after the parent's last
existing descendant.

**`parent` is a path from the root, not a name.** The root node has no `parent`
attribute at all. A direct child of the root uses `parent="."`. A grandchild
uses `parent="Sprite2D"`, and deeper nodes use `parent="Body/Sprite2D"`.

**Sibling names must be unique.** Godot silently renames a duplicate, which
breaks every `get_node()` path and every connection that referenced it.

**Resource ids must resolve.** `ExtResource("2")` and `SubResource("Rect_1")`
must match a declared `id`. A dangling reference fails the whole scene load.

**Connections go last**, after every node section.

## Value literals

| Form | Example |
| --- | --- |
| String | `"res://art/player.png"` |
| StringName | `&"idle"` |
| NodePath | `^"Body/Sprite2D"` |
| Number | `12`, `-1.5`, `1e-05` |
| Bool / null | `true`, `false`, `null` |
| Constructor | `Vector2(0, 8)`, `Color(1, 1, 1, 1)` |
| Array / dict | `[1, 2]`, `{"key": 1}` |

A whole float keeps its `.0`: writing `1` where Godot wrote `1.0` changes the
value's type.

## Use the tools, not a text editor

Prefer `godot_create_scene`, `godot_add_node`, `godot_set_node_property`,
`godot_attach_script` and `godot_connect_signal`. They enforce every rule above
and re-parse the result before writing, so a broken scene never reaches disk.

Reach for direct text editing only for something the tools genuinely cannot
express, and run `godot_run` immediately afterwards to confirm the scene still
loads.

## When a scene will not load

Read the error's `res://file:line`. The most common causes, in order: a
dangling `ExtResource`/`SubResource` id; a missing asset that was never
imported (run `godot_import`); a `parent` path naming a node that does not
exist; and a script attached to the scene that does not compile -- check that
with `godot_check_script` first, since it reports a much clearer error.
