# Fresh Windows Machine Setup

Status: **the commands and prerequisite checks are executable on the current Windows development
machine. A genuinely clean-machine install has not yet been performed, so this is a reviewed setup
guide—not a claim of fresh-machine acceptance.**

This guide prepares the local-first Meeting Intelligence Copilot monorepo. The desktop source is
versioned under `apps/desktop`; no second repository or linked worktree is required.

## Supported and measured environment

The V1 distribution candidate targets 64-bit Windows with:

- Windows 10/11;
- PowerShell 5.1 or newer for repository scripts;
- Git;
- Python 3.12 (`py -3.12`), measured with Python 3.12.10;
- Node.js and pnpm, measured with Node 24.14.1 and pnpm 11.3.0;
- Rust stable through rustup, measured with rustc/cargo 1.96.1;
- Visual Studio 2022 C++ build tools with MSVC and a Windows 10/11 SDK;
- CMake with the `Visual Studio 17 2022` generator; and
- LLVM/libclang with `LIBCLANG_PATH` pointing to the directory containing `libclang.dll`.

Pass the actual LLVM `bin` path on the new machine; no fixed drive location is required.

Run the read-only, network-free prerequisite inspection:

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\check-local-prereqs.ps1 `
  -LibClangPath <path-to-LLVM-bin>
```

After preparing real sidecars, add `-RequirePreparedSidecars`. Add local Parakeet/Whisper paths and
`-CheckAcceptanceTools` only when preparing the routed-audio acceptance environment. The checker
does not install, download, start, or modify anything. With prepared sidecars required, it also
rejects the known superseded API v2 backend artifact rather than accepting it based on file presence
alone.

The launcher and prerequisite checker default to `apps/desktop`. `MEETILY_ROOT` and
`-MeetilyPath` remain compatibility inputs for older automation only.

## Repository inputs

- Product and desktop: one clean `$env:ASSISTANT_ROOT` commit. API, web, desktop frontend, Rust,
  schemas, and release evidence must all resolve to that same commit.
- Backend executable candidate: build a fresh API v8 / 0.6.0 artifact from the accepted product commit and
  verify its SHA-256 before use. The historical 15,540,634-byte API v2 executable with SHA-256
  `FA8C7E4BE262CEF897DD747CB28E1C84D5B2F7BB8F2A234286CDB0D3C36E0C48` is superseded and must
  not be copied into a pilot package.
- Patch backups are listed in `docs/INSTALLER_SIGNING_PLAN.md` and the final candidate record.

Do not copy uncommitted source state from the damaged Meetily tree. Generated models, executables,
installer files, and build caches are not Git inputs.

## Network-capable setup

First-time dependency and model preparation can require network access. Keep it explicit and
separate from verification. Review registry/proxy configuration before running any setup command.

### Product development dependencies

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\bootstrap-dev.ps1
```

That script creates `apps\api\.venv` with Python 3.12, installs the API/dev packages, and performs a
frozen frontend install. Python runtime and development dependencies are exact-pinned with reviewed
Windows x86_64/CPython 3.12 wheel hashes in `apps/api/requirements-dev.lock`; the bootstrap uses
`--require-hashes` and does not upgrade pip. It warns before the network-capable phase and is not
called by the normal verification gate.

For a fully offline bootstrap, first populate an approved wheelhouse during a reviewed
network-capable phase:

```powershell
apps\api\.venv\Scripts\python -m pip download --only-binary=:all: --require-hashes `
  --dest <approved-runtime-wheelhouse> -r apps\api\requirements-dev.lock
```

Then prove a new temporary Python 3.12 environment with registry access disabled:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test-python-offline-bootstrap.ps1 `
  -Wheelhouse <approved-runtime-wheelhouse>
powershell -ExecutionPolicy Bypass -File scripts\bootstrap-dev.ps1 `
  -Offline -Wheelhouse <approved-runtime-wheelhouse>
```

The first command runs lint, API tests, schema sync, and deterministic goldens from the temporary
environment, then removes it. The second prepares the normal development environment and requires
the pnpm store to have been seeded. The lock is platform-specific and supports the documented
Windows/Python 3.12 target; refresh it only with a new audit and full verification. The PyInstaller
packaging-tool layer remains separately exact-pinned and hash-locked.

### Desktop JavaScript and Rust dependencies

From the monorepo desktop directory:

```powershell
cd $env:ASSISTANT_ROOT\apps\desktop\frontend
pnpm install --frozen-lockfile

