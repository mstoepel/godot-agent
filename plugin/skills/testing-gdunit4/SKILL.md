---
name: testing-gdunit4
description: Writing and running gdUnit4 tests for a Godot project, including the headless-mode restrictions that make input tests fail silently. Use when adding tests, running the suite, or diagnosing a test run that produced no report.
---

# Testing with gdUnit4

## Writing a suite

```gdscript
extends GdUnitTestSuite

const Player := preload("res://scripts/player.gd")


func test_takes_damage() -> void:
	var player := Player.new()
	player.health = 10
	player.take_damage(3)
	assert_int(player.health).is_equal(7)


func test_dies_at_zero_health() -> void:
	var player := Player.new()
	player.health = 1
	player.take_damage(5)
	assert_bool(player.is_dead).is_true()
```

Assertions are typed: `assert_int`, `assert_float`, `assert_bool`,
`assert_str`, `assert_array`, `assert_object`, `assert_signal`.

Write tests that could actually fail. A test asserting `true == true` converts
an untested area into a false sense of safety, which is worse than no test.

## Scenes under test

```gdscript
func test_scene_loads() -> void:
	var runner := scene_runner("res://scenes/level.tscn")
	await runner.simulate_frames(10)
	assert_object(runner.get_property("player")).is_not_null()
```

`scene_runner` instantiates the scene and drives frames. Use it to test
behaviour that only emerges once a scene is running.

## Running

Use `godot_run_tests`. It handles two details that are easy to miss:

**gdUnit4 refuses to run under `--headless`** unless `--ignoreHeadlessMode` is
passed. Without it the run aborts having executed nothing, which looks like a
crash rather than a refusal.

**Godot does not deliver `InputEvent`s in headless mode.** A test that
simulates input will fail, or worse pass vacuously, when run headlessly. Run
those with `headless=false`.

## When a run produces no report

`godot_run_tests` reports this explicitly, and it almost always means a test
file failed to compile rather than that the tests failed. Run
`godot_check_script` on each test file to find the parse error.

## Where tests live

Put suites in `res://tests/`, named `<subject>_test.gd`. `godot_scaffold_test`
creates one in the right place with the right shape.
