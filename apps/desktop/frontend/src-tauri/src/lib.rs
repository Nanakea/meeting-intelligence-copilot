use serde::Deserialize;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex as StdMutex;
// Removed unused import

// Long recordings move short-lived audio, ndarray, JSON, and IPC allocations
// across worker threads. Use a Windows allocator designed for bounded
// cross-thread reclamation without changing recording or intelligence ownership.
#[cfg(target_os = "windows")]
#[global_allocator]
static GLOBAL_ALLOCATOR: mimalloc::MiMalloc = mimalloc::MiMalloc;

// Performance optimization: Conditional logging macros for hot paths
#[cfg(debug_assertions)]
macro_rules! perf_debug {
    ($($arg:tt)*) => {
        log::debug!($($arg)*)
    };
}

#[cfg(not(debug_assertions))]
macro_rules! perf_debug {
    ($($arg:tt)*) => {};
}

#[cfg(debug_assertions)]
macro_rules! perf_trace {
    ($($arg:tt)*) => {
        log::trace!($($arg)*)
    };
}

#[cfg(not(debug_assertions))]
macro_rules! perf_trace {
    ($($arg:tt)*) => {};
}

// Re-export async logging macros for external use (removed due to macro conflicts)

// Declare audio module
pub mod analytics;
pub mod api;
pub mod audio;
pub mod config;
pub mod console_utils;
pub mod database;
pub mod meeting_export;
pub mod meeting_intelligence_assurance;
pub mod meeting_intelligence_feedback;
pub mod meeting_intelligence_issue_draft;
pub mod meeting_intelligence_issue_export;
pub mod meeting_intelligence_pilot;
pub mod meeting_intelligence_sidecar;
pub mod meeting_local_data;
pub mod notifications;
pub mod ollama;
pub mod onboarding;
pub mod parakeet_engine;
pub mod state;
pub mod summary;
pub mod tray;
pub mod utils;
pub mod whisper_engine;

use audio::{list_audio_devices, trigger_audio_permission, AudioDevice};
use log::{error as log_error, info as log_info};
use notifications::commands::NotificationManagerState;
use std::sync::Arc;
use tauri::{AppHandle, Emitter, Manager, Runtime};
use tauri_plugin_deep_link::DeepLinkExt;
use tokio::sync::RwLock;

static RECORDING_FLAG: AtomicBool = AtomicBool::new(false);

// Global language preference storage (default to "auto-translate" for automatic translation to English)
static LANGUAGE_PREFERENCE: std::sync::LazyLock<StdMutex<String>> =
    std::sync::LazyLock::new(|| StdMutex::new("auto-translate".to_string()));

#[derive(Debug, Deserialize)]
struct RecordingArgs {
    #[allow(dead_code)] // Retained for legacy command-payload deserialization.
    save_path: String,
}

#[tauri::command]
async fn start_recording<R: Runtime>(
    app: AppHandle<R>,
    mic_device_name: Option<String>,
    system_device_name: Option<String>,
    meeting_name: Option<String>,
) -> Result<(), String> {
    log_info!(
        "Recording start requested (microphone={}, system_audio={}, meeting_name={})",
        mic_device_name.is_some(),
        system_device_name.is_some(),
        meeting_name.is_some()
    );

    if is_recording().await {
        return Err("Recording already in progress".to_string());
    }

    // Call the actual audio recording system with meeting name
    match audio::recording_commands::start_recording_with_devices_and_meeting(
        app.clone(),
        mic_device_name,
        system_device_name,
        meeting_name.clone(),
    )
    .await
    {
        Ok(_) => {
            RECORDING_FLAG.store(true, Ordering::SeqCst);
            tray::update_tray_menu(&app);

            log_info!("Recording started successfully");

            // Show recording started notification through NotificationManager
            // This respects user's notification preferences
            let notification_manager_state = app.state::<NotificationManagerState<R>>();
            if notifications::commands::show_recording_started_notification(
                &app,
                &notification_manager_state,
                meeting_name.clone(),
            )
            .await
            .is_err()
            {
                log_error!("Recording started notification was unavailable");
            } else {
                log_info!("Successfully showed recording started notification");
            }

            Ok(())
        }
        Err(error) => {
            log_error!("Audio recording could not start");
            Err(error)
        }
    }
}

#[tauri::command]
async fn stop_recording<R: Runtime>(app: AppHandle<R>, _args: RecordingArgs) -> Result<(), String> {
    log_info!("Attempting to stop recording...");

    // Check the actual audio recording system state instead of the flag
    if !audio::recording_commands::is_recording().await {
        log_info!("Recording is already stopped");
        return Ok(());
    }

    // Call the actual audio recording system to stop
    match audio::recording_commands::stop_recording(
        app.clone(),
        audio::recording_commands::RecordingArgs {
            save_path: String::new(),
        },
    )
    .await
    {
        Ok(_) => {
            RECORDING_FLAG.store(false, Ordering::SeqCst);
            tray::update_tray_menu(&app);

            // Show recording stopped notification through NotificationManager
            // This respects user's notification preferences
            let notification_manager_state = app.state::<NotificationManagerState<R>>();
            if notifications::commands::show_recording_stopped_notification(
                &app,
                &notification_manager_state,
            )
            .await
            .is_err()
            {
                log_error!("Recording stopped notification was unavailable");
            } else {
                log_info!("Successfully showed recording stopped notification");
            }

            Ok(())
        }
        Err(_) => {
            log_error!("Audio recording did not stop cleanly");
            // Still update the flag even if stopping failed
            RECORDING_FLAG.store(false, Ordering::SeqCst);
            tray::update_tray_menu(&app);
            Err("Recording could not be stopped cleanly".to_string())
        }
    }
}

