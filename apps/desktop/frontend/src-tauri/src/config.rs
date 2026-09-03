/// Application configuration constants
///
/// Centralized definitions for default models and settings.
/// Used across database initialization, import, and retranscription.

/// Default Whisper model for transcription when no preference is configured.
/// This is the recommended balance of accuracy and speed.
pub const DEFAULT_WHISPER_MODEL: &str = "large-v3-turbo-q5_0";

/// Default Parakeet model for transcription when no preference is configured.
/// This is the quantized version optimized for speed.
pub const DEFAULT_PARAKEET_MODEL: &str = "parakeet-tdt-0.6b-v3-int8";

/// Whisper model catalog with metadata for all supported models.
/// Used by both WhisperEngine::discover_models() and discover_models_standalone().
///
/// Format: (name, filename, size_mb, accuracy, speed, description)
pub const WHISPER_MODEL_CATALOG: &[(&str, &str, u32, &str, &str, &str)] = &[
    // Only manifest-pinned models are exposed in pilot builds. Additions require
    // reviewed size, digest, language, license, and signature metadata.
    (
        "large-v3-turbo-q5_0",
        "ggml-large-v3-turbo-q5_0.bin",
        547,
        "High",
        "Medium",
        "Verified multilingual model for Japanese, English, and Korean",
    ),
];
