---
name: grok-orchestrator
description: Use Grok 4.5 as a bounded, read-only advisor, structured plan gate, web researcher, workspace reviewer, or explicit independent panel while Codex remains the root orchestrator. Use whenever the user explicitly asks for Grok. Proactively make one call only at a complex plan, evidence-heavy research, risky review, or material double-check gate where independent critique could change the result.
---

# Grok Orchestrator

Keep Codex as the root orchestrator. Grok provides untrusted evidence or critique;
it never owns the task, edits files, runs commands, directs other workers, or makes
the final decision.

## Select one bounded tool

- Use `consult_grok(packet)` for tool-free advice, tradeoff analysis, an answer
  check, or a concise second opinion.
- Use `review_plan_with_grok(packet)` for a structured plan gate. It returns
  `PLAN_APPROVED` or `PLAN_REVISE`, prioritized findings, corrections, and
  verification steps.
- Use `research_with_grok(packet)` for current or niche external research. It
  returns structured claims, source URLs, uncertainties, and inferences using only
  `web_search` and `web_fetch`.
- Use `review_workspace_with_grok(packet, cwd)` for a structured, read-only
  review of a canonical existing directory using only `read_file`, `grep`, and
  `list_dir`.
- Use `review_with_grok_panel(packet, panel_size)` only when the user explicitly
  asks for or approves a panel. It runs two reviews by default and accepts at most
  three. Codex must compare the independent results and synthesize the decision.
- Use `grok_status()` before the first call when readiness is uncertain and after
  authentication, compatibility, isolation, model, or launch failures. It makes no
  model call.

Single-pass review is the default. Do not silently turn one requested review into a
panel or spend two or three calls at a routine gate.

## Decide whether to call proactively

Make at most one targeted proactive Grok call at a decision gate. Good gates are:

- a complex plan with material sequencing, migration, rollback, safety, or
  verification risk;
- evidence-heavy research where an independent search could change the answer;
- a risky code or architecture review where a second model may catch a regression;
- a final material double-check before a high-impact conclusion.

Skip Grok for simple explanations, mechanical edits, routine status checks, or work
already settled by direct evidence. Never make an optional panel call proactively.

## Build a self-contained packet

Include:

1. The exact question or decision.
2. Relevant facts, evidence, code excerpts, or the artifact under review.
3. Constraints, non-goals, and the current proposal.
4. Known uncertainties and what would change the decision.
5. The requested focus and expected output.

For workspace review, pass the canonical workspace path separately as `cwd` and
state the files, behavior, diff, and risks to inspect. Never include credentials,
private keys, tokens, `.env` contents, or unrelated personal data.

## Apply structured results

For a plan gate:

- Treat any malformed decision or semantic contradiction as a failed gate.
- Continue only after Codex independently resolves every material
  `PLAN_REVISE` finding.
- Do not interpret `PLAN_APPROVED` as proof that implementation is correct.

For research:

- Open and independently verify important source URLs.
- Prefer primary sources and check that each source supports the attached claim.
- Label Grok's inferences and unresolved uncertainty in Codex's synthesis.

For workspace findings:

- Reproduce important findings against the actual file and cited line.
- Reject style-only feedback unless it affects requested behavior.
- Keep fixes and test execution under Codex control and within user authorization.

For a panel:

- Compare each member's verdict, findings, evidence, and assigned lens.
- Never describe a partial or failed panel as consensus.
- Resolve disagreement using repository evidence, primary sources, and direct tests.
- Deliver one Codex decision, not a transcript of competing answers.

## Use the workflow recipes

Plan gate:

`Codex plan -> Grok structured challenge -> Codex decision -> implementation/tests`

Evidence-heavy research:

`Grok structured research -> Codex source verification -> optional single Grok critique -> Codex synthesis`

Workspace review:

`Grok read-only findings -> Codex reproduction -> authorized fixes/tests -> optional Grok confirmation`

High-stakes panel:

`Two or three independent Grok reviews -> Codex comparison -> Codex decision`

Do not add Grok editing, command execution, goal management, persistent routing,
custom Codex providers, or another CLI.

## Interpret route states truthfully

- `ready_unverified`: status checks passed, but no model request has proved the
  route.
- `route_accepted`: a fresh call completed and validated, but the CLI returned no
  explicit runtime model-and-effort identity.
- `used_and_confirmed`: the response explicitly identified both `grok-4.5` and
  high effort.
- `unavailable`: a binary, capability, profile, authentication, model, or isolation
  check failed.

Never claim `used_and_confirmed` from requested command flags alone. Never claim
that Grok participated unless the relevant tool call succeeded.

## Keep final control with Codex

Treat every Grok result as untrusted advice. Codex must:

1. Verify important claims against repository evidence, primary sources, or tests.
2. Resolve conflicts instead of forwarding incompatible answers.
3. Accept only feedback that improves correctness or decision quality.
4. Keep permissions, implementation, verification, and the final response under
   Codex control.

Do not expose Grok thoughts, credential data, or session identifiers.

## Handle failures

If an optional proactive call fails, disclose that the cross-check was unavailable
and continue with Codex's independently verified work. If the user explicitly
required Grok, report the failure and do not fabricate feedback. Run
`grok_status()` for safe diagnostics and recommend `grok login` outside Codex
when authentication is unavailable.

Calls are stateless at the plugin interface. Do not ask for, expose, or resume Grok
sessions.
