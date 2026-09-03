// audio/recording_commands.rs
//
// Slim Tauri command layer for recording functionality.
// Delegates to transcription and recording modules for actual implementation.

use anyhow::Result;
use log::{error, info, warn};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::{
    atomic::{AtomicBool, AtomicU64, Ordering},
    Arc, Mutex,
};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager, Runtime};
use tokio::task::JoinHandle;
use uuid::Uuid;

use super::{
    default_input_device,  // Get default microphone
    default_output_device, // Get default system audio
    parse_audio_device,
    AudioDevice,
    DeviceEvent,
    DeviceMonitorType,
    DeviceType,
    RecordingManager,
};

// Import transcription modules
use super::transcription::{self, reset_sequence_counter, reset_speech_detected_flag};

// Re-export TranscriptUpdate for backward compatibility
pub use super::transcription::TranscriptUpdate;

// ============================================================================
// GLOBAL STATE
// ============================================================================

// Simple recording state tracking
static IS_RECORDING: AtomicBool = AtomicBool::new(false);

// Global recording manager and transcription task to keep them alive during recording
static RECORDING_MANAGER: Mutex<Option<RecordingManager>> = Mutex::new(None);
static TRANSCRIPTION_TASK: Mutex<Option<JoinHandle<()>>> = Mutex::new(None);
static ACTIVE_TRANSCRIPT_SINK: Mutex<Option<crate::audio::recording_saver::TranscriptSink>> =
    Mutex::new(None);
// Opaque identity for the current recording's Meeting Intelligence session.
// The human-readable meeting name remains recording metadata only.
static ACTIVE_INTELLIGENCE_SESSION_ID: Mutex<Option<String>> = Mutex::new(None);
static ACTIVE_INTELLIGENCE_INGRESS_STATUS: Mutex<IntelligenceIngressStatus> =
    Mutex::new(IntelligenceIngressStatus::ExplicitLanguageRequired);
static RECORDING_STOP_METADATA: Mutex<VecDeque<RecordingStoppedPayload>> =
    Mutex::new(VecDeque::new());
const MAX_RECORDING_STOP_METADATA: usize = 8;

#[derive(Debug, Clone, Serialize)]
pub struct RecordingStoppedPayload {
    message: String,
    folder_path: Option<String>,
    meeting_name: Option<String>,
    recording_session_id: Option<String>,
    recording_save_status: String,
    issue_drafts: Vec<serde_json::Value>,
}

struct FinishedIntelligenceSession {
    session_id: String,
    issue_drafts: Vec<serde_json::Value>,
}

fn retain_recording_stop_metadata(payload: RecordingStoppedPayload) {
    let Some(session_id) = payload.recording_session_id.as_deref() else {
        return;
    };
    if let Ok(mut retained) = RECORDING_STOP_METADATA.lock() {
        retained.retain(|item| item.recording_session_id.as_deref() != Some(session_id));
        retained.push_back(payload);
        while retained.len() > MAX_RECORDING_STOP_METADATA {
            retained.pop_front();
        }
    }
}

#[tauri::command]
pub fn get_recording_stop_metadata(session_id: String) -> Option<RecordingStoppedPayload> {
    RECORDING_STOP_METADATA.lock().ok().and_then(|retained| {
        retained
            .iter()
            .rev()
            .find(|item| item.recording_session_id.as_deref() == Some(session_id.as_str()))
            .cloned()
    })
}

#[tauri::command]
pub fn get_latest_recording_stop_metadata() -> Option<RecordingStoppedPayload> {
    RECORDING_STOP_METADATA
        .lock()
        .ok()
        .and_then(|retained| retained.back().cloned())
}

#[tauri::command]
pub fn get_recording_stop_transcripts(session_id: String) -> Result<Vec<TranscriptUpdate>, String> {
    if !crate::meeting_intelligence_pilot::valid_recording_session_id(&session_id) {
        return Err("The recording session identifier is invalid".to_string());
    }
    let payload = get_recording_stop_metadata(session_id.clone())
        .ok_or_else(|| "The finalized recording handoff is unavailable".to_string())?;
    let folder = payload
        .folder_path
        .as_deref()
        .map(std::path::Path::new)
        .ok_or_else(|| "The finalized recording folder is unavailable".to_string())?;
    let segments =
        crate::audio::recording_saver::load_finalized_transcript_history(folder, &session_id)
            .map_err(str::to_string)?;
    Ok(segments
        .into_iter()
        .map(|segment| TranscriptUpdate {
            text: segment.text,
            timestamp: segment.display_time,
            source: "Audio".to_string(),
            sequence_id: segment.sequence_id,
            chunk_start_time: segment.audio_start_time,
            is_partial: false,
            confidence: segment.confidence,
            audio_start_time: segment.audio_start_time,
            audio_end_time: segment.audio_end_time,
            duration: segment.duration,
        })
        .collect())
}

#[tauri::command]
pub fn acknowledge_recording_stop_metadata(session_id: String) -> bool {
    let Ok(mut retained) = RECORDING_STOP_METADATA.lock() else {
        return false;
    };
    let original_len = retained.len();
    retained.retain(|item| item.recording_session_id.as_deref() != Some(session_id.as_str()));
    retained.len() != original_len
}

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecordingStopDiagnostics {
    audio_shutdown_ms: u64,
    transcription_join_ms: u64,
    model_shutdown_ms: u64,
    analytics_ms: u64,
    recording_save_ms: u64,
    recording_queue_peak: u64,
    recording_drain_completed: bool,
    recording_drain_ms: u64,
    recording_final_checkpoint_ms: u64,
    recording_merge_ms: u64,
    recording_checkpoint_cleanup_ms: u64,
    recording_transcript_metadata_ms: u64,
    intelligence_cleanup_ms: u64,
    total_ms: u64,
}

struct RecordingStopDiagnosticsState {
    audio_shutdown_ms: AtomicU64,
    transcription_join_ms: AtomicU64,
    model_shutdown_ms: AtomicU64,
    analytics_ms: AtomicU64,
    recording_save_ms: AtomicU64,
    recording_queue_peak: AtomicU64,
    recording_drain_completed: AtomicBool,
    recording_drain_ms: AtomicU64,
    recording_final_checkpoint_ms: AtomicU64,
    recording_merge_ms: AtomicU64,
    recording_checkpoint_cleanup_ms: AtomicU64,
    recording_transcript_metadata_ms: AtomicU64,
    intelligence_cleanup_ms: AtomicU64,
    total_ms: AtomicU64,
}

impl RecordingStopDiagnosticsState {
    const fn new() -> Self {
        Self {
            audio_shutdown_ms: AtomicU64::new(0),
            transcription_join_ms: AtomicU64::new(0),
            model_shutdown_ms: AtomicU64::new(0),
            analytics_ms: AtomicU64::new(0),
            recording_save_ms: AtomicU64::new(0),
            recording_queue_peak: AtomicU64::new(0),
            recording_drain_completed: AtomicBool::new(false),
            recording_drain_ms: AtomicU64::new(0),
            recording_final_checkpoint_ms: AtomicU64::new(0),
            recording_merge_ms: AtomicU64::new(0),
            recording_checkpoint_cleanup_ms: AtomicU64::new(0),
            recording_transcript_metadata_ms: AtomicU64::new(0),
            intelligence_cleanup_ms: AtomicU64::new(0),
            total_ms: AtomicU64::new(0),
        }
    }

    fn reset(&self) {
        self.audio_shutdown_ms.store(0, Ordering::Relaxed);
        self.transcription_join_ms.store(0, Ordering::Relaxed);
        self.model_shutdown_ms.store(0, Ordering::Relaxed);
        self.analytics_ms.store(0, Ordering::Relaxed);
        self.recording_save_ms.store(0, Ordering::Relaxed);
        self.recording_queue_peak.store(0, Ordering::Relaxed);
        self.recording_drain_completed
            .store(false, Ordering::Relaxed);
        self.recording_drain_ms.store(0, Ordering::Relaxed);
        self.recording_final_checkpoint_ms
            .store(0, Ordering::Relaxed);
        self.recording_merge_ms.store(0, Ordering::Relaxed);
        self.recording_checkpoint_cleanup_ms
            .store(0, Ordering::Relaxed);
        self.recording_transcript_metadata_ms
            .store(0, Ordering::Relaxed);
        self.intelligence_cleanup_ms.store(0, Ordering::Relaxed);
        self.total_ms.store(0, Ordering::Relaxed);
    }

