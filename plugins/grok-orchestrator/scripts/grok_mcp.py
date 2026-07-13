#!/usr/bin/env python3
"""Dependency-free, read-only MCP bridge from Codex to Grok 4.5."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterator
from urllib.parse import urlsplit


SERVER_NAME = "grok-orchestrator"
SERVER_VERSION = "0.2.0"
MODEL = "grok-4.5"
EFFORT = "high"
MIN_GROK_VERSION = (0, 2, 99)

GROK_TIMEOUT_SECONDS = 600
STATUS_TIMEOUT_SECONDS = 30
PROCESS_KILL_GRACE_SECONDS = 2
MAX_PACKET_CHARS = 200_000
MAX_MCP_LINE_BYTES = 1_000_000
MAX_STDOUT_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 256 * 1024
MAX_STATUS_STDOUT_BYTES = 2 * 1024 * 1024
MAX_AUTH_BYTES = 2 * 1024 * 1024

AGENT_PROFILE_ROOT = Path(__file__).resolve().parent / "agent_profiles"
PROFILE_HASHES = {
    "consult": "8f12607f700a001769bcd5e9a4575464a72d98bec04428edbc4fccbaf3def603",
    "panel_review": "8dba408f57afe68f93a3611ebf43c834ee07e92bc76f143c164f6f255c4e1bc3",
    "plan_review": "251fc54f514f33c7d278a330752188836c433c657374bbc295fef8d1234e7d01",
    "research": "533a9cad75891332fdd27a44c6c39d53404ecec4b9a36da54a37f19a5e179085",
    "workspace_review": "e00e2802f0891d726f6fac0c1b0b4bac345d87b546dee64b50cea4efd858723c",
}

PROFILE_FILES = {
    "consult": "consult.md",
    "plan_review": "plan-review.md",
    "research": "research.md",
    "workspace_review": "workspace-review.md",
    "panel_review": "panel-review.md",
}

REQUIRED_PROFILE_MARKERS = (
    "model: grok-4.5",
    "permission_mode: dontAsk",
    "agents_md: false",
    "discoverSkills: false",
    "inheritSkills: false",
)

KNOWN_TOOLS = (
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
    "web_search",
    "web_fetch",
    "open_page",
    "open_page_with_find",
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

BASE_DENY_RULES = ("Bash(*)", "Edit(*)", "MCPTool(*)")
TOOL_FREE_DENY_RULES = BASE_DENY_RULES + ("Read(*)", "Grep(*)", "WebFetch(*)")
RESEARCH_DENY_RULES = BASE_DENY_RULES + ("Read(*)", "Grep(*)")
WORKSPACE_DENY_RULES = BASE_DENY_RULES + ("WebFetch(*)",)

REQUIRED_HELP_FLAGS = (
    "--agent",
    "--check",
    "--cwd",
    "--deny",
    "--disable-web-search",
    "--disallowed-tools",
    "--effort",
    "--json-schema",
    "--max-turns",
    "--model",
    "--no-memory",
    "--no-plan",
    "--no-subagents",
    "--output-format",
    "--permission-mode",
    "--prompt-file",
    "--rules",
    "--sandbox",
    "--tools",
)

BASE_ENV_ALLOWLIST = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TERM",
    "USER",
)

TEST_ENV_ALLOWLIST = (
    "FAKE_GROK_MODE",
    "FAKE_GROK_RECORD_DIR",
    "FAKE_GROK_STATUS_MODE",
)

HARDENED_ENV = {
    "GROK_CLAUDE_AGENTS_ENABLED": "0",
    "GROK_CLAUDE_HOOKS_ENABLED": "0",
    "GROK_CLAUDE_MCPS_ENABLED": "0",
    "GROK_CLAUDE_RULES_ENABLED": "0",
    "GROK_CLAUDE_SKILLS_ENABLED": "0",
    "GROK_CURSOR_AGENTS_ENABLED": "0",
    "GROK_CURSOR_HOOKS_ENABLED": "0",
    "GROK_CURSOR_MCPS_ENABLED": "0",
    "GROK_CURSOR_RULES_ENABLED": "0",
    "GROK_CURSOR_SKILLS_ENABLED": "0",
    "GROK_DEFAULT_MODEL": MODEL,
    "GROK_DISABLE_API_KEY_AUTH": "1",
    "GROK_DISABLE_AUTOUPDATER": "1",
    "GROK_LSP_TOOLS": "0",
    "GROK_MEMORY": "0",
    "GROK_RESPECT_GITIGNORE": "1",
    "GROK_SANDBOX": "strict",
    "GROK_SANDBOX_AUTO_ALLOW_BASH": "0",
    "GROK_SUBAGENTS": "0",
    "GROK_TOOL_SEARCH": "0",
    "GROK_WEB_SEARCH_MODEL": MODEL,
    "GROK_WRITE_FILE": "0",
    "NO_COLOR": "1",
    "RUST_LOG": "error",
}

ROUTE_OVERRIDE_NAMES = (
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
    # Older and third-party wrapper spellings are removed as defense in depth.
    "CLI_CHAT_PROXY_BASE_URL",
    "XAI_API_BASE_URL",
    "XAI_BASE_URL",
    "GROK_BASE_URL",
    "GROK_API_BASE_URL",
)

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)\S+"),
    re.compile(
        r"(?i)([\"']?(?:session[_-]?id|sessionId|request[_-]?id|requestId)[\"']?"
        r"\s*[:=]\s*[\"']?)\S+"
    ),
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
)

TERMINAL_STOP_REASONS = {
    "complete",
    "completed",
    "endturn",
    "end_turn",
    "finished",
    "stop",
}

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_ROUTE_STATE = "ready_unverified"
_ROUTE_STATE_LOCK = threading.Lock()
_SHUTDOWN_EVENT = threading.Event()


def _object_schema(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _current_route_state() -> str:
    with _ROUTE_STATE_LOCK:
        return _ROUTE_STATE


def _record_route_state(state: str) -> None:
    global _ROUTE_STATE
    with _ROUTE_STATE_LOCK:
        _ROUTE_STATE = state


SHORT_TEXT = {"type": "string", "minLength": 1, "maxLength": 4_000}
LONG_TEXT = {"type": "string", "minLength": 1, "maxLength": 20_000}

PLAN_SCHEMA = _object_schema(
    {
        "decision": {"type": "string", "enum": ["PLAN_APPROVED", "PLAN_REVISE"]},
        "summary": LONG_TEXT,
        "findings": {
            "type": "array",
            "maxItems": 20,
            "items": _object_schema(
                {
                    "priority": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "title": SHORT_TEXT,
                    "problem": LONG_TEXT,
                    "correction": LONG_TEXT,
                },
                ("priority", "title", "problem", "correction"),
            ),
        },
        "verification_steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": SHORT_TEXT,
        },
    },
    ("decision", "summary", "findings", "verification_steps"),
)

RESEARCH_SCHEMA = _object_schema(
    {
        "summary": LONG_TEXT,
        "claims": {
            "type": "array",
            "minItems": 1,
            "maxItems": 40,
            "items": _object_schema(
                {
                    "statement": LONG_TEXT,
                    "source_urls": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 10,
                        "items": {"type": "string", "minLength": 1, "maxLength": 4_000},
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
                ("statement", "source_urls", "confidence"),
            ),
        },
        "sources": {
            "type": "array",
            "minItems": 1,
            "maxItems": 80,
            "items": _object_schema(
                {
                    "url": {"type": "string", "minLength": 1, "maxLength": 4_000},
                    "title": SHORT_TEXT,
                    "source_type": {
                        "type": "string",
                        "enum": ["primary", "secondary", "other"],
                    },
                },
                ("url", "title", "source_type"),
            ),
        },
        "uncertainties": {
            "type": "array",
            "maxItems": 30,
            "items": SHORT_TEXT,
        },
        "inferences": {
            "type": "array",
            "maxItems": 30,
            "items": SHORT_TEXT,
        },
    },
    ("summary", "claims", "sources", "uncertainties", "inferences"),
)

WORKSPACE_SCHEMA = _object_schema(
    {
        "summary": LONG_TEXT,
        "findings": {
            "type": "array",
            "maxItems": 100,
            "items": _object_schema(
                {
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "title": SHORT_TEXT,
                    "file": {"type": "string", "minLength": 1, "maxLength": 4_000},
                    "line": {"type": ["integer", "null"], "minimum": 1},
                    "evidence": LONG_TEXT,
                    "impact": LONG_TEXT,
                    "recommendation": LONG_TEXT,
                    "recommended_test": LONG_TEXT,
                },
                (
                    "severity",
                    "title",
                    "file",
                    "line",
                    "evidence",
                    "impact",
                    "recommendation",
                    "recommended_test",
                ),
            ),
        },
        "missing_tests": {
            "type": "array",
            "maxItems": 50,
            "items": SHORT_TEXT,
        },
    },
    ("summary", "findings", "missing_tests"),
)

PANEL_SCHEMA = _object_schema(
    {
        "verdict": {
            "type": "string",
            "enum": ["SUPPORTED", "CHALLENGED", "UNCERTAIN"],
        },
        "summary": LONG_TEXT,
        "findings": {
            "type": "array",
            "maxItems": 20,
            "items": _object_schema(
                {
                    "priority": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "title": SHORT_TEXT,
                    "analysis": LONG_TEXT,
                    "recommendation": LONG_TEXT,
                },
                ("priority", "title", "analysis", "recommendation"),
            ),
        },
        "verification_steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": SHORT_TEXT,
        },
    },
    ("verdict", "summary", "findings", "verification_steps"),
)


MODE_CONFIG: dict[str, dict[str, Any]] = {
    "consult": {
        "profile": "consult.md",
        "tools": (),
        "max_turns": 2,
        "check": False,
        "schema": None,
        "deny_rules": TOOL_FREE_DENY_RULES,
        "disable_web": True,
        "rules": (
            "Act only as a tool-free independent advisor to Codex. Analyze the "
            "self-contained packet, identify material errors and alternatives, and "
            "return concise advice. Do not implement changes or invent evidence."
        ),
    },
    "plan_review": {
        "profile": "plan-review.md",
        "tools": (),
        "max_turns": 4,
        "check": True,
        "schema": PLAN_SCHEMA,
        "deny_rules": TOOL_FREE_DENY_RULES,
        "disable_web": True,
        "rules": (
            "Act as a fail-closed plan gate for Codex. Return PLAN_APPROVED only "
            "when there are no material findings; otherwise return PLAN_REVISE with "
            "prioritized corrections and concrete verification steps. Before "
            "returning, self-check the proposed JSON once for contradictions, "
            "unsupported approval, and missing verification."
        ),
    },
    "research": {
        "profile": "research.md",
        "tools": ("web_search", "web_fetch"),
        "max_turns": 16,
        "check": False,
        "schema": RESEARCH_SCHEMA,
        "deny_rules": RESEARCH_DENY_RULES,
        "disable_web": False,
        "rules": (
            "Act as a bounded web researcher for Codex. Use only web_search and "
            "web_fetch, prefer primary sources, attach direct http(s) source URLs "
            "to every factual claim, and separate uncertainty from inference."
        ),
    },
    "workspace_review": {
        "profile": "workspace-review.md",
        "tools": ("read_file", "grep", "list_dir"),
        "max_turns": 16,
        "check": True,
        "schema": WORKSPACE_SCHEMA,
        "deny_rules": WORKSPACE_DENY_RULES,
        "disable_web": True,
        "rules": (
            "Act as a read-only workspace reviewer for Codex. Use only read_file, "
            "grep, and list_dir. Report prioritized, evidenced findings with safe "
            "relative paths and recommended tests. Never edit or run commands. "
            "Before returning, self-check every finding once for direct evidence, "
            "safe path syntax, impact, and a recommended test."
        ),
    },
    "panel_review": {
        "profile": "panel-review.md",
        "tools": (),
        "max_turns": 4,
        "check": True,
        "schema": PANEL_SCHEMA,
        "deny_rules": TOOL_FREE_DENY_RULES,
        "disable_web": True,
        "rules": (
            "Act as one independent panel reviewer for Codex. Apply only the "
            "assigned lens, challenge assumptions, and recommend verification. "
            "Do not infer or synthesize the views of other panel members. Before "
            "returning, self-check the proposed JSON once for unsupported claims "
            "and missing verification."
        ),
    },
}

PANEL_LENSES = (
    "risk_and_failure_modes",
    "alternatives_and_counterarguments",
    "evidence_and_verification",
)


class GrokBridgeError(RuntimeError):
    """A safe, user-facing bridge failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        mode: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.mode = mode
        self.details = details or {}


