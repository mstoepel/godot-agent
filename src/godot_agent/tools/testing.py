"""Tools for writing and running Godot tests with gdUnit4.

The runner is ``res://addons/gdUnit4/bin/GdUnitCmdTool.gd``, driven through
``godot -s``. Two of its behaviours are easy to get wrong and expensive to
debug:

* gdUnit4 **refuses to run under ``--headless``** unless
  ``--ignoreHeadlessMode`` is passed. Without it the run aborts with a message
  about UI tests rather than running anything.
* Godot's ``InputEvent`` delivery does not work headlessly, so tests that
  simulate input must run with a display. That limitation is reported back
  rather than silently producing green results.

Results come from the JUnit XML the runner writes to
``<report dir>/**/results.xml``; the console output is far too noisy to parse.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Any
from xml.etree import ElementTree

from langchain_core.tools import tool

from godot_agent.engine.cli import run_godot
from godot_agent.tools._common import (
    ProjectArg,
    failure,
    res_to_path,
    resolve_project,
    tool_errors,
)

__all__ = ["godot_run_tests", "godot_scaffold_test"]

#: The gdUnit4 command-line runner, relative to the project root.
RUNNER_SCRIPT = "res://addons/gdUnit4/bin/GdUnitCmdTool.gd"

#: Where gdUnit4 writes reports unless told otherwise.
DEFAULT_REPORT_DIR = "res://reports"

_TEST_TEMPLATE = '''\
# GdUnit4 test suite for {subject}.
extends GdUnitTestSuite

const __source := "{subject}"


func test_placeholder() -> void:
	assert_bool(true).is_true()
'''



def _parse_junit(report_root: Path) -> dict[str, Any] | None:
    """Parse the newest ``results.xml`` under ``report_root``."""
    candidates = sorted(
        report_root.rglob("results.xml"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None

    tree = ElementTree.parse(candidates[0])
    root = tree.getroot()
    suites = root.iter("testsuite") if root.tag == "testsuites" else [root]

    total = passed = failed = skipped = 0
    failures: list[dict[str, str]] = []

    for suite in suites:
        suite_name = suite.get("name", "?")
        for case in suite.iter("testcase"):
            total += 1
            name = case.get("name", "?")
            if case.find("skipped") is not None:
                skipped += 1
                continue
            # `find` returns an Element that is falsy when childless, so these
            # must be compared against None explicitly.
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            if problem is None:
                passed += 1
                continue
            failed += 1
            message = (problem.get("message") or problem.text or "").strip()
            failures.append(
                {
                    "suite": suite_name,
                    "test": name,
                    "message": " ".join(message.split())[:600],
                }
            )

    return {
        "report_file": str(candidates[0]),
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "failures": failures,
    }


@tool
@tool_errors
def godot_run_tests(
    tests: Annotated[
        str,
        "res:// path of the test directory or a single test suite file.",
    ] = "res://tests",
    project: ProjectArg = None,
    headless: Annotated[
        bool,
        "Run without a display. Set false for tests that simulate input, which "
        "Godot does not deliver headlessly.",
    ] = True,
    fail_fast: Annotated[bool, "Stop at the first failing suite."] = False,
    timeout: Annotated[float | None, "Seconds before the run is killed."] = None,
) -> dict[str, Any]:
    """Run the project's gdUnit4 test suite and report structured failures.

    Returns per-test failures parsed from the JUnit report, not raw console
    output. Requires the gdUnit4 addon; use `godot_scaffold_test` first if it
    is missing.
    """
    root = resolve_project(project)
    if not (root / "addons" / "gdUnit4").is_dir():
        return failure(
            "gdUnit4 is not installed in this project "
            "(expected addons/gdUnit4). Install it from the Asset Library or "
            "https://github.com/godot-gdunit-labs/gdUnit4, then enable it in "
            "Project Settings > Plugins.",
            missing_addon="gdUnit4",
        )

    report_dir = res_to_path(root, DEFAULT_REPORT_DIR)
    if report_dir.exists():
        shutil.rmtree(report_dir, ignore_errors=True)

    args: list[str] = []
    if headless:
        # gdUnit4 aborts under --headless unless explicitly told to proceed.
        args += ["--headless"]
    args += ["-s", RUNNER_SCRIPT, "-a", tests, "-rd", DEFAULT_REPORT_DIR]
    if not fail_fast:
        args.append("-c")
    if headless:
        args.append("--ignoreHeadlessMode")

    result = run_godot(args, project=root, timeout=timeout)
    payload = result.to_model()
    payload["tests_path"] = tests

    parsed = _parse_junit(report_dir)
    if parsed is None:
        payload["ok"] = False
        payload["error"] = (
            "The test run produced no JUnit report. The suite probably failed to "
            "load; check the diagnostics below for a parse error in a test file."
        )
        return payload

    payload.update(parsed)
    payload["ok"] = parsed["failed"] == 0 and not result.timed_out
    if headless and parsed["failed"]:
        payload["hint"] = (
            "If these failures involve simulated input, re-run with headless=false: "
            "Godot does not deliver InputEvents in headless mode."
        )
    return payload


@tool
@tool_errors
def godot_scaffold_test(
    subject: Annotated[str, "res:// path of the script under test, e.g. res://scripts/player.gd"],
    project: ProjectArg = None,
    tests_dir: Annotated[str, "res:// directory to put the suite in."] = "res://tests",
) -> dict[str, Any]:
    """Create an empty gdUnit4 test suite for a script.

    Writes `<tests_dir>/<name>_test.gd` extending `GdUnitTestSuite`, ready for
    real cases. Does not overwrite an existing suite.
    """
    root = resolve_project(project)
    subject_path = res_to_path(root, subject)
    if not subject_path.is_file():
        return failure(f"{subject} does not exist (looked in {subject_path})")

    directory = res_to_path(root, tests_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{subject_path.stem}_test.gd"
    if destination.exists():
        return failure(
            f"{destination.name} already exists; edit it instead of scaffolding again."
        )

    destination.write_text(
        _TEST_TEMPLATE.format(subject=subject), encoding="utf-8", newline="\n"
    )
    return {
        "ok": True,
        "created": f"{tests_dir}/{destination.name}",
        "next_step": "Replace test_placeholder with real cases, then run godot_run_tests.",
    }