    fn snapshot(&self) -> RecordingStopDiagnostics {
        RecordingStopDiagnostics {
            audio_shutdown_ms: self.audio_shutdown_ms.load(Ordering::Relaxed),
            transcription_join_ms: self.transcription_join_ms.load(Ordering::Relaxed),
            model_shutdown_ms: self.model_shutdown_ms.load(Ordering::Relaxed),
            analytics_ms: self.analytics_ms.load(Ordering::Relaxed),
            recording_save_ms: self.recording_save_ms.load(Ordering::Relaxed),
            recording_queue_peak: self.recording_queue_peak.load(Ordering::Relaxed),
            recording_drain_completed: self.recording_drain_completed.load(Ordering::Relaxed),
            recording_drain_ms: self.recording_drain_ms.load(Ordering::Relaxed),
            recording_final_checkpoint_ms: self
                .recording_final_checkpoint_ms
                .load(Ordering::Relaxed),
            recording_merge_ms: self.recording_merge_ms.load(Ordering::Relaxed),
            recording_checkpoint_cleanup_ms: self
                .recording_checkpoint_cleanup_ms
                .load(Ordering::Relaxed),
            recording_transcript_metadata_ms: self
                .recording_transcript_metadata_ms
                .load(Ordering::Relaxed),
            intelligence_cleanup_ms: self.intelligence_cleanup_ms.load(Ordering::Relaxed),
            total_ms: self.total_ms.load(Ordering::Relaxed),
        }
    }
}

static RECORDING_STOP_DIAGNOSTICS: RecordingStopDiagnosticsState =
    RecordingStopDiagnosticsState::new();

const RECORDING_STOP_DEADLINE: Duration = Duration::from_secs(15);
const AUDIO_SHUTDOWN_BUDGET: Duration = Duration::from_secs(2);
const TRANSCRIPTION_JOIN_BUDGET: Duration = Duration::from_secs(4);
const MODEL_CONFIG_BUDGET: Duration = Duration::from_millis(250);
const MODEL_SHUTDOWN_BUDGET: Duration = Duration::from_secs(1);
const ANALYTICS_BUDGET: Duration = Duration::from_millis(250);
const RECORDING_SAVE_BUDGET: Duration = Duration::from_secs(3);
const INTELLIGENCE_DRAIN_BUDGET: Duration = Duration::from_secs(10);
const INTELLIGENCE_CLEANUP_RESERVE: Duration = Duration::from_secs(2);

fn elapsed_millis(started: Instant) -> u64 {
    started.elapsed().as_millis().min(u64::MAX as u128) as u64
}

fn remaining_stop_budget(deadline: Instant, maximum: Duration) -> Duration {
    deadline
        .saturating_duration_since(Instant::now())
        .min(maximum)
}

#[tauri::command]
pub fn get_recording_stop_diagnostics() -> RecordingStopDiagnostics {
    RECORDING_STOP_DIAGNOSTICS.snapshot()
}

pub fn add_internal_soak_transcript_segment(
    segment: crate::audio::recording_saver::TranscriptSegment,
) -> bool {
    RECORDING_MANAGER
        .lock()
        .ok()
        .and_then(|manager| {
            manager
                .as_ref()
                .map(|manager| manager.add_transcript_segment_in_memory(segment))
        })
        .unwrap_or(false)
}

