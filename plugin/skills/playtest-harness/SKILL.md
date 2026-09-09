---
name: playtest-harness
description: Driving a running Godot game to verify behaviour - installing the bridge addon, injecting input, reading live game state, and capturing screenshots. Use when verifying that a feature actually works in play, not just that it compiles.
---

# Playtesting a running game

Compiling a Godot project proves that the syntax is valid. It says nothing
about whether pressing "jump" makes the player jump. The bridge addon closes
that gap: it runs inside the game and lets the agent press buttons, step
frames, read live state and capture the screen.

## Setup, once per project

```
godot_install_bridge
```

Two steps have to happen in the editor afterwards, because nothing outside it
can do them:

1. Enable **Godot Agent Bridge** in Project Settings > Plugins.
2. Add `res://addons/godot_agent_bridge/agent_probe.gd` as an autoload named
   `AgentProbe`.

Then launch the game with `GODOT_AGENT_PROBE=1` in the environment. Without it
the probe stays dormant, so a shipped build never opens a socket.

## The headless trap

**Godot does not deliver `InputEvent`s under `--headless`.** Input injection
silently does nothing, and a test that "passes" headlessly has verified
nothing at all.

`godot_playtest_status` reports whether the running game is headless. Believe
it: if it says headless, any result from `godot_playtest_input` is meaningless.
Run the game with a display for input-driven checks.

## A playtest loop

```
godot_playtest_status                         # is the probe up? is it headless?
godot_playtest_state  node="Player" prop="position"
godot_playtest_input  action="jump" mode="tap"
godot_playtest_state  node="Player" prop="position"   # did it actually move?
```

Assert on **state**, not on pixels. `godot_playtest_state` with `node` and
`prop` reads the real value out of the running game -- the score, the health,
whether it is game over. A screenshot only tells you something was drawn.

`mode="tap"` presses and releases over a couple of frames. A press and release
inside a single frame is routinely missed by `is_action_just_pressed`, so a
same-frame tap produces a false negative.

## Screenshots

`godot_playtest_screenshot` reports whether the frame came back blank. A blank
frame nearly always means the scene never rendered -- wrong scene, camera
pointed at nothing, or a headless run. Treat the blank warning as a failure,
not a detail.

## Determinism

Seed the RNG before a run that you want to be able to replay, and prefer
`--write-movie` (via `godot_run`'s recording option) when you need fixed-step
timing: it makes frame counts reproducible, so a failing playtest fails the
same way twice.

## Security

The bridge binds to `127.0.0.1` only and requires a token from
`.godot-agent/bridge_token`, which `godot_install_bridge` adds to `.gitignore`.
Do not commit that file: it is the key to a process that can edit scenes and
inject input.
