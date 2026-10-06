# Auto_YT Engineering Learnings

## Persistent channel browser rule

Browser profiles used for channel operations are persistent. This applies to GPM-Login and local Chromium browsers such as Cốc Cốc, Chrome, Edge, and Brave when Auto_YT scans channels, opens creator tools, posts comments, uploads media, or changes publication state.

- If the selected browser/profile is closed, start it once with CDP and keep it running after the operation.
- If it is already running with CDP, reconnect to the same process and create a new task-owned tab.
- Never focus, navigate, reuse, or close a tab that existed before the task.
- On successful completion, close only the task-owned tab. If it is the final tab, navigate it to `about:blank` so Chromium stays alive.
- Preserve a failed write-operation tab for diagnostics or manual reconciliation.
- Never call GPM stop, kill a browser process, or force-restart a profile as automatic CDP recovery.
- If a browser is running without CDP, fail closed for automated operations and tell the user to reopen it manually through Auto_YT when idle.
- If CDP disappears during a write operation, do not reopen and repeat the action. Use checkpoint/reconciliation to avoid duplicate comments, uploads, or posts.
- Serialize automated operations per profile. A user-requested Stop must return a busy response while the profile has an active or queued browser operation.
- Manual Open actions always create a new tab and leave it available to the user.
- Manual Stop remains explicit. For local browsers, target the selected profile process instead of killing every process with the same executable name.

ChatGPT Browser Service and Google Flow Browser Service are service-owned browsers and retain their dedicated lifecycle managed by the unified Auto_YT start/stop/restart scripts. The persistent channel-browser rule must not weaken their shutdown cleanup.

### Required regression checks

- The browser PID stays unchanged across consecutive operations on the same profile.
- A closed profile receives at most one start request.
- Ordinary operation paths never call browser stop, process kill, `browser.close()`, or `context.close()`.
- Every operation creates a new tab and leaves pre-existing tab URLs unchanged.
- Restarting the backend reconnects to a running profile instead of restarting it.
- GPM proxy and fingerprint isolation remain bound to the expected channel profile.