pub fn persist_transcript_update_internal(update: &TranscriptUpdate) -> bool {
    let segment = crate::audio::recording_saver::TranscriptSegment {
        id: format!("seg_{}", update.sequence_id),
        text: update.text.clone(),
        audio_start_time: update.audio_start_time,
        audio_end_time: update.audio_end_time,
        duration: update.duration,
        display_time: update.timestamp.clone(),
        confidence: update.confidence,
        sequence_id: update.sequence_id,
    };
    ACTIVE_TRANSCRIPT_SINK
        .lock()
        .ok()
        .and_then(|sink| sink.as_ref().map(|sink| sink.add(segment)))
        .unwrap_or(false)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum IntelligenceIngressStatus {
    Ready,
    ExplicitLanguageRequired,
    ProviderLanguageMismatch,
    ConfigurationUnavailable,
}

fn intelligence_ingress_status(
    provider: Option<&str>,
    language: Option<&str>,
) -> IntelligenceIngressStatus {
    let Some(language) = language.filter(|language| matches!(*language, "ja" | "en" | "ko")) else {
        return IntelligenceIngressStatus::ExplicitLanguageRequired;
    };
    match (provider, language) {
        (Some("localWhisper"), "ja" | "en" | "ko") | (Some("parakeet"), "en") => {
            IntelligenceIngressStatus::Ready
        }
        (Some("localWhisper" | "parakeet"), _) => {
            IntelligenceIngressStatus::ProviderLanguageMismatch
        }
        (Some(_), _) => IntelligenceIngressStatus::ProviderLanguageMismatch,
        (None, _) => IntelligenceIngressStatus::ConfigurationUnavailable,
    }
}

fn set_active_intelligence_ingress_status(status: IntelligenceIngressStatus) {
    if let Ok(mut active) = ACTIVE_INTELLIGENCE_INGRESS_STATUS.lock() {
        *active = status;
    }
}

fn get_active_intelligence_ingress_status() -> IntelligenceIngressStatus {
    ACTIVE_INTELLIGENCE_INGRESS_STATUS
        .lock()
        .map(|active| *active)
        .unwrap_or(IntelligenceIngressStatus::ConfigurationUnavailable)
}

fn resolve_recording_device(
    configured_name: Option<&str>,
    default_device: fn() -> Result<AudioDevice>,
    device_label: &str,
    expected_type: DeviceType,
) -> Result<Arc<AudioDevice>, String> {
    match configured_name.filter(|name| !name.trim().is_empty()) {
        Some(name) => normalize_configured_audio_device(name, expected_type)
            .map(Arc::new)
            .map_err(|_| format!("The selected {device_label} is invalid")),
        None => default_device()
            .map(Arc::new)
            .map_err(|_| format!("The default {device_label} is unavailable")),
    }
}

fn normalize_configured_audio_device(
    configured_name: &str,
    expected_type: DeviceType,
) -> Result<AudioDevice> {
    match parse_audio_device(configured_name) {
        Ok(device) if device.device_type == expected_type => Ok(device),
        Ok(_) => Err(anyhow::anyhow!(
            "Configured audio device type does not match"
        )),
        Err(_) if !configured_name.trim().is_empty() => Ok(AudioDevice::new(
            configured_name.trim().to_string(),
            expected_type,
        )),
        Err(error) => Err(error),
    }
}

#[derive(Debug, PartialEq, Eq)]
enum TranscriptionShutdown {
    Completed,
    Failed,
    TimedOut,
}

async fn join_transcription_task(
    mut task_handle: JoinHandle<()>,
    timeout: Duration,
) -> TranscriptionShutdown {
    match tokio::time::timeout(timeout, &mut task_handle).await {
        Ok(Ok(())) => TranscriptionShutdown::Completed,
        Ok(Err(_)) => TranscriptionShutdown::Failed,
        Err(_) => {
            task_handle.abort();
            let _ = task_handle.await;
            TranscriptionShutdown::TimedOut
        }
    }
}

fn begin_intelligence_session() -> String {
    let session_id = format!("meeting-intel-{}", Uuid::new_v4().simple());
    let mut active = ACTIVE_INTELLIGENCE_SESSION_ID.lock().unwrap();
    *active = Some(session_id.clone());
    info!("Started Meeting Intelligence session");
    session_id
}

async fn request_intelligence_session_cleanup(
    session_id: String,
    capability_token: Option<String>,
    timeout: Duration,
) {
    if timeout.is_zero() {
        log::debug!("Meeting Intelligence cleanup skipped after stop deadline");
        return;
    }
    let backend_url = super::transcription::local_only::local_copilot_url_or_default(
        std::env::var("MEETING_COPILOT_URL").ok().as_deref(),
    );
    let session_id_encoded =
        percent_encoding::utf8_percent_encode(&session_id, percent_encoding::NON_ALPHANUMERIC)
            .to_string();

    let client = match reqwest::Client::builder().timeout(timeout).build() {
        Ok(client) => client,
        Err(_) => {
            log::debug!("Meeting Intelligence cleanup client unavailable");
            return;
        }
    };
    let url = format!("{backend_url}/meeting/{session_id_encoded}");
    let mut request = client.delete(url);
    if let Some(token) = capability_token {
        request = request.header(
            crate::meeting_intelligence_sidecar::CAPABILITY_TOKEN_HEADER,
            token,
        );
    }
    match request.send().await {
        Ok(response) => {
            log::debug!(
                "Meeting Intelligence cleanup completed with status {}",
                response.status()
            );
        }
        Err(_) => {
            log::debug!("Meeting Intelligence session cleanup unavailable");
        }
    }
}

async fn request_final_issue_drafts(
    session_id: &str,
    capability_token: Option<&str>,
    timeout: Duration,
) -> Vec<serde_json::Value> {
    if timeout.is_zero() {
        return Vec::new();
    }
    let backend_url = super::transcription::local_only::local_copilot_url_or_default(
        std::env::var("MEETING_COPILOT_URL").ok().as_deref(),
    );
    let Ok(client) = reqwest::Client::builder().timeout(timeout).build() else {
        return Vec::new();
    };
    let mut request = client
        .post(format!("{backend_url}/issues/drafts/final"))
        .json(&serde_json::json!({"session_id": session_id}));
    if let Some(token) = capability_token {
        request = request.header(
            crate::meeting_intelligence_sidecar::CAPABILITY_TOKEN_HEADER,
            token,
        );
    }
    let Ok(response) = request.send().await else {
        log::debug!("Meeting Intelligence final issue drafts unavailable");
        return Vec::new();
    };
    let status = response.status();
    if !status.is_success() {
        log::debug!(
            "Meeting Intelligence final issue drafts returned status {}",
            status
        );
        return Vec::new();
    }
    response
        .json::<serde_json::Value>()
        .await
        .ok()
        .and_then(|value| {
            value
                .get("drafts")
                .and_then(|drafts| drafts.as_array())
                .cloned()
        })
        .map(|drafts| drafts.into_iter().take(50).collect())
        .unwrap_or_default()
}

async fn finish_intelligence_session<R: Runtime>(
    app: &AppHandle<R>,
    stop_deadline: Instant,
) -> Option<FinishedIntelligenceSession> {
    let session_id = ACTIVE_INTELLIGENCE_SESSION_ID
        .lock()
        .ok()
        .and_then(|mut active| active.take());
    let Some(session_id) = session_id else {
        return None;
    };

    if let Some(dispatcher) = app.try_state::<
        crate::audio::transcription::intelligence_dispatcher::IntelligenceDispatcherState,
    >() {
        let flush_budget = stop_deadline
            .saturating_duration_since(Instant::now())
            .saturating_sub(INTELLIGENCE_CLEANUP_RESERVE)
            .min(INTELLIGENCE_DRAIN_BUDGET);
        let flushed = !flush_budget.is_zero() && dispatcher.flush_and_stop(flush_budget).await;
        if !flushed {
            warn!("Meeting Intelligence final replay did not complete before shutdown");
        }
    }
    let capability_token = app
        .try_state::<crate::meeting_intelligence_sidecar::MeetingIntelligenceSidecarState>()
        .and_then(|state| state.capability_token());
    let draft_budget = remaining_stop_budget(stop_deadline, INTELLIGENCE_CLEANUP_RESERVE)
        .min(Duration::from_secs(2));
    let issue_drafts =
        request_final_issue_drafts(&session_id, capability_token.as_deref(), draft_budget).await;
    let cleanup_budget = remaining_stop_budget(stop_deadline, INTELLIGENCE_CLEANUP_RESERVE);
    if cleanup_budget.is_zero() {
        // The dispatcher is already cancelled and its retained replay has been
        // dropped, so a detached delete cannot race a late POST or recreate the
        // session. Keep the recording stop deadline strict while still purging
        // normal-stop recovery data when the backend becomes reachable.
        let cleanup_session_id = session_id.clone();
        tauri::async_runtime::spawn(async move {
            request_intelligence_session_cleanup(
                cleanup_session_id,
                capability_token,
                INTELLIGENCE_CLEANUP_RESERVE,
            )
            .await;
        });
        log::debug!("Meeting Intelligence cleanup continued after the stop deadline");
    } else {
        request_intelligence_session_cleanup(session_id.clone(), capability_token, cleanup_budget)
            .await;
    }
    info!("Ended Meeting Intelligence session");
    Some(FinishedIntelligenceSession {
        session_id,
        issue_drafts,
    })
}

/// Internal accessor used by the transcription worker for the HTTP push URL.
pub fn get_current_intelligence_session_id_internal() -> Option<String> {
    ACTIVE_INTELLIGENCE_SESSION_ID.lock().ok()?.clone()
}

async fn start_intelligence_dispatcher<R: Runtime>(
    app: &AppHandle<R>,
    session_id: String,
) -> IntelligenceIngressStatus {
    let language = crate::get_language_preference_internal();
    let provider =
        match crate::api::api::api_get_transcript_config(app.clone(), app.clone().state(), None)
            .await
        {
            Ok(Some(config)) => Some(config.provider),
            Ok(None) => Some("parakeet".to_string()),
            Err(_) => None,
        };
    let status = intelligence_ingress_status(provider.as_deref(), language.as_deref());
    set_active_intelligence_ingress_status(status);
    if status != IntelligenceIngressStatus::Ready {
        warn!(
            "Meeting Intelligence ingress disabled because language and local STT configuration are not compatible"
        );
        return status;
    }
    let lang = language.expect("ready ingress requires an explicit language");
    if let Some(dispatcher) = app.try_state::<
        crate::audio::transcription::intelligence_dispatcher::IntelligenceDispatcherState,
    >() {
        dispatcher.begin(app, session_id, lang).await;
    }
    status
}

// ============================================================================
// PUBLIC TYPES
// ============================================================================

#[derive(Debug, Deserialize)]
pub struct RecordingArgs {
    pub save_path: String,
}

#[derive(Debug, Serialize, Clone)]
pub struct TranscriptionStatus {
    pub chunks_in_queue: usize,
    pub max_chunks_in_queue: usize,
    pub audio_chunks_in_queue: usize,
    pub max_audio_chunks_in_queue: usize,
    pub is_processing: bool,
    pub last_activity_ms: u64,
}

// ============================================================================
// RECORDING COMMANDS
// ============================================================================

/// Start recording with default devices
pub async fn start_recording<R: Runtime>(app: AppHandle<R>) -> Result<(), String> {
    start_recording_with_meeting_name(app, None).await
}

/// Start recording with default devices and optional meeting name
pub async fn start_recording_with_meeting_name<R: Runtime>(
    app: AppHandle<R>,
    meeting_name: Option<String>,
) -> Result<(), String> {
    info!("Starting recording with default devices");

    let _engine_lifecycle_guard = super::common::acquire_engine_lifecycle_lock().await;

    // Check if already recording
    let current_recording_state = IS_RECORDING.load(Ordering::SeqCst);
    info!("🔍 IS_RECORDING state check: {}", current_recording_state);
    if current_recording_state {
        return Err("Recording already in progress".to_string());
    }
    crate::meeting_intelligence_pilot::consume_recording_consent(&app)?;

    // Validate that transcription models are available before starting recording
    info!("🔍 Validating transcription model availability before starting recording...");
    if transcription::validate_transcription_model_ready(&app)
        .await
        .is_err()
    {
        error!("Transcription model validation failed");

        let user_message = "Recording cannot start because the selected local speech model is not ready. Check the speech model and language settings.";
        let _ = app.emit(
            "transcription-error",
            serde_json::json!({
                "error": "model_not_ready",
                "userMessage": user_message,
                "actionable": false
            }),
        );

        return Err(user_message.to_string());
    }
    info!("✅ Transcription model validation passed");

    // Async-first approach - no more blocking operations!
    info!("🚀 Starting async recording initialization");

    // Create new recording manager
    let mut manager = RecordingManager::new();

    // Load recording preferences to get auto_save AND device preferences
    let (auto_save, save_folder, preferred_mic_name, preferred_system_name) =
        match super::recording_preferences::load_recording_preferences(&app).await {
            Ok(prefs) => {
                info!(
                    "📋 Loaded recording preferences: auto_save={}, preferred_mic={}, preferred_system={}",
                    prefs.auto_save,
                    prefs.preferred_mic_device.is_some(),
                    prefs.preferred_system_device.is_some()
                );
                (
                    prefs.auto_save,
                    prefs.save_folder,
                    prefs.preferred_mic_device,
                    prefs.preferred_system_device,
                )
            }
            Err(_) => {
                warn!("Failed to load recording preferences; using defaults");
                let defaults = super::recording_preferences::RecordingPreferences::default();
                (defaults.auto_save, defaults.save_folder, None, None)
            }
        };
    manager.set_save_folder(save_folder);

    // ============================================================================
    // MICROPHONE DEVICE RESOLUTION: Preference → Default → Error
    // ============================================================================
    let microphone_device = match preferred_mic_name {
        Some(pref_name) => {
            info!("🎤 Attempting to use preferred microphone");
            match normalize_configured_audio_device(&pref_name, DeviceType::Input) {
                Ok(device) => {
                    info!("✅ Using preferred microphone");
                    Some(Arc::new(device))
                }
                Err(_) => {
                    warn!("⚠️ Preferred microphone is not available");
                    warn!("   Falling back to system default microphone...");
                    match default_input_device() {
                        Ok(device) => {
                            info!("✅ Using default microphone");
                            Some(Arc::new(device))
                        }
                        Err(_) => {
                            error!(
                                "❌ No microphone available (preferred and default both failed)"
                            );
                            return Err(
                                "The selected and default microphones are unavailable".to_string()
                            );
                        }
                    }
                }
            }
        }
        None => {
            info!("🎤 No microphone preference set, using system default");
            match default_input_device() {
                Ok(device) => {
                    info!("✅ Using default microphone");
                    Some(Arc::new(device))
                }
                Err(_) => {
                    error!("❌ No default microphone available");
                    return Err("The default microphone is unavailable".to_string());
                }
            }
        }
    };

    // ============================================================================
    // SYSTEM AUDIO DEVICE RESOLUTION: Preference → Default → None (optional)
    // ============================================================================
    let system_device = match preferred_system_name {
        Some(pref_name) => {
            info!("🔊 Attempting to use preferred system audio");
            match normalize_configured_audio_device(&pref_name, DeviceType::Output) {
                Ok(device) => {
                    info!("✅ Using preferred system audio");
                    Some(Arc::new(device))
                }
                Err(_) => {
                    warn!("⚠️ Preferred system audio is not available");
                    warn!("   Falling back to system default...");
                    match default_output_device() {
                        Ok(device) => {
                            info!("✅ Using default system audio");
                            Some(Arc::new(device))
                        }
                        Err(_) => {
                            warn!(
                                "⚠️ No system audio available (preferred and default both failed)"
                            );
                            warn!("   Recording will continue with microphone only");
                            None // System audio is optional
                        }
                    }
                }
            }
        }
        None => {
            info!("🔊 No system audio preference set, using system default");
            match default_output_device() {
                Ok(device) => {
                    info!("✅ Using default system audio");
                    Some(Arc::new(device))
                }
                Err(_) => {
                    warn!("⚠️ No default system audio available");
                    warn!("   Recording will continue with microphone only");
                    None // System audio is optional
                }
            }
        }
    };

    // Always ensure a meeting name is set so incremental saver initializes
    let effective_meeting_name = meeting_name.clone().unwrap_or_else(|| {
        // Example: Meeting 2025-10-03_08-25-23
        let now = chrono::Local::now();
        format!("Meeting {}", now.format("%Y-%m-%d_%H-%M-%S"))
    });
    manager.set_meeting_name(Some(effective_meeting_name.clone()));

    // Set up error callback
    let app_for_error = app.clone();
    manager.set_error_callback(move |error| {
        let _ = app_for_error.emit("recording-error", error.user_message());
    });

    // Start recording with resolved devices (replaces start_recording_with_defaults_and_auto_save call)
    super::recording_state::reset_audio_pipeline_queue_diagnostics();
    transcription::reset_transcription_queue_diagnostics();
    let transcription_receiver = manager
        .start_recording(microphone_device, system_device, auto_save)
        .await
        .map_err(|_| {
            error!("Audio capture could not start");
            "Recording could not start audio capture".to_string()
        })?;

    // Generate the opaque identity only after audio recording has started and
    // before the transcription worker can emit its first backend push.
    let intelligence_session_id = begin_intelligence_session();
    manager.set_meeting_id(intelligence_session_id.clone());

    // Store the manager globally to keep it alive
    {
        let mut global_manager = RECORDING_MANAGER.lock().unwrap();
        *ACTIVE_TRANSCRIPT_SINK.lock().unwrap() = Some(manager.transcript_sink());
        *global_manager = Some(manager);
    }
    start_intelligence_dispatcher(&app, intelligence_session_id.clone()).await;

    // Set recording flag and reset speech detection flag
    info!("🔍 Setting IS_RECORDING to true and resetting SPEECH_DETECTED_EMITTED");
    IS_RECORDING.store(true, Ordering::SeqCst);
    reset_speech_detected_flag(); // Reset for new recording session
    reset_sequence_counter();

    // Publish the session identity before the worker can emit sequence 0, so
    // the webview resets prior transcript state before the first new segment.
    if app
        .emit(
            "recording-started",
            serde_json::json!({
                "message": "Recording started successfully with parallel processing",
                "devices": ["Default Microphone", "Default System Audio"],
                "workers": 3,
                "recording_session_id": intelligence_session_id,
                "meeting_name": effective_meeting_name,
                "intelligence_ingress_status": get_active_intelligence_ingress_status()
            }),
        )
        .is_err()
    {
        warn!("Recording started event was unavailable; backend state remains authoritative");
    }

    let task_handle = transcription::start_transcription_task(app.clone(), transcription_receiver);
    {
        let mut global_task = TRANSCRIPTION_TASK.lock().unwrap();
        *global_task = Some(task_handle);
    }

    // Update tray menu to reflect recording state
    crate::tray::update_tray_menu(&app);

    info!("✅ Recording started successfully with async-first approach");

    Ok(())
}

/// Start recording with specific devices
pub async fn start_recording_with_devices<R: Runtime>(
    app: AppHandle<R>,
    mic_device_name: Option<String>,
    system_device_name: Option<String>,
) -> Result<(), String> {
    start_recording_with_devices_and_meeting(app, mic_device_name, system_device_name, None).await
}

/// Start recording with specific devices and optional meeting name
pub async fn start_recording_with_devices_and_meeting<R: Runtime>(
    app: AppHandle<R>,
    mic_device_name: Option<String>,
    system_device_name: Option<String>,
    meeting_name: Option<String>,
) -> Result<(), String> {
    info!(
        "Starting recording with configured devices: explicit_mic={}, explicit_system={}",
        mic_device_name.is_some(),
        system_device_name.is_some()
    );

    let _engine_lifecycle_guard = super::common::acquire_engine_lifecycle_lock().await;

    // Check if already recording
    let current_recording_state = IS_RECORDING.load(Ordering::SeqCst);
    info!("🔍 IS_RECORDING state check: {}", current_recording_state);
    if current_recording_state {
        return Err("Recording already in progress".to_string());
    }
    crate::meeting_intelligence_pilot::consume_recording_consent(&app)?;

    // Validate that transcription models are available before starting recording
    info!("🔍 Validating transcription model availability before starting recording...");
    if transcription::validate_transcription_model_ready(&app)
        .await
        .is_err()
    {
        error!("Transcription model validation failed");

        let user_message = "Recording cannot start because the selected local speech model is not ready. Check the speech model and language settings.";
        let _ = app.emit(
            "transcription-error",
            serde_json::json!({
                "error": "model_not_ready",
                "userMessage": user_message,
                "actionable": false
            }),
        );

        return Err(user_message.to_string());
    }
    info!("✅ Transcription model validation passed");

    // The frontend represents both "Default" selections as null. Resolve those
    // values here so a default selection cannot silently disable audio capture.
    let mic_device = Some(resolve_recording_device(
        mic_device_name.as_deref(),
        default_input_device,
        "microphone",
        DeviceType::Input,
    )?);
    let system_device = Some(resolve_recording_device(
        system_device_name.as_deref(),
        default_output_device,
        "system audio device",
        DeviceType::Output,
    )?);

    // Async-first approach for custom devices - no more blocking operations!
    info!("🚀 Starting async recording initialization with custom devices");

    // Create new recording manager
    let mut manager = RecordingManager::new();

    // Load recording preferences to check auto_save setting
    let (auto_save, save_folder) =
        match super::recording_preferences::load_recording_preferences(&app).await {
            Ok(prefs) => {
                info!(
                    "📋 Loaded recording preferences: auto_save={}",
                    prefs.auto_save
                );
                (prefs.auto_save, prefs.save_folder)
            }
            Err(_) => {
                warn!("Failed to load recording preferences; using defaults");
                let defaults = super::recording_preferences::RecordingPreferences::default();
                (defaults.auto_save, defaults.save_folder)
            }
        };
    manager.set_save_folder(save_folder);

    // Always ensure a meeting name is set so incremental saver initializes
    let effective_meeting_name = meeting_name.clone().unwrap_or_else(|| {
        let now = chrono::Local::now();
        format!("Meeting {}", now.format("%Y-%m-%d_%H-%M-%S"))
    });
    manager.set_meeting_name(Some(effective_meeting_name.clone()));

    // Set up error callback
    let app_for_error = app.clone();
    manager.set_error_callback(move |error| {
        let _ = app_for_error.emit("recording-error", error.user_message());
    });

    // Start recording with specified devices and auto_save setting
    super::recording_state::reset_audio_pipeline_queue_diagnostics();
    transcription::reset_transcription_queue_diagnostics();
    let transcription_receiver = manager
        .start_recording(mic_device, system_device, auto_save)
        .await
        .map_err(|_| {
            error!("Audio capture could not start");
            "Recording could not start audio capture".to_string()
        })?;

    // Generate the opaque identity only after audio recording has started and
    // before the transcription worker can emit its first backend push.
    let intelligence_session_id = begin_intelligence_session();
    manager.set_meeting_id(intelligence_session_id.clone());

    // Store the manager globally to keep it alive
    {
        let mut global_manager = RECORDING_MANAGER.lock().unwrap();
        *ACTIVE_TRANSCRIPT_SINK.lock().unwrap() = Some(manager.transcript_sink());
        *global_manager = Some(manager);
    }
    start_intelligence_dispatcher(&app, intelligence_session_id.clone()).await;

    // Set recording flag and reset speech detection flag
    info!("🔍 Setting IS_RECORDING to true and resetting SPEECH_DETECTED_EMITTED");
    IS_RECORDING.store(true, Ordering::SeqCst);
    reset_speech_detected_flag(); // Reset for new recording session
    reset_sequence_counter();

    // Publish the session identity before the worker can emit sequence 0, so
    // the webview resets prior transcript state before the first new segment.
    if app
        .emit(
            "recording-started",
            serde_json::json!({
                "message": "Recording started with custom devices and parallel processing",
                "devices": [
                    mic_device_name.unwrap_or_else(|| "Default Microphone".to_string()),
                    system_device_name.unwrap_or_else(|| "Default System Audio".to_string())
                ],
                "workers": 3,
                "recording_session_id": intelligence_session_id,
                "meeting_name": effective_meeting_name,
                "intelligence_ingress_status": get_active_intelligence_ingress_status()
            }),
        )
        .is_err()
    {
        warn!("Recording started event was unavailable; backend state remains authoritative");
    }

    let task_handle = transcription::start_transcription_task(app.clone(), transcription_receiver);
    {
        let mut global_task = TRANSCRIPTION_TASK.lock().unwrap();
        *global_task = Some(task_handle);
    }

    // Update tray menu to reflect recording state
    crate::tray::update_tray_menu(&app);

    info!("✅ Recording started with custom devices using async-first approach");

    Ok(())
}

/// Stop recording with a bounded best-effort flush and deterministic cleanup.
pub async fn stop_recording<R: Runtime>(
    app: AppHandle<R>,
    _args: RecordingArgs,
) -> Result<(), String> {
    // One caller owns final STT, dispatcher flush, and cleanup. Concurrent
    // callers wait, then observe the completed idempotent state below.
    let _engine_lifecycle_guard = super::common::acquire_engine_lifecycle_lock().await;
    info!("🛑 Starting bounded recording shutdown with final transcript flush");

    // Check if recording is active
    if !IS_RECORDING.load(Ordering::SeqCst) {
        info!("Recording was not active");
        return Ok(());
    }
    RECORDING_STOP_DIAGNOSTICS.reset();
    let total_stop_started = Instant::now();
    let stop_deadline = total_stop_started + RECORDING_STOP_DEADLINE;

    // Emit shutdown progress to frontend
    let _ = app.emit(
        "recording-shutdown-progress",
        serde_json::json!({
            "stage": "stopping_audio",
            "message": "Stopping audio capture...",
            "progress": 20
        }),
    );

    // Step 1: Stop audio capture immediately (no more new chunks) with proper error handling
    let audio_shutdown_started = Instant::now();
    let manager_for_cleanup = {
        let mut global_manager = RECORDING_MANAGER.lock().unwrap();
        global_manager.take()
    };

    let stop_result = if let Some(mut manager) = manager_for_cleanup {
        // Use FORCE FLUSH to immediately process all accumulated audio - eliminates 30s delay!
        info!("🚀 Using FORCE FLUSH to eliminate pipeline accumulation delays");
        let result = match tokio::time::timeout(
            remaining_stop_budget(stop_deadline, AUDIO_SHUTDOWN_BUDGET),
            manager.stop_streams_and_force_flush(),
        )
        .await
        {
            Ok(result) => result,
            Err(_) => Err(anyhow::anyhow!("audio shutdown deadline exceeded")),
        };
        // Store manager back for later cleanup
        let manager_for_cleanup = Some(manager);
        (result, manager_for_cleanup)
    } else {
        warn!("No recording manager found to stop");
        (Ok(()), None)
    };

    let (stop_result, manager_for_cleanup) = stop_result;
    RECORDING_STOP_DIAGNOSTICS
        .audio_shutdown_ms
        .store(elapsed_millis(audio_shutdown_started), Ordering::Relaxed);

    let stop_failure = match stop_result {
        Ok(_) => {
            info!("✅ Audio streams stopped successfully - no more chunks will be created");
            None
        }
        Err(e) => {
            error!("❌ Audio streams did not stop cleanly; bounded shutdown will continue");
            Some(e.to_string())
        }
    };

    // Step 2: Give queued transcription work a bounded final processing window.
    let _ = app.emit(
        "recording-shutdown-progress",
        serde_json::json!({
            "stage": "processing_transcripts",
            "message": "Processing remaining transcript chunks...",
            "progress": 40
        }),
    );

    // A wedged model must not hold recording teardown indefinitely.
    let transcription_join_started = Instant::now();
    let transcription_task = {
        let mut global_task = TRANSCRIPTION_TASK.lock().unwrap();
        global_task.take()
    };

    if let Some(task_handle) = transcription_task {
        info!("⏳ Processing final transcription chunks within the shared stop deadline");

        // Enhanced progress monitoring during shutdown
        let progress_app = app.clone();
        let progress_task = tokio::spawn(async move {
            let last_update = std::time::Instant::now();

            loop {
                tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;

                // Emit periodic progress updates during shutdown
                let elapsed = last_update.elapsed().as_secs();
                let _ = progress_app.emit(
                    "recording-shutdown-progress",
                    serde_json::json!({
                        "stage": "processing_transcripts",
                        "message": format!("Processing transcripts... ({}s elapsed)", elapsed),
                        "progress": 40,
                        "detailed": true,
                        "elapsed_seconds": elapsed
                    }),
                );
            }
        });

        let transcription_budget = remaining_stop_budget(stop_deadline, TRANSCRIPTION_JOIN_BUDGET);
        match join_transcription_task(task_handle, transcription_budget).await {
            TranscriptionShutdown::Completed => {
                info!("✅ ALL transcription chunks processed successfully - no data lost");
            }
            TranscriptionShutdown::Failed => {
                warn!("⚠️ Transcription task stopped before all chunks were processed");
                // Continue anyway - the worker may have processed most chunks
            }
            TranscriptionShutdown::TimedOut => {
                warn!("⏱️ Final transcription budget expired; the worker was cancelled before cleanup");
            }
        }

        // Stop progress monitoring
        progress_task.abort();
    } else {
        info!("ℹ️ No transcription task found to wait for");
    }
    // The worker has completed or been cancelled. The manager retained below
    // still owns the same ordered history for finalization.
    ACTIVE_TRANSCRIPT_SINK.lock().unwrap().take();
    RECORDING_STOP_DIAGNOSTICS.transcription_join_ms.store(
        elapsed_millis(transcription_join_started),
        Ordering::Relaxed,
    );

    // Step 3: Now safely unload Whisper model after ALL chunks are processed
    let _ = app.emit(
        "recording-shutdown-progress",
        serde_json::json!({
            "stage": "unloading_model",
            "message": "Unloading speech recognition model...",
            "progress": 70
        }),
    );

    info!("🧠 All transcript chunks processed. Now safely unloading transcription model...");

    let model_shutdown_started = Instant::now();
    // Determine which provider was used and unload the appropriate model (with timeout)
    let config = match tokio::time::timeout(
        remaining_stop_budget(stop_deadline, MODEL_CONFIG_BUDGET),
        crate::api::api::api_get_transcript_config(app.clone(), app.clone().state(), None),
    )
    .await
    {
        Ok(Ok(Some(config))) => Some(config.provider),
        Ok(Ok(None)) => None,
        Ok(Err(e)) => {
            warn!("⚠️ Failed to get transcript config: {:?}", e);
            None
        }
        Err(_) => {
            warn!("⏱️ Transcript config lookup exceeded its stop budget");
            None
        }
    };

    match config.as_deref() {
        Some("parakeet") => {
            info!("🦜 Unloading Parakeet model...");
            let engine_clone = {
                let engine_guard = crate::parakeet_engine::commands::PARAKEET_ENGINE
                    .lock()
                    .unwrap();
                engine_guard.as_ref().cloned()
            };

            if let Some(engine) = engine_clone {
                let shutdown = tokio::time::timeout(
                    remaining_stop_budget(stop_deadline, MODEL_SHUTDOWN_BUDGET),
                    async {
                        let current_model = engine
                            .get_current_model()
                            .await
                            .unwrap_or_else(|| "unknown".to_string());
                        let unloaded = engine.unload_model().await;
                        (current_model, unloaded)
                    },
                )
                .await;
                match shutdown {
                    Ok((current_model, true)) => {
                        info!(
                            "✅ Parakeet model '{}' unloaded successfully",
                            current_model
                        )
                    }
                    Ok((current_model, false)) => {
                        warn!("⚠️ Failed to unload Parakeet model '{}'", current_model)
                    }
                    Err(_) => warn!("⏱️ Parakeet model shutdown exceeded its stop budget"),
                }
            } else {
                warn!("⚠️ No Parakeet engine found to unload model");
            }
        }
        _ => {
            // Default to Whisper
            info!("🎤 Unloading Whisper model...");
            let engine_clone = {
                let engine_guard = crate::whisper_engine::commands::WHISPER_ENGINE
                    .lock()
                    .unwrap();
                engine_guard.as_ref().cloned()
            };

            if let Some(engine) = engine_clone {
                let shutdown = tokio::time::timeout(
                    remaining_stop_budget(stop_deadline, MODEL_SHUTDOWN_BUDGET),
                    async {
                        let current_model = engine
                            .get_current_model()
                            .await
                            .unwrap_or_else(|| "unknown".to_string());
                        let unloaded = engine.unload_model().await;
                        (current_model, unloaded)
                    },
                )
                .await;
                match shutdown {
                    Ok((current_model, true)) => {
                        info!("✅ Whisper model '{}' unloaded successfully", current_model)
                    }
                    Ok((current_model, false)) => {
                        warn!("⚠️ Failed to unload Whisper model '{}'", current_model)
                    }
                    Err(_) => warn!("⏱️ Whisper model shutdown exceeded its stop budget"),
                }
            } else {
                warn!("⚠️ No Whisper engine found to unload model");
            }
        }
    }
    RECORDING_STOP_DIAGNOSTICS
        .model_shutdown_ms
        .store(elapsed_millis(model_shutdown_started), Ordering::Relaxed);

    // Step 3.5: Track meeting ended analytics with privacy-safe metadata
    // Extract all data from manager BEFORE any async operations to avoid Send issues
    let analytics_started = Instant::now();
    let analytics_data = if let Some(ref manager) = manager_for_cleanup {
        let state = manager.get_state();
        let stats = state.get_stats();

        Some((
            manager.get_recording_duration(),
            manager.get_active_recording_duration().unwrap_or(0.0),
            manager.get_total_pause_duration(),
            manager.get_transcript_segments().len() as u64,
            state.has_fatal_error(),
            state.get_microphone_device().map(|d| d.name.clone()),
            state.get_system_device().map(|d| d.name.clone()),
            stats.chunks_processed,
        ))
    } else {
        None
    };

    // Now perform async analytics tracking without holding manager reference
    if let Some((
        total_duration,
        active_duration,
        pause_duration,
        transcript_segments_count,
        had_fatal_error,
        mic_device_name,
        sys_device_name,
        chunks_processed,
    )) = analytics_data
    {
        let analytics = async {
            info!("📊 Collecting analytics for meeting end");

            // Helper function to classify device type from device name (privacy-safe)
            fn classify_device_type(device_name: &str) -> &'static str {
                let name_lower = device_name.to_lowercase();
                // Check for Bluetooth keywords
                if name_lower.contains("bluetooth")
                    || name_lower.contains("airpods")
                    || name_lower.contains("beats")
                    || name_lower.contains("headphones")
                    || name_lower.contains("bt ")
                    || name_lower.contains("wireless")
                {
                    "Bluetooth"
                } else {
                    "Wired"
                }
            }

            // Get transcription model info (already loaded above for model unload)
            let transcription_config = match crate::api::api::api_get_transcript_config(
                app.clone(),
                app.clone().state(),
                None,
            )
            .await
            {
                Ok(Some(config)) => Some((config.provider, config.model)),
                _ => None,
            };

            let (transcription_provider, transcription_model) = transcription_config
                .unwrap_or_else(|| ("unknown".to_string(), "unknown".to_string()));

            // Get summary model info from API
            let summary_config =
                match crate::api::api::api_get_model_config(app.clone(), app.clone().state(), None)
                    .await
                {
                    Ok(Some(config)) => Some((config.provider, config.model)),
                    _ => None,
                };

            let (summary_provider, summary_model) =
                summary_config.unwrap_or_else(|| ("unknown".to_string(), "unknown".to_string()));

            // Classify device types (privacy-safe)
            let microphone_device_type = mic_device_name
                .as_ref()
                .map(|name| classify_device_type(name))
                .unwrap_or("Unknown");

            let system_audio_device_type = sys_device_name
                .as_ref()
                .map(|name| classify_device_type(name))
                .unwrap_or("Unknown");

            // Track meeting ended event with privacy-safe data
            match crate::analytics::commands::track_meeting_ended(
                transcription_provider.clone(),
                transcription_model.clone(),
                summary_provider.clone(),
                summary_model.clone(),
                total_duration,
                active_duration,
                pause_duration,
                microphone_device_type.to_string(),
                system_audio_device_type.to_string(),
                chunks_processed,
                transcript_segments_count,
                had_fatal_error,
            )
            .await
            {
                Ok(_) => info!("✅ Analytics tracked successfully for meeting end"),
                Err(_) => warn!("⚠️ Meeting-end analytics were unavailable"),
            }
        };
        if tokio::time::timeout(
            remaining_stop_budget(stop_deadline, ANALYTICS_BUDGET),
            analytics,
        )
        .await
        .is_err()
        {
            warn!("⏱️ Meeting-end analytics exceeded its stop budget");
        }
    }
    RECORDING_STOP_DIAGNOSTICS
        .analytics_ms
        .store(elapsed_millis(analytics_started), Ordering::Relaxed);

    // Step 4: Finalize recording state and cleanup resources safely
    let _ = app.emit(
        "recording-shutdown-progress",
        serde_json::json!({
            "stage": "finalizing",
            "message": "Finalizing recording and cleaning up resources...",
            "progress": 90
        }),
    );

    // Perform final cleanup with the manager if available
    let recording_save_started = Instant::now();
    let (meeting_folder, meeting_name, recording_save_status, recording_save_diagnostics) =
        if let Some(mut manager) = manager_for_cleanup {
            info!("🧹 Performing final cleanup and saving recording data");

            // Extract meeting info BEFORE async operations
            let meeting_folder = manager.get_meeting_folder();
            let meeting_name = manager.get_meeting_name();

            let recording_save_status = match tokio::time::timeout(
                remaining_stop_budget(stop_deadline, RECORDING_SAVE_BUDGET),
                manager.save_recording_only(&app),
            )
            .await
            {
                Ok(Ok(_)) => {
                    info!("✅ Recording data saved successfully during cleanup");
                    "saved"
                }
                Ok(Err(_)) => {
                    warn!("⚠️ Recording cleanup failed; shutdown will continue");
                    "failed"
                }
                Err(_) => {
                    warn!("⏱️ Recording finalization exceeded its stop budget; shutdown will continue");
                    "failed"
                }
            };

            let recording_save_diagnostics = manager.get_recording_save_diagnostics();
            (
                meeting_folder,
                meeting_name,
                recording_save_status,
                recording_save_diagnostics,
            )
        } else {
            info!("ℹ️ No recording manager available for cleanup");
            (
                None,
                None,
                "failed",
                crate::audio::recording_saver::RecordingSaveDiagnostics::default(),
            )
        };
    RECORDING_STOP_DIAGNOSTICS
        .recording_save_ms
        .store(elapsed_millis(recording_save_started), Ordering::Relaxed);
    RECORDING_STOP_DIAGNOSTICS.recording_queue_peak.store(
        recording_save_diagnostics.queue_peak as u64,
        Ordering::Relaxed,
    );
    RECORDING_STOP_DIAGNOSTICS.recording_drain_completed.store(
        recording_save_diagnostics.drain_completed,
        Ordering::Relaxed,
    );
    RECORDING_STOP_DIAGNOSTICS
        .recording_drain_ms
        .store(recording_save_diagnostics.drain_ms, Ordering::Relaxed);
    RECORDING_STOP_DIAGNOSTICS
        .recording_final_checkpoint_ms
        .store(
            recording_save_diagnostics.final_checkpoint_ms,
            Ordering::Relaxed,
        );
    RECORDING_STOP_DIAGNOSTICS
        .recording_merge_ms
        .store(recording_save_diagnostics.merge_ms, Ordering::Relaxed);
    RECORDING_STOP_DIAGNOSTICS
        .recording_checkpoint_cleanup_ms
        .store(
            recording_save_diagnostics.checkpoint_cleanup_ms,
            Ordering::Relaxed,
        );
    RECORDING_STOP_DIAGNOSTICS
        .recording_transcript_metadata_ms
        .store(
            recording_save_diagnostics.transcript_metadata_ms,
            Ordering::Relaxed,
        );

    // Set recording flag to false
    info!("🔍 Setting IS_RECORDING to false");
    IS_RECORDING.store(false, Ordering::SeqCst);

    // The worker has been joined above, so no further updates can be queued.
    // Flush the replay buffer, cancel delivery, and only then delete backend
    // state so a late task cannot recreate the completed session.
    let intelligence_cleanup_started = Instant::now();
    let completed_intelligence_session = finish_intelligence_session(&app, stop_deadline).await;
    RECORDING_STOP_DIAGNOSTICS.intelligence_cleanup_ms.store(
        elapsed_millis(intelligence_cleanup_started),
        Ordering::Relaxed,
    );
    set_active_intelligence_ingress_status(IntelligenceIngressStatus::ExplicitLanguageRequired);

    // Step 4.5: Prepare metadata for frontend (NO database save)
    // NOTE: We do NOT save to database here. The frontend will save after all transcripts are displayed.
    // This ensures the user sees all transcripts streaming in before the database save happens.
    let (folder_path_str, meeting_name_str) = match (&meeting_folder, &meeting_name) {
        (Some(path), Some(name)) => (Some(path.to_string_lossy().to_string()), Some(name.clone())),
        _ => (None, None),
    };

    info!("📤 Preparing recording metadata for frontend save");

    // Database save removed - frontend will handle this after receiving all transcripts
    info!("ℹ️ Skipping database save in Rust - frontend will save after all transcripts received");

    // Step 5: Complete shutdown
    let _ = app.emit(
        "recording-shutdown-progress",
        serde_json::json!({
            "stage": "complete",
            "message": "Recording stopped successfully",
            "progress": 100
        }),
    );

    // Retain the handoff until the frontend confirms that SQLite persistence
    // committed. A webview reload cannot turn a finalized folder into an orphan.
    let stopped_payload = RecordingStoppedPayload {
        message: "Recording stopped - frontend will save after all transcripts received"
            .to_string(),
        folder_path: folder_path_str,
        meeting_name: meeting_name_str,
        recording_session_id: completed_intelligence_session
            .as_ref()
            .map(|session| session.session_id.clone()),
        recording_save_status: recording_save_status.to_string(),
        issue_drafts: completed_intelligence_session
            .map(|session| session.issue_drafts)
            .unwrap_or_default(),
    };
    retain_recording_stop_metadata(stopped_payload.clone());
    if app.emit("recording-stopped", &stopped_payload).is_err() {
        warn!("Recording stopped event was unavailable; native stop remains committed");
    }

    // Update tray menu to reflect stopped state
    crate::tray::update_tray_menu(&app);

    if stop_failure.is_some() {
        warn!("Recording stop completed with an audio shutdown warning");
    } else {
        info!("Recording stop completed within the bounded lifecycle");
    }
    RECORDING_STOP_DIAGNOSTICS
        .total_ms
        .store(elapsed_millis(total_stop_started), Ordering::Relaxed);
    Ok(())
}