#[tauri::command]
async fn is_recording() -> bool {
    audio::recording_commands::is_recording().await
}

#[tauri::command]
fn get_transcription_status() -> audio::recording_commands::TranscriptionStatus {
    audio::recording_commands::get_transcription_status()
}

fn meeting_intelligence_soak_enabled(value: Option<&str>) -> bool {
    value == Some("1")
}

fn preserve_meeting_intelligence_cache_override(
    soak_flag: Option<&str>,
    configured_cache: Option<&std::ffi::OsStr>,
) -> bool {
    meeting_intelligence_soak_enabled(soak_flag)
        && configured_cache.is_some_and(|value| !value.is_empty())
}

fn spawn_dedicated_async_worker<F>(
    name: &'static str,
    future: F,
) -> std::io::Result<std::thread::JoinHandle<()>>
where
    F: std::future::Future<Output = ()> + Send + 'static,
{
    std::thread::Builder::new()
        .name(name.to_string())
        .spawn(move || {
            let runtime = match tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
            {
                Ok(runtime) => runtime,
                Err(_) => {
                    log::error!("Meeting Intelligence supervisor runtime could not be created");
                    return;
                }
            };
            runtime.block_on(future);
        })
}

#[tauri::command]
fn is_meeting_intelligence_soak_enabled() -> bool {
    meeting_intelligence_soak_enabled(
        std::env::var("MEETING_INTELLIGENCE_INTERNAL_SOAK")
            .ok()
            .as_deref(),
    )
}

fn build_meeting_intelligence_soak_update(
    language: &str,
    sequence_id: u64,
) -> Result<audio::transcription::TranscriptUpdate, String> {
    let text = match language {
        "en" => "The inventory report does not match the ERP report.",
        "ja" => "在庫レポートとERPレポートが一致していません。",
        _ => return Err("unsupported soak language".to_string()),
    };
    let start = sequence_id as f64;
    Ok(audio::transcription::TranscriptUpdate {
        text: text.to_string(),
        timestamp: "00:00:00".to_string(),
        source: "SyntheticSoak".to_string(),
        sequence_id,
        chunk_start_time: start,
        is_partial: false,
        confidence: 1.0,
        audio_start_time: start,
        audio_end_time: start + 1.0,
        duration: 1.0,
    })
}

#[tauri::command]
async fn emit_meeting_intelligence_soak_update<R: Runtime>(
    app: AppHandle<R>,
    language: String,
) -> Result<u64, String> {
    if !is_meeting_intelligence_soak_enabled() {
        return Err("soak bridge unavailable".to_string());
    }
    if !audio::recording_commands::is_recording().await {
        return Err("recording not active".to_string());
    }
    if !matches!(language.as_str(), "en" | "ja" | "ko") {
        return Err("unsupported soak language".to_string());
    }
    let sequence_id = audio::transcription::next_sequence_id();
    let update = build_meeting_intelligence_soak_update(&language, sequence_id)?;
    let history_segment = audio::recording_saver::TranscriptSegment {
        id: format!("soak_{sequence_id}"),
        text: update.text.clone(),
        audio_start_time: update.audio_start_time,
        audio_end_time: update.audio_end_time,
        duration: update.duration,
        display_time: update.timestamp.clone(),
        confidence: update.confidence,
        sequence_id,
    };
    if !audio::recording_commands::add_internal_soak_transcript_segment(history_segment) {
        return Err("recording history unavailable".to_string());
    }
    let dispatcher = app
        .try_state::<audio::transcription::intelligence_dispatcher::IntelligenceDispatcherState>()
        .ok_or_else(|| "intelligence dispatcher unavailable".to_string())?;
    if !dispatcher.enqueue(update) {
        return Err("intelligence dispatcher rejected soak update".to_string());
    }
    Ok(sequence_id)
}

#[tauri::command]
fn read_audio_file(file_path: String) -> Result<Vec<u8>, String> {
    match std::fs::read(&file_path) {
        Ok(data) => Ok(data),
        Err(e) => Err(format!("Failed to read audio file: {}", e)),
    }
}

#[tauri::command]
async fn save_transcript(file_path: String, content: String) -> Result<(), String> {
    log_info!("Saving transcript to a user-selected local file");

    // Ensure parent directory exists
    if let Some(parent) = std::path::Path::new(&file_path).parent() {
        if !parent.exists() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("Failed to create directory: {}", e))?;
        }
    }

    // Write content to file
    std::fs::write(&file_path, content)
        .map_err(|e| format!("Failed to write transcript: {}", e))?;

    log_info!("Transcript saved successfully");
    Ok(())
}

