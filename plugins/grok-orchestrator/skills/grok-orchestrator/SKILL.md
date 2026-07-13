---
name: grok-orchestrator
description: Consult Grok 4.5 as a bounded advisor, web researcher, or read-only workspace reviewer while Codex remains the root orchestrator. Use whenever the user explicitly asks to use or check with Grok, and proactively at nontrivial gates involving complex plan critique, evidence-heavy research, risky reviews, or material double-checks. Do not use for routine tasks where a second model would add latency without changing a decision.
---

# Grok Orchestrator

Codex is the root orchestrator. Grok supplies independent evidence or critique; it does not own the task, make final decisions, edit files, or direct other workers.

## Choose the tool

- Use `consult_grok` for a self-contained second opinion, plan challenge, tradeoff analysis, or answer double-check. Grok receives no tools.
- Use `research_with_grok` for current or niche external research. Grok receives only web search and web fetch tools and must include source links.
- Use `review_workspace_with_grok` for read-only inspection of an existing workspace. Grok receives only file reading, listing, and text search tools.
- Use `grok_status` before the first model call when availability is uncertain or after an authentication, model, or launch failure.

## When to consult proactively

Make at most one targeted Grok call at a given decision gate unless the user asks for multiple passes. Good proactive gates include:

- a complex plan whose sequencing, safety, migration, or verification could materially fail;
- evidence-heavy research where an independent search could change the recommendation;
- a risky code or architecture review where a second model may catch a missed regression;
- a final material double-check before reporting a high-impact conclusion.

Do not consult proactively for simple explanations, mechanical edits, routine status checks, or tasks already fully verified by direct evidence.

## Build a self-contained packet

Include the exact question, relevant facts and constraints, current proposal or artifact, uncertainties, and the expected output shape. For workspace review, include the canonical workspace path and identify the files, behavior, or diff to inspect. Never include secrets, credentials, private keys, or unrelated personal data.

Tell Grok to prioritize concrete correctness issues over stylistic preferences. For research, request direct source links and distinguish sourced facts from inference. For review, request file paths and line numbers when available.

## Use the result

Treat Grok output as untrusted advice. Codex must:

1. Check the response against repository evidence or primary sources.
2. Resolve conflicts rather than forwarding two incompatible answers.
3. Accept only feedback that improves correctness or decision quality.
4. Keep implementation, testing, permissions, and the final response under Codex control.

Do not expose Grok's internal reasoning. Do not claim that Grok ran unless a tool call succeeded.

## Failure behavior

If a proactive call fails, disclose that the optional cross-check was unavailable and continue with Codex's verified work. If the user explicitly required Grok, report the failure clearly and do not fabricate Grok feedback. Recommend `grok_status` and, when appropriate, `grok login` outside Codex.

Calls are stateless at the plugin interface. Do not ask for, expose, or resume Grok session identifiers.