/// Check if recording is active
pub async fn is_recording() -> bool {
    IS_RECORDING.load(Ordering::SeqCst)
}

/// Get recording statistics
pub fn get_transcription_status() -> TranscriptionStatus {
    let snapshot = transcription::transcription_queue_snapshot();
    let audio_snapshot = super::recording_state::audio_pipeline_queue_snapshot();
    TranscriptionStatus {
        chunks_in_queue: snapshot.current_depth,
        max_chunks_in_queue: snapshot.max_depth,
        audio_chunks_in_queue: audio_snapshot.current_depth,
        max_audio_chunks_in_queue: audio_snapshot.max_depth,
        is_processing: snapshot.current_depth > 0 || audio_snapshot.current_depth > 0,
        last_activity_ms: snapshot.last_activity_ms,
    }
}

/// Pause the current recording
#[tauri::command]
pub async fn pause_recording<R: Runtime>(app: AppHandle<R>) -> Result<(), String> {
    info!("Pausing recording");

    // Check if currently recording
    if !IS_RECORDING.load(Ordering::SeqCst) {
        return Err("No recording is currently active".to_string());
    }

    // Access the recording manager and pause it
    let manager_guard = RECORDING_MANAGER.lock().unwrap();
    if let Some(manager) = manager_guard.as_ref() {
        manager.pause_recording().map_err(|e| e.to_string())?;

        // Emit pause event to frontend
        app.emit(
            "recording-paused",
            serde_json::json!({
                "message": "Recording paused"
            }),
        )
        .map_err(|e| e.to_string())?;

        // Update tray menu to reflect paused state
        crate::tray::update_tray_menu(&app);

        info!("Recording paused successfully");
        Ok(())
    } else {
        Err("No recording manager found".to_string())
    }
}

