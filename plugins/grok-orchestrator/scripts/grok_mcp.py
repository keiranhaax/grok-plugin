#!/usr/bin/env python3
"""Read-only MCP bridge from Codex to Grok 4.5 through the Grok CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any


SERVER_NAME = "grok-orchestrator"
SERVER_VERSION = "0.1.0"
MODEL = "grok-4.5"
EFFORT = "high"
GROK_TIMEOUT_SECONDS = 600
STATUS_TIMEOUT_SECONDS = 30
MAX_PACKET_CHARS = 200_000
AGENT_PROFILE_ROOT = Path(__file__).resolve().parent / "agent_profiles"

NON_WEB_TOOLS = ",".join(
    (
        "run_terminal_cmd",
        "read_file",
        "search_replace",
        "list_dir",
        "grep",
        "task",
        "todo_write",
        "kill_task",
        "get_task_output",
        "memory_search",
        "memory_get",
        "search_tool",
        "use_tool",
        "lsp",
        "scheduler_create",
        "scheduler_delete",
        "scheduler_list",
        "monitor",
        "update_goal",
        "x_user_search",
        "x_semantic_search",
        "x_keyword_search",
        "x_thread_fetch",
        "image_gen",
        "image_edit",
        "image_to_video",
        "reference_to_video",
        "write",
        "enter_plan_mode",
        "exit_plan_mode",
        "ask_user_question",
        "Agent",
    )
)
ALL_KNOWN_TOOLS = ",".join(
    ("web_search", "web_fetch", "open_page", "open_page_with_find", NON_WEB_TOOLS)
)

MODE_CONFIG: dict[str, dict[str, str | None]] = {
    "consult": {
        "profile": "consult.md",
        "tools": None,
        "disallowed_tools": ALL_KNOWN_TOOLS,
        "rules": (
            "Act as an independent advisor to Codex, the root orchestrator. Analyze only the "
            "self-contained packet. Identify material errors, missing constraints, and better "
            "alternatives. Do not attempt implementation or claim access to evidence not supplied."
        ),
    },
    "research": {
        "profile": "research.md",
        "tools": "web_search,web_fetch",
        # Grok CLI 0.2.x can fail open when its web allowlist aliases are unmappable.
        # The explicit denylist is applied after the allowlist and removes every
        # non-web tool in the supported 0.2.x registry, including subagents.
        "disallowed_tools": NON_WEB_TOOLS,
        "rules": (
            "Act as a bounded web researcher for Codex, the root orchestrator. Research the packet "
            "using only web search and web fetch. Prefer primary sources, include direct source "
            "links for factual claims, distinguish facts from inference, and report uncertainty."
        ),
    },
    "workspace_review": {
        "profile": "workspace-review.md",
        "tools": "read_file,grep,list_dir",
        "disallowed_tools": "search_tool,use_tool,Agent",
        "rules": (
            "Act as a read-only workspace reviewer for Codex, the root orchestrator. Use only file "
            "reading, directory listing, and text search. Prioritize correctness, regressions, "
            "security, data integrity, and missing tests. Cite file paths and line numbers when "
            "available. Do not edit files or attempt implementation."
        ),
    },
}

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)\S+"),
    re.compile(
        r"(?i)([\"']?(?:session[_-]?id|sessionId|request[_-]?id|requestId)[\"']?"
        r"\s*[:=]\s*[\"']?)\S+"
    ),
)


class GrokBridgeError(RuntimeError):
    """A safe, user-facing bridge failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def resolve_grok() -> Path:
    override = os.environ.get("GROK_CLI_PATH", "").strip()
    if override:
        candidate = Path(override).expanduser()
        if not _is_executable(candidate):
            raise GrokBridgeError(
                "grok_not_found",
                "GROK_CLI_PATH does not point to an executable Grok CLI.",
            )
        return candidate.resolve()

    found = shutil.which("grok")
    if found:
        candidate = Path(found)
        if _is_executable(candidate):
            return candidate.resolve()

    home = Path.home()
    candidates = (
        home / ".local" / "bin" / "grok",
        home / ".grok" / "bin" / "grok",
        Path("/opt/homebrew/bin/grok"),
        Path("/usr/local/bin/grok"),
    )
    for candidate in candidates:
        if _is_executable(candidate):
            return candidate.resolve()

    raise GrokBridgeError(
        "grok_not_found",
        "Grok CLI is not installed or could not be found. Set GROK_CLI_PATH or add grok to PATH.",
    )


