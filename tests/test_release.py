from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "grok-orchestrator"
BRIDGE = PLUGIN / "scripts" / "grok_mcp.py"
MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"


def load_bridge():
    spec = importlib.util.spec_from_file_location("release_grok_mcp", BRIDGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseContractTests(unittest.TestCase):
    def test_version_and_public_metadata_are_consistent(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "grok-orchestrator")
        self.assertEqual(manifest["version"], "0.2.0")
        self.assertEqual(manifest["author"]["name"], "Keiran Haax")
        self.assertEqual(manifest["interface"]["developerName"], "Keiran Haax")
        self.assertEqual(manifest["interface"]["displayName"], "Grok Advisor")
        self.assertEqual(manifest["interface"]["category"], "Productivity")
        self.assertEqual(len(manifest["interface"]["defaultPrompt"]), 3)

        bridge = load_bridge()
        self.assertEqual(bridge.SERVER_NAME, manifest["name"])
        self.assertEqual(bridge.SERVER_VERSION, manifest["version"])
        self.assertEqual(bridge.MODEL, "grok-4.5")
        self.assertEqual(bridge.EFFORT, "high")

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        architecture = readme.split("```mermaid", 1)[1].split("```", 1)[0]
        self.assertIn("version-0.2.0", readme)
        self.assertTrue(readme.startswith("# Grok Advisor for Codex\n"))
        self.assertIn("sequenceDiagram", readme)
        self.assertIn("participant C as Codex<br/>(orchestrator)", readme)
        self.assertIn("box transparent Plugin boundary", readme)
        self.assertIn("participant B as MCP Bridge<br/>(read-only, stdio)", readme)
        self.assertIn("participant G as Grok CLI<br/>(fresh, isolated)", readme)
        self.assertIn("U->>+C: Submit task", readme)
        self.assertIn("C->>+B: Invoke role-specific MCP tool", readme)
        self.assertIn("B->>+G: Run grok-4.5 (high effort)", readme)
        self.assertIn("G-->>-B: Role-specific response", readme)
        self.assertIn("B-->>-C: Stable JSON envelope", readme)
        self.assertIn("C-->>-U: Verified result", readme)
        self.assertIn(
            "> `grok_status` stops after preflight with no model call. Panel mode may run\n"
            "> two or three independent Grok processes in parallel.",
            readme,
        )
        self.assertNotIn("participant A as Grok Advisor", readme)
        self.assertEqual(architecture.count("Note over"), 1)
        self.assertNotIn(";", architecture)
        self.assertIn("## Unreleased", changelog)
        self.assertIn("## 0.2.0", changelog)

    def test_marketplace_and_mcp_launch_contract(self) -> None:
        marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
        self.assertEqual(marketplace["name"], "grok-plugin")
        entries = {item["name"]: item for item in marketplace["plugins"]}
        entry = entries["grok-orchestrator"]
        self.assertEqual(entry["source"]["source"], "local")
        self.assertEqual(entry["source"]["path"], "./plugins/grok-orchestrator")
        self.assertEqual(entry["category"], "Productivity")

        mcp = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
        server = mcp["mcpServers"]["grok-orchestrator"]
        self.assertEqual(server["command"], "python3")
        self.assertEqual(server["args"], ["scripts/grok_mcp.py"])
        self.assertEqual(server["cwd"], ".")
        self.assertEqual(server["tool_timeout_sec"], 900)

    def test_package_contains_only_the_expected_read_only_surfaces(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertNotIn("hooks", manifest)
        self.assertNotIn("agents", manifest)
        self.assertNotIn("commands", manifest)
        self.assertFalse((PLUGIN / "hooks").exists())
        self.assertFalse((PLUGIN / "agents").exists())
        self.assertFalse((PLUGIN / "commands").exists())

        expected_profiles = {
            "consult.md",
            "plan-review.md",
            "research.md",
            "workspace-review.md",
            "panel-review.md",
        }
        profile_root = PLUGIN / "scripts" / "agent_profiles"
        self.assertEqual(
            {path.name for path in profile_root.glob("*.md")},
            expected_profiles,
        )

    def test_bridge_parses_as_python_310_and_has_no_dependency_file(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        ast.parse(source, filename=str(BRIDGE), feature_version=(3, 10))
        self.assertFalse((PLUGIN / "requirements.txt").exists())
        self.assertFalse((PLUGIN / "pyproject.toml").exists())

    def test_skill_and_readme_document_all_six_tools(self) -> None:
        skill = (
            PLUGIN / "skills" / "grok-orchestrator" / "SKILL.md"
        ).read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        names = {
            "consult_grok",
            "review_plan_with_grok",
            "research_with_grok",
            "review_workspace_with_grok",
            "review_with_grok_panel",
            "grok_status",
        }
        for name in names:
            with self.subTest(name=name):
                self.assertIn(name, skill)
                self.assertIn(name, readme)
        self.assertIn("Single-pass", skill)
        self.assertIn("explicit", skill)
        self.assertIn("used_and_confirmed", skill)

    def test_stdio_initialize_ping_and_tool_discovery(self) -> None:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        ]
        result = subprocess.run(
            [sys.executable, str(BRIDGE)],
            input="".join(json.dumps(item) + "\n" for item in requests),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(responses[0]["result"]["serverInfo"]["version"], "0.2.0")
        self.assertEqual(responses[1]["result"], {})
        names = {
            tool["name"] for tool in responses[2]["result"]["tools"]
        }
        self.assertEqual(
            names,
            {
                "consult_grok",
                "review_plan_with_grok",
                "research_with_grok",
                "review_workspace_with_grok",
                "review_with_grok_panel",
                "grok_status",
            },
        )


if __name__ == "__main__":
    unittest.main()