/// Resume the current recording
#[tauri::command]
pub async fn resume_recording<R: Runtime>(app: AppHandle<R>) -> Result<(), String> {
    info!("Resuming recording");

    // Check if currently recording
    if !IS_RECORDING.load(Ordering::SeqCst) {
        return Err("No recording is currently active".to_string());
    }

    // Access the recording manager and resume it
    let manager_guard = RECORDING_MANAGER.lock().unwrap();
    if let Some(manager) = manager_guard.as_ref() {
        manager.resume_recording().map_err(|e| e.to_string())?;

        // Emit resume event to frontend
        app.emit(
            "recording-resumed",
            serde_json::json!({
                "message": "Recording resumed"
            }),
        )
        .map_err(|e| e.to_string())?;

        // Update tray menu to reflect resumed state
        crate::tray::update_tray_menu(&app);

        info!("Recording resumed successfully");
        Ok(())
    } else {
        Err("No recording manager found".to_string())
    }
}

/// Check if recording is currently paused
#[tauri::command]
pub async fn is_recording_paused() -> bool {
    let manager_guard = RECORDING_MANAGER.lock().unwrap();
    if let Some(manager) = manager_guard.as_ref() {
        manager.is_paused()
    } else {
        false
    }
}

/// Get detailed recording state
#[tauri::command]
pub async fn get_recording_state() -> serde_json::Value {
    let is_recording = IS_RECORDING.load(Ordering::SeqCst);
    let manager_guard = RECORDING_MANAGER.lock().unwrap();

    if let Some(manager) = manager_guard.as_ref() {
        serde_json::json!({
            "is_recording": is_recording,
            "is_paused": manager.is_paused(),
            "is_active": manager.is_active(),
            "recording_duration": manager.get_recording_duration(),
            "active_duration": manager.get_active_recording_duration(),
            "recording_session_id": get_current_intelligence_session_id_internal(),
            "intelligence_ingress_status": get_active_intelligence_ingress_status(),
            "total_pause_duration": manager.get_total_pause_duration(),
            "current_pause_duration": manager.get_current_pause_duration()
        })
    } else {
        serde_json::json!({
            "is_recording": is_recording,
            "is_paused": false,
            "is_active": false,
            "recording_duration": null,
            "active_duration": null,
            "recording_session_id": null,
            "intelligence_ingress_status": IntelligenceIngressStatus::ExplicitLanguageRequired,
            "total_pause_duration": 0.0,
            "current_pause_duration": null
        })
    }
}

