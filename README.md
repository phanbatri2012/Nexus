# Nexus Studio Engine

Nexus Studio Engine is the canonical name and repository layout for the video production system. The short product name is **Nexus**.

## Layout

- `src/nexus_engine`: canonical Python package.
- `src/auto_yt`: one-release compatibility alias for legacy imports.
- `frontend`: React/Vite interface.
- `scripts`: maintenance and migration tooling.
- `data`: protected runtime data (not committed).

## Lifecycle

Use the root entrypoints:

- `run_nexus.bat`: start Backend, Frontend, and OmniVoice.
- `run_nexus_stop.bat`: safely stop project-owned processes.
- `run_nexus_restart.bat`: restart only after the maintenance guard confirms the system is idle.

ChatGPT and Google Flow remain on-demand and must not be opened by startup.

Legacy `run_autoyt*.bat`, `*_autoyt.ps1`, `auto_yt.*`, and `AUTO_YT_*`/`AUTOYT_*` inputs are compatibility boundaries for one release. New code must use Nexus names.

## Development

```powershell
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
Set-Location frontend
npm test
npm run lint
npm run build
```

See `HUONG_DAN_SU_DUNG.md` for the user workflow and `QUY_TRINH_RESTART_NEXUS_STUDIO_AN_TOAN.txt` for safe restart rules.