// Audio level monitoring commands
#[tauri::command]
fn start_audio_level_monitoring<R: Runtime>(
    app: AppHandle<R>,
    device_names: Vec<String>,
) -> Result<(), String> {
    log_info!("Starting real audio level monitoring");

    audio::level_monitor::start_monitoring_thread(app, device_names)
        .map_err(|e| format!("Failed to start audio level monitoring: {}", e))
}

#[tauri::command]
fn stop_audio_level_monitoring() -> Result<(), String> {
    log_info!("Stopping audio level monitoring");

    audio::level_monitor::stop_monitoring_thread()
        .map_err(|e| format!("Failed to stop audio level monitoring: {}", e))
}

#[tauri::command]
async fn is_audio_level_monitoring() -> bool {
    audio::level_monitor::is_monitoring()
}

// Analytics commands are now handled by analytics::commands module

// Whisper commands are now handled by whisper_engine::commands module

#[tauri::command]
async fn get_audio_devices() -> Result<Vec<AudioDevice>, String> {
    list_audio_devices()
        .await
        .map_err(|e| format!("Failed to list audio devices: {}", e))
}

#[tauri::command]
async fn trigger_microphone_permission() -> Result<bool, String> {
    trigger_audio_permission()
        .map_err(|e| format!("Failed to trigger microphone permission: {}", e))
}

#[tauri::command]
async fn start_recording_with_devices<R: Runtime>(
    app: AppHandle<R>,
    mic_device_name: Option<String>,
    system_device_name: Option<String>,
) -> Result<(), String> {
    start_recording_with_devices_and_meeting(app, mic_device_name, system_device_name, None).await
}

#[tauri::command]
async fn start_recording_with_devices_and_meeting<R: Runtime>(
    app: AppHandle<R>,
    mic_device_name: Option<String>,
    system_device_name: Option<String>,
    meeting_name: Option<String>,
) -> Result<(), String> {
    log_info!(
        "Recording start requested (microphone={}, system_audio={}, meeting_name={})",
        mic_device_name.is_some(),
        system_device_name.is_some(),
        meeting_name.is_some()
    );

    // Clone meeting_name for notification use later
    let meeting_name_for_notification = meeting_name.clone();

    // Call the recording module functions that support meeting names
    let recording_result = match (mic_device_name.clone(), system_device_name.clone()) {
        (None, None) => {
            log_info!("Starting recording with default audio devices");
            audio::recording_commands::start_recording_with_meeting_name(app.clone(), meeting_name)
                .await
        }
        _ => {
            log_info!("Starting recording with explicit audio device selections");
            audio::recording_commands::start_recording_with_devices_and_meeting(
                app.clone(),
                mic_device_name,
                system_device_name,
                meeting_name,
            )
            .await
        }
    };

    match recording_result {
        Ok(_) => {
            log_info!("Recording started successfully via tauri command");

            // Show recording started notification through NotificationManager
            // This respects user's notification preferences
            let notification_manager_state = app.state::<NotificationManagerState<R>>();
            if notifications::commands::show_recording_started_notification(
                &app,
                &notification_manager_state,
                meeting_name_for_notification.clone(),
            )
            .await
            .is_err()
            {
                log_error!("Recording started notification was unavailable");
            }

            Ok(())
        }
        Err(error) => {
            log_error!("Recording could not start via the Tauri command");
            Err(error)
        }
    }
}

#[tauri::command]
async fn set_language_preference(language: String) -> Result<(), String> {
    let mut lang_pref = LANGUAGE_PREFERENCE
        .lock()
        .map_err(|e| format!("Failed to set language preference: {}", e))?;
    log_info!("Setting language preference to: {}", language);
    *lang_pref = language;
    Ok(())
}

// Internal helper function to get language preference (for use within Rust code)
pub fn get_language_preference_internal() -> Option<String> {
    LANGUAGE_PREFERENCE.lock().ok().map(|lang| lang.clone())
}