/// Get the meeting folder path for the current recording
/// Returns the path if a meeting name was set and folder structure initialized
#[tauri::command]
pub async fn get_meeting_folder_path() -> Result<Option<String>, String> {
    let manager_guard = RECORDING_MANAGER.lock().unwrap();
    if let Some(manager) = manager_guard.as_ref() {
        Ok(manager
            .get_meeting_folder()
            .map(|p| p.to_string_lossy().to_string()))
    } else {
        Ok(None)
    }
}

/// Get accumulated transcript segments from current recording session
/// Used for syncing frontend state after page reload during active recording
#[tauri::command]
pub async fn get_transcript_history(
) -> Result<Vec<crate::audio::recording_saver::TranscriptSegment>, String> {
    let manager_guard = RECORDING_MANAGER.lock().unwrap();

    if let Some(manager) = manager_guard.as_ref() {
        Ok(manager.get_transcript_segments())
    } else {
        Ok(Vec::new()) // No recording active, return empty
    }
}

/// Get meeting name from current recording session
/// Used for syncing frontend state after page reload during active recording
#[tauri::command]
pub async fn get_recording_meeting_name() -> Result<Option<String>, String> {
    let manager_guard = RECORDING_MANAGER.lock().unwrap();

    if let Some(manager) = manager_guard.as_ref() {
        Ok(manager.get_meeting_name())
    } else {
        Ok(None)
    }
}

