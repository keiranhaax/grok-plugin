from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "grok_mcp.py"
SPEC = importlib.util.spec_from_file_location("grok_mcp", MODULE_PATH)
assert SPEC and SPEC.loader
grok_mcp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grok_mcp)


FAKE_GROK = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

args = sys.argv[1:]
status_mode = os.environ.get("FAKE_GROK_STATUS_MODE", "ready")
required_flags = [
    "--prompt-file", "--agent", "--model", "--effort", "--output-format",
    "--sandbox", "--permission-mode", "--no-memory", "--no-auto-update",
    "--cwd", "--rules", "--tools", "--disallowed-tools", "--no-subagents",
    "--no-plan", "--max-turns", "--json-schema", "--check", "--deny",
    "--disable-web-search",
]

normalized_args = [arg for arg in args if arg != "--no-auto-update"]
if (
    "--prompt-file" in normalized_args
    and normalized_args[normalized_args.index("--prompt-file") + 1].endswith("missing-check-probe")
    and "--no-subagents" in normalized_args
    and "--check" in normalized_args
):
    print("--no-subagents cannot be used with --check", file=sys.stderr)
    raise SystemExit(2)
if normalized_args == ["--version"]:
    version = (
        "0.2.98-test"
        if status_mode in {"old_version", "old_version_logged_out"}
        else "0.2.99-test"
    )
    print(f"grok {version}")
    raise SystemExit(0)
if normalized_args == ["--help"]:
    flags = required_flags[:]
    if status_mode == "missing_capability":
        flags.remove("--json-schema")
    print("\n".join(flags))
    raise SystemExit(0)
if normalized_args == ["models"]:
    if status_mode in {"logged_out", "old_version_logged_out"}:
        print(
            "Not logged in.\n\n"
            "Default model: grok-4.5\n\n"
            "Available models:\n  * grok-4.5 (default)"
        )
        raise SystemExit(1)
    if status_mode == "missing_model":
        model = "grok-composer-2.5-fast"
    elif status_mode == "similar_model":
        model = "grok-4.50"
    else:
        model = "grok-4.5"
    print(
        "You are logged in with grok.com.\n\n"
        f"Default model: {model}\n\n"
        f"Available models:\n  * {model} (default)"
    )
    raise SystemExit(0)
if "inspect" in normalized_args and "--json" in normalized_args:
    if status_mode == "malformed_inspect":
        print("not-json")
        raise SystemExit(0)
    cwd = Path(args[args.index("--cwd") + 1])
    unsafe = status_mode == "unsafe_integrations" or (cwd / ".grok-unsafe").exists()
    print(json.dumps({
        "loginPolicy": {
            "apiKeyAuthDisabled": os.environ.get("GROK_DISABLE_API_KEY_AUTH") == "1",
        },
        "hooks": [{"event": "SessionStart"}] if unsafe else [],
        "plugins": [{"name": "unsafe"}] if unsafe else [],
        "mcpServers": [{"name": "unsafe"}] if unsafe else [],
        "configSources": {"layers": [{"role": "project"}] if unsafe else []},
    }))
    raise SystemExit(0)

prompt_path = Path(args[args.index("--prompt-file") + 1])
packet = prompt_path.read_text(encoding="utf-8").strip()
profile = Path(args[args.index("--agent") + 1]).name
record = {
    "args": args,
    "packet": packet,
    "profile": profile,
    "prompt_path": str(prompt_path),
    "prompt_mode": stat.S_IMODE(prompt_path.stat().st_mode),
    "web_fetch_enabled": os.environ.get("GROK_WEB_FETCH"),
    "route_environment": {
        name: os.environ.get(name)
        for name in (
            "XAI_API_KEY",
            "GROK_API_KEY",
            "GROK_XAI_API_BASE_URL",
            "GROK_MODELS_BASE_URL",
            "GROK_MODELS_LIST_URL",
            "GROK_CLI_CHAT_PROXY_BASE_URL",
            "GROK_WS_URL",
            "GROK_WS_ORIGIN",
            "GROK_AUTH_PROVIDER_COMMAND",
            "GROK_WEB_FETCH_PROXY",
            "CLI_CHAT_PROXY_BASE_URL",
            "XAI_API_BASE_URL",
            "ARBITRARY_PROVIDER_KEY",
        )
    },
    "hardening_environment": {
        name: os.environ.get(name)
        for name in (
            "GROK_DISABLE_API_KEY_AUTH",
            "GROK_DISABLE_AUTOUPDATER",
            "GROK_MEMORY",
            "GROK_SUBAGENTS",
            "GROK_WRITE_FILE",
            "GROK_TOOL_SEARCH",
        )
    },
}
record_dir = Path(os.environ["FAKE_GROK_RECORD_DIR"])
record_dir.mkdir(parents=True, exist_ok=True)
record_path = record_dir / f"{os.getpid()}.json"
record_path.write_text(json.dumps(record), encoding="utf-8")