class BridgeShutdown(BaseException):
    """Internal signal used to unwind active calls during MCP shutdown."""

    def __init__(self, signal_number: int) -> None:
        super().__init__(signal_number)
        self.signal_number = signal_number


class _StreamCollector(threading.Thread):
    def __init__(
        self,
        stream: Any,
        limit: int,
        exceeded: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.limit = limit
        self.exceeded = exceeded
        self.data = bytearray()

    def run(self) -> None:
        try:
            read_chunk = getattr(self.stream, "read1", self.stream.read)
            while True:
                chunk = read_chunk(65_536)
                if not chunk:
                    return
                remaining = self.limit - len(self.data)
                if remaining <= 0:
                    self.exceeded.set()
                    return
                self.data.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.exceeded.set()
                    return
        except (OSError, ValueError):
            return


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
    for candidate in (
        home / ".local" / "bin" / "grok",
        home / ".grok" / "bin" / "grok",
        Path("/opt/homebrew/bin/grok"),
        Path("/usr/local/bin/grok"),
    ):
        if _is_executable(candidate):
            return candidate.resolve()

    raise GrokBridgeError(
        "grok_not_found",
        "Grok CLI is not installed or could not be found. Set GROK_CLI_PATH or add grok to PATH.",
    )


def resolve_grok_home() -> Path:
    override = os.environ.get("GROK_HOME", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".grok"


@contextmanager
def _isolated_runtime() -> Iterator[tuple[Path, Path, Path]]:
    with tempfile.TemporaryDirectory(prefix="grok-orchestrator-runtime-") as name:
        root = Path(name)
        root.chmod(0o700)
        home = root / "home"
        grok_home = root / "grok-home"
        home.mkdir(mode=0o700)
        grok_home.mkdir(mode=0o700)

        source = resolve_grok_home() / "auth.json"
        if source.exists():
            source_descriptor: int | None = None
            try:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                source_descriptor = os.open(source, flags)
                source_stat = os.fstat(source_descriptor)
                if not stat.S_ISREG(source_stat.st_mode):
                    raise GrokBridgeError(
                        "auth_isolation_failed",
                        "The Grok login file is not a regular file.",
                    )
                if source_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                    raise GrokBridgeError(
                        "auth_isolation_failed",
                        "The Grok login file has unsafe permissions.",
                    )
                if source_stat.st_size > MAX_AUTH_BYTES:
                    raise GrokBridgeError(
                        "auth_isolation_failed",
                        "The Grok login file exceeds the safe copy limit.",
                    )
                destination = grok_home / "auth.json"
                with os.fdopen(source_descriptor, "rb") as reader, destination.open(
                    "xb"
                ) as writer:
                    while chunk := reader.read(65_536):
                        writer.write(chunk)
                destination.chmod(0o600)
            except GrokBridgeError:
                if source_descriptor is not None:
                    try:
                        os.close(source_descriptor)
                    except OSError:
                        pass
                raise
            except OSError as exc:
                if source_descriptor is not None:
                    try:
                        os.close(source_descriptor)
                    except OSError:
                        pass
                raise GrokBridgeError(
                    "auth_isolation_failed",
                    "The Grok login could not be copied into the isolated runtime.",
                ) from exc

        yield root, home, grok_home


def grok_environment(
    mode: str | None,
    runtime_root: Path,
    runtime_home: Path,
    runtime_grok_home: Path,
) -> dict[str, str]:
    env: dict[str, str] = {}
    for name in BASE_ENV_ALLOWLIST:
        value = os.environ.get(name)
        if value:
            env[name] = value
    env.setdefault("PATH", os.defpath)
    env["HOME"] = str(runtime_home)
    env["GROK_HOME"] = str(runtime_grok_home)
    env["TMPDIR"] = str(runtime_root)
    env.update(HARDENED_ENV)
    env["GROK_WEB_FETCH"] = "1" if mode == "research" else "0"

    if os.environ.get("GROK_ORCHESTRATOR_TESTING") == "1":
        for name in TEST_ENV_ALLOWLIST:
            value = os.environ.get(name)
            if value is not None:
                env[name] = value

    for name in ROUTE_OVERRIDE_NAMES:
        env.pop(name, None)
    return env


def _validate_packet(packet: Any) -> str:
    if not isinstance(packet, str) or not packet.strip():
        raise GrokBridgeError("invalid_packet", "packet must be a non-empty string.")
    if len(packet) > MAX_PACKET_CHARS:
        raise GrokBridgeError(
            "packet_too_large",
            f"packet must not exceed {MAX_PACKET_CHARS} characters.",
        )
    return packet.strip()


def _canonical_workspace(cwd: Any) -> Path:
    if not isinstance(cwd, str) or not cwd.strip():
        raise GrokBridgeError("invalid_workspace", "cwd must be a non-empty directory path.")
    try:
        workspace = Path(cwd).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GrokBridgeError(
            "invalid_workspace",
            "cwd does not resolve to an existing path.",
        ) from exc
    if not workspace.is_dir():
        raise GrokBridgeError("invalid_workspace", "cwd must resolve to a directory.")
    return workspace


def _safe_diagnostic(value: str, packet: str = "") -> str:
    diagnostic = ANSI_ESCAPE.sub("", value).strip()
    if packet:
        diagnostic = diagnostic.replace(packet, "<packet omitted>")
    diagnostic = re.sub(
        r"https?://[^\s?#]+\?[^\s#]*",
        lambda match: match.group(0).split("?", 1)[0] + "?<redacted>",
        diagnostic,
    )
    for pattern in SENSITIVE_PATTERNS:
        diagnostic = pattern.sub(
            r"\1<redacted>" if pattern.groups else "<redacted>",
            diagnostic,
        )
    return diagnostic[-2_000:] if diagnostic else "no diagnostic was returned"


def _write_prompt_file(packet: str, directory: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix="prompt-",
        suffix=".txt",
        dir=directory,
        text=True,
    )
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


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + PROCESS_KILL_GRACE_SECONDS
        while time.monotonic() < deadline:
            process.poll()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            try:
                process.wait(timeout=PROCESS_KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        return

    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=PROCESS_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _close_process_streams(
    process: subprocess.Popen[bytes],
    readers: tuple[_StreamCollector, ...],
) -> None:
    for reader in readers:
        reader.join(timeout=PROCESS_KILL_GRACE_SECONDS)
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()


def _run_process(
    command: list[str],
    *,
    env: dict[str, str],
    timeout: float,
    stdout_limit: int | None = None,
    stderr_limit: int | None = None,
    mode: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if stdout_limit is None:
        stdout_limit = MAX_STDOUT_BYTES
    if stderr_limit is None:
        stderr_limit = MAX_STDERR_BYTES
    try:
        process = subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
            close_fds=True,
        )
    except OSError as exc:
        raise GrokBridgeError(
            "grok_launch_failed",
            "Could not launch the Grok CLI.",
            mode=mode,
        ) from exc

    readers: tuple[_StreamCollector, ...] = ()
    try:
        assert process.stdout is not None
        assert process.stderr is not None
        exceeded = threading.Event()
        stdout_reader = _StreamCollector(process.stdout, stdout_limit, exceeded)
        stderr_reader = _StreamCollector(process.stderr, stderr_limit, exceeded)
        readers = (stdout_reader, stderr_reader)
        stdout_reader.start()
        stderr_reader.start()

        deadline = time.monotonic() + timeout
        timed_out = False
        while process.poll() is None:
            if _SHUTDOWN_EVENT.is_set():
                raise BridgeShutdown(signal.SIGTERM)
            if exceeded.is_set():
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.02)

        if timed_out or exceeded.is_set():
            _terminate_process_group(process)
        else:
            process.wait()
        _close_process_streams(process, readers)
    except BaseException:
        _terminate_process_group(process)
        _close_process_streams(process, readers)
        raise

    if timed_out:
        raise GrokBridgeError(
            "grok_timeout",
            f"Grok did not finish within {timeout} seconds.",
            mode=mode,
        )
    if exceeded.is_set():
        raise GrokBridgeError(
            "output_limit",
            "Grok exceeded the bridge output limit.",
            mode=mode,
        )

    return subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout_reader.data.decode("utf-8", errors="replace"),
        stderr_reader.data.decode("utf-8", errors="replace"),
    )


def _profile_status() -> tuple[bool, dict[str, dict[str, Any]]]:
    profiles: dict[str, dict[str, Any]] = {}
    try:
        root = AGENT_PROFILE_ROOT.resolve(strict=True)
    except (OSError, RuntimeError):
        return False, {
            mode: {"file": filename, "ready": False, "sha256": None}
            for mode, filename in PROFILE_FILES.items()
        }

    for mode, filename in PROFILE_FILES.items():
        path = AGENT_PROFILE_ROOT / filename
        ready = False
        digest: str | None = None
        try:
            file_stat = path.lstat()
            resolved = path.resolve(strict=True)
            content = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            ready = (
                stat.S_ISREG(file_stat.st_mode)
                and not stat.S_ISLNK(file_stat.st_mode)
                and resolved.parent == root
                and not file_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                and digest == PROFILE_HASHES[mode]
                and all(marker in content for marker in REQUIRED_PROFILE_MARKERS)
            )
        except (OSError, RuntimeError, UnicodeError):
            ready = False
        profiles[mode] = {"file": filename, "ready": ready, "sha256": digest}
    return all(item["ready"] for item in profiles.values()), profiles


def _verified_profile(mode: str) -> Path:
    ready, profiles = _profile_status()
    if not ready or not profiles.get(mode, {}).get("ready"):
        raise GrokBridgeError(
            "profile_integrity_failed",
            "A bundled Grok profile failed its integrity check.",
            mode=mode,
        )
    return (AGENT_PROFILE_ROOT / PROFILE_FILES[mode]).resolve(strict=True)


def _parse_version(output: str) -> tuple[str | None, tuple[int, int, int] | None]:
    match = re.search(r"\bgrok\s+(\d+)\.(\d+)\.(\d+)(?:[-+][A-Za-z0-9.-]+)?", output)
    if not match:
        return None, None
    parts = tuple(int(value) for value in match.groups())
    return ".".join(match.groups()), parts


def _parse_models(output: str) -> tuple[set[str], str | None]:
    models: set[str] = set()
    for line in output.splitlines():
        match = re.match(r"^\s*[*-]\s+([^\s(]+)", line)
        if match:
            models.add(match.group(1))
    default_match = re.search(r"^\s*Default model:\s*([^\s]+)", output, re.MULTILINE)
    return models, default_match.group(1) if default_match else None


def _parse_isolation(
    output: str,
    runtime_grok_home: Path,
) -> tuple[bool, bool, bool]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return False, False, False
    if not isinstance(payload, dict):
        return False, False, False
    policy = payload.get("loginPolicy")
    api_key_auth_disabled = (
        isinstance(policy, dict) and policy.get("apiKeyAuthDisabled") is True
    )
    integrations_isolated = all(
        isinstance(payload.get(name), list) and not payload[name]
        for name in ("hooks", "plugins", "mcpServers")
    )
    config_sources = payload.get("configSources")
    configuration_isolated = False
    if isinstance(config_sources, dict) and isinstance(
        config_sources.get("layers"), list
    ):
        configuration_isolated = True
        root = runtime_grok_home.resolve(strict=False)
        for layer in config_sources["layers"]:
            if not isinstance(layer, dict) or not isinstance(layer.get("path"), str):
                configuration_isolated = False
                break
            try:
                Path(layer["path"]).resolve(strict=False).relative_to(root)
            except (OSError, RuntimeError, ValueError):
                configuration_isolated = False
                break
    return api_key_auth_disabled, integrations_isolated, configuration_isolated


def _inspect_runtime(
    grok: Path,
    env: dict[str, str],
    cwd: Path,
    *,
    mode: str | None = None,
) -> tuple[bool, bool, bool]:
    result = _run_process(
        [
            str(grok),
            "--no-auto-update",
            "--cwd",
            str(cwd),
            "inspect",
            "--json",
        ],
        env=env,
        timeout=STATUS_TIMEOUT_SECONDS,
        stdout_limit=MAX_STATUS_STDOUT_BYTES,
        mode=mode,
    )
    if result.returncode != 0:
        return False, False, False
    return _parse_isolation(result.stdout, Path(env["GROK_HOME"]))


def grok_status() -> dict[str, Any]:
    profiles_ready, profiles = _profile_status()
    base: dict[str, Any] = {
        "ok": True,
        "mode": "status",
        "available": False,
        "authenticated": False,
        "model_available": False,
        "capabilities_ready": False,
        "profiles_ready": profiles_ready,
        "profiles": profiles,
        "route_isolation_ready": False,
        "api_key_auth_disabled": False,
        "integrations_isolated": False,
        "configuration_isolated": False,
        "route_state": "unavailable",
        "requested_model": MODEL,
        "effort": EFFORT,
        "default_model": None,
        "cli_path": None,
        "cli_version": None,
        "version_supported": False,
        "missing_capabilities": [],
        "check_with_no_subagents_supported": False,
        "self_check_strategy": "inline_no_subagents",
    }
    try:
        grok = resolve_grok()
        base["cli_path"] = str(grok)
        with _isolated_runtime() as (runtime_root, runtime_home, runtime_grok_home):
            env = grok_environment(
                None,
                runtime_root,
                runtime_home,
                runtime_grok_home,
            )
            with tempfile.TemporaryDirectory(
                prefix="grok-orchestrator-status-cwd-",
                dir=runtime_root,
            ) as scratch_name:
                scratch = Path(scratch_name)
                version_result = _run_process(
                    [str(grok), "--no-auto-update", "--version"],
                    env=env,
                    timeout=STATUS_TIMEOUT_SECONDS,
                    stdout_limit=MAX_STATUS_STDOUT_BYTES,
                )
                help_result = _run_process(
                    [str(grok), "--help"],
                    env=env,
                    timeout=STATUS_TIMEOUT_SECONDS,
                    stdout_limit=MAX_STATUS_STDOUT_BYTES,
                )
                models_result = _run_process(
                    [str(grok), "--no-auto-update", "models"],
                    env=env,
                    timeout=STATUS_TIMEOUT_SECONDS,
                    stdout_limit=MAX_STATUS_STDOUT_BYTES,
                )
                check_probe = _run_process(
                    [
                        str(grok),
                        "--no-auto-update",
                        "--prompt-file",
                        str(runtime_root / "missing-check-probe"),
                        "--no-subagents",
                        "--check",
                    ],
                    env=env,
                    timeout=STATUS_TIMEOUT_SECONDS,
                    stdout_limit=MAX_STATUS_STDOUT_BYTES,
                )
                (
                    api_key_disabled,
                    integrations_isolated,
                    configuration_isolated,
                ) = _inspect_runtime(
                    grok,
                    env,
                    scratch,
                )
    except GrokBridgeError as exc:
        base["error"] = {"code": exc.code, "message": str(exc)}
        return base
    except OSError:
        base["error"] = {
            "code": "status_failed",
            "message": "The isolated Grok status check could not be completed.",
        }
        return base

    version, version_parts = _parse_version(version_result.stdout)
    base["cli_version"] = version
    version_supported = (
        version_result.returncode == 0
        and version_parts is not None
        and version_parts >= MIN_GROK_VERSION
    )
    base["version_supported"] = version_supported

    help_text = ANSI_ESCAPE.sub("", help_result.stdout)
    missing = [
        flag
        for flag in REQUIRED_HELP_FLAGS
        if not re.search(rf"(?<![A-Za-z0-9_-]){re.escape(flag)}(?![A-Za-z0-9_-])", help_text)
    ]
    if version_result.returncode != 0:
        missing.append("--no-auto-update")
    base["missing_capabilities"] = sorted(set(missing))
    check_diagnostic = (check_probe.stdout + check_probe.stderr).lower()
    base["check_with_no_subagents_supported"] = (
        "cannot be used with" not in check_diagnostic
        and "no such file" in check_diagnostic
    )
    capabilities_ready = (
        version_supported and help_result.returncode == 0 and not missing
    )
    base["capabilities_ready"] = capabilities_ready

    models_text = ANSI_ESCAPE.sub("", models_result.stdout)
    authenticated = (
        models_result.returncode == 0
        and re.search(r"logged in with\s+grok\.com", models_text, re.IGNORECASE)
        is not None
    )
    models, default_model = _parse_models(models_text)
    model_available = MODEL in models
    base["authenticated"] = authenticated
    base["model_available"] = model_available
    base["default_model"] = default_model
    base["api_key_auth_disabled"] = api_key_disabled
    base["integrations_isolated"] = integrations_isolated
    base["configuration_isolated"] = configuration_isolated
    base["route_isolation_ready"] = (
        api_key_disabled and integrations_isolated and configuration_isolated
    )

    available = all(
        (
            profiles_ready,
            capabilities_ready,
            authenticated,
            model_available,
            base["route_isolation_ready"],
        )
    )
    base["available"] = available
    base["route_state"] = _current_route_state() if available else "unavailable"
    if not available:
        base["error"] = {
            "code": "grok_unavailable",
            "message": (
                "The isolated Grok route is not ready. Check CLI compatibility, "
                "grok.com login, exact grok-4.5 availability, and bundled profiles."
            ),
        }
    return base


def _disallowed_tools(allowed: tuple[str, ...]) -> str:
    allowed_set = set(allowed)
    return ",".join(tool for tool in KNOWN_TOOLS if tool not in allowed_set)


def _build_command(
    grok: Path,
    mode: str,
    profile: Path,
    prompt_path: Path,
    workspace: Path,
) -> list[str]:
    config = MODE_CONFIG[mode]
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
        "--no-subagents",
        "--no-plan",
        "--no-auto-update",
        "--max-turns",
        str(config["max_turns"]),
        "--cwd",
        str(workspace),
        "--rules",
        str(config["rules"]),
    ]
    tools = tuple(config["tools"])
    command.extend(("--tools", ",".join(tools)))
    command.extend(("--disallowed-tools", _disallowed_tools(tools)))
    for rule in config["deny_rules"]:
        command.extend(("--deny", rule))
    if config["disable_web"]:
        command.append("--disable-web-search")
    if config["schema"] is not None:
        command.extend(
            (
                "--json-schema",
                json.dumps(config["schema"], separators=(",", ":"), sort_keys=True),
            )
        )
    return command