pub fn run() {
    log::set_max_level(log::LevelFilter::Info);

    let mut builder = tauri::Builder::default();

    #[cfg(any(target_os = "macos", windows, target_os = "linux"))]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, args, cwd| {
            log_info!(
                "Second app instance requested ({} argument(s), cwd available: {})",
                args.len(),
                !cwd.is_empty()
            );

            tray::focus_main_window(app);
        }));
    }

    builder
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_deep_link::init())
        .manage(whisper_engine::parallel_commands::ParallelProcessorState::new())
        .manage(Arc::new(RwLock::new(
            None::<notifications::manager::NotificationManager<tauri::Wry>>,
        )) as NotificationManagerState<tauri::Wry>)
        .manage(audio::init_system_audio_state())
        .manage(meeting_intelligence_sidecar::MeetingIntelligenceSidecarState::default())
        .manage(meeting_intelligence_feedback::IntelligenceFeedbackState::default())
        .manage(
            audio::transcription::intelligence_dispatcher::IntelligenceDispatcherState::default(),
        )
        .manage(summary::summary_engine::ModelManagerState(Arc::new(tokio::sync::Mutex::new(None))))
        .setup(|_app| {
            log::info!("Application setup complete");
            let app_handle = _app.handle().clone();
            _app.deep_link().on_open_url(move |event| {
                for url in event.urls() {
                    let value = url.as_str();
                    if value.starts_with("meeting-intelligence://oauth/slack?") {
                        let _ = app_handle.emit(
                            "meeting-intelligence-slack-callback",
                            value.to_owned(),
                        );
                    } else if value.starts_with("meeting-intelligence://oauth/enterprise?") {
                        let _ = app_handle.emit(
                            "meeting-intelligence-enterprise-callback",
                            value.to_owned(),
                        );
                    }
                }
            });
            if let Some(feedback) =
                _app.try_state::<meeting_intelligence_feedback::IntelligenceFeedbackState>()
            {
                if feedback.load_existing(_app.handle()).is_err() {
                    log::warn!("Meeting Intelligence feedback history is unavailable");
                }
            }

            // Initialize system tray
            if let Err(e) = tray::create_tray(_app.handle()) {
                log::error!("Failed to create system tray: {}", e);
            }

            // Initialize notification system with proper defaults
            log::info!("Initializing notification system...");
            let app_for_notif = _app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let notif_state = app_for_notif.state::<NotificationManagerState<tauri::Wry>>();
                match notifications::commands::initialize_notification_manager(app_for_notif.clone()).await {
                    Ok(manager) => {
                        // Set default consent and permissions on first launch
                        if let Err(e) = manager.set_consent(true).await {
                            log::error!("Failed to set initial consent: {}", e);
                        }
                        if let Err(e) = manager.request_permission().await {
                            log::error!("Failed to request initial permission: {}", e);
                        }

                        // Store the initialized manager
                        let mut state_lock = notif_state.write().await;
                        *state_lock = Some(manager);
                        log::info!("Notification system initialized with default permissions");
                    }
                    Err(e) => {
                        log::error!("Failed to initialize notification manager: {}", e);
                    }
                }
            });

            // Set models directory to use app_data_dir (unified storage location)
            whisper_engine::commands::set_models_directory(&_app.handle());

            // Initialize Whisper engine on startup
            tauri::async_runtime::spawn(async {
                if let Err(e) = whisper_engine::commands::whisper_init().await {
                    log::error!("Failed to initialize Whisper engine on startup: {}", e);
                }
            });

            // Set Parakeet models directory
            parakeet_engine::commands::set_models_directory(&_app.handle());

            // Initialize Parakeet engine on startup
            tauri::async_runtime::spawn(async {
                if let Err(e) = parakeet_engine::commands::parakeet_init().await {
                    log::error!("Failed to initialize Parakeet engine on startup: {}", e);
                }
            });

            // Initialize ModelManager for summary engine (async, non-blocking)
            let app_handle_for_model_manager = _app.handle().clone();
            tauri::async_runtime::spawn(async move {
                match summary::summary_engine::commands::init_model_manager_at_startup(&app_handle_for_model_manager).await {
                    Ok(_) => log::info!("ModelManager initialized successfully at startup"),
                    Err(e) => {
                        log::warn!("Failed to initialize ModelManager at startup: {}", e);
                        log::warn!("ModelManager will be lazy-initialized on first use");
                    }
                }
            });

            // Release builds manage the packaged backend by default; debug builds
            // remain manual unless explicitly enabled. Either mode is best-effort.
            // Failure must never block desktop startup, recording, or local STT.
            let intelligence_state = _app
                .handle()
                .state::<meeting_intelligence_sidecar::MeetingIntelligenceSidecarState>();
            let semantic_settings = meeting_intelligence_pilot::load_pilot_settings(_app.handle());
            intelligence_state.configure_semantic_beta(
                semantic_settings.semantic_beta_enabled,
                semantic_settings.semantic_model,
            );
            match _app.handle().path().app_log_dir() {
                Ok(log_directory) => {
                    if intelligence_state
                        .configure_operational_log(&log_directory)
                        .is_err()
                    {
                        log::warn!(
                            "Meeting Intelligence operational diagnostics could not be configured"
                        );
                    }
                }
                Err(_) => log::warn!(
                    "Meeting Intelligence operational diagnostics directory is unavailable"
                ),
            }
            let preserve_soak_cache = preserve_meeting_intelligence_cache_override(
                std::env::var("MEETING_INTELLIGENCE_INTERNAL_SOAK")
                    .ok()
                    .as_deref(),
                std::env::var_os("MEETING_INTELLIGENCE_CACHE_DIR").as_deref(),
            );
            if !preserve_soak_cache {
                if let Ok(local_data) = _app.handle().path().app_local_data_dir() {
                    std::env::set_var(
                        "MEETING_INTELLIGENCE_CACHE_DIR",
                        local_data.join("meeting-intelligence-cache"),
                    );
                } else {
                    log::warn!("Meeting Intelligence recovery-cache directory is unavailable");
                }
            }
            let app_handle_for_intelligence = _app.handle().clone();
            let intelligence_supervisor = async move {
                let state = app_handle_for_intelligence
                    .state::<meeting_intelligence_sidecar::MeetingIntelligenceSidecarState>();
                state.run_supervisor().await;
            };
            if spawn_dedicated_async_worker(
                "meeting-intelligence-supervisor",
                intelligence_supervisor,
            )
            .is_err()
            {
                log::error!("Meeting Intelligence supervisor thread could not be created");
            }
            let app_handle_for_intelligence_status = _app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let state = app_handle_for_intelligence_status
                    .state::<meeting_intelligence_sidecar::MeetingIntelligenceSidecarState>();
                let mut last_status = String::new();
                loop {
                    let status = state.status();
                    let serialized = serde_json::to_string(&status).unwrap_or_default();
                    if serialized != last_status {
                        last_status = serialized;
                        let _ = app_handle_for_intelligence_status.emit(
                            meeting_intelligence_sidecar::BACKEND_LIFECYCLE_EVENT,
                            status.clone(),
                        );
                    }
                    tokio::time::sleep(std::time::Duration::from_millis(250)).await;
                }
            });

            // Trigger system audio permission request on startup (similar to microphone permission)
            // #[cfg(target_os = "macos")]
            // {
            //     tauri::async_runtime::spawn(async {
            //         if let Err(e) = audio::permissions::trigger_system_audio_permission() {
            //             log::warn!("Failed to trigger system audio permission: {}", e);
            //         }
            //     });
            // }

            // Initialize database (handles first launch detection and conditional setup)
            tauri::async_runtime::block_on(async {
                database::setup::initialize_database_on_startup(&_app.handle()).await
            })
            .expect("Failed to initialize database");

            let app_handle_for_retention = _app.handle().clone();
            tauri::async_runtime::spawn(async move {
                match meeting_intelligence_pilot::apply_saved_meeting_intelligence_retention(
                    app_handle_for_retention,
                )
                .await
                {
                    Ok(result) if result.deleted_meetings > 0 || result.file_cleanup_failures > 0 => {
                        log::info!(
                            "Applied local retention: deleted={}, retained_for_retry={}",
                            result.deleted_meetings,
                            result.file_cleanup_failures
                        );
                    }
                    Ok(_) => {}
                    Err(_) => log::warn!("Saved local retention policy will retry later"),
                }
            });

            // Initialize bundled templates directory for dynamic template discovery
            log::info!("Initializing bundled templates directory...");
            if let Ok(resource_path) = _app.handle().path().resource_dir() {
                let templates_dir = resource_path.join("templates");
                log::info!("Setting bundled templates directory to: {:?}", templates_dir);
                summary::templates::set_bundled_templates_dir(templates_dir);
            } else {
                log::warn!("Failed to resolve resource directory for templates");
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    if let Err(e) = window.hide() {
                        log::error!("Failed to hide main window on close request: {}", e);
                    } else {
                        log::info!("Main window hidden to tray on close request");
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            start_recording,
            stop_recording,
            is_recording,
            is_meeting_intelligence_soak_enabled,
            emit_meeting_intelligence_soak_update,
            get_transcription_status,
            read_audio_file,
            save_transcript,
            analytics::commands::init_analytics,
            analytics::commands::disable_analytics,
            analytics::commands::track_event,
            analytics::commands::identify_user,
            analytics::commands::track_meeting_started,
            analytics::commands::track_recording_started,
            analytics::commands::track_recording_stopped,
            analytics::commands::track_meeting_deleted,
            analytics::commands::track_settings_changed,
            analytics::commands::track_feature_used,
            analytics::commands::is_analytics_enabled,
            analytics::commands::start_analytics_session,
            analytics::commands::end_analytics_session,
            analytics::commands::track_daily_active_user,
            analytics::commands::track_user_first_launch,
            analytics::commands::is_analytics_session_active,
            analytics::commands::track_summary_generation_started,
            analytics::commands::track_summary_generation_completed,
            analytics::commands::track_summary_regenerated,
            analytics::commands::track_model_changed,
            analytics::commands::track_custom_prompt_used,
            analytics::commands::track_meeting_ended,
            analytics::commands::track_analytics_enabled,
            analytics::commands::track_analytics_disabled,
            analytics::commands::track_analytics_transparency_viewed,
            whisper_engine::commands::whisper_init,
            whisper_engine::commands::whisper_get_available_models,
            whisper_engine::commands::whisper_load_model,
            whisper_engine::commands::whisper_get_current_model,
            whisper_engine::commands::whisper_is_model_loaded,
            whisper_engine::commands::whisper_has_available_models,
            whisper_engine::commands::whisper_validate_model_ready,
            whisper_engine::commands::whisper_transcribe_audio,
            whisper_engine::commands::whisper_get_models_directory,
            whisper_engine::commands::whisper_download_model,
            whisper_engine::commands::whisper_cancel_download,
            whisper_engine::commands::whisper_delete_corrupted_model,
            // Parakeet engine commands
            parakeet_engine::commands::parakeet_init,
            parakeet_engine::commands::parakeet_get_available_models,
            parakeet_engine::commands::parakeet_load_model,
            parakeet_engine::commands::parakeet_get_current_model,
            parakeet_engine::commands::parakeet_is_model_loaded,
            parakeet_engine::commands::parakeet_has_available_models,
            parakeet_engine::commands::parakeet_validate_model_ready,
            parakeet_engine::commands::parakeet_transcribe_audio,
            parakeet_engine::commands::parakeet_get_models_directory,
            parakeet_engine::commands::parakeet_download_model,
            parakeet_engine::commands::parakeet_retry_download,
            parakeet_engine::commands::parakeet_cancel_download,
            parakeet_engine::commands::parakeet_delete_corrupted_model,
            parakeet_engine::commands::open_parakeet_models_folder,
            // Parallel processing commands
            whisper_engine::parallel_commands::initialize_parallel_processor,
            whisper_engine::parallel_commands::start_parallel_processing,
            whisper_engine::parallel_commands::pause_parallel_processing,
            whisper_engine::parallel_commands::resume_parallel_processing,
            whisper_engine::parallel_commands::stop_parallel_processing,
            whisper_engine::parallel_commands::get_parallel_processing_status,
            whisper_engine::parallel_commands::get_system_resources,
            whisper_engine::parallel_commands::check_resource_constraints,
            whisper_engine::parallel_commands::calculate_optimal_workers,
            whisper_engine::parallel_commands::prepare_audio_chunks,
            whisper_engine::parallel_commands::test_parallel_processing_setup,
            get_audio_devices,
            trigger_microphone_permission,
            start_recording_with_devices,
            start_recording_with_devices_and_meeting,
            start_audio_level_monitoring,
            stop_audio_level_monitoring,
            is_audio_level_monitoring,
            // Recording pause/resume commands
            audio::recording_commands::pause_recording,
            audio::recording_commands::resume_recording,
            audio::recording_commands::is_recording_paused,
            audio::recording_commands::get_recording_state,
            audio::recording_commands::get_recording_stop_metadata,
            audio::recording_commands::get_latest_recording_stop_metadata,
            audio::recording_commands::get_recording_stop_transcripts,
            audio::recording_commands::acknowledge_recording_stop_metadata,
            audio::recording_commands::get_recording_stop_diagnostics,
            audio::recording_commands::get_meeting_folder_path,
            // Reload sync commands (retrieve transcript history and meeting name)
            audio::recording_commands::get_transcript_history,
            audio::recording_commands::get_recording_meeting_name,
            audio::recording_commands::get_recording_intelligence_session_id,
            // Device monitoring commands (AirPods/Bluetooth disconnect/reconnect)
            audio::recording_commands::poll_audio_device_events,
            audio::recording_commands::get_reconnection_status,
            audio::recording_commands::attempt_device_reconnect,
            // Playback device detection (Bluetooth warning)
            audio::recording_commands::get_active_audio_output,
            // Audio recovery commands (for transcript recovery feature)
            audio::incremental_saver::recover_audio_from_checkpoints,
            audio::incremental_saver::cleanup_checkpoints,
            audio::incremental_saver::has_audio_checkpoints,
            audio::incremental_saver::has_recoverable_audio,
            console_utils::show_console,
            console_utils::hide_console,
            console_utils::toggle_console,
            ollama::get_ollama_models,
            ollama::pull_ollama_model,
            ollama::delete_ollama_model,
            ollama::get_ollama_model_context,
            api::api_get_meetings,
            api::api_search_transcripts,
            api::api_get_profile,
            api::api_save_profile,
            api::api_update_profile,
            api::api_get_model_config,
            api::api_save_model_config,
            api::api_get_api_key,
            // api::api_get_auto_generate_setting,
            // api::api_save_auto_generate_setting,
            api::api_get_transcript_config,
            api::api_save_transcript_config,
            api::api_get_transcript_api_key,
            api::api_delete_meeting,
            api::api_get_meeting,
            api::api_get_meeting_metadata,
            api::api_get_meeting_transcripts,
            api::api_save_meeting_title,
            api::api_save_transcript,
            meeting_intelligence_issue_draft::api_get_meeting_issue_draft,
            meeting_intelligence_issue_draft::api_save_meeting_issue_draft,
            meeting_intelligence_issue_draft::api_save_meeting_issue_drafts,
            meeting_intelligence_issue_draft::api_get_meeting_issue_drafts,
            meeting_intelligence_issue_draft::api_get_issue_draft_review_overlays,
            meeting_intelligence_issue_draft::api_save_issue_draft_review_overlay,
            meeting_intelligence_issue_export::export_issue_draft,
            meeting_intelligence_issue_export::export_issue_draft_batch,
            meeting_intelligence_issue_export::export_governance_review_pack,
            meeting_intelligence_issue_export::get_issue_export_settings,
            meeting_intelligence_issue_export::set_issue_export_settings,
            meeting_export::export_local_meeting,
            api::open_meeting_folder,
            api::test_backend_connection,
            api::debug_backend_connection,
            api::open_external_url,
            // Summary commands
            summary::commands::api_process_transcript,
            summary::commands::api_get_summary,
            summary::commands::api_save_meeting_summary,
            summary::commands::api_get_meeting_summary_language,
            summary::commands::api_save_meeting_summary_language,
            summary::commands::api_get_meeting_detected_summary_language,
            summary::commands::api_save_meeting_detected_summary_language,
            summary::commands::api_detect_transcript_summary_language,
            summary::commands::api_cancel_summary,
            // Template commands
            summary::template_commands::api_list_templates,
            summary::template_commands::api_get_template_details,
            summary::template_commands::api_validate_template,
            // Built-in AI commands
            summary::summary_engine::commands::builtin_ai_list_models,
            summary::summary_engine::commands::builtin_ai_get_model_info,
            summary::summary_engine::commands::builtin_ai_download_model,
            summary::summary_engine::commands::builtin_ai_cancel_download,
            summary::summary_engine::commands::builtin_ai_delete_model,
            summary::summary_engine::commands::builtin_ai_is_model_ready,
            summary::summary_engine::commands::builtin_ai_get_available_summary_model,
            summary::summary_engine::commands::builtin_ai_get_recommended_model,
            audio::recording_preferences::get_recording_preferences,
            audio::recording_preferences::set_recording_preferences,
            audio::recording_preferences::get_default_recordings_folder_path,
            audio::recording_preferences::open_recordings_folder,
            audio::recording_preferences::select_recording_folder,
            audio::recording_preferences::get_available_audio_backends,
            audio::recording_preferences::get_current_audio_backend,
            audio::recording_preferences::set_audio_backend,
            audio::recording_preferences::get_audio_backend_info,
            // Language preference commands
            set_language_preference,
            // Notification system commands
            notifications::commands::get_notification_settings,
            notifications::commands::set_notification_settings,
            notifications::commands::request_notification_permission,
            notifications::commands::show_notification,
            notifications::commands::show_test_notification,
            notifications::commands::is_dnd_active,
            notifications::commands::get_system_dnd_status,
            notifications::commands::set_manual_dnd,
            notifications::commands::set_notification_consent,
            notifications::commands::clear_notifications,
            notifications::commands::is_notification_system_ready,
            notifications::commands::initialize_notification_manager_manual,
            notifications::commands::test_notification_with_auto_consent,
            notifications::commands::get_notification_stats,
            // System audio capture commands
            audio::system_audio_commands::start_system_audio_capture_command,
            audio::system_audio_commands::list_system_audio_devices_command,
            audio::system_audio_commands::check_system_audio_permissions_command,
            audio::system_audio_commands::start_system_audio_monitoring,
            audio::system_audio_commands::stop_system_audio_monitoring,
            audio::system_audio_commands::get_system_audio_monitoring_status,
            // Screen Recording permission commands
            audio::permissions::check_screen_recording_permission_command,
            audio::permissions::request_screen_recording_permission_command,
            audio::permissions::trigger_system_audio_permission_command,
            // Database import commands
            database::commands::check_first_launch,
            database::commands::select_legacy_database_path,
            database::commands::detect_legacy_database,
            database::commands::check_default_legacy_database,
            database::commands::check_homebrew_database,
            database::commands::import_and_initialize_database,
            database::commands::initialize_fresh_database,
            // Database and Models path commands
            database::commands::get_database_directory,
            database::commands::open_database_folder,
            whisper_engine::commands::open_models_folder,
            // Onboarding commands
            onboarding::get_onboarding_status,
            onboarding::save_onboarding_status_cmd,
            onboarding::reset_onboarding_status_cmd,
            onboarding::complete_onboarding,
            // System settings commands
            #[cfg(target_os = "macos")]
            utils::open_system_settings,
            // Retranscription commands
            audio::retranscription::start_retranscription_command,
            audio::retranscription::cancel_retranscription_command,
            audio::retranscription::is_retranscription_in_progress_command,
            // Import audio commands
            audio::import::select_and_validate_audio_command,
            audio::import::validate_audio_file_command,
            audio::import::start_import_audio_command,
            audio::import::cancel_import_command,
            audio::import::is_import_in_progress_command,
            meeting_intelligence_sidecar::get_meeting_intelligence_transport,
            meeting_intelligence_sidecar::get_meeting_intelligence_backend_status,
            meeting_intelligence_sidecar::retry_meeting_intelligence_backend,
            meeting_intelligence_sidecar::restart_meeting_intelligence_backend_for_configuration,
            meeting_intelligence_assurance::configure_meeting_intelligence_assurance_schedule,
            audio::transcription::intelligence_dispatcher::get_meeting_intelligence_diagnostics,
            audio::transcription::intelligence_dispatcher::repair_meeting_intelligence_dispatcher,
            meeting_intelligence_feedback::record_meeting_intelligence_feedback,
            meeting_intelligence_feedback::is_meeting_intelligence_suggestion_dismissed,
            meeting_intelligence_feedback::get_meeting_intelligence_feedback_summary,
            meeting_intelligence_feedback::delete_all_meeting_intelligence_feedback,
            meeting_intelligence_pilot::get_meeting_intelligence_pilot_settings,
            meeting_intelligence_pilot::set_meeting_intelligence_pilot_settings,
            meeting_intelligence_pilot::confirm_meeting_recording_consent,
            meeting_intelligence_pilot::get_meeting_intelligence_data_directory,
            meeting_intelligence_pilot::open_meeting_intelligence_data_directory,
            meeting_intelligence_pilot::apply_meeting_intelligence_retention,
            meeting_intelligence_pilot::delete_unsaved_recovery_folder,
            meeting_intelligence_pilot::delete_all_local_meeting_data,
            meeting_intelligence_pilot::get_meeting_intelligence_preflight,
            meeting_intelligence_pilot::run_meeting_intelligence_spoken_preflight,
            meeting_intelligence_pilot::export_meeting_intelligence_diagnostics,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app_handle, event| {
            match event {
                #[cfg(target_os = "macos")]
                tauri::RunEvent::Reopen { .. } => {
                    tray::focus_main_window(_app_handle);
                }
                tauri::RunEvent::Exit => {
                    log::info!("Application exiting, cleaning up resources...");
                    tauri::async_runtime::block_on(async {
                        // Clean up database connection and checkpoint WAL
                        if let Some(app_state) = _app_handle.try_state::<state::AppState>() {
                            log::info!("Starting database cleanup...");
                            if let Err(e) = app_state.db_manager.cleanup().await {
                                log::error!("Failed to cleanup database: {}", e);
                            } else {
                                log::info!("Database cleanup completed successfully");
                            }
                        } else {
                            log::warn!("AppState not available for database cleanup (likely first launch)");
                        }

                        // Clean up sidecar
                        log::info!("Cleaning up sidecar...");
                        if let Err(e) = summary::summary_engine::force_shutdown_sidecar().await {
                            log::error!("Failed to force shutdown sidecar: {}", e);
                        }

                        if let Some(state) = _app_handle
                            .try_state::<meeting_intelligence_sidecar::MeetingIntelligenceSidecarState>()
                        {
                            if let Err(error) = state.stop().await {
                                log::error!(
                                    "Failed to stop Meeting Intelligence backend process tree: {error}"
                                );
                            }
                        }
                    });
                    log::info!("Application cleanup complete");
                }
                _ => {}
            }
        });
}

