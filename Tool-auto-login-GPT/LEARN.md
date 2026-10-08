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

### Browser readiness and durable job recovery

Treat browser process existence and automation readiness as separate states:

- `closed`: the exact assigned profile is not running; Auto_YT may start it once with CDP.
- `running_cdp_ready`: reconnect and open only a new task-owned tab.
- `running_without_cdp`: preserve the process, pause the job as `waiting_for_browser`, and require the user to close it manually when idle.
- `cdp_temporarily_unreachable`: preserve the process and use bounded transient retry; never assume the profile is closed.

Run browser readiness preflight before reserving a publication slot or selecting an upload file. Keep the system job, YouTube publish workflow, and video production state synchronized so a browser-readiness problem is never shown as a permanent upload failure.

A paused `waiting_for_browser` job may resume automatically only while its workflow is safe to replay: upload offset is zero, no YouTube Video ID is known, and no publication record exists. Legacy failures with the same CDP-blocked signature may be migrated into this state under the same safety guard. Once a remote write may have occurred, require checkpoint reconciliation instead of automatic replay.

Facebook browser crossposts follow the same durable-state rule. A missing or unreachable CDP at `CP3_CDP_READY` must keep both the queue item and its system job paused, and may auto-resume only when there is no Meta post ID or upload session/video ID. Checkpoints after CP3 require manual review because a remote write may already have occurred. Retry and resume actions must return the existing system job to the production queue; they must not launch a second worker thread for the same job.

ChatGPT Browser Service and Google Flow Browser Service are service-owned browsers and retain their dedicated lifecycle managed by the unified Auto_YT start/stop/restart scripts. The persistent channel-browser rule must not weaken their shutdown cleanup.

### Required regression checks

- The browser PID stays unchanged across consecutive operations on the same profile.
- A closed profile receives at most one start request.
- Ordinary operation paths never call browser stop, process kill, `browser.close()`, or `context.close()`.
- Every operation creates a new tab and leaves pre-existing tab URLs unchanged.
- Restarting the backend reconnects to a running profile instead of restarting it.
- GPM proxy and fingerprint isolation remain bound to the expected channel profile.
- A running profile without CDP leaves its PID unchanged and produces a paused, recoverable job instead of `failed_permanent`.
- Auto-resume never runs for a workflow with uploaded bytes, a YouTube Video ID, or an existing publication.
- A Facebook CDP wait is displayed as paused in Job Center, not as a failed upload, and retry has one production-queue owner.
