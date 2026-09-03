// Commit name to recover the serial whisper engine processing for smaller meetings [Slower processing but dooes not fail] - "before parallel processing implementation"

use super::acceleration::{whisper_context_acceleration_for, WhisperCompiledBackend};
use super::model_manifest::{
    approved_artifact, digest_matches, has_ggml_header, model_readiness, replace_atomically,
    WhisperModelIntegrity, WhisperModelReadiness,
};
use crate::config::WHISPER_MODEL_CATALOG;
use anyhow::{anyhow, Context, Result};
use reqwest::Client;
use ring::digest;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::ffi::{c_char, c_void};
use std::path::PathBuf;
use std::sync::{Arc, Once};
use tokio::fs;
use tokio::io::AsyncWriteExt;
use tokio::sync::RwLock;
use whisper_rs::{FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters};

static WHISPER_LOG_SILENCER: Once = Once::new();

unsafe extern "C" fn discard_whisper_log(
    _level: whisper_rs::whisper_rs_sys::ggml_log_level,
    _text: *const c_char,
    _user_data: *mut c_void,
) {
}

fn silence_whisper_logs() {
    WHISPER_LOG_SILENCER.call_once(|| unsafe {
        // whisper.cpp debug output includes decoded tokens and must never reach app logs.
        whisper_rs::set_log_callback(Some(discard_whisper_log), std::ptr::null_mut());
    });
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ModelStatus {
    Available,
    Missing,
    Downloading {
        progress: u8,
    },
    Error(String),
    Corrupted {
        file_size: u64,
        expected_min_size: u64,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelInfo {
    pub name: String,
    pub path: PathBuf,
    pub size_mb: u32,
    pub accuracy: String,
    pub speed: String,
    pub status: ModelStatus,
    pub description: String,
}

pub struct WhisperEngine {
    models_dir: PathBuf,
    current_context: Arc<RwLock<Option<WhisperContext>>>,
    current_model: Arc<RwLock<Option<String>>>,
    available_models: Arc<RwLock<HashMap<String, ModelInfo>>>,
    // State tracking for smart logging
    last_transcription_was_short: Arc<RwLock<bool>>,
    short_audio_warning_logged: Arc<RwLock<bool>>,
    // Performance optimization: reduce logging frequency
    transcription_count: Arc<RwLock<u64>>,
    // Download cancellation tracking
    cancel_download_flag: Arc<RwLock<Option<String>>>, // Model name being cancelled
    // Active downloads tracking to prevent concurrent downloads
    active_downloads: Arc<RwLock<HashSet<String>>>, // Set of models currently being downloaded
}

impl WhisperEngine {
    /// Detect available GPU acceleration capabilities
    fn detect_gpu_acceleration() -> bool {
        match WhisperCompiledBackend::current() {
            WhisperCompiledBackend::Metal => {
                log::info!("macOS detected - attempting to enable Metal GPU acceleration");
                true
            }
            WhisperCompiledBackend::Cuda => {
                log::info!("CUDA feature enabled - attempting GPU acceleration");
                true
            }
            WhisperCompiledBackend::Vulkan => {
                log::info!("Vulkan feature enabled - attempting GPU acceleration");
                true
            }
            WhisperCompiledBackend::HipBlas => {
                log::info!("HIP BLAS feature enabled - attempting GPU acceleration");
                true
            }
            WhisperCompiledBackend::Cpu => {
                log::info!("No GPU acceleration features detected - using CPU processing");
                false
            }
        }
    }

    pub fn new() -> Result<Self> {
        Self::new_with_models_dir(None)
    }

    /// Create a new WhisperEngine with optional custom models directory
    /// If models_dir is None, uses default location (app data dir for production, local for dev)
    pub fn new_with_models_dir(models_dir: Option<PathBuf>) -> Result<Self> {
        silence_whisper_logs();

        // PERFORMANCE: Suppress verbose whisper.cpp and Metal logs
        // These C library logs bypass Rust logging and clutter output
        // Set environment variables to reduce C library verbosity
        std::env::set_var("GGML_METAL_LOG_LEVEL", "1"); // 0=off, 1=error, 2=warn, 3=info
        std::env::set_var("WHISPER_LOG_LEVEL", "1"); // Reduce whisper.cpp verbosity

        let models_dir = if let Some(dir) = models_dir {
            // Use provided directory (for production with app_data_dir)
            dir
        } else {
            // Fallback: determine based on debug/release mode
            let current_dir = std::env::current_dir()
                .map_err(|e| anyhow!("Failed to get current directory: {}", e))?;

            // Development: Use frontend/models or backend directories
            // Production: Use system directories (should be overridden by caller)
            if cfg!(debug_assertions) {
                // Development mode - try frontend and backend directories
                if current_dir.join("models").exists() {
                    current_dir.join("models")
                } else if current_dir.join("../models").exists() {
                    current_dir.join("../models")
                } else if current_dir
                    .join("backend/whisper-server-package/models")
                    .exists()
                {
                    current_dir.join("backend/whisper-server-package/models")
                } else if current_dir
                    .join("../backend/whisper-server-package/models")
                    .exists()
                {
                    current_dir.join("../backend/whisper-server-package/models")
                } else {
                    // Create models directory in current directory for development
                    current_dir.join("models")
                }
            } else {
                // Production mode fallback (shouldn't reach here, caller should provide path)
                log::warn!("WhisperEngine: No models directory provided, using fallback path");
                dirs::data_dir()
                    .or_else(|| dirs::home_dir())
                    .ok_or_else(|| anyhow!("Could not find system data directory"))?
                    .join("Meetily")
                    .join("models")
            }
        };

        log::info!("WhisperEngine using configured local models directory");
        log::info!("Debug mode: {}", cfg!(debug_assertions));

        // Log acceleration capabilities
        let gpu_support = Self::detect_gpu_acceleration();
        log::info!(
            "Hardware acceleration support: {}",
            if gpu_support { "enabled" } else { "disabled" }
        );

        #[cfg(feature = "metal")]
        log::info!("Apple Metal GPU support: enabled");

        #[cfg(feature = "openblas")]
        log::info!("OpenBLAS CPU optimization: enabled");

        #[cfg(feature = "coreml")]
        log::info!("Apple CoreML support: enabled");

        #[cfg(feature = "cuda")]
        log::info!("NVIDIA CUDA support: enabled");

        #[cfg(feature = "vulkan")]
        log::info!("Vulkan GPU support: enabled");

        #[cfg(feature = "openmp")]
        log::info!("OpenMP parallel processing: enabled");

        let engine = Self {
            models_dir,
            current_context: Arc::new(RwLock::new(None)),
            current_model: Arc::new(RwLock::new(None)),
            available_models: Arc::new(RwLock::new(HashMap::new())),
            // Initialize state tracking
            last_transcription_was_short: Arc::new(RwLock::new(false)),
            short_audio_warning_logged: Arc::new(RwLock::new(false)),
            // Performance optimization: reduce logging frequency
            transcription_count: Arc::new(RwLock::new(0)),
            // Initialize cancellation tracking
            cancel_download_flag: Arc::new(RwLock::new(None)),
            // Initialize active downloads tracking
            active_downloads: Arc::new(RwLock::new(HashSet::new())),
        };

        Ok(engine)
    }

    pub async fn discover_models(&self) -> Result<Vec<ModelInfo>> {
        let models_dir = &self.models_dir;
        let mut models = Vec::new();
        // Use centralized model catalog from config.rs
        let model_configs = WHISPER_MODEL_CATALOG;

        for &(name, filename, size_mb, accuracy, speed, description) in model_configs {
            let model_path = models_dir.join(filename);
            let readiness = model_readiness(name, model_path.clone(), None).await;
            let status = match readiness.integrity {
                WhisperModelIntegrity::Verified => ModelStatus::Available,
                WhisperModelIntegrity::Missing => ModelStatus::Missing,
                _ => ModelStatus::Corrupted {
                    file_size: readiness.actual_bytes.unwrap_or_default(),
                    expected_min_size: readiness.expected_bytes.unwrap_or_default(),
                },
            };

            let model_info = ModelInfo {
                name: name.to_string(),
                path: model_path,
                size_mb: size_mb as u32,
                accuracy: accuracy.to_string(),
                speed: speed.to_string(),
                status,
                description: description.to_string(),
            };

            models.push(model_info);
        }

        // Update internal cache
        let mut available_models = self.available_models.write().await;
        available_models.clear();
        for model in &models {
            available_models.insert(model.name.clone(), model.clone());
        }

        Ok(models)
    }

    pub async fn verified_model_readiness(
        &self,
        model_name: &str,
        language: Option<&str>,
    ) -> WhisperModelReadiness {
        let model_path = approved_artifact(model_name)
            .map(|(_, artifact)| self.models_dir.join(artifact.file_name))
            .unwrap_or_else(|_| self.models_dir.join(".unapproved-model"));
        model_readiness(model_name, model_path, language).await
    }

    pub async fn load_model(&self, model_name: &str) -> Result<()> {
        let model_info = self
            .available_models
            .read()
            .await
            .get(model_name)
            .cloned()
            .ok_or_else(|| anyhow!("Model {} not found", model_name))?;

        match model_info.status {
            ModelStatus::Available => {
                let readiness = self.verified_model_readiness(model_name, None).await;
                if readiness.integrity != WhisperModelIntegrity::Verified {
                    return Err(anyhow!("model artifact is not verified"));
                }
                // FIX 5: Check if this model is already loaded
                if let Some(current_model) = self.current_model.read().await.as_ref() {
                    if current_model == model_name {
                        log::info!("Model {} is already loaded, skipping reload", model_name);
                        return Ok(());
                    }

                    // FIX 5: Unload current model before loading new one
                    log::info!(
                        "Unloading current model '{}' before loading '{}'",
                        current_model,
                        model_name
                    );
                    self.unload_model().await;
                }

                log::info!("Loading model: {}", model_name);

                // PERFORMANCE OPTIMIZATION: Use comprehensive hardware profile for optimal GPU configuration
                let hardware_profile = crate::audio::HardwareProfile::detect();
                let adaptive_config = hardware_profile.get_whisper_config();
                let acceleration = whisper_context_acceleration_for(
                    WhisperCompiledBackend::current(),
                    hardware_profile.gpu_type,
                    hardware_profile.performance_tier,
                );

                let context_param = WhisperContextParameters {
                    use_gpu: acceleration.use_gpu,
                    gpu_device: acceleration.gpu_device,
                    flash_attn: acceleration.flash_attn,
                    ..Default::default()
                };

                log::info!(
                    "Whisper acceleration decision: compiled_backend={} runtime_detected_gpu={:?} use_gpu={} flash_attn={} gpu_device={}",
                    acceleration.compiled_backend.as_str(),
                    acceleration.runtime_detected_gpu,
                    acceleration.use_gpu,
                    acceleration.flash_attn,
                    acceleration.gpu_device,
                );

                // PERFORMANCE: Suppress verbose C library logs during model loading
                // This hides the excessive Metal/GGML initialization logs in release builds
                let ctx = {
                    // let _suppressor = crate::whisper_engine::StderrSuppressor::new();

                    // Load whisper context with hardware-optimized parameters
                    WhisperContext::new_with_params(
                        &model_info.path.to_string_lossy(),
                        context_param,
                    )
                    .map_err(|e| anyhow!("Failed to load model {}: {}", model_name, e))?
                    // Suppressor dropped here, stderr restored
                };

                // Update current context and model
                *self.current_context.write().await = Some(ctx);
                *self.current_model.write().await = Some(model_name.to_string());

                // Enhanced acceleration status reporting
                let acceleration_status = acceleration.status_label();

                log::info!("Successfully loaded model: {} with {} (Performance Tier: {:?}, Beam Size: {}, Threads: {:?})",
                          model_name, acceleration_status, hardware_profile.performance_tier,
                          adaptive_config.beam_size, adaptive_config.max_threads);
                Ok(())
            }
            ModelStatus::Missing => Err(anyhow!("Model {} is not downloaded", model_name)),
            ModelStatus::Downloading { .. } => {
                Err(anyhow!("Model {} is currently downloading", model_name))
            }
            ModelStatus::Error(ref err) => Err(anyhow!("Model {} has error: {}", model_name, err)),
            ModelStatus::Corrupted { .. } => Err(anyhow!(
                "Model {} is corrupted and cannot be loaded",
                model_name
            )),
        }
    }

    pub async fn unload_model(&self) -> bool {
        let mut ctx_guard = self.current_context.write().await;
        let unloaded = ctx_guard.take().is_some();
        if unloaded {
            log::info!("📉Whisper model unloaded");
        }

        let mut model_name_guard = self.current_model.write().await;
        model_name_guard.take();

        unloaded
    }

    pub async fn get_current_model(&self) -> Option<String> {
        self.current_model.read().await.clone()
    }

    pub async fn is_model_loaded(&self) -> bool {
        self.current_context.read().await.is_some()
    }

    // Enhanced function to clean repetitive text patterns and meaningless outputs
    fn clean_repetitive_text(text: &str) -> String {
        if text.is_empty() {
            return String::new();
        }

        // Check for obviously meaningless patterns first
        if Self::is_meaningless_output(text) {
            // Performance optimization: reduce meaningless output logging to debug level
            perf_debug!(
                "Detected meaningless transcription output (characters: {}), returning empty",
                text.chars().count()
            );
            return String::new();
        }

        let words: Vec<&str> = text.split_whitespace().collect();
        if words.len() < 3 {
            return text.to_string();
        }

        // Enhanced repetition detection with sliding window
        let cleaned_words = Self::remove_word_repetitions(&words);

        // Remove phrase repetitions with more sophisticated detection
        let cleaned_words = Self::remove_phrase_repetitions(&cleaned_words);

        // Check for overall repetition ratio
        let final_text = cleaned_words.join(" ");
        if Self::calculate_repetition_ratio(&final_text) > 0.7 {
            // Performance optimization: reduce repetition ratio logging to debug level
            perf_debug!(
                "High repetition ratio detected (characters: {}), filtering output",
                final_text.chars().count()
            );
            return String::new();
        }

        final_text
    }

    // Check for obviously meaningless patterns
    fn is_meaningless_output(text: &str) -> bool {
        let text_lower = text.to_lowercase();

        // Check for common meaningless patterns
        let meaningless_patterns = [
            "thank you for watching",
            "thanks for watching",
            "like and subscribe",
            "music playing",
            "applause",
            "laughter",
            "um um um",
            "uh uh uh",
            "ah ah ah",
        ];

        for pattern in &meaningless_patterns {
            if text_lower.contains(pattern) {
                return true;
            }
        }

        // Check if text is mostly the same character or very short repetitive patterns
        let unique_chars: HashSet<char> = text.chars().collect();
        if unique_chars.len() <= 3 && text.len() > 10 {
            return true;
        }

        false
    }

    // Enhanced word repetition removal
    fn remove_word_repetitions<'a>(words: &'a [&'a str]) -> Vec<&'a str> {
        let mut cleaned_words = Vec::new();
        let mut i = 0;

        while i < words.len() {
            let current_word = words[i];
            let mut repeat_count = 1;

            // Count consecutive repetitions of the same word
            while i + repeat_count < words.len() && words[i + repeat_count] == current_word {
                repeat_count += 1;
            }

            // Be more aggressive: if word is repeated 2+ times, only keep one instance
            if repeat_count >= 2 {
                cleaned_words.push(current_word);
                i += repeat_count;
            } else {
                cleaned_words.push(current_word);
                i += 1;
            }
        }

        cleaned_words
    }

    // Enhanced phrase repetition removal with variable length detection
    fn remove_phrase_repetitions<'a>(words: &'a [&'a str]) -> Vec<&'a str> {
        if words.len() < 4 {
            return words.to_vec();
        }

        let mut final_words = Vec::new();
        let mut i = 0;

        while i < words.len() {
            let mut phrase_found = false;

            // Check for 2-word to 5-word phrase repetitions
            for phrase_len in 2..=std::cmp::min(5, (words.len() - i) / 2) {
                if i + phrase_len * 2 <= words.len() {
                    let phrase1 = &words[i..i + phrase_len];
                    let phrase2 = &words[i + phrase_len..i + phrase_len * 2];

                    if phrase1 == phrase2 {
                        // Add the phrase once and skip the repetition
                        final_words.extend_from_slice(phrase1);
                        i += phrase_len * 2;
                        phrase_found = true;
                        break;
                    }
                }
            }

            if !phrase_found {
                final_words.push(words[i]);
                i += 1;
            }
        }

        final_words
    }

    // Calculate repetition ratio in text
    fn calculate_repetition_ratio(text: &str) -> f32 {
        let words: Vec<&str> = text.split_whitespace().collect();
        if words.len() < 4 {
            return 0.0;
        }

        let mut word_counts = HashMap::new();
        for word in &words {
            *word_counts.entry(word.to_lowercase()).or_insert(0) += 1;
        }

        let total_words = words.len() as f32;
        let repeated_words: usize = word_counts
            .values()
            .map(|&count| if count > 1 { count - 1 } else { 0 })
            .sum();

        repeated_words as f32 / total_words
    }

    /// Transcribe audio with streaming support for partial results and adaptive quality
    pub async fn transcribe_audio_with_confidence(
        &self,
        audio_data: Vec<f32>,
        language: Option<String>,
    ) -> Result<(String, f32, bool)> {
        let ctx_lock = self.current_context.read().await;
        let ctx = ctx_lock
            .as_ref()
            .ok_or_else(|| anyhow!("No model loaded. Please load a model first."))?;

        // Get adaptive configuration based on hardware
        let hardware_profile = crate::audio::HardwareProfile::detect();
        let adaptive_config = hardware_profile.get_whisper_config();

        // ADAPTIVE parameters - optimized for current hardware
        let mut params = FullParams::new(SamplingStrategy::BeamSearch {
            beam_size: adaptive_config.beam_size as i32,
            patience: 1.0,
        });

        // Configure with adaptive settings
        // If language is "auto" or None, use automatic language detection (pass None)
        // If language is "auto-translate", enable translation to English
        // Otherwise, use the specified language code
        let (language_code, should_translate) = match language.as_deref() {
            Some("auto") | None => (None, false),
            Some("auto-translate") => (None, true),
            Some(lang) => (Some(lang), false),
        };
        params.set_language(language_code);
        params.set_translate(should_translate);

        // CRITICAL: Disable timestamp tokens to prevent whisper.cpp chunking heuristics
        // The "single timestamp ending - skip entire chunk" optimization incorrectly discards
        // complete, valid transcriptions. Disabling timestamps forces whisper to return ALL text.
        params.set_no_timestamps(true); // Prevent timestamp-based segment skipping
        params.set_token_timestamps(true); // Keep for any timestamp-aware features

        // PERFORMANCE: Disable ALL whisper.cpp internal printing
        // This reduces C library log spam significantly
        params.set_print_special(false); // Don't print special tokens
        params.set_print_progress(false); // Don't print progress
        params.set_print_realtime(false); // Don't print realtime info
        params.set_print_timestamps(false); // Don't print timestamps

        // Additional suppression to reduce C library verbosity
        params.set_suppress_blank(true);
        params.set_suppress_non_speech_tokens(true);
        params.set_temperature(adaptive_config.temperature);
        params.set_max_initial_ts(1.0);
        params.set_entropy_thold(2.4);
        params.set_logprob_thold(-1.0);
        // BALANCED FIX: Lowered from 0.75 to 0.55 to allow quiet speech detection
        // Previous value was too aggressive and rejected valid quiet speech
        // 0.55 is balanced - prevents hallucinations while preserving quiet speech
        params.set_no_speech_thold(0.55);
        params.set_max_len(200);
        params.set_single_segment(false);

        // Set thread count based on hardware (if supported by whisper.cpp)
        if let Some(_max_threads) = adaptive_config.max_threads {
            // Note: whisper.cpp may or may not expose thread control through params
            // Removed debug log to reduce I/O overhead in transcription hot path
        }

        let duration_seconds = audio_data.len() as f64 / 16000.0;
        let is_partial = duration_seconds < 15.0; // Consider chunks under 15s as partial

        // PERFORMANCE: Suppress verbose C library logs during transcription
        // This hides whisper_full_with_state debug logs and beam search details
        let (num_segments, state) = {
            // let _suppressor = crate::whisper_engine::StderrSuppressor::new();

            let mut state = ctx.create_state()?;
            state.full(params, &audio_data)?;
            let num_segments = state.full_n_segments();

            (num_segments, state)
            // Suppressor dropped here, stderr restored
        };
        let mut result = String::new();
        let mut total_confidence = 0.0;
        let mut segment_count = 0;

        let num_segments = num_segments?;
        for i in 0..num_segments {
            let segment_text = match state.full_get_segment_text_lossy(i) {
                Ok(text) => text,
                Err(_) => continue,
            };

            // Calculate confidence based on segment length and duration (simplified approach)
            let segment_length = segment_text.len() as f32;
            let segment_confidence = if segment_length > 0.0 {
                (segment_length / 100.0).min(0.9) + 0.1 // 0.1 to 1.0 confidence based on text length
            } else {
                0.1
            };
            total_confidence += segment_confidence;
            segment_count += 1;

            let cleaned_text = segment_text.trim();
            if !cleaned_text.is_empty() {
                if !result.is_empty() {
                    result.push(' ');
                }
                result.push_str(cleaned_text);
            }
        }

        let final_result = result.trim().to_string();
        let cleaned_result = Self::clean_repetitive_text(&final_result);

        let avg_confidence = if segment_count > 0 {
            total_confidence / segment_count as f32
        } else {
            0.0
        };

        Ok((cleaned_result, avg_confidence, is_partial))
    }

    pub async fn transcribe_audio(
        &self,
        audio_data: Vec<f32>,
        language: Option<String>,
    ) -> Result<String> {
        let ctx_lock = self.current_context.read().await;
        let ctx = ctx_lock
            .as_ref()
            .ok_or_else(|| anyhow!("No model loaded. Please load a model first."))?;

        // Get adaptive configuration based on hardware
        let hardware_profile = crate::audio::HardwareProfile::detect();
        let adaptive_config = hardware_profile.get_whisper_config();

        // ADAPTIVE parameters - optimized for current hardware
        let mut params = FullParams::new(SamplingStrategy::BeamSearch {
            beam_size: adaptive_config.beam_size as i32,
            patience: 1.0,
        });

        // Configure for good quality
        // If language is "auto" or None, use automatic language detection (pass None)
        // If language is "auto-translate", enable translation to English
        // Otherwise, use the specified language code
        let (language_code, should_translate) = match language.as_deref() {
            Some("auto") | None => (None, false),
            Some("auto-translate") => (None, true),
            Some(lang) => (Some(lang), false),
        };
        params.set_language(language_code);
        params.set_translate(should_translate);

        // CRITICAL: Disable timestamp tokens to prevent whisper.cpp chunking heuristics
        // The "single timestamp ending - skip entire chunk" optimization incorrectly discards
        // complete, valid transcriptions. Disabling timestamps forces whisper to return ALL text.
        params.set_no_timestamps(true); // Prevent timestamp-based segment skipping
        params.set_token_timestamps(true); // Keep for any timestamp-aware features

        params.set_print_special(false);
        params.set_print_progress(false);
        params.set_print_realtime(false);
        params.set_print_timestamps(false);

        // BALANCED settings - good quality with reasonable speed
        params.set_suppress_blank(true);
        params.set_suppress_non_speech_tokens(true);
        params.set_temperature(0.3); // Lower than 0.4 for consistency, higher than 0.0 for quality
        params.set_max_initial_ts(1.0);
        params.set_entropy_thold(2.4);
        params.set_logprob_thold(-1.0);
        // BALANCED FIX: Lowered from 0.75 to 0.55 to allow quiet speech detection
        // Previous value was too aggressive and rejected valid quiet speech
        // 0.55 is balanced - prevents hallucinations while preserving quiet speech
        params.set_no_speech_thold(0.55);

        // Reasonable length limits
        params.set_max_len(200); // Reasonable length
        params.set_single_segment(false); // Allow multiple segments for better accuracy

        // Note: compression_ratio_threshold would be ideal but not available in current whisper-rs
        // This would help detect repetitive outputs: params.set_compression_ratio_threshold(2.4);

        // Duration-based optimization is handled by beam search parameters
        let duration_seconds = audio_data.len() as f64 / 16000.0; // Assuming 16kHz
        let is_short_audio = duration_seconds < 1.0;

        // Smart logging based on audio duration and previous states
        let mut should_log_transcription = true;
        let mut should_log_short_warning = false;

        if is_short_audio {
            let last_was_short = *self.last_transcription_was_short.read().await;
            let warning_logged = *self.short_audio_warning_logged.read().await;

            if !warning_logged {
                should_log_short_warning = true;
                *self.short_audio_warning_logged.write().await = true;
            }

            // Only log transcription start if it's the first short audio or previous wasn't short
            should_log_transcription = !last_was_short;

            *self.last_transcription_was_short.write().await = true;
        } else {
            let last_was_short = *self.last_transcription_was_short.read().await;

            // Always log when transitioning from short to normal audio
            if last_was_short {
                log::info!("Audio duration normalized, resuming transcription");
                *self.short_audio_warning_logged.write().await = false;
            }

            *self.last_transcription_was_short.write().await = false;
        }

        if should_log_short_warning {
            log::warn!("Audio duration is short ({:.1}s < 1.0s). Consider padding the input audio with silence. Further short audio warnings will be suppressed.", duration_seconds);
        }

        // Performance optimization: reduce transcription start logging frequency
        let transcription_count = {
            let mut count = self.transcription_count.write().await;
            *count += 1;
            *count
        };

        // Only log every 10th transcription or significant audio (>10s) to reduce I/O overhead
        if should_log_transcription && (transcription_count % 10 == 0 || duration_seconds > 10.0) {
            log::info!(
                "Starting transcription #{} of {} samples ({:.1}s duration)",
                transcription_count,
                audio_data.len(),
                duration_seconds
            );
        }
        let mut state = ctx.create_state()?;
        state.full(params, &audio_data)?;

        // Extract text with improved segment handling
        let num_segments = state.full_n_segments()?;

        // Performance optimization: reduce segment completion logging
        // Only log for significant transcriptions to avoid I/O overhead
        if (should_log_transcription || num_segments > 0)
            && (num_segments > 3 || duration_seconds > 5.0)
        {
            perf_debug!(
                "Transcription #{} completed with {} segments ({:.1}s)",
                transcription_count,
                num_segments,
                duration_seconds
            );
        }
        let mut result = String::new();

        for i in 0..num_segments {
            let segment_text = match state.full_get_segment_text_lossy(i) {
                Ok(text) => text,
                Err(_) => continue,
            };

            let _start_time = state.full_get_segment_t0(i).unwrap_or(0);
            let _end_time = state.full_get_segment_t1(i).unwrap_or(0);

            // Performance optimization: remove per-segment debug logging
            // This was causing significant I/O overhead during transcription
            // Only log segments for very long audio (>30s) or when explicitly debugging
            if duration_seconds > 30.0 {
                perf_trace!(
                    "Segment {} ({:.2}s-{:.2}s), chars={}",
                    i,
                    _start_time as f64 / 100.0,
                    _end_time as f64 / 100.0,
                    segment_text.chars().count()
                );
            }

            // Clean and append segment text
            let cleaned_text = segment_text.trim();
            if !cleaned_text.is_empty() {
                if !result.is_empty() {
                    result.push(' ');
                }
                result.push_str(cleaned_text);
            }
        }

        let final_result = result.trim().to_string();

        // Check for repetition loops and clean them up
        let cleaned_result = Self::clean_repetitive_text(&final_result);

        // Performance optimization: smart logging for transcription results
        if cleaned_result.is_empty() {
            // Only log empty results occasionally to reduce spam
            if should_log_transcription && transcription_count % 20 == 0 {
                perf_debug!(
                    "Transcription #{} result is empty - no speech detected",
                    transcription_count
                );
            }
        } else {
            if cleaned_result != final_result {
                log::info!(
                    "Cleaned repetitive transcription #{} (chars: {} -> {})",
                    transcription_count,
                    final_result.chars().count(),
                    cleaned_result.chars().count()
                );
            }
            // Reduce successful transcription logging frequency
            // Only log every 5th result or significant results (>50 chars) to reduce I/O overhead
            if transcription_count % 5 == 0 || cleaned_result.len() > 50 || duration_seconds > 10.0
            {
                log::info!(
                    "Transcription #{} result accepted (chars: {}, duration: {:.1}s)",
                    transcription_count,
                    cleaned_result.chars().count(),
                    duration_seconds
                );
            } else {
                perf_debug!(
                    "Transcription #{} result accepted (chars: {}, duration: {:.1}s)",
                    transcription_count,
                    cleaned_result.chars().count(),
                    duration_seconds
                );
            }
        }

        Ok(cleaned_result)
    }

    pub async fn get_models_directory(&self) -> PathBuf {
        self.models_dir.clone()
    }

    pub async fn delete_model(&self, model_name: &str) -> Result<String> {
        log::info!("Attempting to delete model: {}", model_name);

        // Get model info to find the file path
        let model_info = {
            let models = self.available_models.read().await;
            models.get(model_name).cloned()
        };

        let model_info = model_info.ok_or_else(|| anyhow!("Model '{}' not found", model_name))?;

        // Check if model is corrupted before allowing deletion
        log::info!("Model '{}' has status: {:?}", model_name, model_info.status);
        match &model_info.status {
            ModelStatus::Corrupted {
                file_size,
                expected_min_size,
            } => {
                log::info!(
                    "Deleting corrupted model '{}' (file size: {} bytes, expected min: {} bytes)",
                    model_name,
                    file_size,
                    expected_min_size
                );

                // Delete the file
                if model_info.path.exists() {
                    fs::remove_file(&model_info.path)
                        .await
                        .map_err(|_| anyhow!("model file could not be deleted"))?;
                    log::info!("Corrupted model file deleted (model_id={})", model_name);
                } else {
                    log::warn!("Model file was already absent (model_id={})", model_name);
                }

                // Update model status to Missing
                {
                    let mut models = self.available_models.write().await;
                    if let Some(model) = models.get_mut(model_name) {
                        model.status = ModelStatus::Missing;
                    }
                }

                Ok(format!(
                    "Successfully deleted corrupted model '{}'",
                    model_name
                ))
            }
            ModelStatus::Available => {
                // Allow deletion of available models for testing/cleanup
                log::info!("Deleting available model '{}' (for cleanup)", model_name);

                if model_info.path.exists() {
                    fs::remove_file(&model_info.path)
                        .await
                        .map_err(|_| anyhow!("model file could not be deleted"))?;
                    log::info!("Available model file deleted (model_id={})", model_name);
                } else {
                    log::warn!("Model file was already absent (model_id={})", model_name);
                }

                // Update model status to Missing
                {
                    let mut models = self.available_models.write().await;
                    if let Some(model) = models.get_mut(model_name) {
                        model.status = ModelStatus::Missing;
                    }
                }

                Ok(format!("Successfully deleted model '{}'", model_name))
            }
            _ => Err(anyhow!(
                "Can only delete corrupted or available models. Model '{}' has status: {:?}",
                model_name,
                model_info.status
            )),
        }
    }

    pub async fn download_model(
        &self,
        model_name: &str,
        progress_callback: Option<Box<dyn Fn(u8) + Send + Sync>>,
    ) -> Result<()> {
        {
            let mut active = self.active_downloads.write().await;
            if !active.insert(model_name.to_string()) {
                return Err(anyhow!("model download is already active"));
            }
        }
        *self.cancel_download_flag.write().await = None;

        let result = self
            .download_approved_model(model_name, progress_callback.as_deref())
            .await;
        self.active_downloads.write().await.remove(model_name);

        if result.is_err() {
            let partial_path = self
                .models_dir
                .join(format!("ggml-{model_name}.bin.partial"));
            let _ = fs::remove_file(partial_path).await;
            if let Some(model) = self.available_models.write().await.get_mut(model_name) {
                model.status = ModelStatus::Error("verified_download_failed".to_string());
            }
            log::warn!(
                "Verified Whisper model download failed (model_id={})",
                model_name
            );
        }
        result
    }

    async fn download_approved_model(
        &self,
        model_name: &str,
        progress_callback: Option<&(dyn Fn(u8) + Send + Sync)>,
    ) -> Result<()> {
        let (_, artifact) = approved_artifact(model_name)?;
        let file_path = self.models_dir.join(&artifact.file_name);
        let partial_path = self
            .models_dir
            .join(format!("{}.partial", artifact.file_name));

        fs::create_dir_all(&self.models_dir)
            .await
            .context("model directory is unavailable")?;
        let _ = fs::remove_file(&partial_path).await;
        if let Some(model) = self.available_models.write().await.get_mut(model_name) {
            model.status = ModelStatus::Downloading { progress: 0 };
        }
        if let Some(callback) = progress_callback {
            callback(0);
        }

        let client = Client::builder()
            .connect_timeout(std::time::Duration::from_secs(15))
            .timeout(std::time::Duration::from_secs(7_200))
            .user_agent("Meetily-Whisper-Artifact/0.6.1")
            .build()
            .context("model download client is unavailable")?;
        let response = client
            .get(&artifact.immutable_url)
            .send()
            .await
            .context("model download could not start")?;
        if !response.status().is_success() {
            return Err(anyhow!("model download was rejected"));
        }
        if response
            .content_length()
            .is_some_and(|length| length != artifact.byte_count)
        {
            return Err(anyhow!("model download size does not match the manifest"));
        }

        let mut file = fs::File::create(&partial_path)
            .await
            .context("model partial file could not be created")?;
        let mut hash = digest::Context::new(&digest::SHA256);
        let mut downloaded = 0_u64;
        let mut last_progress = 0_u8;
        let mut stream = response.bytes_stream();
        use futures_util::StreamExt;

        while let Some(chunk) = stream.next().await {
            if self.cancel_download_flag.read().await.as_deref() == Some(model_name) {
                return Err(anyhow!("model download was cancelled"));
            }
            let chunk = chunk.context("model download stream failed")?;
            downloaded = downloaded
                .checked_add(chunk.len() as u64)
                .ok_or_else(|| anyhow!("model download exceeded its size limit"))?;
            if downloaded > artifact.byte_count {
                return Err(anyhow!("model download exceeded its size limit"));
            }
            hash.update(&chunk);
            file.write_all(&chunk)
                .await
                .context("model partial file could not be written")?;

            let progress = ((downloaded * 100) / artifact.byte_count) as u8;
            if progress > last_progress {
                if let Some(model) = self.available_models.write().await.get_mut(model_name) {
                    model.status = ModelStatus::Downloading { progress };
                }
                if let Some(callback) = progress_callback {
                    callback(progress);
                }
                last_progress = progress;
            }
        }

        file.flush()
            .await
            .context("model partial file could not be flushed")?;
        file.sync_all()
            .await
            .context("model partial file could not be synchronized")?;
        drop(file);

        if downloaded != artifact.byte_count {
            return Err(anyhow!("model download is incomplete"));
        }
        let actual_digest = hash.finish();
        if !digest_matches(&artifact.sha256, &actual_digest)? {
            return Err(anyhow!("model digest does not match the manifest"));
        }
        if !has_ggml_header(&partial_path) {
            return Err(anyhow!("model format is invalid"));
        }
        replace_atomically(&partial_path, &file_path)
            .context("verified model could not be installed")?;

        if let Some(model) = self.available_models.write().await.get_mut(model_name) {
            model.status = ModelStatus::Available;
            model.path = file_path;
        }
        if let Some(callback) = progress_callback {
            callback(100);
        }
        log::info!("Verified Whisper model installed (model_id={})", model_name);
        Ok(())
    }

    pub async fn cancel_download(&self, model_name: &str) -> Result<()> {
        log::info!("Cancelling download for model: {}", model_name);

        // Set cancellation flag to interrupt the download loop
        {
            let mut cancel_flag = self.cancel_download_flag.write().await;
            *cancel_flag = Some(model_name.to_string());
        }

        // Remove from active downloads
        {
            let mut active = self.active_downloads.write().await;
            active.remove(model_name);
        }

        // Update model status to Missing (so it can be retried)
        {
            let mut models = self.available_models.write().await;
            if let Some(model_info) = models.get_mut(model_name) {
                model_info.status = ModelStatus::Missing;
            }
        }

        // Clean up partially downloaded files
        tokio::time::sleep(tokio::time::Duration::from_millis(100)).await; // Brief delay to let download loop detect cancellation

        let filename = format!("ggml-{}.bin.partial", model_name);
        let file_path = self.models_dir.join(&filename);
        if file_path.exists() {
            if fs::remove_file(&file_path).await.is_err() {
                log::warn!("Cancelled model partial could not be removed");
            } else {
                log::info!("Cancelled model partial removed");
            }
        }

        Ok(())
    }
}

#[cfg(test)]
mod verified_download_tests {
    use super::*;

    #[tokio::test]
    async fn cancellation_removes_only_partial_and_preserves_installed_model() {
        let directory = tempfile::tempdir().unwrap();
        let final_path = directory.path().join("ggml-large-v3-turbo-q5_0.bin");
        let partial_path = directory
            .path()
            .join("ggml-large-v3-turbo-q5_0.bin.partial");
        std::fs::write(&final_path, b"existing verified model").unwrap();
        std::fs::write(&partial_path, b"unfinished replacement").unwrap();
        let engine =
            WhisperEngine::new_with_models_dir(Some(directory.path().to_path_buf())).unwrap();

        engine.cancel_download("large-v3-turbo-q5_0").await.unwrap();

        assert_eq!(
            std::fs::read(final_path).unwrap(),
            b"existing verified model"
        );
        assert!(!partial_path.exists());
    }
}