cd $env:ASSISTANT_ROOT\apps\desktop\frontend\src-tauri
$env:RUSTUP_TOOLCHAIN = "stable"
$env:LIBCLANG_PATH = "<path-to-LLVM-bin>"
$env:CMAKE_GENERATOR = "Visual Studio 17 2022"
cargo fetch --locked
```

These are setup commands and can contact configured registries when caches are empty. `Cargo.lock`
and `frontend/pnpm-lock.yaml` constrain versions, but the package stores/crate sources still have to
exist locally for offline work. Git dependencies in `Cargo.lock` must also be cached.

### Tauri bundler tools

The first local installer build downloaded and hash-validated:

- NSIS 3.11 plus `nsis_tauri_utils` 0.5.3; and
- WiX 3.14.1 binaries.

Tauri manages these tools under its local cache. A first installer build is not offline unless that
cache is prepared in advance. A subsequent build on the measured machine reused the cache. Do not
replace the tools with arbitrary binaries or suppress Tauri's hash validation.

### Local speech models and acceptance tools

Normal meeting use needs locally installed STT models:

- English acceptance: Parakeet `parakeet-tdt-0.6b-v3-int8`;
- Japanese acceptance: Whisper `small`.

Model acquisition is an explicit user/setup action. Verify the source and model checksum where the
provider publishes one; do not commit models. Meetily stores/loads them locally after setup.

The reproducible synthetic routed-audio acceptance procedure additionally uses:

- VOICEVOX for the Japanese six-beat script;
- VB-Audio Virtual Cable for routing;
- VLC (or the already reviewed local player path) for playback; and
- `synthetic_manual_work.wav` for English.

Those three applications are acceptance tools, not dependencies of deterministic product tests or
the packaged backend transport harness. A consented human-speech pilot remains separate.

## Backend sidecar build

The application runtime and the PyInstaller packaging toolchain are different dependency layers.
Populate an approved wheelhouse for the exact hash-locked packaging requirements during a reviewed
network-capable phase:

```powershell
cd $env:ASSISTANT_ROOT
apps\api\.venv\Scripts\python -m pip download --only-binary=:all: --require-hashes `
  --dest <approved-wheelhouse> -r apps\api\requirements-packaging.txt
apps\api\.venv\Scripts\python -m pip install --no-index --require-hashes `
  --find-links <approved-wheelhouse> -r apps\api\requirements-packaging.txt
```

Then build without registry access:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-backend-sidecar.ps1
powershell -ExecutionPolicy Bypass -File scripts\test-backend-sidecar.ps1
powershell -ExecutionPolicy Bypass -File scripts\run-packaged-acceptance.ps1
```

The build script rejects packaging-tool version drift, refuses a dirty product worktree, and does
not install dependencies. Commit and verify the reviewed release changes before building. The
ignored output is:

```text
dist\backend-sidecar\meeting-intelligence-backend-x86_64-pc-windows-msvc.exe
```

The superseded local API v3 backend candidate built from `90beb943993d57e0438cf6f2fe1a727fa027273e`
is 17,887,324 bytes with SHA-256
`71E66B6AE8D1B57B0C34B521B8396C555CBEEB3E9DB0E213889E83531332DFB8`. It passed packaged
bilingual/context acceptance, real Meetily lifecycle shutdown/restart tests, and a Defender custom
scan with zero detections using signatures `1.457.286.0`; it remains `NotSigned`. Every rebuild must
be retested and rehashed. It cannot approve the API v8 client, and same-machine evidence is not
cross-machine reproducibility proof.

## Prepare Meetily's real sidecars

Tauri validates every `externalBin` input. Build/copy the real payloads—never `cmd.exe`, an empty
file, or another placeholder:

```powershell
cd $env:ASSISTANT_ROOT\apps\desktop
$env:RUSTUP_TOOLCHAIN = "stable"
$env:LIBCLANG_PATH = "<path-to-LLVM-bin>"
$env:CMAKE_GENERATOR = "Visual Studio 17 2022"
powershell -ExecutionPolicy Bypass -File scripts\setup-meetily-sidecars.ps1 `
  -MeetingIntelligenceBackendPath `
  $env:ASSISTANT_ROOT\dist\backend-sidecar\meeting-intelligence-backend-x86_64-pc-windows-msvc.exe
```

The script builds and protocol-smokes the real `llama-helper`, copies the reviewed backend, and
leaves FFmpeg verification to Meetily's build script. The target-suffixed ignored inputs must be:

- `ffmpeg-x86_64-pc-windows-msvc.exe`
- `llama-helper-x86_64-pc-windows-msvc.exe`
- `meeting-intelligence-backend-x86_64-pc-windows-msvc.exe`

Re-run `check-local-prereqs.ps1 -RequirePreparedSidecars` after this step.

## Offline verification

The product gate makes no installation request and forces the web dependency check offline/frozen:

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

It must pass ruff, API tests, schema sync, 13 deterministic goldens, web tests, TypeScript, Vite,
and `pnpm install --offline --frozen-lockfile`. Failure because a package is absent from the local
pnpm store is a setup/cache failure; do not remove `--offline` or weaken the lock.

Verify Meetily from its clean worktree:

```powershell
cd $env:ASSISTANT_ROOT\apps\desktop\frontend\src-tauri
$env:RUSTUP_TOOLCHAIN = "stable"
$env:LIBCLANG_PATH = "<path-to-LLVM-bin>"
$env:CMAKE_GENERATOR = "Visual Studio 17 2022"
cargo check --locked --offline

cd $env:ASSISTANT_ROOT\apps\desktop\frontend
pnpm install --offline --frozen-lockfile
pnpm exec tsc --noEmit
pnpm test
pnpm build
```