/// Get the opaque Meeting Intelligence session identity for the active recording.
/// This is deliberately separate from the human-readable meeting name.
#[tauri::command]
pub fn get_recording_intelligence_session_id() -> Result<Option<String>, String> {
    Ok(get_current_intelligence_session_id_internal())
}

// ============================================================================
// DEVICE MONITORING COMMANDS (AirPods/Bluetooth disconnect/reconnect support)
// ============================================================================

/// Response structure for device events
#[derive(Debug, Serialize, Clone)]
#[serde(tag = "type")]
pub enum DeviceEventResponse {
    DeviceDisconnected {
        device_name: String,
        device_type: String,
    },
    DeviceReconnected {
        device_name: String,
        device_type: String,
    },
    DeviceListChanged,
}

impl From<DeviceEvent> for DeviceEventResponse {
    fn from(event: DeviceEvent) -> Self {
        match event {
            DeviceEvent::DeviceDisconnected {
                device_name,
                device_type,
            } => DeviceEventResponse::DeviceDisconnected {
                device_name,
                device_type: format!("{:?}", device_type),
            },
            DeviceEvent::DeviceReconnected {
                device_name,
                device_type,
            } => DeviceEventResponse::DeviceReconnected {
                device_name,
                device_type: format!("{:?}", device_type),
            },
            DeviceEvent::DeviceListChanged => DeviceEventResponse::DeviceListChanged,
        }
    }
}

/// Reconnection status information
#[derive(Debug, Serialize, Clone)]
pub struct ReconnectionStatus {
    pub is_reconnecting: bool,
    pub disconnected_device: Option<DisconnectedDeviceInfo>,
}

/// Information about a disconnected device
#[derive(Debug, Serialize, Clone)]
pub struct DisconnectedDeviceInfo {
    pub name: String,
    pub device_type: String,
}

/// Poll for audio device events (disconnect/reconnect)
/// Should be called periodically (every 1-2 seconds) by frontend during recording
#[tauri::command]
pub async fn poll_audio_device_events() -> Result<Option<DeviceEventResponse>, String> {
    let mut manager_guard = RECORDING_MANAGER.lock().unwrap();

    if let Some(manager) = manager_guard.as_mut() {
        if let Some(event) = manager.poll_device_events() {
            info!("📱 Device event polled: {:?}", event);
            Ok(Some(event.into()))
        } else {
            Ok(None)
        }
    } else {
        // Not recording, no events
        Ok(None)
    }
}

