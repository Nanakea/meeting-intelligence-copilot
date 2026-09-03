use anyhow::Result;
use log::{error, info, warn};
use serde::{Deserialize, Serialize};
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;
use tauri::{AppHandle, Emitter, Runtime};
use tokio::sync::Mutex as AsyncMutex;
use tokio::sync::{mpsc, oneshot};
use tokio::task::JoinHandle;

use super::audio_processing::create_meeting_folder;
use super::incremental_saver::IncrementalAudioSaver;
use super::recording_state::AudioChunk;

const TRANSCRIPT_JOURNAL_FILE: &str = ".transcripts.jsonl";
const ACCUMULATION_STOP_TIMEOUT: tokio::time::Duration = tokio::time::Duration::from_secs(2);
const RECORDING_AUDIO_QUEUE_CAPACITY: usize = 256;

#[derive(Debug, Clone, Copy, Default)]
pub struct RecordingSaveDiagnostics {
    pub queue_peak: usize,
    pub drain_completed: bool,
    pub drain_ms: u64,
    pub final_checkpoint_ms: u64,
    pub merge_ms: u64,
    pub checkpoint_cleanup_ms: u64,
    pub transcript_metadata_ms: u64,
}

#[derive(Clone)]
pub struct RecordingAudioSender {
    sender: mpsc::Sender<AudioChunk>,
    queue_depth: Arc<AtomicUsize>,
    queue_peak: Arc<AtomicUsize>,
}

impl RecordingAudioSender {
    pub async fn send(&self, chunk: AudioChunk) -> Result<(), mpsc::error::SendError<AudioChunk>> {
        let permit = match self.sender.reserve().await {
            Ok(permit) => permit,
            Err(_) => return Err(mpsc::error::SendError(chunk)),
        };
        let depth = self.queue_depth.fetch_add(1, Ordering::Relaxed) + 1;
        self.queue_peak.fetch_max(depth, Ordering::Relaxed);
        permit.send(chunk);
        Ok(())
    }
}

fn elapsed_millis(started: Instant) -> u64 {
    started.elapsed().as_millis().min(u64::MAX as u128) as u64
}

#[derive(Serialize)]
struct TranscriptFile<'a> {
    version: &'static str,
    segments: &'a [TranscriptSegment],
    last_updated: String,
    total_segments: usize,
}

#[derive(Deserialize)]
struct StoredTranscriptFile {
    segments: Vec<TranscriptSegment>,
}

const MAX_FINALIZED_TRANSCRIPT_BYTES: u64 = 64 * 1024 * 1024;