mode = os.environ.get("FAKE_GROK_MODE", "success")
if mode == "malformed":
    print("not-json")
    raise SystemExit(0)
if mode == "error_object":
    print(json.dumps({"type": "error", "message": "remote failure"}))
    raise SystemExit(0)
if mode == "nonzero" or (mode == "panel_partial" and "Panel member 2" in packet):
    print("Bearer top-secret", file=sys.stderr)
    print("session_id=019f59b1-0beb-7a51-a6ca-e868226cbcc5", file=sys.stderr)
    print(packet, file=sys.stderr)
    raise SystemExit(7)
if mode == "sleep":
    time.sleep(5)
if mode == "spawn_child_sleep":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    record["child_pid"] = child.pid
    record_path.write_text(json.dumps(record), encoding="utf-8")
    time.sleep(30)
if mode == "oversized":
    print(json.dumps({"text": "x" * 10000}))
    raise SystemExit(0)
if mode == "oversized_stderr":
    print("x" * 10000, file=sys.stderr)
    time.sleep(5)

if profile == "plan-review.md":
    structured = {
        "decision": "PLAN_REVISE",
        "summary": "One material gap remains.",
        "findings": [{
            "priority": "high",
            "title": "Rollback gap",
            "problem": "The plan has no rollback trigger.",
            "correction": "Define a measurable rollback threshold.",
        }],
        "verification_steps": ["Exercise rollback in a disposable environment."],
    }
    if mode == "bad_plan_decision":
        structured["decision"] = "LOOKS_GOOD"
    elif mode == "approved_with_findings":
        structured["decision"] = "PLAN_APPROVED"
    elif mode == "revise_without_findings":
        structured["findings"] = []
    elif mode == "unordered_plan":
        structured["findings"] = [
            {"priority": "low", "title": "Low", "problem": "Low", "correction": "Low"},
            {"priority": "high", "title": "High", "problem": "High", "correction": "High"},
        ]
    elif mode == "plan_without_verification":
        structured["verification_steps"] = []
elif profile == "research.md":
    structured = {
        "summary": "Primary-source research summary.",
        "claims": [{
            "statement": "The documented capability is available.",
            "source_urls": ["https://docs.example.com/capability"],
            "confidence": "high",
        }],
        "sources": [{
            "url": "https://docs.example.com/capability",
            "title": "Capability documentation",
            "source_type": "primary",
        }],
        "uncertainties": ["Runtime availability can vary by account."],
        "inferences": ["A capability probe is still required."],
    }
    if mode == "bad_research_url":
        structured["claims"][0]["source_urls"] = ["javascript:alert(1)"]
    elif mode == "malformed_research_authority":
        structured["claims"][0]["source_urls"] = ["http://["]
    elif mode == "uncataloged_research_url":
        structured["claims"][0]["source_urls"] = ["https://other.example.com/missing"]
    elif mode == "duplicate_research_source":
        structured["sources"].append(structured["sources"][0].copy())
elif profile == "workspace-review.md":
    structured = {
        "summary": "One high-severity finding.",
        "findings": [{
            "severity": "high",
            "title": "Unchecked failure",
            "file": "src/example.py",
            "line": 42,
            "evidence": "The return value is ignored.",
            "impact": "Failures can be reported as success.",
            "recommendation": "Check and propagate the failure.",
            "recommended_test": "Assert a failing child returns an error.",
        }],
        "missing_tests": ["Nonzero child exit"],
    }
    if mode == "bad_workspace_shape":
        del structured["findings"][0]["recommended_test"]
    elif mode == "escaping_workspace_path":
        structured["findings"][0]["file"] = "../outside.py"
    elif mode == "invalid_workspace_line":
        structured["findings"][0]["line"] = 0