/// Get current reconnection status
/// Returns whether the system is attempting to reconnect and which device
#[tauri::command]
pub async fn get_reconnection_status() -> Result<ReconnectionStatus, String> {
    let manager_guard = RECORDING_MANAGER.lock().unwrap();

    if let Some(manager) = manager_guard.as_ref() {
        let state = manager.get_state();
        let disconnected_device = state
            .get_disconnected_device()
            .map(|(device, device_type)| DisconnectedDeviceInfo {
                name: device.name.clone(),
                device_type: format!("{:?}", device_type),
            });

        Ok(ReconnectionStatus {
            is_reconnecting: manager.is_reconnecting(),
            disconnected_device,
        })
    } else {
        // Not recording, no reconnection in progress
        Ok(ReconnectionStatus {
            is_reconnecting: false,
            disconnected_device: None,
        })
    }
}

/// Get information about the active audio output device
/// Used to warn users about Bluetooth playback issues
#[tauri::command]
pub async fn get_active_audio_output() -> Result<super::playback_monitor::AudioOutputInfo, String> {
    super::playback_monitor::get_active_audio_output()
        .await
        .map_err(|e| format!("Failed to get audio output info: {}", e))
}

/// Manually trigger device reconnection attempt
/// Useful for UI "Retry" button
#[tauri::command]
pub async fn attempt_device_reconnect(
    device_name: String,
    device_type: String,
) -> Result<bool, String> {
    // Parse device type first
    let monitor_type = match device_type.as_str() {
        "Microphone" => DeviceMonitorType::Microphone,
        "SystemAudio" => DeviceMonitorType::SystemAudio,
        _ => return Err(format!("Invalid device type: {}", device_type)),
    };

    // Check if recording is active
    {
        let manager_guard = RECORDING_MANAGER.lock().unwrap();
        if manager_guard.is_none() {
            return Err("Recording not active".to_string());
        }
    } // Release lock

    // Spawn blocking task to handle the async reconnection
    let result = tokio::task::spawn_blocking(move || {
        tokio::runtime::Handle::current().block_on(async {
            let mut manager_guard = RECORDING_MANAGER.lock().unwrap();
            if let Some(manager) = manager_guard.as_mut() {
                manager
                    .attempt_device_reconnect(&device_name, monitor_type)
                    .await
            } else {
                Err(anyhow::anyhow!("Recording not active"))
            }
        })
    })
    .await
    .map_err(|e| format!("Task join error: {}", e))?;

    match result {
        Ok(success) => {
            if success {
                info!("✅ Manual reconnection successful");
            } else {
                warn!("❌ Manual reconnection failed - device not available");
            }
            Ok(success)
        }
        Err(e) => {
            error!("Manual reconnection error: {}", e);
            Err(e.to_string())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicBool;

    fn test_default_microphone() -> Result<AudioDevice> {
        Ok(AudioDevice::new(
            "Default test microphone".to_string(),
            DeviceType::Input,
        ))
    }

    fn default_must_not_be_called() -> Result<AudioDevice> {
        panic!("an explicit device must not invoke default resolution")
    }

    #[test]
    fn null_recording_device_resolves_the_system_default() {
        let device = resolve_recording_device(
            None,
            test_default_microphone,
            "microphone",
            DeviceType::Input,
        )
        .unwrap();

        assert_eq!(device.name, "Default test microphone");
        assert_eq!(device.device_type, DeviceType::Input);
    }

    #[test]
    fn explicit_recording_device_preserves_its_capture_type() {
        let device = resolve_recording_device(
            Some("Conference output (output)"),
            default_must_not_be_called,
            "system audio device",
            DeviceType::Output,
        )
        .unwrap();

        assert_eq!(device.name, "Conference output");
        assert_eq!(device.device_type, DeviceType::Output);
    }

    #[test]
    fn legacy_raw_recording_device_name_uses_the_expected_capture_type() {
        let device = resolve_recording_device(
            Some("potentially-sensitive-device-name"),
            default_must_not_be_called,
            "microphone",
            DeviceType::Input,
        )
        .unwrap();

        assert_eq!(device.name, "potentially-sensitive-device-name");
        assert_eq!(device.device_type, DeviceType::Input);
    }

    #[test]
    fn contradictory_recording_device_suffix_is_rejected_without_echoing_the_name() {
        let error = resolve_recording_device(
            Some("potentially-sensitive-device-name (output)"),
            default_must_not_be_called,
            "microphone",
            DeviceType::Input,
        )
        .unwrap_err();

        assert_eq!(error, "The selected microphone is invalid");
        assert!(!error.contains("potentially-sensitive-device-name"));
    }

    #[test]
    fn intelligence_ingress_accepts_only_provider_language_pairs_the_stt_supports() {
        assert_eq!(
            intelligence_ingress_status(Some("parakeet"), Some("en")),
            IntelligenceIngressStatus::Ready
        );
        assert_eq!(
            intelligence_ingress_status(Some("localWhisper"), Some("en")),
            IntelligenceIngressStatus::Ready
        );
        assert_eq!(
            intelligence_ingress_status(Some("localWhisper"), Some("ja")),
            IntelligenceIngressStatus::Ready
        );
        assert_eq!(
            intelligence_ingress_status(Some("localWhisper"), Some("ko")),
            IntelligenceIngressStatus::Ready
        );
        assert_eq!(
            intelligence_ingress_status(Some("parakeet"), Some("ja")),
            IntelligenceIngressStatus::ProviderLanguageMismatch
        );
        assert_eq!(
            intelligence_ingress_status(Some("parakeet"), Some("ko")),
            IntelligenceIngressStatus::ProviderLanguageMismatch
        );
    }

    #[test]
    fn intelligence_ingress_fails_closed_for_implicit_or_unknown_configuration() {
        assert_eq!(
            intelligence_ingress_status(Some("parakeet"), Some("auto")),
            IntelligenceIngressStatus::ExplicitLanguageRequired
        );
        assert_eq!(
            intelligence_ingress_status(Some("cloudProvider"), Some("en")),
            IntelligenceIngressStatus::ProviderLanguageMismatch
        );
        assert_eq!(
            intelligence_ingress_status(None, Some("en")),
            IntelligenceIngressStatus::ConfigurationUnavailable
        );
    }

    #[test]
    fn recording_stop_metadata_is_session_keyed_and_acknowledged_after_handoff() {
        let session_id = format!("meeting-intel-{}", Uuid::new_v4().simple());
        retain_recording_stop_metadata(RecordingStoppedPayload {
            message: "stopped".to_string(),
            folder_path: Some("private-folder".to_string()),
            meeting_name: Some("private-title".to_string()),
            recording_session_id: Some(session_id.clone()),
            recording_save_status: "saved".to_string(),
            issue_drafts: Vec::new(),
        });

        assert_eq!(
            get_recording_stop_metadata(session_id.clone())
                .and_then(|payload| payload.recording_session_id),
            Some(session_id.clone())
        );
        assert_eq!(
            get_latest_recording_stop_metadata().and_then(|payload| payload.recording_session_id),
            Some(session_id.clone())
        );
        assert!(acknowledge_recording_stop_metadata(session_id.clone()));
        assert!(get_recording_stop_metadata(session_id.clone()).is_none());
        assert!(!acknowledge_recording_stop_metadata(session_id));
    }

    #[test]
    fn recording_stop_diagnostics_expose_aggregate_camel_case_saver_phases() {
        let diagnostics = RecordingStopDiagnostics {
            audio_shutdown_ms: 1,
            transcription_join_ms: 2,
            model_shutdown_ms: 3,
            analytics_ms: 4,
            recording_save_ms: 5,
            recording_queue_peak: 6,
            recording_drain_completed: true,
            recording_drain_ms: 7,
            recording_final_checkpoint_ms: 8,
            recording_merge_ms: 9,
            recording_checkpoint_cleanup_ms: 10,
            recording_transcript_metadata_ms: 11,
            intelligence_cleanup_ms: 12,
            total_ms: 13,
        };

        let value = serde_json::to_value(diagnostics).unwrap();
        assert_eq!(value["recordingQueuePeak"], 6);
        assert_eq!(value["recordingDrainCompleted"], true);
        assert_eq!(value["recordingMergeMs"], 9);
        assert!(value.get("recording_queue_peak").is_none());
    }

    #[tokio::test]
    async fn timed_out_transcription_task_is_cancelled_before_shutdown_continues() {
        struct DropSignal(Arc<AtomicBool>);

        impl Drop for DropSignal {
            fn drop(&mut self) {
                self.0.store(true, Ordering::SeqCst);
            }
        }

        let dropped = Arc::new(AtomicBool::new(false));
        let dropped_by_task = dropped.clone();
        let task = tokio::spawn(async move {
            let _signal = DropSignal(dropped_by_task);
            std::future::pending::<()>().await;
        });
        tokio::task::yield_now().await;

        let outcome = join_transcription_task(task, Duration::from_millis(1)).await;

        assert_eq!(outcome, TranscriptionShutdown::TimedOut);
        assert!(dropped.load(Ordering::SeqCst));
    }
}
