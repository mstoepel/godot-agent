---
name: godot-2d-pipeline
description: Getting 2D art into a Godot project correctly - import settings, pixel-art filtering, sprite sheets, AnimatedSprite2D and TileSet resources. Use when adding images, setting up animation, building tilemaps, or when sprites look blurry or misaligned.
---

# The 2D asset pipeline

## Import is not optional

A new image file has no `.import` sidecar and no UID until the engine imports
it. Until then every scene referencing it fails to load, and the error names
the *scene*, not the missing image. After adding or replacing any asset:

```
godot_import
```

Never hand-edit a `.import` file or anything under `.godot/`. Both are
regenerated, so the edit is silently discarded.

## Pixel art must not be filtered

Linear filtering blurs pixel art into mush. Set the project default once, in
`project.godot`:

```
[rendering]

textures/canvas_textures/default_texture_filter=0
```

`0` is nearest-neighbour. `godot_scaffold_project` sets this already. For a
single texture, override it on the node's `texture_filter` property instead of
changing the project default.

Design at the game's true pixel resolution -- a 640x360 viewport scaled up
beats a 1920x1080 viewport with tiny sprites. Set `window/stretch/mode` to
`canvas_items` so the scaling is handled for you.

Generate art at its final pixel size. Generating large and downscaling destroys
the grid alignment that makes pixel art read as pixel art.

## Sprite sheets and animation

For a sheet of equal frames, use `AnimatedSprite2D` with a `SpriteFrames`
resource, or `Sprite2D` with `hframes` / `vframes` and `frame` for simple
cases.

`SpriteFrames` holds one entry per animation, each with its frames, `loop` and
`speed`:

```
[sub_resource type="SpriteFrames" id="1"]
animations = [{
"frames": [{"duration": 1.0, "texture": ExtResource("2")}],
"loop": 1,
"name": &"idle",
"speed": 5.0
}]
```

Animation names are `StringName` (`&"idle"`), and `play("idle")` matches them
exactly.

## Tilemaps

Godot 4 uses `TileMapLayer` nodes with a shared `TileSet` resource. Keep the
`TileSet` in its own `.tres` so several layers can share it. Tile size must
match the art's grid exactly -- a 16px tileset in a 17px grid produces seams
that look like a rendering bug.

## Layout

```
assets/
  sprites/     # character and object art
  tilesets/    # tile sheets and their .tres
  ui/          # fonts, panels, icons
  STYLE.md     # palette, resolution and style conventions
```

`STYLE.md` is what keeps separately generated assets looking like one game.
Read it before generating art and update it when a new convention is set.

## When sprites look wrong

| Symptom | Cause |
| --- | --- |
| Blurry | Linear filtering; set `texture_filter` to nearest |
| Seams between tiles | Tile size does not match the art grid |
| Sprite invisible | `z_index`, or the node is outside the camera |
| Scene will not load | The texture was never imported -- run `godot_import` |