pub fn load_finalized_transcript_history(
    meeting_folder: &Path,
    expected_session_id: &str,
) -> Result<Vec<TranscriptSegment>, &'static str> {
    if !meeting_folder.is_absolute() {
        return Err("The finalized transcript location is invalid");
    }
    let folder_metadata = std::fs::symlink_metadata(meeting_folder)
        .map_err(|_| "The finalized transcript location is unavailable")?;
    if !folder_metadata.is_dir() || folder_metadata.file_type().is_symlink() {
        return Err("The finalized transcript location is invalid");
    }

    let metadata_path = meeting_folder.join("metadata.json");
    let metadata_file = std::fs::symlink_metadata(&metadata_path)
        .map_err(|_| "The finalized recording metadata is unavailable")?;
    if !metadata_file.is_file()
        || metadata_file.file_type().is_symlink()
        || metadata_file.len() > 64 * 1024
    {
        return Err("The finalized recording metadata is invalid");
    }
    let metadata: MeetingMetadata = serde_json::from_slice(
        &std::fs::read(metadata_path)
            .map_err(|_| "The finalized recording metadata is unavailable")?,
    )
    .map_err(|_| "The finalized recording metadata is invalid")?;
    if metadata.meeting_id.as_deref() != Some(expected_session_id)
        || metadata.transcript_file != "transcripts.json"
    {
        return Err("The finalized recording does not match this session");
    }

    let transcript_path = meeting_folder.join("transcripts.json");
    let transcript_file = std::fs::symlink_metadata(&transcript_path)
        .map_err(|_| "The finalized transcript is unavailable")?;
    if !transcript_file.is_file()
        || transcript_file.file_type().is_symlink()
        || transcript_file.len() > MAX_FINALIZED_TRANSCRIPT_BYTES
    {
        return Err("The finalized transcript is invalid");
    }
    let mut stored: StoredTranscriptFile = serde_json::from_slice(
        &std::fs::read(transcript_path).map_err(|_| "The finalized transcript is unavailable")?,
    )
    .map_err(|_| "The finalized transcript is invalid")?;

    // A failed or cancelled final checkpoint can leave newer, already durable
    // segments only in the append-only journal. Merge them for the stop handoff
    // without deleting the journal; startup recovery still owns compaction.
    let journal_path = meeting_folder.join(TRANSCRIPT_JOURNAL_FILE);
    match std::fs::symlink_metadata(&journal_path) {
        Ok(journal_file) => {
            if !journal_file.is_file()
                || journal_file.file_type().is_symlink()
                || journal_file.len() > MAX_FINALIZED_TRANSCRIPT_BYTES
            {
                return Err("The finalized transcript journal is invalid");
            }
            let journal = BufReader::new(
                std::fs::File::open(journal_path)
                    .map_err(|_| "The finalized transcript journal is unavailable")?,
            );
            for line in journal.lines() {
                let line = line.map_err(|_| "The finalized transcript journal is invalid")?;
                if line.trim().is_empty() {
                    continue;
                }
                if let Ok(segment) = serde_json::from_str::<TranscriptSegment>(&line) {
                    RecordingSaver::upsert_transcript_segment(&mut stored.segments, segment);
                }
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return Err("The finalized transcript journal is unavailable"),
    }
    stored.segments.sort_by_key(|segment| segment.sequence_id);
    if stored
        .segments
        .windows(2)
        .any(|pair| pair[0].sequence_id == pair[1].sequence_id)
        || stored.segments.iter().any(|segment| {
            !segment.audio_start_time.is_finite()
                || !segment.audio_end_time.is_finite()
                || !segment.duration.is_finite()
                || !segment.confidence.is_finite()
        })
    {
        return Err("The finalized transcript is invalid");
    }
    Ok(stored.segments)
}

/// Structured transcript segment for JSON export
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TranscriptSegment {
    pub id: String,
    pub text: String,
    pub audio_start_time: f64, // Seconds from recording start
    pub audio_end_time: f64,   // Seconds from recording start
    pub duration: f64,         // Segment duration in seconds
    pub display_time: String,  // Formatted time for display like "[02:15]"
    pub confidence: f32,
    pub sequence_id: u64,
}

#[derive(Clone)]
pub struct TranscriptSink {
    meeting_folder: Option<PathBuf>,
    transcript_segments: Arc<Mutex<Vec<TranscriptSegment>>>,
}

impl TranscriptSink {
    pub fn add(&self, segment: TranscriptSegment) -> bool {
        if let Some(folder) = &self.meeting_folder {
            if RecordingSaver::append_transcript_journal(folder, &segment).is_err() {
                warn!("Failed to append incremental transcript update");
            }
        }

        let Ok(mut segments) = self.transcript_segments.lock() else {
            error!("Failed to lock transcript history for an update");
            return false;
        };
        let inserted = RecordingSaver::upsert_transcript_segment(&mut segments, segment);
        if inserted && (segments.len() == 1 || segments.len() % 100 == 0) {
            info!("Transcript history contains {} segments", segments.len());
        }
        true
    }
}

/// Meeting metadata structure
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MeetingMetadata {
    pub version: String,
    pub meeting_id: Option<String>,
    pub meeting_name: Option<String>,
    pub created_at: String,
    pub completed_at: Option<String>,
    pub duration_seconds: Option<f64>,
    pub devices: DeviceInfo,
    pub audio_file: String,
    pub transcript_file: String,
    pub sample_rate: u32,
    pub status: String, // "recording", "completed", "error"
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceInfo {
    pub microphone: Option<String>,
    pub system_audio: Option<String>,
}

/// New recording saver using incremental saving strategy
pub struct RecordingSaver {
    incremental_saver: Option<Arc<AsyncMutex<IncrementalAudioSaver>>>,
    base_folder: PathBuf,
    meeting_folder: Option<PathBuf>,
    meeting_name: Option<String>,
    metadata: Option<MeetingMetadata>,
    transcript_segments: Arc<Mutex<Vec<TranscriptSegment>>>,
    chunk_receiver: Option<mpsc::Receiver<AudioChunk>>,
    accumulation_stop: Option<oneshot::Sender<()>>,
    accumulation_task: Option<JoinHandle<()>>,
    queue_depth: Arc<AtomicUsize>,
    queue_peak: Arc<AtomicUsize>,
    last_stop_diagnostics: RecordingSaveDiagnostics,
    is_saving: bool,
}

impl RecordingSaver {
    fn upsert_transcript_segment(
        segments: &mut Vec<TranscriptSegment>,
        segment: TranscriptSegment,
    ) -> bool {
        match segments.binary_search_by_key(&segment.sequence_id, |existing| existing.sequence_id) {
            Ok(index) => {
                segments[index] = segment;
                false
            }
            Err(index) => {
                segments.insert(index, segment);
                true
            }
        }
    }

    pub fn new() -> Self {
        Self {
            incremental_saver: None,
            base_folder: super::recording_preferences::get_default_recordings_folder(),
            meeting_folder: None,
            meeting_name: None,
            metadata: None,
            transcript_segments: Arc::new(Mutex::new(Vec::new())),
            chunk_receiver: None,
            accumulation_stop: None,
            accumulation_task: None,
            queue_depth: Arc::new(AtomicUsize::new(0)),
            queue_peak: Arc::new(AtomicUsize::new(0)),
            last_stop_diagnostics: RecordingSaveDiagnostics::default(),
            is_saving: false,
        }
    }

    /// Set the meeting name for this recording session
    pub fn set_meeting_name(&mut self, name: Option<String>) {
        self.meeting_name = name;
    }

    /// Set the user-configured root before accumulation creates any artifacts.
    pub fn set_base_folder(&mut self, base_folder: PathBuf) {
        self.base_folder = base_folder;
        if Self::recover_transcript_journals(&self.base_folder).is_err() {
            warn!("Incomplete transcript recovery was not completed");
        }
    }

    /// Associate saved artifacts with the opaque live intelligence session.
    pub fn set_meeting_id(&mut self, meeting_id: String) {
        if let Some(ref mut metadata) = self.metadata {
            metadata.meeting_id = Some(meeting_id);
            if let Some(folder) = &self.meeting_folder {
                let metadata_clone = metadata.clone();
                if self.write_metadata(folder, &metadata_clone).is_err() {
                    warn!("Failed to update recording session metadata");
                }
            }
        }
    }

    /// Set device information in metadata
    pub fn set_device_info(&mut self, mic_name: Option<String>, sys_name: Option<String>) {
        if let Some(ref mut metadata) = self.metadata {
            metadata.devices.microphone = mic_name;
            metadata.devices.system_audio = sys_name;

            // Write updated metadata to disk if folder exists
            if let Some(folder) = &self.meeting_folder {
                let metadata_clone = metadata.clone();
                if self.write_metadata(folder, &metadata_clone).is_err() {
                    warn!("Failed to update metadata with device info");
                }
            }
        }
    }

    /// Add or update a structured transcript segment (upserts based on sequence_id)
    /// Also appends a crash-recoverable journal entry without rewriting history.
    pub fn add_transcript_segment(&self, segment: TranscriptSegment) {
        let _ = self.transcript_sink().add(segment);
    }

    pub fn transcript_sink(&self) -> TranscriptSink {
        TranscriptSink {
            meeting_folder: self.meeting_folder.clone(),
            transcript_segments: Arc::clone(&self.transcript_segments),
        }
    }

    /// Add a segment to active history without rewriting the complete file.
    /// This is reserved for the opt-in packaged soak generator.
    pub fn add_transcript_segment_in_memory(&self, segment: TranscriptSegment) -> bool {
        let Ok(mut segments) = self.transcript_segments.lock() else {
            return false;
        };
        Self::upsert_transcript_segment(&mut segments, segment);
        true
    }

    /// Legacy method for backward compatibility - converts text to basic segment
    pub fn add_transcript_chunk(&self, text: String) {
        let segment = TranscriptSegment {
            id: format!("seg_{}", chrono::Utc::now().timestamp_millis()),
            text,
            audio_start_time: 0.0,
            audio_end_time: 0.0,
            duration: 0.0,
            display_time: "[00:00]".to_string(),
            confidence: 1.0,
            sequence_id: 0,
        };
        self.add_transcript_segment(segment);
    }

    /// Start accumulation with optional incremental saving
    ///
    /// # Arguments
    /// * `auto_save` - If true, creates checkpoints and enables saving. If false, audio chunks are discarded.
    pub fn start_accumulation(&mut self, auto_save: bool) -> RecordingAudioSender {
        if auto_save {
            info!("Initializing incremental audio saver for recording (auto-save ENABLED)");
        } else {
            info!(
                "Starting recording without audio saving (auto-save DISABLED - transcripts only)"
            );
        }

        // Create channel for receiving audio chunks
        let (sender, receiver) = mpsc::channel::<AudioChunk>(RECORDING_AUDIO_QUEUE_CAPACITY);
        self.chunk_receiver = Some(receiver);
        // Per-session counters prevent a late aborted task from mutating the
        // next recording's queue diagnostics.
        self.queue_depth = Arc::new(AtomicUsize::new(0));
        self.queue_peak = Arc::new(AtomicUsize::new(0));
        self.last_stop_diagnostics = RecordingSaveDiagnostics::default();

        // Initialize meeting folder and incremental saver ONLY if auto_save is enabled
        if auto_save {
            if let Some(name) = self.meeting_name.clone() {
                match self.initialize_meeting_folder(&name, true) {
                    Ok(()) => info!("Successfully initialized meeting folder with checkpoints"),
                    Err(_) => {
                        error!("Failed to initialize the local meeting folder");
                        // Continue anyway - will use fallback flat structure
                    }
                }
            }
        } else {
            // When auto_save is false, still create meeting folder for transcripts/metadata
            // but skip .checkpoints directory
            if let Some(name) = self.meeting_name.clone() {
                match self.initialize_meeting_folder(&name, false) {
                    Ok(()) => info!("Successfully initialized meeting folder (transcripts only)"),
                    Err(_) => {
                        error!("Failed to initialize the local meeting folder");
                    }
                }
            }
        }

        // Start accumulation task
        let incremental_saver_arc = self.incremental_saver.clone();
        let queue_depth = self.queue_depth.clone();
        let save_audio = auto_save;

        // Publish the active state and stop channel before the receiver can run.
        self.is_saving = true;
        if let Some(stop) = self.accumulation_stop.take() {
            let _ = stop.send(());
        }
        if let Some(task) = self.accumulation_task.take() {
            task.abort();
        }
        let (stop_sender, mut stop_receiver) = oneshot::channel();
        self.accumulation_stop = Some(stop_sender);

        if let Some(mut receiver) = self.chunk_receiver.take() {
            self.accumulation_task = Some(tokio::spawn(async move {
                info!(
                    "Recording saver accumulation task started (save_audio: {})",
                    save_audio
                );

                loop {
                    tokio::select! {
                        biased;
                        _ = &mut stop_receiver => {
                            receiver.close();
                            while let Some(chunk) = receiver.recv().await {
                                queue_depth.fetch_sub(1, Ordering::Relaxed);
                                Self::persist_audio_chunk(
                                    save_audio,
                                    &incremental_saver_arc,
                                    chunk,
                                ).await;
                            }
                            break;
                        }
                        chunk = receiver.recv() => match chunk {
                            Some(chunk) => {
                                queue_depth.fetch_sub(1, Ordering::Relaxed);
                                Self::persist_audio_chunk(
                                    save_audio,
                                    &incremental_saver_arc,
                                    chunk,
                                ).await
                            },
                            None => break,
                        },
                    }
                }

                info!("Recording saver accumulation task ended");
            }));
        }

        RecordingAudioSender {
            sender,
            queue_depth: self.queue_depth.clone(),
            queue_peak: self.queue_peak.clone(),
        }
    }

    async fn persist_audio_chunk(
        save_audio: bool,
        incremental_saver: &Option<Arc<AsyncMutex<IncrementalAudioSaver>>>,
        chunk: AudioChunk,
    ) {
        if !save_audio {
            return;
        }
        let Some(saver) = incremental_saver else {
            error!("Incremental saver was unavailable while accumulating");
            return;
        };
        if saver.lock().await.add_chunk(chunk).is_err() {
            error!("Failed to persist an incremental audio chunk");
        }
    }

    async fn stop_accumulation(&mut self) -> bool {
        self.is_saving = false;
        if let Some(stop) = self.accumulation_stop.take() {
            let _ = stop.send(());
        }
        let Some(mut task) = self.accumulation_task.take() else {
            return true;
        };
        match tokio::time::timeout(ACCUMULATION_STOP_TIMEOUT, &mut task).await {
            Ok(Ok(())) => true,
            Ok(Err(_)) => {
                warn!("Recording saver accumulation task ended unexpectedly");
                false
            }
            Err(_) => {
                task.abort();
                let _ = task.await;
                warn!("Recording saver accumulation drain reached its deadline");
                false
            }
        }
    }

    /// Initialize meeting folder structure and metadata
    ///
    /// # Arguments
    /// * `meeting_name` - Name of the meeting
    /// * `create_checkpoints` - Whether to create .checkpoints/ directory and IncrementalAudioSaver
    fn initialize_meeting_folder(
        &mut self,
        meeting_name: &str,
        create_checkpoints: bool,
    ) -> Result<()> {
        // Create meeting folder structure (with or without .checkpoints/ subdirectory)
        let meeting_folder =
            create_meeting_folder(&self.base_folder, meeting_name, create_checkpoints)?;

        // Only initialize incremental saver if checkpoints are needed (auto_save is true)
        if create_checkpoints {
            let incremental_saver = IncrementalAudioSaver::new(meeting_folder.clone(), 48000)?;
            self.incremental_saver = Some(Arc::new(AsyncMutex::new(incremental_saver)));
            info!("✅ Incremental audio saver initialized for the active meeting");
        } else {
            info!("⚠️  Skipped incremental audio saver (auto-save disabled)");
        }

        // Create initial metadata
        let metadata = MeetingMetadata {
            version: "1.0".to_string(),
            meeting_id: None, // Will be set by backend
            meeting_name: Some(meeting_name.to_string()),
            created_at: chrono::Utc::now().to_rfc3339(),
            completed_at: None,
            duration_seconds: None,
            devices: DeviceInfo {
                microphone: None, // Could be enhanced to store actual device names
                system_audio: None,
            },
            audio_file: if create_checkpoints {
                "audio.mp4".to_string()
            } else {
                "".to_string()
            },
            transcript_file: "transcripts.json".to_string(),
            sample_rate: 48000,
            status: "recording".to_string(),
        };

        // Keep an immediately valid snapshot for crash-recovery and safe local
        // deletion. Live updates are appended to the adjacent journal.
        Self::write_transcript_file(&meeting_folder, &[])?;

        // Write initial metadata.json
        self.write_metadata(&meeting_folder, &metadata)?;

        self.meeting_folder = Some(meeting_folder);
        self.metadata = Some(metadata);

        Ok(())
    }

    /// Write metadata.json to disk (atomic write with temp file)
    fn write_metadata(&self, folder: &PathBuf, metadata: &MeetingMetadata) -> Result<()> {
        let metadata_path = folder.join("metadata.json");
        let temp_path = folder.join(".metadata.json.tmp");

        let json_string = serde_json::to_string_pretty(metadata)?;
        std::fs::write(&temp_path, json_string)?;
        std::fs::rename(&temp_path, &metadata_path)?; // Atomic

        Ok(())
    }

    fn write_transcript_file(folder: &Path, segments: &[TranscriptSegment]) -> Result<()> {
        let transcript_path = folder.join("transcripts.json");
        let temp_path = folder.join(".transcripts.json.tmp");

        let total_segments = segments.len();
        let payload = TranscriptFile {
            version: "1.0",
            segments,
            last_updated: chrono::Utc::now().to_rfc3339(),
            total_segments,
        };
        let temp_file = std::fs::File::create(&temp_path).map_err(|_| {
            error!("Failed to create the transcript temporary file");
            anyhow::anyhow!("Failed to create the transcript temporary file")
        })?;
        let mut writer = BufWriter::new(temp_file);
        serde_json::to_writer(&mut writer, &payload).map_err(|_| {
            error!("Failed to serialize transcripts to JSON");
            anyhow::anyhow!("Transcript serialization failed")
        })?;
        writer.flush().map_err(|_| {
            error!("Failed to flush the transcript temporary file");
            anyhow::anyhow!("Failed to flush the transcript temporary file")
        })?;
        drop(writer);
        drop(payload);

        // Verify temp file was written correctly
        if !temp_path.exists() {
            error!("Transcript temporary file verification failed");
            return Err(anyhow::anyhow!("Temp file verification failed"));
        }

        // Atomic rename
        std::fs::rename(&temp_path, &transcript_path).map_err(|_| {
            error!("Failed to atomically replace the transcript file");
            anyhow::anyhow!("Failed to atomically replace the transcript file")
        })?;

        info!(
            "✅ Successfully wrote transcripts.json with {} segments",
            total_segments
        );
        Ok(())
    }

    fn append_transcript_journal(folder: &Path, segment: &TranscriptSegment) -> Result<()> {
        let encoded = serde_json::to_vec(segment)?;
        let mut journal = OpenOptions::new()
            .create(true)
            .append(true)
            .open(folder.join(TRANSCRIPT_JOURNAL_FILE))?;
        // Prefix each record so a partial process-crash tail cannot merge with
        // the next successfully appended event.
        journal.write_all(b"\n")?;
        journal.write_all(&encoded)?;
        journal.flush()?;
        Ok(())
    }

    fn recover_transcript_journal(folder: &Path) -> Result<usize> {
        let journal_path = folder.join(TRANSCRIPT_JOURNAL_FILE);
        if !journal_path.is_file() {
            return Ok(0);
        }

        let transcript_path = folder.join("transcripts.json");
        let mut segments = if transcript_path.is_file() {
            let file = std::fs::File::open(&transcript_path)?;
            match serde_json::from_reader::<_, StoredTranscriptFile>(file) {
                Ok(snapshot) => snapshot.segments,
                Err(_) => {
                    std::fs::rename(
                        &transcript_path,
                        Self::transcript_quarantine_path(folder, "json"),
                    )?;
                    warn!("A corrupt transcript snapshot was quarantined");
                    Vec::new()
                }
            }
        } else {
            Vec::new()
        };
        let journal = BufReader::new(std::fs::File::open(&journal_path)?);
        let mut recovered = 0;
        let mut malformed = 0;
        for line in journal.lines() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }
            match serde_json::from_str::<TranscriptSegment>(&line) {
                Ok(segment) => {
                    Self::upsert_transcript_segment(&mut segments, segment);
                    recovered += 1;
                }
                Err(_) => malformed += 1,
            }
        }

        Self::write_transcript_file(folder, &segments)?;
        if malformed == 0 {
            std::fs::remove_file(journal_path)?;
        } else {
            std::fs::rename(
                journal_path,
                Self::transcript_quarantine_path(folder, "jsonl"),
            )?;
            warn!(
                "Quarantined {} malformed transcript journal entries",
                malformed
            );
        }
        Ok(recovered)
    }

    fn transcript_quarantine_path(folder: &Path, extension: &str) -> PathBuf {
        let nonce = chrono::Utc::now()
            .timestamp_nanos_opt()
            .unwrap_or_else(|| chrono::Utc::now().timestamp_millis() * 1_000_000);
        folder.join(format!(".transcripts.corrupt-{nonce}.{extension}"))
    }

    fn recover_transcript_journals(base_folder: &Path) -> Result<usize> {
        if !base_folder.is_dir() {
            return Ok(0);
        }
        let mut recovered = 0;
        for entry in std::fs::read_dir(base_folder)? {
            let entry = entry?;
            if !entry.file_type()?.is_dir() {
                continue;
            }
            match Self::recover_transcript_journal(&entry.path()) {
                Ok(count) => recovered += count,
                Err(_) => warn!("An incomplete transcript journal could not be recovered"),
            }
        }
        if recovered > 0 {
            info!("Recovered {} transcript journal entries", recovered);
        }
        Ok(recovered)
    }

    /// Write transcripts.json to disk (atomic write with temp file and validation)
    fn write_transcripts_json(&self, folder: &PathBuf) -> Result<()> {
        // Finalization runs after the worker has joined, so borrowing the slice
        // while streaming it avoids two complete transcript copies safely.
        let segments = if let Ok(segments) = self.transcript_segments.lock() {
            segments
        } else {
            error!("Failed to lock transcript segments for writing");
            return Err(anyhow::anyhow!("Failed to lock transcript segments"));
        };
        info!("Writing {} transcript segments to JSON", segments.len());
        Self::write_transcript_file(folder, &segments)
    }

    // in frontend/src-tauri/src/audio/recording_saver.rs
    pub fn get_stats(&self) -> (usize, u32) {
        if let Some(ref saver) = self.incremental_saver {
            if let Ok(guard) = saver.try_lock() {
                (guard.get_checkpoint_count() as usize, 48000)
            } else {
                (0, 48000)
            }
        } else {
            (0, 48000)
        }
    }

    fn finalize_transcripts_and_metadata(
        &mut self,
        recording_duration: Option<f64>,
        status: &str,
    ) -> Result<(), String> {
        let folder = self
            .meeting_folder
            .clone()
            .ok_or_else(|| "Meeting folder was not initialized".to_string())?;
        if self.write_transcripts_json(&folder).is_err() {
            error!("Final transcript write failed");
            return Err("Failed to save finalized transcripts".to_string());
        }

        let journal_path = folder.join(TRANSCRIPT_JOURNAL_FILE);
        if journal_path.exists() {
            if std::fs::remove_file(&journal_path).is_err() {
                warn!("Compacted transcript journal could not be removed");
            }
        }

        if !folder.join("transcripts.json").exists() {
            error!("Final transcript verification failed");
            return Err("Transcript file verification failed".to_string());
        }
        info!("Final transcripts saved and verified");

        let mut metadata = self
            .metadata
            .clone()
            .ok_or_else(|| "Recording metadata was not initialized".to_string())?;
        metadata.status = status.to_string();
        metadata.completed_at = Some(chrono::Utc::now().to_rfc3339());
        metadata.duration_seconds = recording_duration.or_else(|| {
            self.transcript_segments
                .lock()
                .ok()
                .and_then(|segments| segments.last().map(|segment| segment.audio_end_time))
        });

        if self.write_metadata(&folder, &metadata).is_err() {
            error!("Recording metadata finalization failed");
            return Err("Failed to update finalized recording metadata".to_string());
        }

        info!(
            "Metadata updated with duration: {:?}s",
            metadata.duration_seconds
        );
        self.metadata = Some(metadata);
        Ok(())
    }

    /// Stop and save using incremental saving approach
    ///
    /// # Arguments
    /// * `app` - Tauri app handle for emitting events
    /// * `recording_duration` - Actual recording duration in seconds (from RecordingState)
    pub async fn stop_and_save<R: Runtime>(
        &mut self,
        app: &AppHandle<R>,
        recording_duration: Option<f64>,
    ) -> Result<Option<String>, String> {
        info!("Stopping recording saver");
        self.last_stop_diagnostics.queue_peak = self.queue_peak.load(Ordering::Relaxed);

        // Close ingress and drain every chunk already accepted by the saver.
        // A timeout is non-fatal so recording shutdown cannot deadlock.
        let drain_started = Instant::now();
        self.last_stop_diagnostics.drain_completed = self.stop_accumulation().await;
        self.last_stop_diagnostics.drain_ms = elapsed_millis(drain_started);

        // Finalize audio when enabled. Transcript-only recordings still need
        // final transcripts and completed metadata below.
        let final_audio_path = if let Some(saver_arc) = &self.incremental_saver {
            let mut saver = saver_arc.lock().await;
            match saver.finalize_with_diagnostics().await {
                Ok((path, diagnostics)) => {
                    self.last_stop_diagnostics.final_checkpoint_ms =
                        diagnostics.final_checkpoint_ms;
                    self.last_stop_diagnostics.merge_ms = diagnostics.merge_ms;
                    self.last_stop_diagnostics.checkpoint_cleanup_ms =
                        diagnostics.checkpoint_cleanup_ms;
                    info!("Audio finalization completed");
                    Some(path)
                }
                Err(_) => {
                    error!("Audio finalization failed");
                    return Err("Failed to finalize recording audio".to_string());
                }
            }
        } else {
            info!("Audio saving was disabled; finalizing transcripts and metadata");
            None
        };

        let transcript_metadata_started = Instant::now();
        let recording_status = if self.last_stop_diagnostics.drain_completed {
            "completed"
        } else {
            "error"
        };
        self.finalize_transcripts_and_metadata(recording_duration, recording_status)?;
        self.last_stop_diagnostics.transcript_metadata_ms =
            elapsed_millis(transcript_metadata_started);

        if !self.last_stop_diagnostics.drain_completed {
            return Err("Recording audio queue did not drain before shutdown".to_string());
        }

        // Emit save event with audio and transcript paths
        let save_event = serde_json::json!({
            "audio_file": final_audio_path.as_ref()
                .map(|path| path.to_string_lossy().to_string()),
            "transcript_file": self.meeting_folder.as_ref()
                .map(|f| f.join("transcripts.json").to_string_lossy().to_string()),
            "meeting_name": self.meeting_name,
            "meeting_folder": self.meeting_folder.as_ref()
                .map(|f| f.to_string_lossy().to_string())
        });

        if app.emit("recording-saved", &save_event).is_err() {
            warn!("Failed to emit recording-saved event");
        }

        // Clean up transcript segments
        if let Ok(mut segments) = self.transcript_segments.lock() {
            segments.clear();
        }

        Ok(final_audio_path.map(|path| path.to_string_lossy().to_string()))
    }

    /// Get the meeting folder path (for passing to backend)
    pub fn get_meeting_folder(&self) -> Option<&PathBuf> {
        self.meeting_folder.as_ref()
    }

    /// Get accumulated transcript segments (for reload sync)
    pub fn get_transcript_segments(&self) -> Vec<TranscriptSegment> {
        if let Ok(segments) = self.transcript_segments.lock() {
            segments.clone()
        } else {
            Vec::new()
        }
    }

    /// Get meeting name (for reload sync)
    pub fn get_meeting_name(&self) -> Option<String> {
        self.meeting_name.clone()
    }

    pub fn get_last_stop_diagnostics(&self) -> RecordingSaveDiagnostics {
        self.last_stop_diagnostics
    }
}

