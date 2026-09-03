//! Privacy-safe controls used by the synthetic-tested internal pilot.

use cpal::traits::{DeviceTrait, HostTrait};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use sysinfo::{Disks, System};
use tauri::{AppHandle, Manager, Runtime, State};

use crate::audio::decoder::decode_audio_file;
use crate::audio::transcription::intelligence_dispatcher::{
    IntelligenceDispatcherDiagnostics, IntelligenceDispatcherState,
};
use crate::audio::transcription::local_only::local_copilot_url_or_default;
use crate::audio::transcription::{
    get_or_init_transcription_engine, validate_transcription_model_ready, TranscriptionEngine,
};
use crate::audio::{default_input_device, default_output_device};
use crate::database::repositories::meeting::MeetingsRepository;
use crate::meeting_intelligence_feedback::{
    IntelligenceFeedbackState, IntelligenceFeedbackSummaryRow,
};
use crate::meeting_intelligence_sidecar::{
    MeetingIntelligenceSidecarState, MeetingIntelligenceSidecarStatus,
};
use crate::state::AppState;
use crate::whisper_engine::WhisperModelReadiness;

const PILOT_SETTINGS_FILE: &str = "meeting-intelligence-pilot-settings.json";
const RECORDING_CONSENT_FILE: &str = "meeting-intelligence-recording-consent.json";
const PENDING_RECORDING_CONSENT_FILE: &str = "meeting-intelligence-recording-consent.pending.json";
const DIAGNOSTICS_DIRECTORY: &str = "meeting-intelligence-diagnostics";
const CAPABILITY_TOKEN_HEADER: &str = "X-Meeting-Intelligence-Token";
const SPOKEN_PREFLIGHT_TIMEOUT: Duration = Duration::from_secs(120);

#[cfg(target_os = "windows")]
const CHECK_PREFLIGHT_VOICE_SCRIPT: &str = r#"
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$culture = [System.Globalization.CultureInfo]::GetCultureInfo($env:MEETILY_PREFLIGHT_CULTURE)
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $voice = $synth.GetInstalledVoices($culture) |
        Where-Object { $_.Enabled } |
        Select-Object -First 1
    if ($null -eq $voice) { exit 3 }
} finally {
    $synth.Dispose()
}
"#;

#[cfg(target_os = "windows")]
const SYNTHESIZE_PREFLIGHT_SCRIPT: &str = r#"
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$culture = [System.Globalization.CultureInfo]::GetCultureInfo($env:MEETILY_PREFLIGHT_CULTURE)
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $voice = $synth.GetInstalledVoices($culture) |
        Where-Object { $_.Enabled } |
        Select-Object -First 1
    if ($null -eq $voice) { exit 3 }
    $synth.SelectVoice($voice.VoiceInfo.Name)
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
        16000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono
    )
    $synth.SetOutputToWaveFile($env:MEETILY_PREFLIGHT_WAV, $format)
    $synth.Speak($env:MEETILY_PREFLIGHT_PHRASE)
} finally {
    $synth.Dispose()
}
"#;

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum MeetingRetention {
    SevenDays,
    ThirtyDays,
    NinetyDays,
    Forever,
}

impl Default for MeetingRetention {
    fn default() -> Self {
        Self::Forever
    }
}

