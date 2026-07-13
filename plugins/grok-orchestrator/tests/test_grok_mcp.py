from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import textwrap
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
import sys
import time

args = sys.argv[1:]
if args == ["--version"]:
    print("grok 0.test")
    raise SystemExit(0)
if args == ["models"]:
    print("You are logged in with grok.com.\n\nDefault model: grok-4.5\n\nAvailable models:\n  * grok-4.5 (default)")
    raise SystemExit(0)

prompt_path = Path(args[args.index("--prompt-file") + 1])
packet = prompt_path.read_text(encoding="utf-8").strip()
record = {
    "args": args,
    "packet": packet,
    "prompt_path": str(prompt_path),
    "prompt_mode": stat.S_IMODE(prompt_path.stat().st_mode),
    "web_fetch_enabled": os.environ.get("GROK_WEB_FETCH"),
}
Path(os.environ["FAKE_GROK_RECORD"]).write_text(json.dumps(record), encoding="utf-8")
mode = os.environ.get("FAKE_GROK_MODE", "success")
if mode == "malformed":
    print("not-json")
elif mode == "error_object":
    print(json.dumps({"type": "error", "message": "remote failure"}))
elif mode == "nonzero":
    print("Bearer top-secret", file=sys.stderr)
    print("session_id=019f59b1-0beb-7a51-a6ca-e868226cbcc5", file=sys.stderr)
    print(packet, file=sys.stderr)
    raise SystemExit(7)
elif mode == "sleep":
    time.sleep(2)
else:
    print(json.dumps({
        "text": "fake answer",
        "thought": "must not escape",
        "sessionId": "must-not-escape",
        "stopReason": "EndTurn",
    }))