impl Default for RecordingSaver {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::{
        load_finalized_transcript_history, RecordingAudioSender, RecordingSaver, TranscriptSegment,
    };
    use crate::audio::recording_state::{AudioChunk, DeviceType};
    use std::io::Write;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use tokio::sync::mpsc;

    fn test_chunk(chunk_id: u64) -> AudioChunk {
        AudioChunk {
            data: vec![0.0; 16],
            sample_rate: 48_000,
            timestamp: chunk_id as f64,
            chunk_id,
            device_type: DeviceType::System,
        }
    }

    #[tokio::test]
    async fn recording_audio_sender_applies_lossless_backpressure_at_capacity() {
        let (sender, mut receiver) = mpsc::channel(2);
        let queue_depth = Arc::new(AtomicUsize::new(0));
        let queue_peak = Arc::new(AtomicUsize::new(0));
        let sender = RecordingAudioSender {
            sender,
            queue_depth: queue_depth.clone(),
            queue_peak: queue_peak.clone(),
        };

        sender.send(test_chunk(0)).await.unwrap();
        sender.send(test_chunk(1)).await.unwrap();
        assert!(tokio::time::timeout(
            tokio::time::Duration::from_millis(10),
            sender.send(test_chunk(2)),
        )
        .await
        .is_err());
        assert_eq!(queue_peak.load(Ordering::Relaxed), 2);

        receiver.recv().await.unwrap();
        queue_depth.fetch_sub(1, Ordering::Relaxed);
        sender.send(test_chunk(2)).await.unwrap();
        assert_eq!(queue_peak.load(Ordering::Relaxed), 2);
    }

