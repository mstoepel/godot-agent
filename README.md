# godot-agent

A specialized coding agent for **Godot 4** game development, built on
[LangChain Deep Agents](https://github.com/langchain-ai/deepagents). It is
designed to carry a 2D game from idea to playable build: writing GDScript,
authoring scenes, generating and importing art, running the game headlessly,
writing and running tests, and *actually playing the game* to check that a
feature works.

It runs three ways from one tool registry:

| Surface | What it is | Needs |
| --- | --- | --- |
| **MCP server** | `godot-agent-mcp` over stdio | any MCP client, including `dcode` |
| **dcode plugin** | skills, hooks, agent profile, optional in-process extension | `dcode` |
| **Python library** | `create_godot_agent()` for CI and evals | Python 3.12+ |

## Requirements

* Python 3.12+ (`uv` recommended)
* Godot **4.5+** on `PATH`, or `GODOT_BIN` pointing at the executable
* Optional: [gdUnit4](https://github.com/godot-gdunit-labs/gdUnit4) in the
  target project, for tests

```bash
uv sync
uv run godot-agent doctor      # verifies Godot, the project and the tool surfaces
```

## Using it from dcode

```bash
# 1. Install the agent profile (main prompt + subagents + skills)
godot-agent install-dcode --set-default

# 2. Install the plugin (MCP tools, validation hooks, skills)
dcode plugin marketplace add /path/to/godot-agent
dcode plugin install godot-agent@godot-agent
```

Then run `dcode` in a Godot project. `/tools` should list the `godot_*` tools
and `/agents` the four subagents.

The plugin also ships an optional in-process
[Python extension](plugin/extension.py). It needs
`DEEPAGENTS_CODE_EXPERIMENTAL=1` and is *not* required — the MCP server
provides the same tools and the hooks provide the same validation without it.

## Using it from any MCP client

```json
{"mcpServers": {"godot": {"command": "godot-agent-mcp"}}}
```

## Using it as a library

```python
from godot_agent.agent import create_godot_agent

agent = create_godot_agent(model="claude-sonnet-5", project_path="./game")
agent.invoke({"messages": [{"role": "user", "content": "Add a double jump"}]})
```

Or headlessly, for CI and evals:

```bash
godot-agent run "Add a score counter to the HUD" --project ./game
```

## What it can do

**Project** — `godot_doctor`, `godot_project_info`, `godot_import`,
`godot_scaffold_project`

**Scenes** — read, create, add/remove nodes, set properties, attach scripts,
connect signals. Every write is re-parsed before it lands, so a scene the
engine cannot load never reaches disk.

**Running & validating** — `godot_run` (always frame-bounded),
`godot_check_script` (parse-check in about a second), `godot_run_script`

**Testing** — gdUnit4 scaffolding and runs, with failures parsed out of the
JUnit report rather than scraped from console output

**Playtesting** — install the bridge addon, then inject input, read live game
state and capture screenshots from the running game

**Assets** — provider-agnostic image, pixel-art and 3D generation, always
routed through the Godot importer, with a session spend cap

### Asset providers

| Provider | Kinds | Key |
| --- | --- | --- |
| `placeholder` | image, pixel | none — renders locally |
| `retrodiffusion` | pixel | `RETRODIFFUSION_API_KEY` |
| `openai` | image | `OPENAI_API_KEY` |
| `meshy` | model3d | `MESHY_API_KEY` |

`placeholder` is not a stub: it renders real, correctly sized PNGs offline, so
a whole game can be built and played before any key is configured. Set
`GODOT_AGENT_ASSET_BUDGET` (default `$5`) to cap paid spend per session.

## The bridge addon

`godot-addon/addons/godot_agent_bridge/` is copied into a target project by
`godot_install_bridge`. It has two halves: an `EditorPlugin` for scene
inspection and reimport, and an `AgentProbe` autoload inside the running game
for input injection, state reads and screenshots.

Both listen on `127.0.0.1` only and require a token from
`.godot-agent/bridge_token` (gitignored automatically). The probe stays dormant
unless `GODOT_AGENT_PROBE=1` is set, so shipped builds carry no open socket.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `GODOT_BIN` | discovered | Godot executable |
| `GODOT_PROJECT` | discovered | Project directory |
| `GODOT_AGENT_TIMEOUT` | `300` | Seconds per Godot subprocess |
| `GODOT_AGENT_ASSET_BUDGET` | `5.0` | USD cap on paid generation per session |
| `GODOT_AGENT_BRIDGE_PORT` | `45719` | Editor half; the probe uses this +1 |

## Layout

| Path | What it is |
| --- | --- |
| `src/godot_agent/gdformat/`, `tscn/` | Godot text-format parsers and writers |
| `src/godot_agent/engine/` | Executable discovery, invocation, output parsing |
| `src/godot_agent/tools/` | The tool registry — the single source for all surfaces |
| `src/godot_agent/assets/` | Providers, budget, Godot importer |
| `src/godot_agent/middleware/` | Project context injection, write validation |
| `src/godot_agent_mcp/` | The MCP stdio server |
| `plugin/`, `dcode-profile/` | The dcode plugin and agent profile |
| `godot-addon/` | The GDScript bridge addon |

`dcode-profile/` is **generated** from `src/godot_agent/prompts.py`. Edit the
prompts, then run `godot-agent sync-profile`; a test fails if they drift.

## Development

```bash
uv sync --group dev
uv run pytest                  # 185 tests, no Godot or network needed
uv run pytest -m "not godot"   # skip anything needing a real Godot binary
uv run ruff check .
```

Scene-format tests round-trip unmodified files from
[godot-demo-projects](https://github.com/godotengine/godot-demo-projects)
byte for byte, so the parser is checked against text the engine itself wrote.

## Status

The Python side is fully tested and the bridge protocol is covered end to end
against a mock server. The GDScript addon has not yet been exercised against a
running Godot binary — see the verification checklist in the plan.

## License

MIT