def grok_environment(mode: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["RUST_LOG"] = "error"
    env["NO_COLOR"] = "1"
    if mode == "research":
        env["GROK_WEB_FETCH"] = "1"
    return env


def _validate_packet(packet: Any) -> str:
    if not isinstance(packet, str) or not packet.strip():
        raise GrokBridgeError("invalid_packet", "`packet` must be a non-empty string.")
    if len(packet) > MAX_PACKET_CHARS:
        raise GrokBridgeError(
            "packet_too_large",
            f"`packet` must not exceed {MAX_PACKET_CHARS} characters.",
        )
    return packet.strip()


def _canonical_workspace(cwd: Any) -> Path:
    if not isinstance(cwd, str) or not cwd.strip():
        raise GrokBridgeError("invalid_workspace", "`cwd` must be a non-empty directory path.")
    try:
        workspace = Path(cwd).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GrokBridgeError("invalid_workspace", "`cwd` does not resolve to an existing path.") from exc
    if not workspace.is_dir():
        raise GrokBridgeError("invalid_workspace", "`cwd` must resolve to a directory.")
    return workspace


def _safe_diagnostic(value: str, packet: str = "") -> str:
    diagnostic = ANSI_ESCAPE.sub("", value).strip()
    if packet:
        diagnostic = diagnostic.replace(packet, "<packet omitted>")
    for pattern in SENSITIVE_PATTERNS:
        diagnostic = pattern.sub(r"\1<redacted>", diagnostic)
    if not diagnostic:
        return "no diagnostic was returned"
    return diagnostic[-2_000:]


def _write_prompt_file(packet: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix="grok-orchestrator-", suffix=".txt", text=True)
    path = Path(name)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(packet)
            handle.write("\n")
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    return path


def _run_grok(mode: str, packet: Any, cwd: Any = None) -> dict[str, Any]:
    clean_packet = _validate_packet(packet)
    config = MODE_CONFIG[mode]
    scratch = None
    if mode == "workspace_review":
        workspace = _canonical_workspace(cwd)
    else:
        scratch = tempfile.TemporaryDirectory(prefix="grok-orchestrator-cwd-")
        workspace = Path(scratch.name)
    grok = resolve_grok()
    profile = AGENT_PROFILE_ROOT / str(config["profile"])
    if not profile.is_file():
        if scratch is not None:
            scratch.cleanup()
        raise GrokBridgeError("profile_missing", "The bundled Grok agent profile is missing.")
    prompt_path = _write_prompt_file(clean_packet)
    command = [
        str(grok),
        "--prompt-file",
        str(prompt_path),
        "--agent",
        str(profile),
        "--model",
        MODEL,
        "--effort",
        EFFORT,
        "--output-format",
        "json",
        "--sandbox",
        "strict",
        "--permission-mode",
        "dontAsk",
        "--no-memory",
        "--no-auto-update",
        "--cwd",
        str(workspace),
        "--rules",
        str(config["rules"]),
    ]
    if config["tools"] is not None:
        command.extend(("--tools", str(config["tools"])))
    if config["disallowed_tools"]:
        command.extend(("--disallowed-tools", str(config["disallowed_tools"])))

    try:
        result = subprocess.run(
            command,
            env=grok_environment(mode),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GROK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GrokBridgeError(
            "grok_timeout",
            f"Grok did not finish within {GROK_TIMEOUT_SECONDS} seconds.",
        ) from exc
    except OSError as exc:
        raise GrokBridgeError("grok_launch_failed", f"Could not launch Grok CLI: {exc}.") from exc
    finally:
        prompt_path.unlink(missing_ok=True)
        if scratch is not None:
            scratch.cleanup()

    if result.returncode != 0:
        detail = _safe_diagnostic(result.stderr or result.stdout, clean_packet)
        raise GrokBridgeError(
            "grok_failed",
            f"Grok CLI exited with status {result.returncode}: {detail}",
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GrokBridgeError("malformed_response", "Grok CLI returned malformed JSON.") from exc
    if not isinstance(payload, dict):
        raise GrokBridgeError("malformed_response", "Grok CLI returned an unexpected JSON value.")
    if payload.get("type") == "error":
        message = payload.get("message")
        detail = _safe_diagnostic(message if isinstance(message, str) else "Grok reported an error.")
        raise GrokBridgeError("grok_failed", detail)

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise GrokBridgeError("malformed_response", "Grok CLI response did not contain text.")

    return {
        "ok": True,
        "mode": mode,
        "text": text.strip(),
        "requested_model": MODEL,
        "effort": EFFORT,
        "stop_reason": payload.get("stopReason") if isinstance(payload.get("stopReason"), str) else None,
    }


def grok_status() -> dict[str, Any]:
    try:
        grok = resolve_grok()
    except GrokBridgeError as exc:
        return {
            "ok": True,
            "mode": "status",
            "available": False,
            "authenticated": False,
            "model_available": False,
            "requested_model": MODEL,
            "effort": EFFORT,
            "error": {"code": exc.code, "message": str(exc)},
        }

    try:
        version_result = subprocess.run(
            [str(grok), "--version"],
            env=grok_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=STATUS_TIMEOUT_SECONDS,
            check=False,
        )
        models_result = subprocess.run(
            [str(grok), "models"],
            env=grok_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=STATUS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": True,
            "mode": "status",
            "available": False,
            "authenticated": False,
            "model_available": False,
            "requested_model": MODEL,
            "effort": EFFORT,
            "cli_path": str(grok),
            "error": {"code": "status_failed", "message": f"Could not inspect Grok CLI: {exc}."},
        }

    models_output = ANSI_ESCAPE.sub("", models_result.stdout)
    authenticated = "logged in with" in models_output.lower()
    model_available = any(
        MODEL in line and line.strip().startswith(("*", "-"))
        for line in models_output.splitlines()
    )
    default_match = re.search(r"^Default model:\s*(\S+)", models_output, re.MULTILINE)
    available = (
        version_result.returncode == 0
        and models_result.returncode == 0
        and authenticated
        and model_available
    )
    response: dict[str, Any] = {
        "ok": True,
        "mode": "status",
        "available": available,
        "authenticated": authenticated,
        "model_available": model_available,
        "default_model": default_match.group(1) if default_match else None,
        "requested_model": MODEL,
        "effort": EFFORT,
        "cli_path": str(grok),
        "cli_version": ANSI_ESCAPE.sub("", version_result.stdout).strip() or None,
    }
    if not available:
        response["error"] = {
            "code": "grok_unavailable",
            "message": "Grok CLI is not logged in or grok-4.5 is not available; run `grok login` and retry.",
        }
    return response


def _annotations() -> dict[str, bool]:
    return {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }


def tool_definitions() -> list[dict[str, Any]]:
    packet_schema = {
        "type": "object",
        "properties": {
            "packet": {
                "type": "string",
                "description": "Self-contained task, context, constraints, evidence, and requested output.",
            }
        },
        "required": ["packet"],
        "additionalProperties": False,
    }
    return [
        {
            "name": "consult_grok",
            "title": "Consult Grok 4.5",
            "description": "Get a high-effort, tool-free second opinion from Grok 4.5.",
            "inputSchema": packet_schema,
            "annotations": _annotations(),
        },
        {
            "name": "research_with_grok",
            "title": "Research with Grok 4.5",
            "description": "Ask Grok 4.5 to research a self-contained question using only web search and fetch.",
            "inputSchema": packet_schema,
            "annotations": _annotations(),
        },
        {
            "name": "review_workspace_with_grok",
            "title": "Review a workspace with Grok 4.5",
            "description": "Ask Grok 4.5 to inspect a workspace using only read, list, and grep tools.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "packet": packet_schema["properties"]["packet"],
                    "cwd": {
                        "type": "string",
                        "description": "Canonical path to the existing workspace directory to review.",
                    },
                },
                "required": ["packet", "cwd"],
                "additionalProperties": False,
            },
            "annotations": _annotations(),
        },
        {
            "name": "grok_status",
            "title": "Check Grok 4.5 status",
            "description": "Check Grok CLI login and grok-4.5 availability without a model call.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "annotations": _annotations(),
        },
    ]


def _success_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
        "isError": False,
    }


