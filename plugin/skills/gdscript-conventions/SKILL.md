---
name: gdscript-conventions
description: Idiomatic GDScript for Godot 4 - static typing, node access, signals, lifecycle methods and the mistakes that produce silent runtime failures. Use when writing or reviewing any .gd file.
---

# GDScript conventions

## Always type

```gdscript
extends CharacterBody2D

@export var speed: float = 220.0
@export var jump_velocity: float = -400.0

@onready var sprite: AnimatedSprite2D = $AnimatedSprite2D

var _coyote_time: float = 0.0


func _physics_process(delta: float) -> void:
	velocity.y += get_gravity().y * delta
	move_and_slide()
```

Static types are not decoration: `godot_check_script` can only find a whole
class of errors when the types are there. An untyped script defers those to a
runtime crash during play.

Indent with **tabs**. Godot's own formatter and every engine-shipped script use
them, and mixing spaces in produces confusing diffs.

## Node access

Use `@onready var x: Type = $Path` once, not `get_node()` on every frame.
`$Sprite2D` is shorthand for `get_node("Sprite2D")` and matches the node's
exact name -- renaming a node in the scene silently breaks it.

For a node that may be absent, use `get_node_or_null()` and check, rather than
letting a null dereference crash the frame.

## Signals over polling

```gdscript
signal health_changed(amount: int)

func _ready() -> void:
	$Timer.timeout.connect(_on_timeout)

func _on_timeout() -> void:
	pass
```

Prefer wiring connections in the scene file (`godot_connect_signal`) so the
relationship is visible in the scene rather than buried in `_ready`.

## Lifecycle

| Method | When | Use for |
| --- | --- | --- |
| `_init()` | On construction | Pure data setup; the tree does not exist yet |
| `_ready()` | Once, after children enter the tree | Node references, signal wiring |
| `_process(delta)` | Every frame | Rendering, UI, non-physics |
| `_physics_process(delta)` | Fixed step | Movement, collisions |
| `_input(event)` | On input | One-shot actions |

Never touch `$Child` before `_ready()` -- in `_init()` the child does not exist.

## Input

Only action names present in the project's input map work:

```gdscript
if Input.is_action_just_pressed("jump"):
	velocity.y = jump_velocity
```

An action name that is not in the map **does not error** -- it silently never
fires. Check the real names with `godot_project_info` before writing input
code, and add missing ones to `project.godot` rather than inventing them.

## Common silent failures

* Assigning to `position` inside `_physics_process` instead of using
  `velocity` + `move_and_slide()` -- collisions stop working.
* Forgetting `await` on a coroutine, so it never runs to completion.
* Comparing a freed node with `==` instead of `is_instance_valid()`.
* Shadowing a built-in property such as `name`, `position` or `scale`.
* `class_name` colliding with an existing global class, which breaks the whole
  project's script cache rather than just that file.
