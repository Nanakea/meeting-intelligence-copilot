# Windows Installer and Signing Plan

> **Historical API v2 evidence:** the artifact hashes, installer results, and frozen commits below
> predate the current API v8 transport contract. They must not be used to approve or package the
> current pilot. Use `docs/PACKAGED_ACCEPTANCE_CHECKLIST.md` for the API v8 gate.
>
Status: **unsigned Windows NSIS and MSI candidates build locally with the real Meetily, FFmpeg,
`llama-helper`, and Meeting Intelligence backend payloads. Automated NSIS install/upgrade/rollback/
re-upgrade/uninstall and MSI payload-extraction checks pass. They are not signed or approved for
end-user distribution; installed GUI/audio acceptance still requires an independent human run.**

This record covers the local-first V1 Windows path only. It does not add cloud AI, Ollama, RAG,
credentials, certificates, or committed generated binaries.

## Frozen inputs

- Product repository: `$env:ASSISTANT_ROOT`; packaged backend source state at
  product commit `4d7afad3c0bd902eb1bdfba9b96173089c522d4b`.
- Clean Meetily distribution worktree: `$env:MEETILY_ROOT`, branch
  `spike/distribution-readiness`, commit
  `58f26bbbf8827d3bba427b10749ebe25ec9a6d4e`.
- Unsigned build flavor commit: `a81c39d` (`feat: add unsigned Windows installer build`).
- User-initiated updater hardening commit: `b3b6b9c` (`fix: keep update checks user initiated`).
- Distribution hardening commit: `58f26bb` (`fix: harden Windows distribution lifecycle`).
- Backend sidecar: 15,540,634 bytes, SHA-256
  `FA8C7E4BE262CEF897DD747CB28E1C84D5B2F7BB8F2A234286CDB0D3C36E0C48`.
- Patch backups:
  - `<legacy-patch-backup>\meetily-unsigned-installer-a81c39d.patch`
    (`D66F61E2FD72981BACB3EF311C61A5B39703D95548AF03F0A2A6CDF7505A1FA4`)
  - `<legacy-patch-backup>\meetily-user-initiated-updater-b3b6b9c.patch`
    (`B68ED16B4532CF7532A5900820134D1C1E703CB694B86F37291E144D93B58BA2`)

The damaged `<legacy-meetily-worktree>` tree was not inspected or used.

## Build design

The normal `frontend/src-tauri/tauri.conf.json` remains the signed-release configuration. It keeps:

- Windows `signCommand` routed through `frontend/src-tauri/scripts/sign-windows.ps1`;
- `createUpdaterArtifacts: true` for release/update publishing; and
- the checked-in updater public key and HTTPS release endpoint.

The separate `tauri.unsigned.conf.json` changes only `createUpdaterArtifacts` to `false`. The
reviewed wrapper invokes Tauri with `--no-sign`, verifies that all three real external binaries are
present and non-empty, and rejects missing installers, unexpected Authenticode state, or newly
created updater `.sig` files. This does not weaken the signed-release default.

From the clean Meetily worktree:

```powershell
cd $env:MEETILY_ROOT
$env:RUSTUP_TOOLCHAIN = "stable"
$env:LIBCLANG_PATH = "<path-to-LLVM-bin>"
$env:CMAKE_GENERATOR = "Visual Studio 17 2022"
powershell -ExecutionPolicy Bypass -File `
  frontend\src-tauri\scripts\build-unsigned-windows.ps1 -Bundle both
```

Use `-Bundle nsis` or `-Bundle msi` to build one format. The wrapper requires Windows and `pnpm`.
The generated `target/` tree and sidecar inputs under `frontend/src-tauri/binaries/` are ignored;
none is committed.

## Measured unsigned artifacts

The artifacts below were rebuilt after automatic startup update checks were disabled. No
`DIGICERT_KEYPAIR_ALIAS`, `TAURI_SIGNING_PRIVATE_KEY`, or
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD` was present.

| Format | Ignored path | Bytes | SHA-256 | Authenticode | Defender |
|---|---|---:|---|---|---|
| NSIS | `$env:MEETILY_ROOT\target\release\bundle\nsis\meetily_0.4.0_x64-setup.exe` | 58,741,887 | `1A9356623521803DB7F1C22A1039B44EF447B9B1FD9307E7B3957744C21D2C59` | `NotSigned` | zero detections |
| MSI | `$env:MEETILY_ROOT\target\release\bundle\msi\meetily_0.4.0_x64_en-US.msi` | 85,483,520 | `2B2CD35FA5102CBD29DC2FA4913B8259C55C16513247E461C0E85EFD760230B7` | `NotSigned` | zero detections |

Defender was enabled with real-time protection on and signature version `1.455.125.0`. A clean scan
is point-in-time evidence, not a guarantee that another endpoint policy will allow an unsigned file.
The NSIS candidate passed automated first install, same-format upgrade from the archived original
candidate, rollback, re-upgrade, and uninstall. Installed payload hashes and application-data-root
preservation were checked. MSI administrative extraction found 12 expected payload files and an
exact backend-sidecar hash. Interactive app launch and GUI/audio acceptance remain human work.

## Tauri bundling and sidecar placement

