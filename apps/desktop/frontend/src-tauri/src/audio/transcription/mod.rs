// audio/transcription/mod.rs
//
// Transcription module: Provider abstraction, engine management, and worker pool.

pub mod engine;
pub mod intelligence_dispatcher;
pub mod local_only;
pub mod parakeet_provider;
pub mod provider;
pub mod whisper_provider;
pub mod worker;

// Re-export commonly used types
pub use engine::{
    get_or_init_transcription_engine, get_or_init_whisper, validate_transcription_model_ready,
    TranscriptionEngine,
};
pub use parakeet_provider::ParakeetProvider;
pub use provider::{TranscriptResult, TranscriptionError, TranscriptionProvider};
pub use whisper_provider::WhisperProvider;
pub use worker::{
    next_sequence_id, reset_sequence_counter, reset_speech_detected_flag, start_transcription_task,
    transcription_queue_snapshot, TranscriptUpdate,
};
pub(crate) use worker::{
    observe_transcription_chunk_queued, observe_transcription_chunk_released,
    reset_transcription_queue_diagnostics,
};