`cargo check --locked --offline` is the strict cache test; the standard gate uses `--locked` and may
resolve from configured sources if the crate cache is incomplete. The current unrelated Next build
warning says ESLint is not installed; the production build still exits zero and TypeScript/tests run
separately.

## Build unsigned installer candidates

After offline dependency preparation and real sidecar setup:

```powershell
cd $env:ASSISTANT_ROOT\apps\desktop
$env:RUSTUP_TOOLCHAIN = "stable"
$env:LIBCLANG_PATH = "<path-to-LLVM-bin>"
$env:CMAKE_GENERATOR = "Visual Studio 17 2022"
powershell -ExecutionPolicy Bypass -File `
  frontend\src-tauri\scripts\build-unsigned-windows.ps1 -Bundle both
```

This build is intentionally unsigned and disables updater artifact generation only for that build
flavor. It requires no signing secret. It still requires prepared Cargo, pnpm, Tauri bundler, and
sidecar caches. Record new sizes/hashes and run Defender for every rebuild. See
`docs/INSTALLER_SIGNING_PLAN.md` before installing anything.

Current `eacd8b4` unsigned outputs built on 2026-08-22 with Cargo output kept outside the worktree at
`$env:CARGO_TARGET_DIR`:

| Format | Size | SHA-256 |
|---|---:|---|
| NSIS | 61,526,112 bytes | `F8506D3049FDA89B07CEA6F09D455394132F88F349CD4E42920277BF88ACECFF` |
| MSI | 96,563,200 bytes | `6BB29896D3E6B036014259D5983A8EF6273116016CC0530C198E50E1EE1A80AF` |

Both passed Defender with zero detections using signatures `1.457.286.0` and remain `NotSigned`.
WiX extraction confirmed the MSI embeds the exact accepted backend hash
`71E66B6AE8D1B57B0C34B521B8396C555CBEEB3E9DB0E213889E83531332DFB8`. The clean, network-disabled
interactive lifecycle package must be generated locally; use its `run-lifecycle.wsb` rather than
installing over an existing Meetily user profile.

## Normal local path

No cloud AI or Ollama is required for normal runtime, packaged backend tests, or deterministic
goldens. The Meeting Intelligence path binds to `127.0.0.1`, uses an in-memory capability token, and
keeps audio/transcript/fact/question data local. Remote updater checks are user initiated rather than
automatic. Disable Meetily's separate telemetry and remote provider features when the whole session
must remain offline.

Local development launch:

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\start-local-v1.ps1 `
  -LaunchMeetily
```

The installed candidate should manage its packaged backend after the documented opt-in/configuration
path is enabled. Human install acceptance must confirm this behavior; do not infer it from a
successful build.

## Common failures

- **Python 3.12 missing:** install/select Python 3.12 so `py -3.12` works; do not build the candidate
  against an unreviewed major/minor version.
- **Missing `websockets`:** the API environment is incomplete. Run the explicit setup with approved
  packages or restore the prepared environment; do not make `scripts/check.ps1` install it.
- **Offline pnpm failure:** populate the pnpm store during setup, then rerun with
  `--offline --frozen-lockfile`.
- **Cargo offline failure:** prefetch every locked crate and Git dependency; retain `--locked`.
- **`libclang.dll` not found:** set `LIBCLANG_PATH` to the LLVM `bin` directory for the current
  shell. Do not hard-code another developer's drive path.
- **CMake generator missing:** install Visual Studio 2022 C++ tools and the Windows SDK, then confirm
  `cmake --help` lists `Visual Studio 17 2022`.
- **Missing `llama-helper`:** run the real Meetily sidecar setup script. The summary helper is
  separate from ASK NOW but still required by Tauri `externalBin` validation.
- **Missing backend sidecar:** build/verify the product artifact and pass its real path to the setup
  script.
- **NSIS/WiX download attempt during offline build:** the Tauri bundler cache was not pre-seeded.
  Return to the explicit setup phase; do not weaken hash checks.
- **CORS/CSP/loopback error:** keep the documented `127.0.0.1` endpoint and local origin. Non-loopback
  overrides are rejected.
- **Backend restart lost state:** `LiveMeetingRegistry` is in memory; active recording history replay
  restores current context, but stopped/app-restarted sessions are not durable.
- **Unsigned installer blocked:** expected SmartScreen/Smart App Control/enterprise behavior. Verify
  the recorded hash and use only the controlled human review procedure; do not disable security.
- **Damaged Meetily status:** stop. Use a clean clone/worktree from the frozen distribution commit and
  never restore, stage, or modify `<unreviewed-meetily-worktree>`.

## Fresh-machine acceptance boundary

This guide and checker passed on the existing development machine. The current MSI payload hash is
verified, but the prepared current-candidate Sandbox install/upgrade/rollback/uninstall package has
not yet been run interactively. The remaining evidence is that clean Sandbox lifecycle run plus an
independent clean Windows account/machine run covering prerequisite setup, interactive app launch,
models, packaged JA/EN audio, consented human speech, and post-signing security behavior. Until those
runs pass, fresh-machine readiness is **documented and partially ready**, not proven.