def _require_object(
    value: Any,
    fields: tuple[str, ...],
    context: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GrokBridgeError(
            "invalid_structured_output",
            f"{context} must be a JSON object.",
        )
    missing = [field for field in fields if field not in value]
    if missing:
        raise GrokBridgeError(
            "invalid_structured_output",
            f"{context} is missing required field {missing[0]}.",
        )
    extra = sorted(set(value) - set(fields))
    if extra:
        raise GrokBridgeError(
            "invalid_structured_output",
            f"{context} contains unsupported field {extra[0]}.",
        )
    return value


def _require_string(value: Any, context: str, max_length: int = 20_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise GrokBridgeError(
            "invalid_structured_output",
            f"{context} must be a bounded non-empty string.",
        )
    return value.strip()


def _require_string_list(
    value: Any,
    context: str,
    *,
    minimum: int = 0,
    maximum: int = 100,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise GrokBridgeError(
            "invalid_structured_output",
            f"{context} must be a bounded array.",
        )
    return [
        _require_string(item, f"{context}[{index}]", 4_000)
        for index, item in enumerate(value)
    ]


def _validate_priority_order(
    items: list[dict[str, Any]],
    field: str,
    context: str,
) -> None:
    values = [PRIORITY_ORDER[item[field]] for item in items]
    if values != sorted(values):
        raise GrokBridgeError(
            "invalid_structured_output",
            f"{context} must be ordered from highest to lowest priority.",
        )


def _validate_plan(data: Any) -> dict[str, Any]:
    root = _require_object(
        data,
        ("decision", "summary", "findings", "verification_steps"),
        "plan result",
    )
    decision = root["decision"]
    if decision not in {"PLAN_APPROVED", "PLAN_REVISE"}:
        raise GrokBridgeError(
            "invalid_structured_output",
            "plan decision must be PLAN_APPROVED or PLAN_REVISE.",
        )
    summary = _require_string(root["summary"], "plan summary")
    if not isinstance(root["findings"], list) or len(root["findings"]) > 20:
        raise GrokBridgeError(
            "invalid_structured_output",
            "plan findings must be a bounded array.",
        )
    findings: list[dict[str, Any]] = []
    for index, value in enumerate(root["findings"]):
        item = _require_object(
            value,
            ("priority", "title", "problem", "correction"),
            f"plan finding {index}",
        )
        priority = item["priority"]
        if priority not in PRIORITY_ORDER:
            raise GrokBridgeError(
                "invalid_structured_output",
                f"plan finding {index} has an invalid priority.",
            )
        findings.append(
            {
                "priority": priority,
                "title": _require_string(item["title"], "finding title", 4_000),
                "problem": _require_string(item["problem"], "finding problem"),
                "correction": _require_string(item["correction"], "finding correction"),
            }
        )
    _validate_priority_order(findings, "priority", "plan findings")
    if decision == "PLAN_APPROVED" and findings:
        raise GrokBridgeError(
            "invalid_structured_output",
            "PLAN_APPROVED cannot contain material findings.",
        )
    if decision == "PLAN_REVISE" and not findings:
        raise GrokBridgeError(
            "invalid_structured_output",
            "PLAN_REVISE requires at least one finding.",
        )
    verification = _require_string_list(
        root["verification_steps"],
        "verification_steps",
        minimum=1,
        maximum=20,
    )
    return {
        "decision": decision,
        "summary": summary,
        "findings": findings,
        "verification_steps": verification,
    }


def _validate_source_url(value: Any) -> str:
    url = _require_string(value, "source URL", 4_000)
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise GrokBridgeError(
            "invalid_structured_output",
            "Every source URL must be a valid http(s) URL without embedded credentials.",
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise GrokBridgeError(
            "invalid_structured_output",
            "Every source URL must be an http(s) URL without embedded credentials.",
        )
    return url


def _validate_research(data: Any) -> dict[str, Any]:
    root = _require_object(
        data,
        ("summary", "claims", "sources", "uncertainties", "inferences"),
        "research result",
    )
    summary = _require_string(root["summary"], "research summary")
    if not isinstance(root["sources"], list) or not 1 <= len(root["sources"]) <= 80:
        raise GrokBridgeError(
            "invalid_structured_output",
            "research sources must be a non-empty bounded array.",
        )
    sources: list[dict[str, Any]] = []
    catalog: set[str] = set()
    for index, value in enumerate(root["sources"]):
        item = _require_object(
            value,
            ("url", "title", "source_type"),
            f"research source {index}",
        )
        url = _validate_source_url(item["url"])
        if url in catalog:
            raise GrokBridgeError(
                "invalid_structured_output",
                "research source URLs must be unique.",
            )
        source_type = item["source_type"]
        if source_type not in {"primary", "secondary", "other"}:
            raise GrokBridgeError(
                "invalid_structured_output",
                f"research source {index} has an invalid source_type.",
            )
        catalog.add(url)
        sources.append(
            {
                "url": url,
                "title": _require_string(item["title"], "source title", 4_000),
                "source_type": source_type,
            }
        )

    if not isinstance(root["claims"], list) or not 1 <= len(root["claims"]) <= 40:
        raise GrokBridgeError(
            "invalid_structured_output",
            "research claims must be a non-empty bounded array.",
        )
    claims: list[dict[str, Any]] = []
    for index, value in enumerate(root["claims"]):
        item = _require_object(
            value,
            ("statement", "source_urls", "confidence"),
            f"research claim {index}",
        )
        urls = [_validate_source_url(url) for url in _require_string_list(
            item["source_urls"],
            f"research claim {index} source_urls",
            minimum=1,
            maximum=10,
        )]
        if len(urls) != len(set(urls)):
            raise GrokBridgeError(
                "invalid_structured_output",
                "Claim source URLs must be unique.",
            )
        if any(url not in catalog for url in urls):
            raise GrokBridgeError(
                "invalid_structured_output",
                "Every claim source URL must appear in the source catalog.",
            )
        confidence = item["confidence"]
        if confidence not in {"high", "medium", "low"}:
            raise GrokBridgeError(
                "invalid_structured_output",
                f"research claim {index} has an invalid confidence.",
            )
        claims.append(
            {
                "statement": _require_string(item["statement"], "claim statement"),
                "source_urls": urls,
                "confidence": confidence,
            }
        )
    return {
        "summary": summary,
        "claims": claims,
        "sources": sources,
        "uncertainties": _require_string_list(
            root["uncertainties"],
            "research uncertainties",
            maximum=30,
        ),
        "inferences": _require_string_list(
            root["inferences"],
            "research inferences",
            maximum=30,
        ),
    }


def _safe_workspace_path(value: Any, workspace: Path) -> str:
    relative = _require_string(value, "workspace finding file", 4_000)
    candidate = Path(relative)
    if candidate.is_absolute():
        raise GrokBridgeError(
            "invalid_structured_output",
            "Workspace finding paths must be relative.",
        )
    try:
        (workspace / candidate).resolve(strict=False).relative_to(workspace)
    except (OSError, RuntimeError, ValueError) as exc:
        raise GrokBridgeError(
            "invalid_structured_output",
            "Workspace finding paths must stay inside the reviewed directory.",
        ) from exc
    return candidate.as_posix()


def _validate_workspace(data: Any, workspace: Path) -> dict[str, Any]:
    root = _require_object(
        data,
        ("summary", "findings", "missing_tests"),
        "workspace result",
    )
    summary = _require_string(root["summary"], "workspace summary")
    if not isinstance(root["findings"], list) or len(root["findings"]) > 100:
        raise GrokBridgeError(
            "invalid_structured_output",
            "workspace findings must be a bounded array.",
        )
    findings: list[dict[str, Any]] = []
    fields = (
        "severity",
        "title",
        "file",
        "line",
        "evidence",
        "impact",
        "recommendation",
        "recommended_test",
    )
    for index, value in enumerate(root["findings"]):
        item = _require_object(value, fields, f"workspace finding {index}")
        severity = item["severity"]
        if severity not in PRIORITY_ORDER:
            raise GrokBridgeError(
                "invalid_structured_output",
                f"workspace finding {index} has an invalid severity.",
            )
        line = item["line"]
        if line is not None and (
            type(line) is not int or line < 1
        ):
            raise GrokBridgeError(
                "invalid_structured_output",
                f"workspace finding {index} has an invalid line.",
            )
        findings.append(
            {
                "severity": severity,
                "title": _require_string(item["title"], "finding title", 4_000),
                "file": _safe_workspace_path(item["file"], workspace),
                "line": line,
                "evidence": _require_string(item["evidence"], "finding evidence"),
                "impact": _require_string(item["impact"], "finding impact"),
                "recommendation": _require_string(
                    item["recommendation"],
                    "finding recommendation",
                ),
                "recommended_test": _require_string(
                    item["recommended_test"],
                    "finding recommended_test",
                ),
            }
        )
    _validate_priority_order(findings, "severity", "workspace findings")
    return {
        "summary": summary,
        "findings": findings,
        "missing_tests": _require_string_list(
            root["missing_tests"],
            "missing_tests",
            maximum=50,
        ),
    }


def _validate_panel_member(data: Any) -> dict[str, Any]:
    root = _require_object(
        data,
        ("verdict", "summary", "findings", "verification_steps"),
        "panel result",
    )
    verdict = root["verdict"]
    if verdict not in {"SUPPORTED", "CHALLENGED", "UNCERTAIN"}:
        raise GrokBridgeError(
            "invalid_structured_output",
            "Panel verdict must be SUPPORTED, CHALLENGED, or UNCERTAIN.",
        )
    if not isinstance(root["findings"], list) or len(root["findings"]) > 20:
        raise GrokBridgeError(
            "invalid_structured_output",
            "panel findings must be a bounded array.",
        )
    findings: list[dict[str, Any]] = []
    for index, value in enumerate(root["findings"]):
        item = _require_object(
            value,
            ("priority", "title", "analysis", "recommendation"),
            f"panel finding {index}",
        )
        priority = item["priority"]
        if priority not in PRIORITY_ORDER:
            raise GrokBridgeError(
                "invalid_structured_output",
                f"panel finding {index} has an invalid priority.",
            )
        findings.append(
            {
                "priority": priority,
                "title": _require_string(item["title"], "panel finding title", 4_000),
                "analysis": _require_string(item["analysis"], "panel finding analysis"),
                "recommendation": _require_string(
                    item["recommendation"],
                    "panel finding recommendation",
                ),
            }
        )
    _validate_priority_order(findings, "priority", "panel findings")
    return {
        "verdict": verdict,
        "summary": _require_string(root["summary"], "panel summary"),
        "findings": findings,
        "verification_steps": _require_string_list(
            root["verification_steps"],
            "panel verification_steps",
            minimum=1,
            maximum=20,
        ),
    }


def _validate_structured(
    mode: str,
    data: Any,
    workspace: Path,
) -> dict[str, Any]:
    if mode == "plan_review":
        return _validate_plan(data)
    if mode == "research":
        return _validate_research(data)
    if mode == "workspace_review":
        return _validate_workspace(data, workspace)
    if mode == "panel_review":
        return _validate_panel_member(data)
    raise GrokBridgeError("invalid_mode", f"Unsupported structured mode: {mode}.")


def _runtime_identity(payload: dict[str, Any], mode: str) -> tuple[str, bool, bool]:
    runtime_model = payload.get("model")
    if runtime_model is not None and not isinstance(runtime_model, str):
        raise GrokBridgeError(
            "route_mismatch",
            "Grok returned malformed runtime model metadata.",
            mode=mode,
        )
    effort_values = [
        payload[name]
        for name in ("reasoningEffort", "reasoning_effort", "effort")
        if name in payload
    ]
    if any(not isinstance(value, str) for value in effort_values):
        raise GrokBridgeError(
            "route_mismatch",
            "Grok returned malformed runtime effort metadata.",
            mode=mode,
        )
    if len(set(effort_values)) > 1:
        raise GrokBridgeError(
            "route_mismatch",
            "Grok returned conflicting runtime effort metadata.",
            mode=mode,
        )
    runtime_effort = effort_values[0] if effort_values else None
    if runtime_model is not None and runtime_model != MODEL:
        raise GrokBridgeError(
            "route_mismatch",
            f"Grok did not use the requested {MODEL} model.",
            mode=mode,
        )
    if runtime_effort is not None and runtime_effort.lower() != EFFORT:
        raise GrokBridgeError(
            "route_mismatch",
            f"Grok did not use {EFFORT} reasoning effort.",
            mode=mode,
        )
    model_confirmed = runtime_model == MODEL
    effort_confirmed = (
        isinstance(runtime_effort, str) and runtime_effort.lower() == EFFORT
    )
    state = (
        "used_and_confirmed"
        if model_confirmed and effort_confirmed
        else "route_accepted"
    )
    return state, model_confirmed, effort_confirmed


def _run_grok(
    mode: str,
    packet: Any,
    cwd: Any = None,
    *,
    _preflight: bool = True,
) -> dict[str, Any]:
    if mode not in MODE_CONFIG:
        raise GrokBridgeError("invalid_mode", f"Unsupported Grok mode: {mode!r}.")
    clean_packet = _validate_packet(packet)
    workspace = _canonical_workspace(cwd) if mode == "workspace_review" else None
    if _preflight:
        status_result = grok_status()
        if not status_result["available"]:
            raise GrokBridgeError(
                "grok_not_ready",
                "The isolated Grok route is not ready; call grok_status for safe diagnostics.",
                mode=mode,
            )
    _record_route_state("ready_unverified")

    scratch: tempfile.TemporaryDirectory[str] | None = None
    try:
        grok = resolve_grok()
        profile = _verified_profile(mode)
        with _isolated_runtime() as (runtime_root, runtime_home, runtime_grok_home):
            env = grok_environment(
                mode,
                runtime_root,
                runtime_home,
                runtime_grok_home,
            )
            if workspace is None:
                scratch = tempfile.TemporaryDirectory(
                    prefix="grok-orchestrator-cwd-",
                    dir=runtime_root,
                )
                effective_workspace = Path(scratch.name)
            else:
                effective_workspace = workspace
                (
                    api_key_disabled,
                    integrations_isolated,
                    configuration_isolated,
                ) = _inspect_runtime(
                    grok,
                    env,
                    effective_workspace,
                    mode=mode,
                )
                if not all(
                    (
                        api_key_disabled,
                        integrations_isolated,
                        configuration_isolated,
                    )
                ):
                    raise GrokBridgeError(
                        "route_isolation_failed",
                        "The workspace activates configuration that the read-only route cannot isolate.",
                        mode=mode,
                    )

            prompt_path = _write_prompt_file(clean_packet, runtime_root)
            command = _build_command(
                grok,
                mode,
                profile,
                prompt_path,
                effective_workspace,
            )
            try:
                result = _run_process(
                    command,
                    env=env,
                    timeout=GROK_TIMEOUT_SECONDS,
                    mode=mode,
                )
            finally:
                prompt_path.unlink(missing_ok=True)
    finally:
        if scratch is not None:
            scratch.cleanup()

    if result.returncode != 0:
        raise GrokBridgeError(
            "grok_failed",
            f"Grok CLI exited with status {result.returncode}; diagnostic: <redacted>.",
            mode=mode,
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GrokBridgeError(
            "malformed_response",
            "Grok CLI returned malformed JSON.",
            mode=mode,
        ) from exc
    if not isinstance(payload, dict):
        raise GrokBridgeError(
            "malformed_response",
            "Grok CLI returned an unexpected JSON value.",
            mode=mode,
        )
    if payload.get("type") == "error":
        message = payload.get("message")
        detail = _safe_diagnostic(
            message if isinstance(message, str) else "Grok reported an error.",
            clean_packet,
        )
        raise GrokBridgeError("grok_failed", detail, mode=mode)

    text_value = payload.get("text")
    if not isinstance(text_value, str) or not text_value.strip():
        raise GrokBridgeError(
            "malformed_response",
            "Grok CLI response did not contain text.",
            mode=mode,
        )
    stop_reason = (
        payload.get("stopReason")
        if isinstance(payload.get("stopReason"), str)
        else None
    )
    if (
        MODE_CONFIG[mode]["check"]
        and stop_reason is not None
        and stop_reason.replace("-", "_").lower() not in TERMINAL_STOP_REASONS
    ):
        raise GrokBridgeError(
            "incomplete_response",
            "Grok did not report a terminal completion state.",
            mode=mode,
        )

    route_state, model_confirmed, effort_confirmed = _runtime_identity(payload, mode)
    response: dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "text": text_value.strip(),
        "requested_model": MODEL,
        "effort": EFFORT,
        "stop_reason": stop_reason,
        "route_state": route_state,
        "runtime_model_confirmed": model_confirmed,
        "runtime_effort_confirmed": effort_confirmed,
    }
    if MODE_CONFIG[mode]["schema"] is not None:
        try:
            raw_data = json.loads(text_value)
        except json.JSONDecodeError as exc:
            raise GrokBridgeError(
                "invalid_structured_output",
                "Grok returned malformed structured output.",
                mode=mode,
            ) from exc
        data = _validate_structured(mode, raw_data, effective_workspace)
        response["data"] = data
        response["text"] = json.dumps(
            data,
            separators=(",", ":"),
            sort_keys=True,
        )
    _record_route_state(route_state)
    return response


def _validate_panel_size(panel_size: Any) -> int:
    if type(panel_size) is not int or panel_size not in {2, 3}:
        raise GrokBridgeError(
            "invalid_panel_size",
            "panel_size must be the integer 2 or 3.",
            mode="panel_review",
        )
    return panel_size


def _run_panel(packet: Any, panel_size: Any = 2) -> dict[str, Any]:
    clean_packet = _validate_packet(packet)
    size = _validate_panel_size(panel_size)
    status_result = grok_status()
    if not status_result["available"]:
        raise GrokBridgeError(
            "grok_not_ready",
            "The isolated Grok route is not ready; call grok_status for safe diagnostics.",
            mode="panel_review",
        )

    completed: dict[int, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(
        max_workers=size,
        thread_name_prefix="grok-panel",
    ) as executor:
        futures = {}
        for member in range(1, size + 1):
            lens = PANEL_LENSES[member - 1]
            member_packet = (
                f"Panel member {member} of {size}.\n"
                f"Assigned lens: {lens}.\n"
                "Review independently. Do not infer peer opinions or panel consensus.\n\n"
                f"Packet:\n{clean_packet}"
            )
            if len(member_packet) > MAX_PACKET_CHARS:
                raise GrokBridgeError(
                    "packet_too_large",
                    "packet leaves no room for the panel assignment wrapper.",
                    mode="panel_review",
                )
            future = executor.submit(
                _run_grok,
                "panel_review",
                member_packet,
                _preflight=False,
            )
            futures[future] = (member, lens)
        for future in as_completed(futures):
            member, lens = futures[future]
            try:
                result = future.result()
                completed[member] = {"lens": lens, "result": result}
            except GrokBridgeError as exc:
                failures.append({"member": member, "code": exc.code})
            except Exception:
                failures.append({"member": member, "code": "panel_member_failed"})

    if failures or len(completed) != size:
        _record_route_state("ready_unverified")
        raise GrokBridgeError(
            "panel_incomplete",
            "The Grok panel did not complete every independent review.",
            mode="panel_review",
            details={
                "requested_reviews": size,
                "completed_reviews": len(completed),
                "failures": sorted(failures, key=lambda item: item["member"]),
            },
        )

    reviews = []
    for member in range(1, size + 1):
        item = completed[member]
        result = item["result"]
        reviews.append(
            {
                "member": member,
                "lens": item["lens"],
                "verdict": result["data"]["verdict"],
                "summary": result["data"]["summary"],
                "findings": result["data"]["findings"],
                "verification_steps": result["data"]["verification_steps"],
                "route_state": result["route_state"],
                "stop_reason": result["stop_reason"],
            }
        )
    data = {"reviews": reviews}
    confirmed = all(
        item["result"]["route_state"] == "used_and_confirmed"
        for item in completed.values()
    )
    route_state = "used_and_confirmed" if confirmed else "route_accepted"
    _record_route_state(route_state)
    return {
        "ok": True,
        "mode": "panel_review",
        "text": json.dumps(data, separators=(",", ":"), sort_keys=True),
        "data": data,
        "panel_size": size,
        "requested_model": MODEL,
        "effort": EFFORT,
        "stop_reason": None,
        "route_state": route_state,
        "runtime_model_confirmed": all(
            item["result"]["runtime_model_confirmed"]
            for item in completed.values()
        ),
        "runtime_effort_confirmed": all(
            item["result"]["runtime_effort_confirmed"]
            for item in completed.values()
        ),
    }


def _annotations(
    *,
    idempotent: bool = False,
    open_world: bool = True,
) -> dict[str, bool]:
    return {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": idempotent,
        "openWorldHint": open_world,
    }


def tool_definitions() -> list[dict[str, Any]]:
    packet_property = {
        "type": "string",
        "description": (
            "Self-contained task, context, constraints, evidence, and requested output."
        ),
    }
    packet_schema = {
        "type": "object",
        "properties": {"packet": packet_property},
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
            "name": "review_plan_with_grok",
            "title": "Gate a plan with Grok 4.5",
            "description": (
                "Return a fail-closed PLAN_APPROVED or PLAN_REVISE decision with "
                "structured findings and verification steps."
            ),
            "inputSchema": packet_schema,
            "annotations": _annotations(),
        },
        {
            "name": "research_with_grok",
            "title": "Research with Grok 4.5",
            "description": (
                "Research with only web search and fetch, returning structured "
                "claims, source URLs, uncertainties, and inferences."
            ),
            "inputSchema": packet_schema,
            "annotations": _annotations(),
        },
        {
            "name": "review_workspace_with_grok",
            "title": "Review a workspace with Grok 4.5",
            "description": (
                "Inspect a canonical directory with only read_file, grep, and "
                "list_dir, returning structured read-only findings."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "packet": packet_property,
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Path to the existing workspace directory to review."
                        ),
                    },
                },
                "required": ["packet", "cwd"],
                "additionalProperties": False,
            },
            "annotations": _annotations(),
        },
        {
            "name": "review_with_grok_panel",
            "title": "Run an independent Grok 4.5 panel",
            "description": (
                "Run two or three fresh, tool-free high-effort reviews and return "
                "each result for Codex to compare and synthesize."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "packet": packet_property,
                    "panel_size": {
                        "type": "integer",
                        "enum": [2, 3],
                        "minimum": 2,
                        "maximum": 3,
                        "default": 2,
                        "description": "Number of independent Grok reviews.",
                    },
                },
                "required": ["packet"],
                "additionalProperties": False,
            },
            "annotations": _annotations(),
        },
        {
            "name": "grok_status",
            "title": "Check the isolated Grok 4.5 route",
            "description": (
                "Check CLI compatibility, grok.com login, model availability, "
                "profile integrity, and isolation without making a model call."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "annotations": _annotations(idempotent=True, open_world=False),
        },
    ]


def _success_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
        "isError": False,
    }


