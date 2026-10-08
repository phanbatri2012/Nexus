# AGENTS.md

## 1. Scope and precedence

- This file is the canonical policy for the `Tool-auto-login-GPT` Git repository.
- When project rules conflict, prioritize security and data integrity, then Auto-YT invariants, then workflow preferences.
- If a required action is unsafe, impossible, or needs authority outside the task, stop only that action, explain the constraint, and continue any safe in-scope work.

## 2. Communication

- Communicate with the user concisely in Vietnamese. Use English for identifiers, code comments, and technical artifacts unless localization is part of the task.
- Before a related group of tool calls, provide one concise Vietnamese progress update. Update again only when the approach changes, work runs long, or a material result is found.
- State assumptions only when they materially affect the result. Ask for clarification only when different answers would change the implementation or risk.
- For multi-step work, give a brief plan and proceed unless approval is required for a destructive, external, or materially ambiguous action.
- Label suggestions outside the requested scope as optional; do not mix them into the main deliverable.

## 3. Change discipline and verification

- Make the smallest change that fully solves the request. Do not add speculative features, unrelated refactors, or adjacent formatting changes.
- Prefer simple code and existing project patterns. Avoid premature abstractions; extract constants or configuration only for repeated or domain-significant values.
- Remove only imports, variables, functions, and files made obsolete by the current change. Mention unrelated dead code without deleting it.
- Validate external inputs early. Never silently swallow exceptions; log enough context to diagnose the failure without exposing secrets.
- Fix root causes rather than adding video-specific or conversation-specific workarounds.
- Define verifiable success criteria. Add or update regression tests for bugs and behavior changes when practical, then run checks proportional to the risk.
- Report any verification that could not be completed and why.
- Finish the agreed plan before proposing optional upgrades.

## 4. Auto-YT invariants

- Use Playwright with the authenticated browser session for all ChatGPT prompting and data retrieval. Do not replace this workflow with direct ChatGPT API calls.
- Implement behavior generically for every video. A video ID, URL, title, conversation, prompt version, or stored record may be used to reproduce a problem but never to special-case the solution.
- Starting Auto_YT must not directly launch comment jobs, scanners, channel work, or GPM profiles. Such work may start only after readiness or through an explicit user action.

## 5. Unified service lifecycle

- `start_autoyt.ps1` and `stop_autoyt.ps1` are the canonical PowerShell lifecycle implementations.
- `run_autoyt.bat`, `run_autoyt_stop.bat`, and `run_autoyt_restart.bat` are the canonical Windows wrappers. Outer workspace wrappers delegate to them.
- When adding a service, worker, external tool, or daemon, update the canonical start, stop, and restart flow in the same change.
- Startup must probe every registered service and report ready only after all required services are healthy.
- Shutdown must release registered ports, terminate only project-owned processes and Chromium profiles, and remove relevant stale locks and state markers. Never terminate the user's personal browser.
- No pipeline service may run as an unmonitored orphan outside the unified lifecycle.

## 6. Safe restart policy

- Restart only after runtime-affecting changes, when required for verification, or when explicitly requested. Documentation-only and instruction-only changes do not require a restart.
- Before restarting, check `system_jobs`, background workers, Playwright sessions, and GPM profile activity for active or immediately claimable generation, TTS, rendering, browser, or background work.
- Treat `processing`, `running`, `queued`, due `retry_wait`, and any work immediately claimable by a worker as busy.
- Treat an inability to verify idle state as busy.
- If the system is idle, run `run_autoyt_restart.bat` and verify that every registered service becomes ready.
- If the system is busy:
  1. Abort the restart immediately. Do not wait for or interrupt active jobs.
  2. Continue all remaining work and verification that does not require a restart.
  3. Record which runtime verification could not be completed.
  4. At final handoff, report that restart was skipped and instruct the user to run `run_autoyt_restart.bat` manually after the system becomes idle.

## 7. GPM and channel network isolation

- Use GPM-Login v3 endpoints first. Fall back to v1 only after confirming that the required v3 route is unavailable; an unmapped v3 route may return plain text `b"GPM-Login"`.
- Every managed YouTube channel requires a dedicated GPM profile with its own usable proxy and browser fingerprint.
- Browser operations, including YouTube Studio uploads and UI verification, must run inside the assigned GPM profile through Playwright CDP.
- All authenticated background HTTP operations for a channel, including token refresh, synchronization, resumable uploads, captions, and thumbnails, must use the assigned proxy through `src/auto_yt/services/proxy_utils.py`.
- Missing or unusable channel isolation must fail closed. Never fall back to the host machine's direct WAN connection.

## 8. Credential security

- Treat API keys, OAuth tokens, page tokens, app secrets, cookies, passwords, and proxy credentials as secrets.
- Persist application-managed secrets only through the centralized DPAPI-backed secret store. Never store them in plaintext databases, JSON, backups, caches, generated artifacts, frontend state, URLs, DOM attributes, or command-line arguments.
- Authenticated session data may persist only inside its dedicated GPM or Playwright browser profile. Never copy it into application storage, logs, or API responses.
- Credentials may enter through an authenticated backend endpoint but must never be returned. Return only non-sensitive status metadata and use `Cache-Control: no-store` for sensitive responses.
- Put bearer tokens in authorization headers and upstream-required secrets in request bodies. Never include secrets or recoverable fragments in URLs, redirects, logs, exceptions, or diagnostics.
- Pass external errors, tracebacks, job errors, and diagnostic output through centralized secret redaction before logging, persisting, or returning them.
- Validate credential identity, scopes, and actual expiry. Never silently select the first account or claim that a credential is permanent without authoritative validation.
- Credential-related changes require regression coverage for response filtering, plaintext persistence, redaction, cache control, and proxy fail-closed behavior.
- If exposure is discovered, stop further exposure, report affected locations, and add a regression test. Do not delete backups or revoke, rotate, or otherwise modify external credentials without explicit user authorization.

## 9. Tool availability

- Use project memory or browser-research tooling only when it is available and relevant. If a named tool is unavailable, use the safest supported alternative and report any resulting limitation.