elif profile == "panel-review.md":
    structured = {
        "verdict": "CHALLENGED",
        "summary": "The proposal needs another verification gate.",
        "findings": [{
            "priority": "medium",
            "title": "Evidence gap",
            "analysis": "The core assumption is not independently checked.",
            "recommendation": "Add an independent capability probe.",
        }],
        "verification_steps": ["Run the capability probe before rollout."],
    }
else:
    structured = None

if mode == "inner_malformed_structured" and structured is not None:
    text = "not-json"
else:
    text = "fake answer" if structured is None else json.dumps(structured)
payload = {
    "text": text,
    "thought": "must not escape",
    "sessionId": "must-not-escape",
    "stopReason": "EndTurn",
}
if mode == "incomplete_stop":
    payload["stopReason"] = "MaxTurns"
if mode == "confirmed_route":
    payload["model"] = "grok-4.5"
    payload["reasoningEffort"] = "high"
elif mode == "wrong_route":
    payload["model"] = "grok-composer-2.5-fast"
    payload["reasoningEffort"] = "low"
print(json.dumps(payload))
'''


class GrokMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        grok_mcp._record_route_state("ready_unverified")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.fake_grok = self.root / "grok"
        self.fake_grok.write_text(textwrap.dedent(FAKE_GROK), encoding="utf-8")
        self.fake_grok.chmod(0o755)
        self.record_dir = self.root / "records"
        self.env = mock.patch.dict(
            os.environ,
            {
                "GROK_CLI_PATH": str(self.fake_grok),
                "FAKE_GROK_RECORD_DIR": str(self.record_dir),
                "FAKE_GROK_MODE": "success",
                "FAKE_GROK_STATUS_MODE": "ready",
                "GROK_ORCHESTRATOR_TESTING": "1",
                "GROK_HOME": str(self.root / "fake-grok-home"),
                "XAI_API_KEY": "must-not-leak",
                "GROK_API_KEY": "must-not-leak",
                "GROK_XAI_API_BASE_URL": "https://untrusted.invalid",
                "GROK_MODELS_BASE_URL": "https://untrusted.invalid",
                "GROK_MODELS_LIST_URL": "https://untrusted.invalid/models",
                "GROK_CLI_CHAT_PROXY_BASE_URL": "https://untrusted.invalid",
                "GROK_WS_URL": "https://untrusted.invalid",
                "GROK_WS_ORIGIN": "https://untrusted.invalid",
                "GROK_AUTH_PROVIDER_COMMAND": "steal-auth",
                "GROK_WEB_FETCH_PROXY": "https://untrusted.invalid",
                "CLI_CHAT_PROXY_BASE_URL": "https://untrusted.invalid",
                "XAI_API_BASE_URL": "https://untrusted.invalid",
                "ARBITRARY_PROVIDER_KEY": "must-not-leak",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp_dir.cleanup()

    def read_records(self) -> list[dict[str, object]]:
        if not self.record_dir.exists():
            return []
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.record_dir.glob("*.json"))
        ]

    def read_record(self) -> dict[str, object]:
        records = self.read_records()
        self.assertEqual(len(records), 1, records)
        return records[0]

    def clear_records(self) -> None:
        if self.record_dir.exists():
            for path in self.record_dir.glob("*.json"):
                path.unlink()

    def test_resolve_grok_uses_override(self) -> None:
        self.assertEqual(grok_mcp.resolve_grok(), self.fake_grok.resolve())

    def test_consult_pins_and_hardens_route(self) -> None:
        response = grok_mcp._run_grok("consult", "check this")
        record = self.read_record()
        args = record["args"]
        self.assertEqual(response["text"], "fake answer")
        self.assertEqual(response["route_state"], "route_accepted")
        self.assertNotIn("thought", response)
        self.assertNotIn("sessionId", response)
        self.assertEqual(record["prompt_mode"], 0o600)
        self.assertFalse(Path(record["prompt_path"]).exists())
        self.assertEqual(args[args.index("--model") + 1], "grok-4.5")
        self.assertEqual(args[args.index("--effort") + 1], "high")
        self.assertEqual(args[args.index("--sandbox") + 1], "strict")
        self.assertEqual(args[args.index("--max-turns") + 1], "2")
        self.assertIn("--no-subagents", args)
        self.assertIn("--no-memory", args)
        self.assertIn("--no-plan", args)
        self.assertIn("--no-auto-update", args)
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertNotIn("--json-schema", args)
        self.assertNotIn("--check", args)
        denied = args[args.index("--disallowed-tools") + 1]
        self.assertIn("web_search", denied)
        self.assertIn("run_terminal_cmd", denied)
        self.assertIn("Agent", denied)
        self.assertIn("Bash(*)", [args[i + 1] for i, value in enumerate(args) if value == "--deny"])
        self.assertEqual(Path(args[args.index("--agent") + 1]).name, "consult.md")
        self.assertNotIn("check this", args)
        self.assertNotIn("--yolo", args)
        self.assertTrue(all(value is None for value in record["route_environment"].values()))
        self.assertEqual(record["hardening_environment"]["GROK_DISABLE_API_KEY_AUTH"], "1")
        self.assertEqual(record["hardening_environment"]["GROK_SUBAGENTS"], "0")

    def test_plan_review_is_structured_checked_and_fail_closed(self) -> None:
        response = grok_mcp._run_grok("plan_review", "review this plan")
        record = self.read_record()
        args = record["args"]
        self.assertEqual(response["data"]["decision"], "PLAN_REVISE")
        self.assertEqual(response["data"]["findings"][0]["priority"], "high")
        self.assertIn("--json-schema", args)
        self.assertNotIn("--check", args)
        self.assertIn("self-check", args[args.index("--rules") + 1])
        self.assertIn("schema-conforming JSON", args[args.index("--rules") + 1])
        self.assertIn("without Markdown fences", args[args.index("--rules") + 1])
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertEqual(args[args.index("--max-turns") + 1], "4")
        self.assertEqual(record["profile"], "plan-review.md")
        schema = json.loads(args[args.index("--json-schema") + 1])
        self.assertEqual(schema["properties"]["decision"]["enum"], ["PLAN_APPROVED", "PLAN_REVISE"])

        for mode in ("bad_plan_decision", "approved_with_findings", "revise_without_findings"):
            with self.subTest(mode=mode):
                self.record_dir.mkdir(exist_ok=True)
                for path in self.record_dir.glob("*.json"):
                    path.unlink()
                os.environ["FAKE_GROK_MODE"] = mode
                with self.assertRaises(grok_mcp.GrokBridgeError):
                    grok_mcp._run_grok("plan_review", "review this plan")

        for mode, message in (
            ("unordered_plan", "ordered"),
            ("plan_without_verification", "verification_steps"),
            ("incomplete_stop", "terminal completion"),
        ):
            with self.subTest(mode=mode):
                self.clear_records()
                os.environ["FAKE_GROK_MODE"] = mode
                with self.assertRaisesRegex(grok_mcp.GrokBridgeError, message):
                    grok_mcp._run_grok("plan_review", "review this plan")

    def test_research_is_structured_and_web_only(self) -> None:
        response = grok_mcp._run_grok("research", "find sources")
        record = self.read_record()
        args = record["args"]
        self.assertEqual(args[args.index("--tools") + 1], "web_search,web_fetch")
        self.assertEqual(args[args.index("--max-turns") + 1], "16")
        self.assertIn("--json-schema", args)
        self.assertNotIn("--check", args)
        denied = args[args.index("--disallowed-tools") + 1]
        self.assertIn("run_terminal_cmd", denied)
        self.assertIn("read_file", denied)
        self.assertIn("Agent", denied)
        self.assertNotIn("web_search", denied.split(","))
        self.assertEqual(record["profile"], "research.md")
        self.assertEqual(record["web_fetch_enabled"], "1")
        self.assertIn("schema-conforming JSON", args[args.index("--rules") + 1])
        self.assertTrue(response["data"]["sources"][0]["url"].startswith("https://"))

        os.environ["FAKE_GROK_MODE"] = "bad_research_url"
        with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "source URL"):
            grok_mcp._run_grok("research", "find sources")

        for mode, message in (
            ("uncataloged_research_url", "source catalog"),
            ("duplicate_research_source", "unique"),
            ("malformed_research_authority", "valid http"),
        ):
            with self.subTest(mode=mode):
                os.environ["FAKE_GROK_MODE"] = mode
                with self.assertRaisesRegex(grok_mcp.GrokBridgeError, message):
                    grok_mcp._run_grok("research", "find sources")

    def test_workspace_review_is_structured_read_only_and_canonical(self) -> None:
        workspace = self.root / "workspace"
        workspace.mkdir()
        response = grok_mcp._run_grok("workspace_review", "review", str(workspace / "."))
        record = self.read_record()
        args = record["args"]
        self.assertEqual(args[args.index("--tools") + 1], "read_file,grep,list_dir")
        denied = args[args.index("--disallowed-tools") + 1].split(",")
        self.assertIn("run_terminal_cmd", denied)
        self.assertIn("search_replace", denied)
        self.assertIn("web_search", denied)
        self.assertIn("Agent", denied)
        self.assertNotIn("read_file", denied)
        self.assertEqual(args[args.index("--cwd") + 1], str(workspace.resolve()))
        self.assertEqual(args[args.index("--max-turns") + 1], "16")
        self.assertNotIn("--check", args)
        self.assertIn("self-check", args[args.index("--rules") + 1])
        self.assertIn("schema-conforming JSON", args[args.index("--rules") + 1])
        self.assertEqual(response["data"]["findings"][0]["line"], 42)

        os.environ["FAKE_GROK_MODE"] = "bad_workspace_shape"
        with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "recommended_test"):
            grok_mcp._run_grok("workspace_review", "review", str(workspace))

        for mode, message in (
            ("escaping_workspace_path", "stay inside"),
            ("invalid_workspace_line", "invalid line"),
        ):
            with self.subTest(mode=mode):
                os.environ["FAKE_GROK_MODE"] = mode
                with self.assertRaisesRegex(grok_mcp.GrokBridgeError, message):
                    grok_mcp._run_grok("workspace_review", "review", str(workspace))

        os.environ["FAKE_GROK_MODE"] = "success"
        (workspace / ".grok-unsafe").touch()
        with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "cannot isolate"):
            grok_mcp._run_grok("workspace_review", "review", str(workspace))

    def test_workspace_review_rejects_missing_directory(self) -> None:
        with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "existing path"):
            grok_mcp._run_grok("workspace_review", "review", str(self.root / "missing"))

    def test_panel_runs_two_or_three_independent_tool_free_reviews(self) -> None:
        response = grok_mcp._run_panel("high-stakes decision", 3)
        records = self.read_records()
        self.assertEqual(response["panel_size"], 3)
        self.assertEqual(len(response["data"]["reviews"]), 3)
        self.assertEqual(
            [review["member"] for review in response["data"]["reviews"]],
            [1, 2, 3],
        )
        self.assertEqual(
            [review["lens"] for review in response["data"]["reviews"]],
            list(grok_mcp.PANEL_LENSES),
        )
        self.assertEqual(len(records), 3)
        self.assertEqual({record["profile"] for record in records}, {"panel-review.md"})
        self.assertEqual(len({record["packet"] for record in records}), 3)
        for record in records:
            args = record["args"]
            self.assertEqual(args[args.index("--tools") + 1], "")
            self.assertNotIn("--check", args)
            self.assertIn("self-check", args[args.index("--rules") + 1])
            self.assertIn("schema-conforming JSON", args[args.index("--rules") + 1])
            self.assertIn("--no-subagents", args)

        for invalid in (True, 1, 4, "2"):
            with self.subTest(panel_size=invalid):
                with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "panel_size"):
                    grok_mcp._run_panel("packet", invalid)

    def test_incomplete_panel_fails_and_reports_partial_state(self) -> None:
        os.environ["FAKE_GROK_MODE"] = "panel_partial"
        with self.assertRaises(grok_mcp.GrokBridgeError) as context:
            grok_mcp._run_panel("packet", 2)
        self.assertEqual(context.exception.code, "panel_incomplete")
        self.assertEqual(context.exception.details["requested_reviews"], 2)
        self.assertEqual(context.exception.details["completed_reviews"], 1)

    def test_status_reports_capabilities_profiles_and_truthful_state_without_model_call(self) -> None:
        status = grok_mcp.grok_status()
        self.assertTrue(status["available"])
        self.assertTrue(status["authenticated"])
        self.assertTrue(status["model_available"])
        self.assertTrue(status["capabilities_ready"])
        self.assertTrue(status["profiles_ready"])
        self.assertTrue(status["configuration_isolated"])
        self.assertFalse(status["check_with_no_subagents_supported"])
        self.assertEqual(status["self_check_strategy"], "inline_no_subagents")
        self.assertEqual(status["route_state"], "ready_unverified")
        self.assertEqual(status["default_model"], "grok-4.5")
        self.assertEqual(status["requested_model"], "grok-4.5")
        self.assertEqual(status["minimum_cli_version"], "0.2.99")
        self.assertEqual(status["readiness_issues"], [])
        self.assertEqual(set(status["profiles"]), {"consult", "plan_review", "research", "workspace_review", "panel_review"})
        self.assertFalse(self.record_dir.exists())

    def test_status_fails_closed_for_missing_capability_login_or_model(self) -> None:
        cases = {
            "missing_capability": (["cli_capabilities_unavailable"], "--json-schema"),
            "logged_out": (["authentication_unavailable"], None),
            "missing_model": (["model_unavailable"], None),
            "similar_model": (["model_unavailable"], None),
            "old_version": (["cli_version_unsupported"], None),
            "unsafe_integrations": (["route_isolation_unavailable"], None),
            "malformed_inspect": (["route_isolation_unavailable"], None),
        }
        for status_mode, (expected_issues, missing_flag) in cases.items():
            with self.subTest(status_mode=status_mode):
                os.environ["FAKE_GROK_STATUS_MODE"] = status_mode
                status = grok_mcp.grok_status()
                self.assertFalse(status["available"])
                self.assertEqual(status["route_state"], "unavailable")
                self.assertEqual(status["readiness_issues"], expected_issues)
                if missing_flag:
                    self.assertIn(missing_flag, status["missing_capabilities"])

        os.environ["FAKE_GROK_STATUS_MODE"] = "old_version_logged_out"
        status = grok_mcp.grok_status()
        self.assertEqual(
            status["readiness_issues"],
            ["cli_version_unsupported", "authentication_unavailable"],
        )

    def test_status_tracks_only_truthful_in_memory_route_state(self) -> None:
        self.assertEqual(grok_mcp.grok_status()["route_state"], "ready_unverified")
        grok_mcp._run_grok("consult", "check")
        self.assertEqual(grok_mcp.grok_status()["route_state"], "route_accepted")
        os.environ["FAKE_GROK_MODE"] = "confirmed_route"
        grok_mcp._run_grok("consult", "check")
        self.assertEqual(grok_mcp.grok_status()["route_state"], "used_and_confirmed")

    def test_preflight_is_rechecked_before_model_call(self) -> None:
        os.environ["FAKE_GROK_STATUS_MODE"] = "logged_out"
        with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "not ready") as context:
            grok_mcp._run_grok("consult", "check")
        self.assertEqual(
            context.exception.details,
            {
                "readiness_issues": ["authentication_unavailable"],
                "cli_version": "0.2.99",
                "minimum_cli_version": "0.2.99",
            },
        )
        self.assertFalse(self.record_dir.exists())

    def test_status_rejects_an_insecure_login_file(self) -> None:
        grok_home = Path(os.environ["GROK_HOME"])
        grok_home.mkdir(parents=True)
        auth = grok_home / "auth.json"
        auth.write_text("{}", encoding="utf-8")
        auth.chmod(0o644)
        status = grok_mcp.grok_status()
        self.assertFalse(status["available"])
        self.assertEqual(status["route_state"], "unavailable")
        self.assertEqual(status["error"]["code"], "auth_isolation_failed")
        self.assertEqual(status["readiness_issues"], ["auth_isolation_failed"])

    def test_structured_output_failure_reports_stage_without_retry(self) -> None:
        expected_common = {
            "automatic_retry_performed": False,
            "manual_retry_allowed": True,
        }

        os.environ["FAKE_GROK_MODE"] = "inner_malformed_structured"
        with self.assertRaises(grok_mcp.GrokBridgeError) as context:
            grok_mcp._run_grok("plan_review", "review this plan")
        self.assertEqual(context.exception.code, "invalid_structured_output")
        self.assertEqual(
            context.exception.details,
            {"failure_stage": "json_decode", **expected_common},
        )
        self.assertEqual(len(self.read_records()), 1)
        self.assertEqual(grok_mcp.grok_status()["route_state"], "ready_unverified")

        self.clear_records()
        os.environ["FAKE_GROK_MODE"] = "bad_plan_decision"
        with self.assertRaises(grok_mcp.GrokBridgeError) as context:
            grok_mcp._run_grok("plan_review", "review this plan")
        self.assertEqual(context.exception.code, "invalid_structured_output")
        self.assertEqual(
            context.exception.details,
            {"failure_stage": "contract_validation", **expected_common},
        )
        self.assertEqual(len(self.read_records()), 1)
        self.assertEqual(grok_mcp.grok_status()["route_state"], "ready_unverified")

    def test_runtime_identity_is_confirmed_only_from_metadata(self) -> None:
        os.environ["FAKE_GROK_MODE"] = "confirmed_route"
        response = grok_mcp._run_grok("consult", "check")
        self.assertEqual(response["route_state"], "used_and_confirmed")
        self.assertTrue(response["runtime_model_confirmed"])
        self.assertTrue(response["runtime_effort_confirmed"])

        os.environ["FAKE_GROK_MODE"] = "wrong_route"
        with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "did not use"):
            grok_mcp._run_grok("consult", "check")
        self.assertEqual(grok_mcp.grok_status()["route_state"], "ready_unverified")

    def test_malformed_json_and_error_object_fail_closed(self) -> None:
        os.environ["FAKE_GROK_MODE"] = "malformed"
        with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "malformed JSON"):
            grok_mcp._run_grok("consult", "check")
        record = self.read_record()
        self.assertFalse(Path(record["prompt_path"]).exists())

        self.clear_records()
        os.environ["FAKE_GROK_MODE"] = "error_object"
        with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "remote failure"):
            grok_mcp._run_grok("consult", "check")

    def test_nonzero_exit_redacts_packet_and_credentials(self) -> None:
        os.environ["FAKE_GROK_MODE"] = "nonzero"
        with self.assertRaises(grok_mcp.GrokBridgeError) as context:
            grok_mcp._run_grok("consult", "private packet")
        message = str(context.exception)
        self.assertNotIn("private packet", message)
        self.assertNotIn("top-secret", message)
        self.assertNotIn("019f59b1-0beb-7a51-a6ca-e868226cbcc5", message)
        self.assertIn("<redacted>", message)

    def test_output_limit_fails_closed(self) -> None:
        os.environ["FAKE_GROK_MODE"] = "oversized"
        with mock.patch.object(grok_mcp, "MAX_STDOUT_BYTES", 512):
            with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "output limit"):
                grok_mcp._run_grok("consult", "large")

        self.clear_records()
        os.environ["FAKE_GROK_MODE"] = "oversized_stderr"
        with mock.patch.object(grok_mcp, "MAX_STDERR_BYTES", 512):
            with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "output limit"):
                grok_mcp._run_grok("consult", "large stderr")

    def test_profile_integrity_rejects_writable_or_symlinked_profiles(self) -> None:
        profile_root = self.root / "profiles"
        shutil.copytree(grok_mcp.AGENT_PROFILE_ROOT, profile_root)
        (profile_root / "consult.md").chmod(0o666)
        with mock.patch.object(grok_mcp, "AGENT_PROFILE_ROOT", profile_root):
            ready, profiles = grok_mcp._profile_status()
        self.assertFalse(ready)
        self.assertFalse(profiles["consult"]["ready"])

        (profile_root / "consult.md").unlink()
        (profile_root / "consult.md").symlink_to(
            grok_mcp.AGENT_PROFILE_ROOT / "consult.md"
        )
        with mock.patch.object(grok_mcp, "AGENT_PROFILE_ROOT", profile_root):
            ready, profiles = grok_mcp._profile_status()
        self.assertFalse(ready)
        self.assertFalse(profiles["consult"]["ready"])

    def test_timeout_cleans_prompt_and_process_group(self) -> None:
        os.environ["FAKE_GROK_MODE"] = "spawn_child_sleep"
        with mock.patch.object(grok_mcp, "GROK_TIMEOUT_SECONDS", 0.5):
            with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "0.5 seconds"):
                grok_mcp._run_grok("consult", "slow")
        record = self.read_record()
        self.assertFalse(Path(record["prompt_path"]).exists())
        child_pid = int(record["child_pid"])
        if os.name == "posix":
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                os.kill(child_pid, signal.SIGKILL)
                self.fail("Grok child process survived timeout cleanup")

    @unittest.skipUnless(os.name == "posix", "requires POSIX process groups")
    def test_mcp_shutdown_cleans_child_prompt_and_isolated_runtime(self) -> None:
        os.environ["FAKE_GROK_MODE"] = "spawn_child_sleep"
        server = subprocess.Popen(
            [sys.executable, str(MODULE_PATH)],
            env=os.environ.copy(),
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        child_pid: int | None = None
        try:
            assert server.stdin is not None
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "consult_grok",
                    "arguments": {"packet": "slow shutdown test"},
                },
            }
            server.stdin.write(json.dumps(request) + "\n")
            server.stdin.flush()

            deadline = time.monotonic() + 8
            records: list[dict[str, object]] = []
            while time.monotonic() < deadline:
                records = self.read_records()
                if records and "child_pid" in records[0]:
                    break
                time.sleep(0.05)
            self.assertEqual(len(records), 1, records)
            record = records[0]
            child_pid = int(record["child_pid"])
            prompt_path = Path(record["prompt_path"])
            runtime_path = prompt_path.parent

            server.send_signal(signal.SIGTERM)
            server.wait(timeout=8)
            self.assertFalse(prompt_path.exists())
            self.assertFalse(runtime_path.exists())

            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("Grok child process survived MCP shutdown cleanup")
        finally:
            if server.stdin is not None:
                server.stdin.close()
            if server.poll() is None:
                server.kill()
                server.wait()
            if child_pid is not None:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_mcp_surface_has_six_bounded_tools(self) -> None:
        initialized = grok_mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(initialized["result"]["serverInfo"]["version"], "0.2.1")
        listed = grok_mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = listed["result"]["tools"]
        names = {tool["name"] for tool in tools}
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
        panel = next(tool for tool in tools if tool["name"] == "review_with_grok_panel")
        panel_size = panel["inputSchema"]["properties"]["panel_size"]
        self.assertEqual((panel_size["minimum"], panel_size["maximum"], panel_size["default"]), (2, 3, 2))
        for tool in tools:
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertFalse(tool["annotations"]["destructiveHint"])

    def test_mcp_success_invalid_arguments_and_unknown_tool(self) -> None:
        success = grok_mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "review_plan_with_grok", "arguments": {"packet": "check"}},
            }
        )
        payload = json.loads(success["result"]["content"][0]["text"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["decision"], "PLAN_REVISE")

        invalid = grok_mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "review_with_grok_panel", "arguments": {"packet": "check", "panel_size": 4}},
            }
        )
        self.assertTrue(invalid["result"]["isError"])
        error_payload = json.loads(invalid["result"]["content"][0]["text"])
        self.assertEqual(error_payload["error"]["code"], "invalid_panel_size")

        unknown = grok_mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "missing", "arguments": {}},
            }
        )
        self.assertTrue(unknown["result"]["isError"])

        extra = grok_mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "consult_grok",
                    "arguments": {"packet": "check", "unexpected": True},
                },
            }
        )
        self.assertTrue(extra["result"]["isError"])
        extra_payload = json.loads(extra["result"]["content"][0]["text"])
        self.assertEqual(extra_payload["error"]["code"], "invalid_arguments")

    def test_mcp_serializes_safe_readiness_and_structured_failure_details(self) -> None:
        os.environ["FAKE_GROK_STATUS_MODE"] = "logged_out"
        unavailable = grok_mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "consult_grok",
                    "arguments": {"packet": "check"},
                },
            }
        )
        unavailable_payload = json.loads(
            unavailable["result"]["content"][0]["text"]
        )
        self.assertEqual(unavailable_payload["error"]["code"], "grok_not_ready")
        self.assertEqual(
            unavailable_payload["details"],
            {
                "readiness_issues": ["authentication_unavailable"],
                "cli_version": "0.2.99",
                "minimum_cli_version": "0.2.99",
            },
        )

        os.environ["FAKE_GROK_STATUS_MODE"] = "ready"
        os.environ["FAKE_GROK_MODE"] = "inner_malformed_structured"
        structured = grok_mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "review_plan_with_grok",
                    "arguments": {"packet": "check"},
                },
            }
        )
        structured_payload = json.loads(
            structured["result"]["content"][0]["text"]
        )
        self.assertEqual(
            structured_payload["error"]["code"],
            "invalid_structured_output",
        )
        self.assertEqual(
            structured_payload["details"],
            {
                "failure_stage": "json_decode",
                "automatic_retry_performed": False,
                "manual_retry_allowed": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