def _error_result(error: GrokBridgeError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "mode": error.mode,
        "text": None,
        "error": {"code": error.code, "message": str(error)},
        "requested_model": MODEL,
        "effort": EFFORT,
    }
    if error.details:
        payload["details"] = error.details
    return {
        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
        "isError": True,
    }


def _validate_arguments(
    arguments: Any,
    *,
    allowed: tuple[str, ...],
    required: tuple[str, ...],
    mode: str,
) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise GrokBridgeError(
            "invalid_arguments",
            "Tool arguments must be an object.",
            mode=mode,
        )
    extra = sorted(set(arguments) - set(allowed))
    if extra:
        raise GrokBridgeError(
            "invalid_arguments",
            f"Unsupported tool argument: {extra[0]}.",
            mode=mode,
        )
    missing = [name for name in required if name not in arguments]
    if missing:
        raise GrokBridgeError(
            "invalid_arguments",
            f"Missing required tool argument: {missing[0]}.",
            mode=mode,
        )
    return arguments


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
        try:
            if name == "consult_grok":
                values = _validate_arguments(
                    arguments,
                    allowed=("packet",),
                    required=("packet",),
                    mode="consult",
                )
                result = _success_result(_run_grok("consult", values["packet"]))
            elif name == "review_plan_with_grok":
                values = _validate_arguments(
                    arguments,
                    allowed=("packet",),
                    required=("packet",),
                    mode="plan_review",
                )
                result = _success_result(
                    _run_grok("plan_review", values["packet"])
                )
            elif name == "research_with_grok":
                values = _validate_arguments(
                    arguments,
                    allowed=("packet",),
                    required=("packet",),
                    mode="research",
                )
                result = _success_result(_run_grok("research", values["packet"]))
            elif name == "review_workspace_with_grok":
                values = _validate_arguments(
                    arguments,
                    allowed=("packet", "cwd"),
                    required=("packet", "cwd"),
                    mode="workspace_review",
                )
                result = _success_result(
                    _run_grok(
                        "workspace_review",
                        values["packet"],
                        values["cwd"],
                    )
                )
            elif name == "review_with_grok_panel":
                values = _validate_arguments(
                    arguments,
                    allowed=("packet", "panel_size"),
                    required=("packet",),
                    mode="panel_review",
                )
                result = _success_result(
                    _run_panel(
                        values["packet"],
                        values.get("panel_size", 2),
                    )
                )
            elif name == "grok_status":
                _validate_arguments(
                    arguments,
                    allowed=(),
                    required=(),
                    mode="status",
                )
                result = _success_result(grok_status())
            else:
                raise GrokBridgeError(
                    "unknown_tool",
                    f"Unknown tool: {name!r}.",
                )
        except GrokBridgeError as exc:
            result = _error_result(exc)
        except Exception:
            result = _error_result(
                GrokBridgeError(
                    "internal_error",
                    "The Grok bridge encountered an unexpected internal failure.",
                )
            )
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _discard_oversized_line(stream: Any) -> None:
    while True:
        chunk = stream.readline(MAX_MCP_LINE_BYTES + 1)
        if not chunk or chunk.endswith(b"\n"):
            return


def _shutdown_handler(signal_number: int, _frame: Any) -> None:
    _SHUTDOWN_EVENT.set()
    raise BridgeShutdown(signal_number)


def main() -> int:
    _SHUTDOWN_EVENT.clear()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _shutdown_handler)
        signal.signal(signal.SIGINT, _shutdown_handler)
    stream = sys.stdin.buffer
    try:
        while True:
            raw = stream.readline(MAX_MCP_LINE_BYTES + 1)
            if not raw:
                break
            if len(raw) > MAX_MCP_LINE_BYTES and not raw.endswith(b"\n"):
                _discard_oversized_line(stream)
                response: dict[str, Any] | None = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "MCP request exceeds the input limit."},
                }
            else:
                try:
                    request = json.loads(raw.decode("utf-8"))
                    if not isinstance(request, dict):
                        raise ValueError("request must be an object")
                    response = handle_request(request)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    response = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": str(exc)},
                    }
            if response is not None:
                print(json.dumps(response, separators=(",", ":")), flush=True)
    except BridgeShutdown as shutdown:
        return 128 + shutdown.signal_number
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
