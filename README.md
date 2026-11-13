# Local Clipper

An original, local-first application for turning recordings into 9:16 clips.
Application code is written from scratch. Existing open-source clippers informed
the design; none of their application code is copied or vendored.

**Development status:** the source implementation is present, but this is not yet a
production-ready release. API startup, real rendering, model inference, YouTube
import, and 8 GB operation still require validation. Dependency acquisition on the
development machine was stopped after access failures; no administrator restriction
will be bypassed.

## What belongs in the repository

- `requirements.txt`: all Python runtime packages; pip resolves their dependencies.
- `requirements-dev.txt`: runtime packages plus pytest, HTTPX, and Ruff.
- `Start.ps1`: project-local setup, offline diagnostics, and launch.
- `clipper\`: API, persistence, worker, media adapters, and browser UI.
- `tests\`: offline core tests, API tests, and optional real-media tests.
- `architecture.html`: offline target-design explorer, not a feature-completion claim.

Virtual environments, model weights, media, generated outputs, and downloaded tools
are excluded from Git. No custom `.exe` installer or offline binary bundle is
required for this repository workflow.

## Prerequisites

The first supported target is **Windows x64 with Python 3.11**.

| Dependency | Needed for | Accepted local location |
|---|---|---|
| Python 3.11 | Application and project environment | Approved Python installation |
| Python packages | API, speech/vision adapters, YouTube import | Project `.venv`, from `requirements.txt` |
| FFmpeg and FFprobe | Inspection, audio extraction, previews, exports | `.tools\ffmpeg\bin\`, or existing `PATH` |
| Node.js compatible with the installed yt-dlp | YouTube JavaScript challenges | `.tools\node\node.exe`, or existing `PATH` |
| CTranslate2-format multilingual Whisper `small` | English transcription / translation | `.models\whisper-small\` |
| YuNet ONNX weights | Optional podcast face following | `.models\yunet.onnx` |
| Whisper `medium` | Optional higher-quality speech setting | `.models\whisper-medium\` |
| Ollama and a local Qwen3 model | Optional reranking; disabled by default | Existing local service, not required |

Whisper directories need the complete converted model, including `model.bin`,
`config.json`, `tokenizer.json`, and the vocabulary files supplied with that model.
Do not substitute an English-only `.en` model for multilingual transcription.
FFmpeg must support `libx264`, AAC, and the `ass` subtitle filter.

**FFmpeg executables and Whisper/YuNet weights are not installed by
`pip install -r requirements.txt`.** Obtain them separately from administrator-approved
sources and put them in the locations above. Python speech dependencies can include
packaged VAD assets. The application never installs tools or pulls models implicitly.

## Clone, install, run

Run commands from the repository root. Do not change script-execution, certificate,
proxy, or administrator policies to make setup work.

Check local prerequisites without any network requests:

```powershell
.\Start.ps1 -Check
```

First-time setup using **permitted normal network access**:

```powershell
.\Start.ps1 -Setup -AllowNetwork
```

The launcher displays the dependency scope and asks before installing. It creates
an isolated `.venv`, installs the requirements, then launches the application.
It does not acquire FFmpeg, models, or videos. Missing components are shown in
the UI and block only the processing steps that need them.

For an administrator-provided local wheelhouse, with **no package-network access**:

```powershell
.\Start.ps1 -Setup -Wheelhouse 'D:\ApprovedWheels'
```

Use this only for an explicitly approved local source, not as a workaround for
blocked content. Failed acquisition never switches to a mirror or disables TLS.

Subsequent launches:

```powershell
.\Start.ps1
```

Open `http://127.0.0.1:8765` in a browser. Use `-Port 8766` if the default port is busy.
The server binds only to loopback. A second instance cannot use the same data directory.

The equivalent conventional setup, when package acquisition is permitted:

```powershell
py -3.11 -m venv .venv
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```powershell
.\.venv\Scripts\python.exe -m clipper
```

## Workflow

1. Create a project using an absolute local video path or a single YouTube video URL.
2. Inspect the source. URL inspection fetches metadata, not the video.
3. Configure clip preferences and layout. Gaming requires a confirmed facecam rectangle.
4. Analyze, or add clip ranges and English captions manually.
5. Render a preview, edit, and export; automatic export is an explicit project setting.

Each YouTube download needs a separate per-job approval. Ongoing live streams,
playlists, cookie extraction, authenticated videos, and automatic publishing are
not part of this implementation. Do not use the app to bypass access restrictions.

The selection policy estimates interest using audio variation, low-resolution
visual changes, speech boundaries, and available public replay information.
It does not recognize every game's events or promise viral / most-watched clips.
Auto selection has a safety ceiling of 100 clips per analysis; individual output
ranges are limited to 10 minutes. No maximum input duration or RAM guarantee has
been established.

Gaming composition retains the full selected gameplay region with Fit, or crops
edges with Fill, and enlarges the confirmed facecam above it. A facecam already
baked into the gameplay image remains there. Podcast face following is not
active-speaker identification; a two-person layout and region-drawing UI remain
future work. Facecam coordinates can currently be entered numerically.

English-source word timestamps can drive highlighting. Translated English captions
are phrase-timed; their text is not claimed to align word-for-word to Hindi audio.
Original source audio is retained. Translation, framing, and editorial quality
require real-footage evaluation.

## Engineering boundaries

The API validates requests and updates durable state; it does not run inference.
SQLite uses transactions, WAL, a versioned schema, and optimistic project revisions.
Only one job per project can be active, and one heavy worker runs at a time.
There is no claim of distributed or multi-user scalability.

Workers stream media features, checkpoint bounded transcription chunks, and publish
renders only after inspecting their dimensions and duration. Interrupted work is
distinct from success. Retry the action to reuse valid completed work.
Cancellation targets only application-owned processes. Preview and export share
the same layout calculations.

Data defaults to `.data`; model and tool directories default to `.models` and
`.tools`. Override with `CLIPPER_DATA_DIR`, `CLIPPER_MODELS_DIR`, and
`CLIPPER_TOOLS_DIR`. These paths are resolved at runtime, never tied to a developer's
home directory. Back up `.data` with the application stopped, including its database
and project artifacts. Original videos are read-only and are not copied automatically.

Local APIs reject foreign browser origins and require a per-process token for
mutations. There is no cloud inference fallback, external UI CDN, telemetry,
GitHub account integration, or automatic remote publishing.

## Development and release discipline

Development dependencies are installed separately, with the same approval rules:

```powershell
.\Start.ps1 -Setup -Dev -AllowNetwork
```

Run the local quality checks:

```powershell
.\.venv\Scripts\python.exe -m ruff check clipper tools tests
```

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

```powershell
node --check .\clipper\web\app.js
```

Tests never download dependencies or fixtures. API and real-media tests explicitly
skip when their prerequisites are unavailable; skipped tests do not count as
validated capabilities.

The current requirements specify compatibility ranges, not a validated release lock.
After obtaining an approved complete wheelhouse, a maintainer can resolve and hash-lock
the exact Windows/Python 3.11 dependency set without contacting a package server:

```powershell
.\.venv\Scripts\python.exe -m tools.lock_dependencies --wheelhouse 'D:\ApprovedWheels'
```

Review and commit the generated `requirements-windows-py311.lock` only after a clean
installation, API/media regression checks, and dependency-license review. The launcher
automatically uses an available lock with hash verification. The lock generator
does not establish that the package contents are trusted; its input must already be
approved. Do not claim a production release until clean-clone setup, cancellation,
restart recovery, English/Hindi footage, and the target memory profile have been
exercised with the actual dependencies.
