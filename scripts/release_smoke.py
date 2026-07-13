#!/usr/bin/env python3
"""Isolated install, reinstall, cache, skill, and MCP discovery smoke test."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "grok-orchestrator"
MARKETPLACE_NAME = "grok-plugin"
VERSION = "0.2.1"
DISPLAY_NAME = "Grok Advisor"
EXPECTED_TOOLS = {
    "consult_grok",
    "review_plan_with_grok",
    "research_with_grok",
    "review_workspace_with_grok",
    "review_with_grok_panel",
    "grok_status",
}


def run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stderr.strip()}"
        )
    return result


def cached_plugin(codex_home: Path) -> Path:
    matches = []
    for manifest_path in codex_home.rglob(".codex-plugin/plugin.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("name") == PLUGIN_NAME and manifest.get("version") == VERSION:
            matches.append(manifest_path.parents[1])
    if len(matches) != 1:
        raise RuntimeError(f"Expected one cached {PLUGIN_NAME} {VERSION}, found {matches}")
    return matches[0]


def verify_cached_metadata(plugin: Path) -> None:
    manifest_path = plugin / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("interface", {}).get("displayName") != DISPLAY_NAME:
        raise RuntimeError("Cached plugin did not expose the Grok Advisor name.")


def discover_tools(plugin: Path) -> set[str]:
    server = subprocess.Popen(
        [sys.executable, "scripts/grok_mcp.py"],
        cwd=plugin,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert server.stdin is not None
    assert server.stdout is not None
    try:
        server.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n"
        )
        server.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"
        )
        server.stdin.flush()
        initialized = json.loads(server.stdout.readline())
        listed = json.loads(server.stdout.readline())
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    if initialized["result"]["serverInfo"]["version"] != VERSION:
        raise RuntimeError("Cached MCP server version is inconsistent.")
    return {tool["name"] for tool in listed["result"]["tools"]}


def main() -> int:
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("codex is not available on PATH.")
    with tempfile.TemporaryDirectory(prefix="grok-plugin-release-") as name:
        codex_home = Path(name) / "codex-home"
        codex_home.mkdir()
        env = {
            "CODEX_HOME": str(codex_home),
            "HOME": os.environ.get("HOME", str(Path.home())),
            "PATH": os.environ.get("PATH", os.defpath),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        run([codex, "plugin", "marketplace", "add", str(ROOT), "--json"], env)
        selector = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
        run([codex, "plugin", "add", selector, "--json"], env)
        plugin = cached_plugin(codex_home)
        verify_cached_metadata(plugin)
        if not (plugin / "skills" / "grok-orchestrator" / "SKILL.md").is_file():
            raise RuntimeError("Cached plugin is missing the orchestration skill.")
        if discover_tools(plugin) != EXPECTED_TOOLS:
            raise RuntimeError("Cached plugin did not expose the expected six tools.")

        run([codex, "plugin", "remove", selector, "--json"], env)
        run([codex, "plugin", "add", selector, "--json"], env)
        reinstalled = cached_plugin(codex_home)
        verify_cached_metadata(reinstalled)
        if discover_tools(reinstalled) != EXPECTED_TOOLS:
            raise RuntimeError("Reinstalled plugin tool discovery failed.")

    print("release smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