def _error_result(error: GrokBridgeError) -> dict[str, Any]:
    payload = {
        "ok": False,
        "error": {"code": error.code, "message": str(error)},
        "requested_model": MODEL,
        "effort": EFFORT,
    }
    return {
        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
        "isError": True,
    }


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    if request_id is None:
        return None
    method = request.get("method")

    if method == "initialize":
        result: Any = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_definitions()}
    elif method == "tools/call":
        params = request.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
        if not isinstance(arguments, dict):
            result = _error_result(GrokBridgeError("invalid_arguments", "Tool arguments must be an object."))
        else:
            try:
                if name == "consult_grok":
                    result = _success_result(_run_grok("consult", arguments.get("packet")))
                elif name == "research_with_grok":
                    result = _success_result(_run_grok("research", arguments.get("packet")))
                elif name == "review_workspace_with_grok":
                    result = _success_result(
                        _run_grok("workspace_review", arguments.get("packet"), arguments.get("cwd"))
                    )
                elif name == "grok_status":
                    if arguments:
                        raise GrokBridgeError("invalid_arguments", "grok_status does not accept arguments.")
                    result = _success_result(grok_status())
                else:
                    raise GrokBridgeError("unknown_tool", f"Unknown tool: {name!r}.")
            except GrokBridgeError as exc:
                result = _error_result(exc)
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = handle_request(request)
        except (json.JSONDecodeError, ValueError) as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(exc)},
            }
        if response is not None:
            print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