'''


class GrokMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.fake_grok = self.root / "grok"
        self.fake_grok.write_text(textwrap.dedent(FAKE_GROK), encoding="utf-8")
        self.fake_grok.chmod(0o755)
        self.record = self.root / "record.json"
        self.env = mock.patch.dict(
            os.environ,
            {
                "GROK_CLI_PATH": str(self.fake_grok),
                "FAKE_GROK_RECORD": str(self.record),
                "FAKE_GROK_MODE": "success",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp_dir.cleanup()

    def read_record(self) -> dict[str, object]:
        return json.loads(self.record.read_text(encoding="utf-8"))

    def test_resolve_grok_uses_override(self) -> None:
        self.assertEqual(grok_mcp.resolve_grok(), self.fake_grok.resolve())

    def test_consult_pins_route_and_hides_internal_fields(self) -> None:
        response = grok_mcp._run_grok("consult", "check this")
        record = self.read_record()
        args = record["args"]
        self.assertEqual(response["text"], "fake answer")
        self.assertNotIn("thought", response)
        self.assertNotIn("sessionId", response)
        self.assertEqual(record["prompt_mode"], 0o600)
        self.assertFalse(Path(record["prompt_path"]).exists())
        self.assertEqual(args[args.index("--model") + 1], "grok-4.5")
        self.assertEqual(args[args.index("--effort") + 1], "high")
        self.assertEqual(args[args.index("--sandbox") + 1], "strict")
        self.assertNotIn("--tools", args)
        self.assertIn("web_search", args[args.index("--disallowed-tools") + 1])
        self.assertIn("Agent", args[args.index("--disallowed-tools") + 1])
        self.assertNotIn("--no-subagents", args)
        self.assertIn("--no-auto-update", args)
        self.assertEqual(
            Path(args[args.index("--agent") + 1]).name,
            "consult.md",
        )
        self.assertNotIn("check this", args)
        self.assertNotIn("--yolo", args)

    def test_research_has_only_web_tools(self) -> None:
        grok_mcp._run_grok("research", "find sources")
        args = self.read_record()["args"]
        self.assertEqual(args[args.index("--tools") + 1], "web_search,web_fetch")
        denied = args[args.index("--disallowed-tools") + 1]
        self.assertIn("run_terminal_cmd", denied)
        self.assertIn("search_tool", denied)
        self.assertIn("Agent", denied)
        self.assertEqual(
            Path(args[args.index("--agent") + 1]).name,
            "research.md",
        )

    def test_workspace_review_has_only_read_tools_and_canonical_cwd(self) -> None:
        workspace = self.root / "workspace"
        workspace.mkdir()
        grok_mcp._run_grok("workspace_review", "review", str(workspace / "."))
        args = self.read_record()["args"]
        self.assertEqual(args[args.index("--tools") + 1], "read_file,grep,list_dir")
        self.assertEqual(args[args.index("--disallowed-tools") + 1], "search_tool,use_tool,Agent")
        self.assertEqual(args[args.index("--cwd") + 1], str(workspace.resolve()))

    def test_research_uses_ephemeral_cwd_and_enables_web_fetch(self) -> None:
        grok_mcp._run_grok("research", "find sources")
        record = self.read_record()
        args = record["args"]
        self.assertFalse(Path(args[args.index("--cwd") + 1]).exists())
        self.assertEqual(record["web_fetch_enabled"], "1")

    def test_workspace_review_rejects_missing_directory(self) -> None:
        with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "existing path"):
            grok_mcp._run_grok("workspace_review", "review", str(self.root / "missing"))

    def test_status_does_not_make_model_call(self) -> None:
        status = grok_mcp.grok_status()
        self.assertTrue(status["available"])
        self.assertTrue(status["authenticated"])
        self.assertTrue(status["model_available"])
        self.assertEqual(status["default_model"], "grok-4.5")
        self.assertFalse(self.record.exists())

    def test_malformed_json_fails_closed(self) -> None:
        os.environ["FAKE_GROK_MODE"] = "malformed"
        with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "malformed JSON"):
            grok_mcp._run_grok("consult", "check")

    def test_error_object_fails_closed(self) -> None:
        os.environ["FAKE_GROK_MODE"] = "error_object"
        with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "remote failure"):
            grok_mcp._run_grok("consult", "check")

    def test_nonzero_exit_redacts_packet_and_bearer_token(self) -> None:
        os.environ["FAKE_GROK_MODE"] = "nonzero"
        with self.assertRaises(grok_mcp.GrokBridgeError) as context:
            grok_mcp._run_grok("consult", "private packet")
        message = str(context.exception)
        self.assertNotIn("private packet", message)
        self.assertNotIn("top-secret", message)
        self.assertNotIn("019f59b1-0beb-7a51-a6ca-e868226cbcc5", message)
        self.assertIn("<redacted>", message)

    def test_timeout_is_reported_and_prompt_is_cleaned(self) -> None:
        os.environ["FAKE_GROK_MODE"] = "sleep"
        with mock.patch.object(grok_mcp, "GROK_TIMEOUT_SECONDS", 0.5):
            with self.assertRaisesRegex(grok_mcp.GrokBridgeError, "0.5 seconds"):
                grok_mcp._run_grok("consult", "slow")
        prompt_path = Path(self.read_record()["prompt_path"])
        self.assertFalse(prompt_path.exists())

    def test_mcp_initialize_list_and_unknown_tool(self) -> None:
        initialized = grok_mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "grok-orchestrator")
        listed = grok_mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertEqual(
            names,
            {"consult_grok", "research_with_grok", "review_workspace_with_grok", "grok_status"},
        )
        unknown = grok_mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "missing", "arguments": {}},
            }
        )
        self.assertTrue(unknown["result"]["isError"])

    def test_mcp_success_and_argument_error(self) -> None:
        success = grok_mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "consult_grok", "arguments": {"packet": "check"}},
            }
        )
        payload = json.loads(success["result"]["content"][0]["text"])
        self.assertTrue(payload["ok"])
        invalid = grok_mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "consult_grok", "arguments": {}},
            }
        )
        self.assertTrue(invalid["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