Tauri 2 emits Windows installers as either an NSIS setup executable or a WiX MSI. The checked-in
application version is `0.4.0` in `tauri.conf.json`, `frontend/package.json`, and
`frontend/src-tauri/Cargo.toml`; the Tauri configuration is the release version authority.

`externalBin` contains:

```json
[
  "binaries/llama-helper",
  "binaries/ffmpeg",
  "binaries/meeting-intelligence-backend"
]
```

For Windows, Tauri requires target-suffixed build inputs and installs stable runtime names beside
`meetily.exe`:

- `ffmpeg.exe`
- `llama-helper.exe`
- `meeting-intelligence-backend.exe`

The generated NSIS and WiX manifests both contain all three. The backend resolver searches beside
the packaged application, so no absolute development path is needed at runtime. A missing real
`llama-helper` blocks Tauri configuration/build validation even though that helper belongs to the
separate summary path; run `scripts\setup-meetily-sidecars.ps1` and never substitute a fake binary.

## Install, upgrade, and uninstall behavior

- NSIS defaults to `currentUser`, installs under `%LOCALAPPDATA%\meetily`, writes current-user
  uninstall metadata, checks for prior NSIS/WiX installs, and removes the three sidecars during
  uninstall.
- The generated MSI is `perMachine`, targets the 64-bit Program Files directory, uses a stable
  upgrade code, and performs a WiX major upgrade. It therefore normally needs elevation. The
  generated manifest currently permits downgrades.
- NSIS and MSI differ in privilege and downgrade behavior. Do not alternate formats for a public
  release until cross-format upgrade/rollback has been exercised on a clean account.
- Automated NSIS uninstall removed the installation directory and preserved pre-existing Roaming/
  Local `com.meetily.ai` data-root existence. A human must still decide and verify the final retention
  policy for actual meeting data and models against the signed build.

## Signing boundary

There are two separate signatures:

1. **Windows Authenticode** signs application executables, sidecars, and the outer installer. The
   current repository uses a DigiCert `smctl` custom sign command when
   `DIGICERT_KEYPAIR_ALIAS` is supplied. No certificate or signing credential is available in this
   workspace.
2. **Tauri updater signatures** authenticate update artifacts with the updater private key. Tauri
   requires these signatures; verification cannot be disabled. Release builds with
   `createUpdaterArtifacts: true` require `TAURI_SIGNING_PRIVATE_KEY` and optionally
   `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.

Private keys, certificate files, service credentials, aliases treated as sensitive by policy, and
passwords must live in the CI secret store or signing service. They must never be committed, placed
in `.env`, printed, or passed through artifact metadata. A later CI job should:

1. build from a clean tagged commit with pinned/frozen dependencies;
2. obtain the real sidecars from a controlled build/artifact stage and verify recorded SHA-256s;
3. sign every executable and installer through the configured service;
4. generate updater artifacts only in the protected release job;
5. verify Authenticode, updater signatures, hashes, and version metadata after signing;
6. scan the final signed payload; and
7. publish only after clean-account install/upgrade/uninstall acceptance.

Azure Artifact Signing or another managed CA-backed service is a possible future replacement for
the existing DigiCert command, but choosing or provisioning one requires owner credentials and is
outside this local milestone.

## Unsigned warning expectations

These candidates are not suitable for general download. Windows Defender SmartScreen can display
“Windows protected your PC,” enterprise policy can block the files, and Smart App Control can block
unknown unsigned code. A self-signed certificate does not solve public trust. Even a newly
CA-signed file can receive a reputation warning until that exact file hash accumulates reputation;
signing establishes publisher identity and integrity but is not a promise of zero prompts.

Official references:

- [Tauri Windows installers](https://v2.tauri.app/distribute/windows-installer/)
- [Tauri Windows code signing](https://v2.tauri.app/distribute/sign/windows/)
- [Tauri updater signing](https://v2.tauri.app/plugin/updater/)
- [Microsoft SmartScreen reputation](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
- [Microsoft Windows signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)

## Updater and local-first behavior

The updater endpoint remains configured for a future signed release, but commit `b3b6b9c` disables
the automatic check on application mount. Remote release metadata is requested only after the user
explicitly chooses the update action. The update flow does not own MeetingState and must never send
audio, transcripts, facts, questions, capability tokens, or local model data.

Updater installation has not been tested for this candidate. Do not publish update metadata until
the exact signed installer, version transition, signature, rollback plan, and failure behavior have
passed acceptance.

## Manual acceptance still required

1. Transfer the recorded candidate by a trusted local mechanism and verify SHA-256 before launch.
2. Observe SmartScreen/UAC behavior; do not disable endpoint security.
3. Test interactive install and launch on an independent clean Windows account.
4. Confirm the app, all three sidecars, local models, and backend lifecycle work without system
   Python or cloud/Ollama.
5. Run the packaged JA/EN GUI and routed-audio checklist.
6. Force backend crash, port collision, missing sidecar, and incompatible-backend paths.
7. Repeat the automated same-format upgrade, rollback/downgrade, and uninstall evidence on the clean
   account while checking process/listener cleanup and the chosen user-data retention behavior.
8. Repeat the artifact hash, Authenticode, Defender, and SmartScreen review after real signing.

Until those steps pass, the correct verdict remains: **unsigned local distribution candidate ready
for human install/signing review; end-user distribution not ready**.