#[cfg(test)]
mod meeting_intelligence_soak_tests {
    use super::{
        build_meeting_intelligence_soak_update, meeting_intelligence_soak_enabled,
        preserve_meeting_intelligence_cache_override, spawn_dedicated_async_worker,
    };
    use std::ffi::OsStr;
    use std::sync::mpsc;
    use std::time::Duration;

    #[test]
    fn packaged_soak_control_requires_explicit_opt_in() {
        assert!(meeting_intelligence_soak_enabled(Some("1")));
        assert!(!meeting_intelligence_soak_enabled(None));
        assert!(!meeting_intelligence_soak_enabled(Some("0")));
        assert!(!meeting_intelligence_soak_enabled(Some("true")));
    }

    #[test]
    fn cache_override_is_preserved_only_for_an_explicit_internal_soak() {
        let cache = OsStr::new("isolated-cache");
        assert!(preserve_meeting_intelligence_cache_override(
            Some("1"),
            Some(cache)
        ));
        assert!(!preserve_meeting_intelligence_cache_override(
            Some("0"),
            Some(cache)
        ));
        assert!(!preserve_meeting_intelligence_cache_override(
            Some("1"),
            None
        ));
        assert!(!preserve_meeting_intelligence_cache_override(
            Some("1"),
            Some(OsStr::new(""))
        ));
    }

    #[test]
    fn synthetic_soak_updates_are_bounded_and_sequence_aware() {
        let update = build_meeting_intelligence_soak_update("en", 42).unwrap();
        assert_eq!(update.sequence_id, 42);
        assert!(!update.is_partial);
        assert_eq!(update.source, "SyntheticSoak");
        assert!(build_meeting_intelligence_soak_update("auto", 43).is_err());
    }

    #[test]
    fn intelligence_supervisor_worker_uses_its_own_named_runtime_thread() {
        let (sender, receiver) = mpsc::channel();
        let worker =
            spawn_dedicated_async_worker("meeting-intelligence-test-supervisor", async move {
                let name = std::thread::current().name().map(str::to_string);
                sender.send(name).unwrap();
                tokio::time::sleep(Duration::from_millis(10)).await;
            })
            .unwrap();

        assert_eq!(
            receiver.recv_timeout(Duration::from_secs(1)).unwrap(),
            Some("meeting-intelligence-test-supervisor".to_string())
        );
        worker.join().unwrap();
    }
}