    #[test]
    fn initializes_recording_under_configured_base_folder() {
        let root = tempfile::tempdir().unwrap();
        let configured = root.path().join("configured-recordings");
        let mut saver = RecordingSaver::new();
        saver.set_base_folder(configured.clone());

        saver
            .initialize_meeting_folder("Pilot Meeting", false)
            .unwrap();

        let meeting_folder = saver.get_meeting_folder().unwrap();
        assert!(meeting_folder.starts_with(&configured));
        assert!(meeting_folder.join("metadata.json").is_file());
        assert!(!meeting_folder.join(".checkpoints").exists());
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn accumulation_is_active_before_spawn_and_drains_accepted_audio() {
        let root = tempfile::tempdir().unwrap();
        let mut saver = RecordingSaver::new();
        saver.set_base_folder(root.path().to_path_buf());
        saver.set_meeting_name(Some("Drain Accepted Audio".to_string()));

        let sender = saver.start_accumulation(true);
        assert!(saver.is_saving);
        for chunk_id in 0..64 {
            sender
                .send(AudioChunk {
                    data: vec![0.0; 16],
                    sample_rate: 48_000,
                    timestamp: chunk_id as f64,
                    chunk_id,
                    device_type: DeviceType::System,
                })
                .await
                .unwrap();
        }

        assert!(saver.stop_accumulation().await);
        assert!(!saver.is_saving);
        assert!(sender
            .send(AudioChunk {
                data: vec![0.0; 16],
                sample_rate: 48_000,
                timestamp: 65.0,
                chunk_id: 65,
                device_type: DeviceType::System,
            })
            .await
            .is_err());
        let incremental = saver.incremental_saver.as_ref().unwrap().lock().await;
        assert_eq!(incremental.get_total_samples_written(), 64 * 16);
    }

    #[test]
    fn transcript_only_recording_is_marked_completed() {
        let root = tempfile::tempdir().unwrap();
        let mut saver = RecordingSaver::new();
        saver.set_base_folder(root.path().to_path_buf());
        saver
            .initialize_meeting_folder("Transcript Only", false)
            .unwrap();

        saver
            .finalize_transcripts_and_metadata(Some(12.5), "completed")
            .unwrap();

        let meeting_folder = saver.get_meeting_folder().unwrap();
        let metadata: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(meeting_folder.join("metadata.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(metadata["status"], "completed");
        assert_eq!(metadata["duration_seconds"], 12.5);
        assert!(meeting_folder.join("transcripts.json").is_file());
    }

    #[test]
    fn incomplete_recording_is_marked_error() {
        let root = tempfile::tempdir().unwrap();
        let mut saver = RecordingSaver::new();
        saver.set_base_folder(root.path().to_path_buf());
        saver
            .initialize_meeting_folder("Incomplete Recording", false)
            .unwrap();

        saver
            .finalize_transcripts_and_metadata(Some(12.5), "error")
            .unwrap();

        let meeting_folder = saver.get_meeting_folder().unwrap();
        let metadata: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(meeting_folder.join("metadata.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(metadata["status"], "error");
        assert!(meeting_folder.join("transcripts.json").is_file());
    }

    #[test]
    fn in_memory_soak_history_upserts_without_requiring_a_meeting_folder() {
        let saver = RecordingSaver::new();
        let segment = |text: &str| TranscriptSegment {
            id: "soak_7".to_string(),
            text: text.to_string(),
            audio_start_time: 7.0,
            audio_end_time: 8.0,
            duration: 1.0,
            display_time: "00:00:07".to_string(),
            confidence: 1.0,
            sequence_id: 7,
        };

        assert!(saver.add_transcript_segment_in_memory(segment("first")));
        assert!(saver.add_transcript_segment_in_memory(segment("replacement")));
        let history = saver.get_transcript_segments();
        assert_eq!(history.len(), 1);
        assert_eq!(history[0].sequence_id, 7);
        assert_eq!(history[0].text, "replacement");
        assert!(saver.get_meeting_folder().is_none());
    }

    #[test]
    fn live_transcripts_append_a_journal_and_compact_once() {
        let root = tempfile::tempdir().unwrap();
        let mut saver = RecordingSaver::new();
        saver.set_base_folder(root.path().to_path_buf());
        saver
            .initialize_meeting_folder("Journaled Transcript", false)
            .unwrap();
        let folder = saver.get_meeting_folder().unwrap().clone();
        let initial_snapshot = std::fs::read(folder.join("transcripts.json")).unwrap();

        for sequence_id in 0..100 {
            saver.add_transcript_segment(TranscriptSegment {
                id: format!("segment_{sequence_id}"),
                text: "A bounded journal entry".to_string(),
                audio_start_time: sequence_id as f64,
                audio_end_time: sequence_id as f64 + 1.0,
                duration: 1.0,
                display_time: "00:00:00".to_string(),
                confidence: 1.0,
                sequence_id,
            });
        }

        assert_eq!(
            std::fs::read(folder.join("transcripts.json")).unwrap(),
            initial_snapshot,
            "live updates must not rewrite the aggregate snapshot",
        );
        assert_eq!(
            std::fs::read_to_string(folder.join(super::TRANSCRIPT_JOURNAL_FILE))
                .unwrap()
                .lines()
                .filter(|line| !line.trim().is_empty())
                .count(),
            100,
        );

        saver
            .finalize_transcripts_and_metadata(Some(100.0), "completed")
            .unwrap();
        let transcript: serde_json::Value =
            serde_json::from_slice(&std::fs::read(folder.join("transcripts.json")).unwrap())
                .unwrap();
        assert_eq!(transcript["total_segments"], 100);
        assert!(!folder.join(super::TRANSCRIPT_JOURNAL_FILE).exists());
    }

    #[test]
    fn detached_transcript_sink_updates_final_history_and_journal() {
        let root = tempfile::tempdir().unwrap();
        let mut saver = RecordingSaver::new();
        saver.set_base_folder(root.path().to_path_buf());
        saver
            .initialize_meeting_folder("Final drain", false)
            .unwrap();
        let folder = saver.get_meeting_folder().unwrap().clone();
        let sink = saver.transcript_sink();

        assert!(sink.add(TranscriptSegment {
            id: "segment_0".to_string(),
            text: "late final segment".to_string(),
            audio_start_time: 0.0,
            audio_end_time: 1.0,
            duration: 1.0,
            display_time: "00:00:00".to_string(),
            confidence: 1.0,
            sequence_id: 0,
        }));

        assert_eq!(saver.get_transcript_segments().len(), 1);
        assert!(folder.join(super::TRANSCRIPT_JOURNAL_FILE).is_file());
    }

    #[test]
    fn finalized_history_handoff_includes_a_journal_only_tail() {
        let root = tempfile::tempdir().unwrap();
        let mut saver = RecordingSaver::new();
        saver.set_base_folder(root.path().to_path_buf());
        saver
            .initialize_meeting_folder("Final handoff", false)
            .unwrap();
        let folder = saver.get_meeting_folder().unwrap().clone();
        let session_id = "meeting-intel-0123456789abcdef0123456789abcdef";
        saver.set_meeting_id(session_id.to_string());
        let sink = saver.transcript_sink();
        assert!(sink.add(TranscriptSegment {
            id: "segment_4".to_string(),
            text: "durable journal tail".to_string(),
            audio_start_time: 4.0,
            audio_end_time: 5.0,
            duration: 1.0,
            display_time: "00:00:04".to_string(),
            confidence: 1.0,
            sequence_id: 4,
        }));

        let recovered = load_finalized_transcript_history(&folder, session_id).unwrap();
        assert_eq!(recovered.len(), 1);
        assert_eq!(recovered[0].sequence_id, 4);
        assert!(folder.join(super::TRANSCRIPT_JOURNAL_FILE).is_file());
    }

    #[test]
    fn configuring_recordings_root_recovers_ordered_journal_updates() {
        let root = tempfile::tempdir().unwrap();
        let mut saver = RecordingSaver::new();
        saver.set_base_folder(root.path().to_path_buf());
        saver
            .initialize_meeting_folder("Interrupted Transcript", false)
            .unwrap();
        let folder = saver.get_meeting_folder().unwrap().clone();
        let segment = |sequence_id, text: &str| TranscriptSegment {
            id: format!("segment_{sequence_id}"),
            text: text.to_string(),
            audio_start_time: sequence_id as f64,
            audio_end_time: sequence_id as f64 + 1.0,
            duration: 1.0,
            display_time: "00:00:00".to_string(),
            confidence: 1.0,
            sequence_id,
        };
        saver.add_transcript_segment(segment(2, "later"));
        saver.add_transcript_segment(segment(0, "first"));
        saver.add_transcript_segment(segment(0, "corrected"));
        drop(saver);

        let mut recovered = RecordingSaver::new();
        recovered.set_base_folder(root.path().to_path_buf());

        let transcript: serde_json::Value =
            serde_json::from_slice(&std::fs::read(folder.join("transcripts.json")).unwrap())
                .unwrap();
        assert_eq!(transcript["total_segments"], 2);
        assert_eq!(transcript["segments"][0]["sequence_id"], 0);
        assert_eq!(transcript["segments"][0]["text"], "corrected");
        assert_eq!(transcript["segments"][1]["sequence_id"], 2);
        assert!(!folder.join(super::TRANSCRIPT_JOURNAL_FILE).exists());
    }

    #[test]
    fn recovery_keeps_valid_entries_before_a_truncated_journal_tail() {
        let root = tempfile::tempdir().unwrap();
        let mut saver = RecordingSaver::new();
        saver.set_base_folder(root.path().to_path_buf());
        saver
            .initialize_meeting_folder("Truncated Journal", false)
            .unwrap();
        let folder = saver.get_meeting_folder().unwrap().clone();
        saver.add_transcript_segment(TranscriptSegment {
            id: "segment_0".to_string(),
            text: "Durable before crash".to_string(),
            audio_start_time: 0.0,
            audio_end_time: 1.0,
            duration: 1.0,
            display_time: "00:00:00".to_string(),
            confidence: 1.0,
            sequence_id: 0,
        });
        let mut journal = std::fs::OpenOptions::new()
            .append(true)
            .open(folder.join(super::TRANSCRIPT_JOURNAL_FILE))
            .unwrap();
        journal.write_all(b"\n{\"sequence_id\":").unwrap();
        drop(journal);
        drop(saver);

        let mut recovered = RecordingSaver::new();
        recovered.set_base_folder(root.path().to_path_buf());

        let transcript: serde_json::Value =
            serde_json::from_slice(&std::fs::read(folder.join("transcripts.json")).unwrap())
                .unwrap();
        assert_eq!(transcript["total_segments"], 1);
        assert_eq!(transcript["segments"][0]["text"], "Durable before crash");
        assert!(!folder.join(super::TRANSCRIPT_JOURNAL_FILE).exists());
        assert!(std::fs::read_dir(&folder).unwrap().any(|entry| {
            entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".transcripts.corrupt-")
        }));
    }

    #[test]
    fn final_transcript_write_streams_a_large_ordered_history() {
        let root = tempfile::tempdir().unwrap();
        let mut saver = RecordingSaver::new();
        saver.set_base_folder(root.path().to_path_buf());
        saver
            .initialize_meeting_folder("Large Transcript", false)
            .unwrap();
        {
            let mut segments = saver.transcript_segments.lock().unwrap();
            segments.extend((0..10_000).map(|sequence_id| TranscriptSegment {
                id: format!("segment_{sequence_id}"),
                text: "The inventory report does not match the ERP report.".to_string(),
                audio_start_time: sequence_id as f64,
                audio_end_time: sequence_id as f64 + 1.0,
                duration: 1.0,
                display_time: "00:00:00".to_string(),
                confidence: 1.0,
                sequence_id,
            }));
        }

        let folder = saver.get_meeting_folder().unwrap().clone();
        saver.write_transcripts_json(&folder).unwrap();

        let bytes = std::fs::read(folder.join("transcripts.json")).unwrap();
        let transcript: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(transcript["total_segments"], 10_000);
        assert_eq!(transcript["segments"][0]["sequence_id"], 0);
        assert_eq!(transcript["segments"][9_999]["sequence_id"], 9_999);
        assert!(!bytes.windows(2).any(|window| window == b"\n "));
    }
}