impl MeetingRetention {
    fn days(self) -> Option<i64> {
        match self {
            Self::SevenDays => Some(7),
            Self::ThirtyDays => Some(30),
            Self::NinetyDays => Some(90),
            Self::Forever => None,
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
#[serde(default)]
pub struct PilotSettings {
    pub retention: MeetingRetention,
    pub semantic_beta_enabled: bool,
    pub semantic_model: String,
}

impl Default for PilotSettings {
    fn default() -> Self {
        Self {
            retention: MeetingRetention::Forever,
            semantic_beta_enabled: false,
            semantic_model: "qwen3:8b".to_string(),
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct RecordingConsentConfirmation {
    pub confirmed: bool,
    pub confirmed_at_epoch_seconds: u64,
    pub app_version: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PilotPreflight {
    pub language: Option<String>,
    pub language_supported: bool,
    pub local_stt_ready: bool,
    pub local_stt_model: String,
    pub whisper_model_readiness: Option<WhisperModelReadiness>,
    pub available_memory_bytes: u64,
    pub local_speech_voice_ready: bool,
    pub microphone_configured: bool,
    pub microphone_available: bool,
    pub microphone_device_name: String,
    pub system_audio_configured: bool,
    pub system_audio_available: bool,
    pub system_audio_device_name: String,
    pub backend: MeetingIntelligenceSidecarStatus,
    pub available_disk_bytes: Option<u64>,
    pub pilot_data_available_disk_bytes: Option<u64>,
    pub recording_directory: String,
    pub local_data_directory: String,
    pub retention: MeetingRetention,
}

#[cfg(target_os = "windows")]
fn local_speech_voice_ready(language: Option<&str>) -> bool {
    let culture = match language {
        Some("ja") => "ja-JP",
        Some("en") => "en-US",
        Some("ko") => "ko-KR",
        _ => return false,
    };
    Command::new("powershell")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            CHECK_PREFLIGHT_VOICE_SCRIPT,
        ])
        .env("MEETILY_PREFLIGHT_CULTURE", culture)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

#[cfg(not(target_os = "windows"))]
fn local_speech_voice_ready(_language: Option<&str>) -> bool {
    false
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SpokenPreflightResult {
    pub session_id: String,
    pub received_sequence_id: u64,
    pub state_version: u64,
    pub transcript_character_count: usize,
    pub duration_ms: u64,
}

#[derive(Debug, Deserialize)]
struct SpokenPreflightAcknowledgement {
    status: String,
    received_sequence_id: Option<u64>,
    next_expected_sequence_id: u64,
    state_version: u64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct AggregateDiagnosticsExport {
    schema_version: u8,
    generated_at_epoch_seconds: u64,
    app_version: String,
    backend: MeetingIntelligenceSidecarStatus,
    transport: IntelligenceDispatcherDiagnostics,
    feedback: Vec<IntelligenceFeedbackSummaryRow>,
    connected_context: Option<ConnectedContextDiagnostics>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
struct ConnectedContextDiagnostics {
    search_requests: u64,
    citation_results: u64,
    unavailable_provider_results: u64,
    circuit_opened: u64,
    max_search_latency_ms: u64,
    local_index_documents: u64,
    citation_feedback_records: u64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MeetingDataDeletionResult {
    pub deleted_meetings: usize,
    pub file_cleanup_failures: usize,
    pub recovery_cleanup_failed: bool,
}

#[derive(Debug, Deserialize)]
struct RecoveryCleanupResponse {
    reset: bool,
}

#[derive(Debug, Deserialize)]
struct GovernanceCleanupResponse {
    deleted: bool,
}

fn settings_path<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    pilot_data_directory(app).map(|directory| directory.join(PILOT_SETTINGS_FILE))
}

fn recording_consent_path<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    pilot_data_directory(app).map(|directory| directory.join(RECORDING_CONSENT_FILE))
}

fn pending_recording_consent_path<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    pilot_data_directory(app).map(|directory| directory.join(PENDING_RECORDING_CONSENT_FILE))
}

fn pilot_data_directory<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map_err(|_| "Pilot data location is unavailable".to_string())
}

fn open_local_directory(path: &Path) -> Result<(), String> {
    fs::create_dir_all(path).map_err(|_| "Local data folder is unavailable".to_string())?;

    #[cfg(target_os = "windows")]
    let mut command = Command::new("explorer");
    #[cfg(target_os = "macos")]
    let mut command = Command::new("open");
    #[cfg(target_os = "linux")]
    let mut command = Command::new("xdg-open");

    command
        .arg(path)
        .spawn()
        .map_err(|_| "Local data folder could not be opened".to_string())?;
    Ok(())
}

fn read_settings(path: &Path) -> PilotSettings {
    fs::read(path)
        .ok()
        .and_then(|contents| serde_json::from_slice(&contents).ok())
        .unwrap_or_default()
}

pub fn load_pilot_settings<R: Runtime>(app: &AppHandle<R>) -> PilotSettings {
    settings_path(app)
        .map(|path| read_settings(&path))
        .unwrap_or_default()
}

fn write_atomic(path: &Path, contents: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "Pilot storage location is unavailable".to_string())?;
    fs::create_dir_all(parent).map_err(|_| "Pilot storage location is unavailable".to_string())?;
    let temporary = path.with_extension(format!("{}.tmp", uuid::Uuid::new_v4().simple()));
    fs::write(&temporary, contents).map_err(|_| "Pilot settings could not be saved".to_string())?;
    replace_file(&temporary, path).map_err(|_| {
        let _ = fs::remove_file(&temporary);
        "Pilot settings could not be saved".to_string()
    })
}

#[cfg(target_os = "windows")]
fn replace_file(source: &Path, destination: &Path) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source_wide: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination_wide: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    let replaced = unsafe {
        MoveFileExW(
            source_wide.as_ptr(),
            destination_wide.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if replaced == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(not(target_os = "windows"))]
fn replace_file(source: &Path, destination: &Path) -> std::io::Result<()> {
    fs::rename(source, destination)
}

fn available_space_for(path: &Path) -> Option<u64> {
    let canonical = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    Disks::new_with_refreshed_list()
        .iter()
        .filter(|disk| canonical.starts_with(disk.mount_point()))
        .max_by_key(|disk| disk.mount_point().as_os_str().len())
        .map(|disk| disk.available_space())
}

fn configured_device_base_name(device_name: &str) -> &str {
    device_name
        .strip_suffix(" (input)")
        .or_else(|| device_name.strip_suffix(" (output)"))
        .unwrap_or(device_name)
}

fn microphone_available(device_name: Option<&str>) -> bool {
    let host = cpal::default_host();
    let selected = device_name.filter(|value| !value.trim().is_empty());
    match selected {
        Some(name) => host.input_devices().ok().is_some_and(|devices| {
            let name = configured_device_base_name(name);
            devices
                .filter_map(|device| device.name().ok())
                .any(|candidate| candidate == name)
        }),
        None => host.default_input_device().is_some(),
    }
}

fn system_audio_available(device_name: Option<&str>) -> bool {
    let Some(name) = device_name.filter(|value| !value.trim().is_empty()) else {
        return default_output_device().is_ok();
    };
    let name = configured_device_base_name(name);
    let host = cpal::default_host();
    host.output_devices().ok().is_some_and(|devices| {
        devices
            .filter_map(|device| device.name().ok())
            .any(|candidate| candidate == name)
    })
}

pub(crate) fn valid_recording_session_id(session_id: &str) -> bool {
    let Some(suffix) = session_id.strip_prefix("meeting-intel-") else {
        return false;
    };
    suffix.len() == 32
        && suffix
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn spoken_preflight_phrase(language: &str) -> Option<(&'static str, &'static str)> {
    match language {
        "en" => Some((
            "en-US",
            "The warehouse side and ERP side inventory do not match.",
        )),
        "ja" => Some((
            "ja-JP",
            "\u{5009}\u{5eab}\u{5074}\u{3068}ERP\u{5074}\u{3067}\u{5728}\u{5eab}\u{304c}\u{5408}\u{3044}\u{307e}\u{305b}\u{3093}\u{3002}",
        )),
        "ko" => Some((
            "ko-KR",
            "WMS\u{c640} NetSuite\u{c758} \u{c7ac}\u{ace0} \u{b370}\u{c774}\u{d130}\u{ac00} \u{c77c}\u{ce58}\u{d558}\u{c9c0} \u{c54a}\u{c2b5}\u{b2c8}\u{b2e4}.",
        )),
        _ => None,
    }
}

#[cfg(target_os = "windows")]
async fn synthesize_spoken_preflight(
    wav_path: PathBuf,
    culture: &'static str,
    phrase: &'static str,
) -> Result<(), String> {
    tokio::task::spawn_blocking(move || {
        use std::os::windows::process::CommandExt;

        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let status = Command::new("powershell.exe")
            .args([
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                SYNTHESIZE_PREFLIGHT_SCRIPT,
            ])
            .env("MEETILY_PREFLIGHT_WAV", &wav_path)
            .env("MEETILY_PREFLIGHT_CULTURE", culture)
            .env("MEETILY_PREFLIGHT_PHRASE", phrase)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .status()
            .map_err(|_| "The local synthetic voice could not be started".to_string())?;
        if !status.success() || !wav_path.is_file() {
            return Err("A local voice for the selected language is unavailable".to_string());
        }
        Ok(())
    })
    .await
    .map_err(|_| "The local synthetic voice test was interrupted".to_string())?
}

#[cfg(not(target_os = "windows"))]
async fn synthesize_spoken_preflight(
    _wav_path: PathBuf,
    _culture: &'static str,
    _phrase: &'static str,
) -> Result<(), String> {
    Err("The spoken preflight is available in Windows pilot builds".to_string())
}

async fn transcribe_spoken_preflight<R: Runtime>(
    app: &AppHandle<R>,
    samples: Vec<f32>,
    language: &str,
) -> Result<String, String> {
    validate_transcription_model_ready(app)
        .await
        .map_err(|_| "The configured local speech model is not ready".to_string())?;
    let engine = get_or_init_transcription_engine(app)
        .await
        .map_err(|_| "The configured local speech model could not be loaded".to_string())?;
    let result = match engine {
        TranscriptionEngine::Whisper(engine) => engine
            .transcribe_audio_with_confidence(samples, Some(language.to_string()))
            .await
            .map(|(text, _, _)| text),
        TranscriptionEngine::Parakeet(engine) => engine.transcribe_audio(samples).await,
        TranscriptionEngine::Provider(provider) => provider
            .transcribe(samples, Some(language.to_string()))
            .await
            .map(|result| result.text)
            .map_err(anyhow::Error::new),
    }
    .map_err(|_| "The local speech model could not transcribe the synthetic voice".to_string())?;
    let text = result.trim().to_string();
    if text.is_empty() {
        return Err("The local speech model returned no text for the synthetic voice".to_string());
    }
    Ok(text)
}

async fn delete_meetings(
    app_state: &AppState,
    cutoff: Option<chrono::DateTime<chrono::Utc>>,
) -> Result<MeetingDataDeletionResult, String> {
    if crate::audio::recording_commands::is_recording().await {
        return Err("Stop recording before deleting local meeting data".to_string());
    }
    if crate::audio::import::is_import_in_progress() {
        return Err(
            "Wait for the active audio import before deleting local meeting data".to_string(),
        );
    }
    let meetings = MeetingsRepository::get_meetings(app_state.db_manager.pool())
        .await
        .map_err(|_| "Local meeting data could not be listed".to_string())?;
    let mut result = MeetingDataDeletionResult {
        deleted_meetings: 0,
        file_cleanup_failures: 0,
        recovery_cleanup_failed: false,
    };
    for meeting in meetings {
        if cutoff.is_some_and(|limit| meeting.created_at.0 >= limit) {
            continue;
        }
        crate::summary::service::SummaryService::cancel_summary(&meeting.id);
        if crate::meeting_local_data::delete_meeting_folder(meeting.folder_path.as_deref()).is_err()
        {
            result.file_cleanup_failures += 1;
            continue;
        }
        let deleted = MeetingsRepository::delete_meeting(app_state.db_manager.pool(), &meeting.id)
            .await
            .map_err(|_| "Local meeting data deletion was incomplete".to_string())?;
        result.deleted_meetings += usize::from(deleted);
    }
    Ok(result)
}

pub async fn apply_saved_meeting_intelligence_retention<R: Runtime>(
    app: AppHandle<R>,
) -> Result<MeetingDataDeletionResult, String> {
    let settings = read_settings(&settings_path(&app)?);
    let Some(days) = settings.retention.days() else {
        return Ok(MeetingDataDeletionResult {
            deleted_meetings: 0,
            file_cleanup_failures: 0,
            recovery_cleanup_failed: false,
        });
    };
    let _lifecycle_guard = crate::audio::common::acquire_engine_lifecycle_lock().await;
    let cutoff = chrono::Utc::now() - chrono::Duration::days(days);
    let app_state = app.state::<AppState>();
    delete_meetings(app_state.inner(), Some(cutoff)).await
}

#[tauri::command]
pub async fn delete_unsaved_recovery_folder(
    app_state: State<'_, AppState>,
    folder_path: String,
) -> Result<(), String> {
    let _lifecycle_guard = crate::audio::common::acquire_engine_lifecycle_lock().await;
    if crate::audio::recording_commands::is_recording().await
        || crate::audio::import::is_import_in_progress()
    {
        return Err("Stop active recording or import before deleting recovery data".to_string());
    }

    let requested = PathBuf::from(&folder_path);
    match requested.try_exists() {
        Ok(false) => return Ok(()),
        Ok(true) => {}
        Err(_) => return Err("The recovery recording folder could not be inspected".to_string()),
    }
    let requested_identity = requested
        .canonicalize()
        .map_err(|_| "The recovery recording folder could not be inspected".to_string())?;
    let meetings = MeetingsRepository::get_meetings(app_state.db_manager.pool())
        .await
        .map_err(|_| "Saved meetings could not be checked before recovery deletion".to_string())?;
    let already_saved = meetings.iter().any(|meeting| {
        meeting.folder_path.as_deref().is_some_and(|path| {
            Path::new(path)
                .canonicalize()
                .is_ok_and(|identity| identity == requested_identity)
        })
    });
    if already_saved {
        return Err("The recovery folder belongs to a saved meeting".to_string());
    }
    crate::meeting_local_data::delete_meeting_folder(Some(&folder_path)).map_err(str::to_string)
}

#[tauri::command]
pub fn get_meeting_intelligence_pilot_settings<R: Runtime>(
    app: AppHandle<R>,
) -> Result<PilotSettings, String> {
    Ok(load_pilot_settings(&app))
}

#[tauri::command]
pub fn set_meeting_intelligence_pilot_settings<R: Runtime>(
    app: AppHandle<R>,
    sidecar: State<'_, MeetingIntelligenceSidecarState>,
    settings: PilotSettings,
) -> Result<PilotSettings, String> {
    if settings.semantic_model != "qwen3:8b" {
        return Err("The semantic beta model profile is unsupported".to_string());
    }
    let encoded = serde_json::to_vec_pretty(&settings)
        .map_err(|_| "Pilot settings could not be saved".to_string())?;
    write_atomic(&settings_path(&app)?, &encoded)?;
    sidecar.configure_semantic_beta(
        settings.semantic_beta_enabled,
        settings.semantic_model.clone(),
    );
    Ok(settings)
}

#[tauri::command]
pub fn confirm_meeting_recording_consent<R: Runtime>(
    app: AppHandle<R>,
    confirmed: bool,
) -> Result<RecordingConsentConfirmation, String> {
    if !confirmed {
        return Err("Participant notification must be confirmed before recording".to_string());
    }
    let confirmation = RecordingConsentConfirmation {
        confirmed,
        confirmed_at_epoch_seconds: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| "Recording confirmation time is unavailable".to_string())?
            .as_secs(),
        app_version: app.package_info().version.to_string(),
    };
    let encoded = serde_json::to_vec_pretty(&confirmation)
        .map_err(|_| "Recording confirmation could not be saved".to_string())?;
    write_atomic(&pending_recording_consent_path(&app)?, &encoded)
        .map_err(|_| "Recording confirmation could not be saved".to_string())?;
    Ok(confirmation)
}

fn valid_recording_consent(
    confirmation: &RecordingConsentConfirmation,
    app_version: &str,
    now_epoch_seconds: u64,
) -> bool {
    confirmation.confirmed
        && confirmation.app_version == app_version
        && now_epoch_seconds.saturating_sub(confirmation.confirmed_at_epoch_seconds) <= 15 * 60
        && confirmation.confirmed_at_epoch_seconds <= now_epoch_seconds
}

pub(crate) fn consume_recording_consent<R: Runtime>(app: &AppHandle<R>) -> Result<(), String> {
    let pending = pending_recording_consent_path(app)?;
    let confirmation = fs::read(&pending)
        .ok()
        .and_then(|encoded| serde_json::from_slice::<RecordingConsentConfirmation>(&encoded).ok())
        .ok_or_else(|| "Confirm that participants were informed before recording".to_string())?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "Recording confirmation time is unavailable".to_string())?
        .as_secs();
    if !valid_recording_consent(&confirmation, &app.package_info().version.to_string(), now) {
        let _ = fs::remove_file(&pending);
        return Err("Confirm that participants were informed before recording".to_string());
    }
    replace_file(&pending, &recording_consent_path(app)?)
        .map_err(|_| "Recording confirmation could not be consumed".to_string())
}

#[tauri::command]
pub fn get_meeting_intelligence_data_directory<R: Runtime>(
    app: AppHandle<R>,
) -> Result<String, String> {
    let directory = pilot_data_directory(&app)?;
    fs::create_dir_all(&directory).map_err(|_| "Pilot data location is unavailable".to_string())?;
    Ok(directory.to_string_lossy().into_owned())
}

#[tauri::command]
pub fn open_meeting_intelligence_data_directory<R: Runtime>(
    app: AppHandle<R>,
) -> Result<(), String> {
    open_local_directory(&pilot_data_directory(&app)?)
}

#[tauri::command]
pub async fn apply_meeting_intelligence_retention(
    app_state: State<'_, AppState>,
    settings: PilotSettings,
) -> Result<MeetingDataDeletionResult, String> {
    let _lifecycle_guard = crate::audio::common::acquire_engine_lifecycle_lock().await;
    let cutoff = settings
        .retention
        .days()
        .map(|days| chrono::Utc::now() - chrono::Duration::days(days));
    if cutoff.is_none() {
        return Ok(MeetingDataDeletionResult {
            deleted_meetings: 0,
            file_cleanup_failures: 0,
            recovery_cleanup_failed: false,
        });
    }
    delete_meetings(&app_state, cutoff).await
}

#[tauri::command]
pub async fn delete_all_local_meeting_data<R: Runtime>(
    app: AppHandle<R>,
    app_state: State<'_, AppState>,
    backend: State<'_, MeetingIntelligenceSidecarState>,
) -> Result<MeetingDataDeletionResult, String> {
    let _lifecycle_guard = crate::audio::common::acquire_engine_lifecycle_lock().await;
    crate::summary::service::SummaryService::cancel_all_summaries();
    let mut result = delete_meetings(&app_state, None).await?;
    MeetingsRepository::delete_orphaned_meeting_data(app_state.db_manager.pool())
        .await
        .map_err(|_| "Orphaned local meeting data could not be deleted".to_string())?;
    let retained_paths = MeetingsRepository::get_meetings(app_state.db_manager.pool())
        .await
        .map_err(|_| "Local meeting data cleanup could not be verified".to_string())?
        .into_iter()
        .filter_map(|meeting| meeting.folder_path.map(PathBuf::from))
        .collect::<Vec<_>>();
    match crate::audio::recording_preferences::load_recording_preferences(&app).await {
        Ok(preferences) => {
            match crate::meeting_local_data::delete_orphaned_meeting_folders(
                &preferences.save_folder,
                &retained_paths,
            ) {
                Ok((_, failures)) => result.file_cleanup_failures += failures,
                Err(_) => result.file_cleanup_failures += 1,
            }
        }
        Err(_) => result.file_cleanup_failures += 1,
    }
    result.recovery_cleanup_failed = !delete_all_live_recovery_state(&backend).await;
    for path in [
        recording_consent_path(&app)?,
        pending_recording_consent_path(&app)?,
    ] {
        if fs::remove_file(path).is_err_and(|error| error.kind() != std::io::ErrorKind::NotFound) {
            result.file_cleanup_failures += 1;
        }
    }
    Ok(result)
}

async fn delete_all_live_recovery_state(backend: &MeetingIntelligenceSidecarState) -> bool {
    let base_url =
        local_copilot_url_or_default(std::env::var("MEETING_COPILOT_URL").ok().as_deref());
    let client = match Client::builder().timeout(Duration::from_secs(3)).build() {
        Ok(client) => client,
        Err(_) => return false,
    };
    let token = backend.capability_token();
    let mut recovery_request = client.delete(format!("{base_url}/meetings"));
    if let Some(value) = token.as_deref() {
        recovery_request = recovery_request.header(CAPABILITY_TOKEN_HEADER, value);
    }
    let recovery_deleted = match recovery_request.send().await {
        Ok(response) if response.status().is_success() => response
            .json::<RecoveryCleanupResponse>()
            .await
            .is_ok_and(|body| body.reset),
        Ok(response) => {
            log::warn!(
                "Meeting Intelligence recovery cleanup rejected (status: {})",
                response.status()
            );
            false
        }
        Err(_) => false,
    };
    let mut governance_request = client.delete(format!("{base_url}/governance"));
    if let Some(value) = token.as_deref() {
        governance_request = governance_request.header(CAPABILITY_TOKEN_HEADER, value);
    }
    let governance_deleted = match governance_request.send().await {
        Ok(response) if response.status().is_success() => response
            .json::<GovernanceCleanupResponse>()
            .await
            .is_ok_and(|body| body.deleted),
        Ok(response) => {
            log::warn!(
                "Meeting Intelligence governance cleanup rejected (status: {})",
                response.status()
            );
            false
        }
        Err(_) => false,
    };
    recovery_deleted && governance_deleted
}

#[tauri::command]
pub async fn get_meeting_intelligence_preflight<R: Runtime>(
    app: AppHandle<R>,
    backend: State<'_, MeetingIntelligenceSidecarState>,
    language: Option<String>,
    microphone_device: Option<String>,
    system_audio_device: Option<String>,
) -> Result<PilotPreflight, String> {
    let local_data_directory = pilot_data_directory(&app)?;
    fs::create_dir_all(&local_data_directory)
        .map_err(|_| "Pilot data location is unavailable".to_string())?;
    let recording_directory = crate::audio::recording_preferences::load_recording_preferences(&app)
        .await
        .map(|preferences| preferences.save_folder)
        .unwrap_or_else(|_| crate::audio::recording_preferences::get_default_recordings_folder());
    let settings = read_settings(&settings_path(&app)?);
    let language_supported = matches!(language.as_deref(), Some("ja" | "en" | "ko"));
    let transcript_config =
        crate::api::api::api_get_transcript_config(app.clone(), app.clone().state(), None)
            .await
            .ok()
            .flatten()
            .unwrap_or(crate::api::api::TranscriptConfig {
                provider: "parakeet".to_string(),
                model: crate::config::DEFAULT_PARAKEET_MODEL.to_string(),
                api_key: None,
            });
    let provider_supports_language =
        match (transcript_config.provider.as_str(), language.as_deref()) {
            ("parakeet", Some("en")) => true,
            ("localWhisper", Some("ja" | "en" | "ko")) => true,
            _ => false,
        };
    let whisper_model_readiness = if transcript_config.provider == "localWhisper" {
        let _ = crate::whisper_engine::commands::whisper_init().await;
        Some(
            crate::whisper_engine::commands::whisper_verified_model_readiness(
                &transcript_config.model,
                language.as_deref(),
            )
            .await,
        )
    } else {
        None
    };
    let verified_whisper_ready = whisper_model_readiness
        .as_ref()
        .map_or(true, WhisperModelReadiness::ready);
    let local_stt_ready = language_supported
        && provider_supports_language
        && verified_whisper_ready
        && crate::audio::transcription::validate_transcription_model_ready(&app)
            .await
            .is_ok();
    let local_stt_model = format!(
        "{} / {}",
        transcript_config.provider, transcript_config.model
    );

    let configured_microphone = microphone_device
        .as_deref()
        .filter(|value| !value.trim().is_empty());
    let configured_system_audio = system_audio_device
        .as_deref()
        .filter(|value| !value.trim().is_empty());
    let microphone_device_name = configured_microphone
        .map(str::to_string)
        .or_else(|| default_input_device().ok().map(|device| device.to_string()))
        .unwrap_or_else(|| "default-input".to_string());
    let system_audio_device_name = configured_system_audio
        .map(str::to_string)
        .or_else(|| {
            default_output_device()
                .ok()
                .map(|device| device.to_string())
        })
        .unwrap_or_else(|| "default-output".to_string());
    let mut system = System::new();
    system.refresh_memory();
    let local_speech_voice_ready = local_speech_voice_ready(language.as_deref());
    Ok(PilotPreflight {
        language,
        language_supported,
        local_stt_ready,
        local_stt_model,
        whisper_model_readiness,
        available_memory_bytes: system.available_memory(),
        local_speech_voice_ready,
        microphone_configured: configured_microphone.is_some(),
        microphone_available: microphone_available(configured_microphone),
        microphone_device_name,
        system_audio_configured: configured_system_audio.is_some(),
        system_audio_available: system_audio_available(configured_system_audio),
        system_audio_device_name,
        backend: backend.status(),
        available_disk_bytes: available_space_for(&recording_directory),
        pilot_data_available_disk_bytes: available_space_for(&local_data_directory),
        recording_directory: recording_directory.to_string_lossy().into_owned(),
        local_data_directory: local_data_directory.to_string_lossy().into_owned(),
        retention: settings.retention,
    })
}

async fn run_spoken_preflight_inner<R: Runtime>(
    app: AppHandle<R>,
    backend: &MeetingIntelligenceSidecarState,
    session_id: String,
    language: String,
) -> Result<SpokenPreflightResult, String> {
    if crate::audio::recording_commands::is_recording().await {
        return Err("Stop recording before running the spoken pipeline test".to_string());
    }
    if !valid_recording_session_id(&session_id) {
        return Err("The spoken pipeline test session is invalid".to_string());
    }
    let (culture, phrase) = spoken_preflight_phrase(&language).ok_or_else(|| {
        "Select Japanese, English, or Korean for the spoken pipeline test".to_string()
    })?;
    let started = Instant::now();
    let temporary = tempfile::tempdir()
        .map_err(|_| "Temporary local audio storage is unavailable".to_string())?;
    let wav_path = temporary.path().join("spoken-preflight.wav");
    synthesize_spoken_preflight(wav_path.clone(), culture, phrase).await?;

    let samples = tokio::task::spawn_blocking(move || {
        decode_audio_file(&wav_path)
            .map(|audio| audio.to_whisper_format())
            .map_err(|_| "The synthetic voice audio could not be decoded".to_string())
    })
    .await
    .map_err(|_| "The synthetic voice audio test was interrupted".to_string())??;
    let audio_duration = samples.len() as f64 / 16_000.0;
    let text = transcribe_spoken_preflight(&app, samples, &language).await?;
    let transcript_character_count = text.chars().count();

    let base_url =
        local_copilot_url_or_default(std::env::var("MEETING_COPILOT_URL").ok().as_deref());
    let client = Client::builder()
        .connect_timeout(Duration::from_secs(3))
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|_| "The local intelligence transport could not be prepared".to_string())?;
    let payload = serde_json::json!({
        "adapter": "meetily",
        "lang": language,
        "payload": {
            "text": text,
            "timestamp": chrono::Utc::now().to_rfc3339(),
            "source": "Preflight",
            "sequence_id": 0,
            "chunk_start_time": 0.0,
            "is_partial": false,
            "confidence": null,
            "audio_start_time": 0.0,
            "audio_end_time": audio_duration,
            "duration": audio_duration
        }
    });
    let mut request = client
        .post(format!("{base_url}/ingest/live/{session_id}"))
        .json(&payload);
    if let Some(token) = backend.capability_token() {
        request = request.header(CAPABILITY_TOKEN_HEADER, token);
    }
    let response = request.send().await.map_err(|_| {
        "The synthetic transcript did not reach the local intelligence backend".to_string()
    })?;
    if !response.status().is_success() {
        log::warn!(
            "Spoken preflight ingest rejected (status: {}, session: {})",
            response.status(),
            session_id
        );
        return Err("The local intelligence backend rejected the spoken pipeline test".to_string());
    }
    let acknowledgement: SpokenPreflightAcknowledgement = response
        .json()
        .await
        .map_err(|_| "The local intelligence acknowledgement was invalid".to_string())?;
    if acknowledgement.status != "applied"
        || acknowledgement.received_sequence_id != Some(0)
        || acknowledgement.next_expected_sequence_id < 1
        || acknowledgement.state_version < 1
    {
        return Err(
            "The local intelligence backend did not apply the spoken pipeline test".to_string(),
        );
    }

    log::info!(
        "Spoken preflight applied (session: {}, characters: {}, duration_ms: {})",
        session_id,
        transcript_character_count,
        started.elapsed().as_millis()
    );
    Ok(SpokenPreflightResult {
        session_id,
        received_sequence_id: 0,
        state_version: acknowledgement.state_version,
        transcript_character_count,
        duration_ms: started.elapsed().as_millis().min(u64::MAX as u128) as u64,
    })
}

#[tauri::command]
pub async fn run_meeting_intelligence_spoken_preflight<R: Runtime>(
    app: AppHandle<R>,
    backend: State<'_, MeetingIntelligenceSidecarState>,
    session_id: String,
    language: String,
) -> Result<SpokenPreflightResult, String> {
    tokio::time::timeout(
        SPOKEN_PREFLIGHT_TIMEOUT,
        run_spoken_preflight_inner(app, &backend, session_id, language),
    )
    .await
    .map_err(|_| "The spoken pipeline test timed out".to_string())?
}

#[tauri::command]
pub async fn export_meeting_intelligence_diagnostics<R: Runtime>(
    app: AppHandle<R>,
    dispatcher: State<'_, IntelligenceDispatcherState>,
    feedback: State<'_, IntelligenceFeedbackState>,
    backend: State<'_, MeetingIntelligenceSidecarState>,
) -> Result<String, String> {
    let base_url =
        local_copilot_url_or_default(std::env::var("MEETING_COPILOT_URL").ok().as_deref());
    let connected_context = if let Some(token) = backend.capability_token() {
        if let Ok(client) = Client::builder().timeout(Duration::from_secs(2)).build() {
            match client
                .get(format!("{base_url}/context/diagnostics"))
                .header(CAPABILITY_TOKEN_HEADER, token)
                .send()
                .await
            {
                Ok(response) => response.json::<ConnectedContextDiagnostics>().await.ok(),
                Err(_) => None,
            }
        } else {
            None
        }
    } else {
        None
    };
    let export = AggregateDiagnosticsExport {
        schema_version: 2,
        generated_at_epoch_seconds: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs(),
        app_version: app.package_info().version.to_string(),
        backend: backend.status(),
        transport: dispatcher.diagnostics(),
        feedback: feedback.summary()?,
        connected_context,
    };
    let directory = app
        .path()
        .app_local_data_dir()
        .map_err(|_| "Diagnostics location is unavailable".to_string())?
        .join(DIAGNOSTICS_DIRECTORY);
    fs::create_dir_all(&directory)
        .map_err(|_| "Diagnostics location is unavailable".to_string())?;
    let path = directory.join(format!(
        "aggregate-{}.json",
        export.generated_at_epoch_seconds
    ));
    let encoded = serde_json::to_vec_pretty(&export)
        .map_err(|_| "Diagnostics export could not be created".to_string())?;
    write_atomic(&path, &encoded)?;
    Ok(path.to_string_lossy().into_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(target_os = "windows")]
    #[derive(Deserialize)]
    struct RoutedAudioCorpusManifest {
        scenario_count: usize,
        scenarios: Vec<RoutedAudioScenario>,
    }

    #[cfg(target_os = "windows")]
    #[derive(Deserialize)]
    struct RoutedAudioScenario {
        scenario_id: String,
        audio_file: String,
        lang: String,
    }

    #[test]
    fn retention_defaults_to_forever() {
        assert_eq!(
            PilotSettings::default().retention,
            MeetingRetention::Forever
        );
    }

    #[test]
    fn semantic_beta_defaults_off_without_downloading_a_model() {
        let settings = PilotSettings::default();
        assert!(!settings.semantic_beta_enabled);
        assert_eq!(settings.semantic_model, "qwen3:8b");
    }

    #[test]
    fn settings_contract_has_no_content_fields() {
        let serialized = serde_json::to_string(&PilotSettings::default()).unwrap();
        for forbidden in ["transcript", "audio", "meetingTitle", "token", "speaker"] {
            assert!(!serialized.contains(forbidden));
        }
    }

    #[test]
    fn recording_consent_contract_is_minimal_and_content_free() {
        let confirmation = RecordingConsentConfirmation {
            confirmed: true,
            confirmed_at_epoch_seconds: 1_777_777_777,
            app_version: "0.6.0".to_string(),
        };
        let serialized = serde_json::to_value(confirmation).unwrap();

        assert_eq!(
            serialized
                .as_object()
                .unwrap()
                .keys()
                .cloned()
                .collect::<std::collections::BTreeSet<_>>(),
            ["appVersion", "confirmed", "confirmedAtEpochSeconds"]
                .into_iter()
                .map(str::to_string)
                .collect()
        );
        for forbidden in [
            "participant",
            "speaker",
            "meeting",
            "title",
            "transcript",
            "token",
        ] {
            assert!(!serialized.to_string().to_lowercase().contains(forbidden));
        }
    }

    #[test]
    fn recording_consent_is_current_version_bounded_and_not_future_dated() {
        let confirmation = RecordingConsentConfirmation {
            confirmed: true,
            confirmed_at_epoch_seconds: 1_000,
            app_version: "0.6.0".to_string(),
        };

        assert!(valid_recording_consent(&confirmation, "0.6.0", 1_900));
        assert!(!valid_recording_consent(&confirmation, "0.6.0", 1_901));
        assert!(!valid_recording_consent(&confirmation, "0.5.0", 1_001));
        assert!(!valid_recording_consent(&confirmation, "0.6.0", 999));
    }

    #[test]
    fn atomic_replace_overwrites_existing_file() {
        let directory = tempfile::tempdir().unwrap();
        let destination = directory.path().join("settings.json");
        fs::write(&destination, b"old").unwrap();

        write_atomic(&destination, b"new").unwrap();

        assert_eq!(fs::read(destination).unwrap(), b"new");
        assert_eq!(
            fs::read_dir(directory.path()).unwrap().count(),
            1,
            "temporary replacement files must not remain"
        );
    }

    #[test]
    fn spoken_preflight_session_requires_the_recording_identity_format() {
        assert!(valid_recording_session_id(
            "meeting-intel-0123456789abcdef0123456789abcdef"
        ));
        for invalid in [
            "meeting-intel-0123456789ABCDEF0123456789ABCDEF",
            "meeting-intel-0123456789abcdef",
            "preflight-0123456789abcdef0123456789abcdef",
            "meeting-intel-0123456789abcdef0123456789abcde/",
        ] {
            assert!(!valid_recording_session_id(invalid), "{invalid}");
        }
    }

    #[test]
    fn spoken_preflight_contract_contains_no_transcript_or_token() {
        let result = SpokenPreflightResult {
            session_id: "meeting-intel-0123456789abcdef0123456789abcdef".to_string(),
            received_sequence_id: 0,
            state_version: 1,
            transcript_character_count: 24,
            duration_ms: 1250,
        };
        let serialized = serde_json::to_string(&result).unwrap();
        for forbidden in ["text", "transcriptText", "token", "phrase", "speaker"] {
            assert!(!serialized.contains(forbidden), "{forbidden}");
        }
    }

    #[test]
    fn spoken_preflight_supports_only_pilot_languages() {
        assert_eq!(spoken_preflight_phrase("en").unwrap().0, "en-US");
        assert_eq!(spoken_preflight_phrase("ja").unwrap().0, "ja-JP");
        assert_eq!(spoken_preflight_phrase("ko").unwrap().0, "ko-KR");
        assert!(spoken_preflight_phrase("auto").is_none());
    }

    #[test]
    fn configured_audio_device_names_match_raw_cpal_names() {
        assert_eq!(
            configured_device_base_name("Studio Microphone (input)"),
            "Studio Microphone"
        );
        assert_eq!(
            configured_device_base_name("Meeting Speakers (output)"),
            "Meeting Speakers"
        );
        assert_eq!(configured_device_base_name("default"), "default");
    }

    #[test]
    fn active_transcription_logs_do_not_format_recognized_content() {
        let sources = [
            include_str!("audio/transcription/worker.rs"),
            include_str!("whisper_engine/whisper_engine.rs"),
            include_str!("parakeet_engine/parakeet_engine.rs"),
            include_str!("audio/import.rs"),
            include_str!("audio/retranscription.rs"),
            include_str!("audio/audio_processing.rs"),
            include_str!("audio/decoder.rs"),
            include_str!("audio/recording_preferences.rs"),
            include_str!("audio/recording_saver.rs"),
            include_str!("api/api.rs"),
            include_str!("database/repositories/transcript.rs"),
            include_str!("notifications/commands.rs"),
            include_str!("summary/commands.rs"),
            include_str!("summary/service.rs"),
            include_str!("summary/processor.rs"),
            include_str!("summary/summary_engine/client.rs"),
        ]
        .join("\n");
        for forbidden in [
            "chunk.chunk_id, cleaned_text,",
            "transcription result: '{}'",
            "filtering out: '{}'",
            "text='{}'",
            "First transcript data:",
            "query '{}'",
            "query: '{}'",
            "meeting '{}': {}",
            "Opening meeting folder: {}",
            "Folder path does not exist: {}",
            "Failed to write transcripts.json: {}",
            "Failed to write metadata.json: {}",
            "Failed to update metadata.json: {}",
            "Model discovery error (continuing): {}",
            "Failed to query transcript config: {}",
            "Error getting meetings: {}",
            "Error retrieving meeting {}: {}",
            "Decoding audio file: {}",
            "Created meeting folder with checkpoints: {}",
            "Created meeting folder without checkpoints: {}",
            "Opened recordings folder: {}",
            "recording started notification for meeting: {:?}",
            "Failed to create meeting '{}': {}",
            "Failed to save transcript segment for meeting {}: {}",
            "Incremental audio saver initialized for meeting: {}",
            "meeting_name: {:?}",
            "Extracted meeting name from summary: '{}'",
            "Fetched meeting title: {}",
            "Failed to fetch meeting title: {}",
            "Failed to parse summary result JSON: {}",
            "Showing custom notification: {}",
            "Using direct Tauri notification fallback: {} - {}",
            "Failed to initialize meeting folder: {}",
            "Temp transcript file does not exist after write: {}",
            "Failed to rename transcript file from {} to {}: {}",
            "Failed to access store: {}, using defaults",
            "Using custom OpenAI endpoint: {}",
            "Processing failed for meeting_id {}: {}",
            "English normalization pass failed; returning pass-1 markdown without hard fail: {}",
            "Failed processing chunk {}/{}: {}",
            "Response: {}",
            "Found auth token: {}",
            "Response body: {}",
            "Failed to parse response: {}",
            "\"error\": e.to_string()",
            "\"userMessage\": format!(\"Transcription failed:",
            "Failed to emit speech-detected event: {}",
            "Failed to emit transcript update: {}",
            "Worker {}: {}",
            "Transcription failed: {}",
            "panicked: {:?}",
            "emit(\"transcription-warning\", e.to_string())",
        ] {
            assert!(!sources.contains(forbidden), "{forbidden}");
        }
    }

    #[cfg(target_os = "windows")]
    #[tokio::test]
    #[ignore = "requires an installed Windows SAPI English voice"]
    async fn real_windows_spoken_preflight_audio_decodes_to_speech_samples() {
        let temporary = tempfile::tempdir().unwrap();
        let wav_path = temporary.path().join("spoken-preflight.wav");
        let (culture, phrase) = spoken_preflight_phrase("en").unwrap();

        synthesize_spoken_preflight(wav_path.clone(), culture, phrase)
            .await
            .unwrap();
        let audio = decode_audio_file(&wav_path).unwrap();
        let samples = audio.to_whisper_format();
        let mean_energy =
            samples.iter().map(|sample| sample * sample).sum::<f32>() / samples.len() as f32;

        assert_eq!(audio.sample_rate, 16_000);
        assert_eq!(audio.channels, 1);
        assert!(samples.len() > 16_000);
        assert!(mean_energy > 0.0001);
    }

    #[cfg(target_os = "windows")]
    fn installed_pilot_models_directory() -> PathBuf {
        PathBuf::from(std::env::var_os("APPDATA").expect("APPDATA is required"))
            .join("com.nanakea.meetingintelligence")
            .join("models")
    }

    #[cfg(target_os = "windows")]
    async fn generated_preflight_samples(language: &str) -> Vec<f32> {
        let temporary = tempfile::tempdir().unwrap();
        let wav_path = temporary.path().join("spoken-preflight.wav");
        let (culture, phrase) = spoken_preflight_phrase(language).unwrap();
        synthesize_spoken_preflight(wav_path.clone(), culture, phrase)
            .await
            .unwrap();
        decode_audio_file(&wav_path).unwrap().to_whisper_format()
    }

    #[cfg(target_os = "windows")]
    fn routed_audio_scenarios(language: &str) -> Vec<(String, PathBuf)> {
        let root = PathBuf::from(
            std::env::var_os("MEETILY_ROUTED_AUDIO_CORPUS")
                .expect("MEETILY_ROUTED_AUDIO_CORPUS must point to the generated corpus"),
        );
        let manifest: RoutedAudioCorpusManifest =
            serde_json::from_slice(&fs::read(root.join("MANIFEST.json")).unwrap()).unwrap();
        assert_eq!(manifest.scenario_count, 108);
        assert_eq!(manifest.scenarios.len(), manifest.scenario_count);

        let scenarios = manifest
            .scenarios
            .into_iter()
            .filter(|scenario| scenario.lang == language)
            .map(|scenario| {
                let path = root.join(&scenario.audio_file);
                assert!(
                    path.is_file(),
                    "routed audio is missing for {}",
                    scenario.scenario_id
                );
                (scenario.scenario_id, path)
            })
            .collect::<Vec<_>>();
        assert_eq!(scenarios.len(), 36);
        scenarios
    }

    #[cfg(target_os = "windows")]
    #[test]
    #[ignore = "requires the generated routed-audio corpus"]
    fn routed_audio_corpus_decodes_all_declared_scenarios() {
        let scenarios = ["en", "ja", "ko"]
            .into_iter()
            .flat_map(routed_audio_scenarios)
            .collect::<Vec<_>>();
        assert_eq!(scenarios.len(), 108);

        for (scenario_id, path) in scenarios {
            let audio = decode_audio_file(&path).unwrap();
            let samples = audio.to_whisper_format();
            assert_eq!(audio.sample_rate, 16_000, "{scenario_id}");
            assert_eq!(audio.channels, 1, "{scenario_id}");
            assert!(samples.len() > 32_000, "{scenario_id}");
        }
    }

    #[cfg(target_os = "windows")]
    #[tokio::test]
    #[ignore = "requires the installed pilot Parakeet model"]
    async fn real_windows_spoken_preflight_transcribes_with_parakeet() {
        let engine = crate::parakeet_engine::ParakeetEngine::new_with_models_dir(Some(
            installed_pilot_models_directory(),
        ))
        .unwrap();
        engine.discover_models().await.unwrap();
        engine
            .load_model(crate::config::DEFAULT_PARAKEET_MODEL)
            .await
            .unwrap();

        let text = engine
            .transcribe_audio(generated_preflight_samples("en").await)
            .await
            .unwrap();

        assert!(!text.trim().is_empty());
    }

    #[cfg(target_os = "windows")]
    #[tokio::test]
    #[ignore = "requires the generated routed-audio corpus and installed pilot Parakeet model"]
    async fn real_routed_audio_corpus_transcribes_all_english_scenarios() {
        let engine = crate::parakeet_engine::ParakeetEngine::new_with_models_dir(Some(
            installed_pilot_models_directory(),
        ))
        .unwrap();
        engine.discover_models().await.unwrap();
        engine
            .load_model(crate::config::DEFAULT_PARAKEET_MODEL)
            .await
            .unwrap();

        for (scenario_id, path) in routed_audio_scenarios("en") {
            let samples = decode_audio_file(&path).unwrap().to_whisper_format();
            let text = engine.transcribe_audio(samples).await.unwrap();
            let character_count = text.trim().chars().count();
            assert!(character_count > 0, "{scenario_id}");
            println!("ROUTED_AUDIO_RESULT {scenario_id} characters={character_count}");
        }
    }

    #[cfg(target_os = "windows")]
    #[tokio::test]
    #[ignore = "requires the installed verified pilot Whisper model"]
    async fn real_windows_spoken_preflight_transcribes_with_whisper() {
        let engine = crate::whisper_engine::WhisperEngine::new_with_models_dir(Some(
            installed_pilot_models_directory(),
        ))
        .unwrap();
        engine.discover_models().await.unwrap();
        engine
            .load_model(crate::config::DEFAULT_WHISPER_MODEL)
            .await
            .unwrap();

        let (text, _, _) = engine
            .transcribe_audio_with_confidence(
                generated_preflight_samples("ja").await,
                Some("ja".to_string()),
            )
            .await
            .unwrap();

        assert!(!text.trim().is_empty());
    }

    #[cfg(target_os = "windows")]
    #[tokio::test]
    #[ignore = "requires the generated routed-audio corpus and installed verified pilot Whisper model"]
    async fn real_routed_audio_corpus_transcribes_all_japanese_scenarios() {
        let engine = crate::whisper_engine::WhisperEngine::new_with_models_dir(Some(
            installed_pilot_models_directory(),
        ))
        .unwrap();
        engine.discover_models().await.unwrap();
        engine
            .load_model(crate::config::DEFAULT_WHISPER_MODEL)
            .await
            .unwrap();

        for (scenario_id, path) in routed_audio_scenarios("ja") {
            let samples = decode_audio_file(&path).unwrap().to_whisper_format();
            let (text, _, _) = engine
                .transcribe_audio_with_confidence(samples, Some("ja".to_string()))
                .await
                .unwrap();
            let character_count = text.trim().chars().count();
            assert!(character_count > 0, "{scenario_id}");
            println!("ROUTED_AUDIO_RESULT {scenario_id} characters={character_count}");
        }
    }

    #[cfg(target_os = "windows")]
    #[tokio::test]
    #[ignore = "requires the generated routed-audio corpus and installed verified pilot Whisper model"]
    async fn real_routed_audio_corpus_transcribes_all_korean_scenarios() {
        let engine = crate::whisper_engine::WhisperEngine::new_with_models_dir(Some(
            installed_pilot_models_directory(),
        ))
        .unwrap();
        engine.discover_models().await.unwrap();
        engine
            .load_model(crate::config::DEFAULT_WHISPER_MODEL)
            .await
            .unwrap();

        for (scenario_id, path) in routed_audio_scenarios("ko") {
            let samples = decode_audio_file(&path).unwrap().to_whisper_format();
            let (text, _, _) = engine
                .transcribe_audio_with_confidence(samples, Some("ko".to_string()))
                .await
                .unwrap();
            let character_count = text.trim().chars().count();
            assert!(character_count > 0, "{scenario_id}");
            println!("ROUTED_AUDIO_RESULT {scenario_id} characters={character_count}");
        }
    }
}
