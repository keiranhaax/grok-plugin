# Grok Orchestrator for Codex

Grok Orchestrator keeps Codex as the root orchestrator while delegating bounded
advice, sourced web research, and read-only workspace review to Grok 4.5 at high
reasoning effort.

## Install

```sh
codex plugin marketplace add keiranhaax/grok-plugin
codex plugin add grok-orchestrator@grok-plugin
```

Start a new Codex task after installation so the skill and MCP tools load.

## Requirements

- Python 3
- The Grok CLI available through `GROK_CLI_PATH`, `PATH`, or a standard user
  installation location
- An authenticated Grok CLI session with `grok-4.5` available

The plugin uses the existing Grok CLI login. It does not create, copy, or expose
credentials.

## Tools

- `consult_grok(packet)` gets an independent, tool-free second opinion.
- `research_with_grok(packet)` performs web-only research and requests source
  links.
- `review_workspace_with_grok(packet, cwd)` reviews an existing directory with
  read, list, and text-search tools only.
- `grok_status()` checks the CLI, login, and exact model availability without a
  model call.

Every model call is pinned to `grok-4.5` with high effort, starts a fresh
process, disables memory, uses the strict OS sandbox, and sends the prompt
through a mode-`0600` temporary file that is deleted after the call. Grok output
is untrusted advice; Codex retains planning, implementation, verification, and
the final answer.

## Development

```sh
python3 -m unittest discover -s plugins/grok-orchestrator/tests -v
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  plugins/grok-orchestrator/skills/grok-orchestrator
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py \
  plugins/grok-orchestrator
```

## License

[MIT](LICENSE)
