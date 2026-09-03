use super::recording_state::AudioChunk;
use anyhow::{anyhow, Result};
use log::{error, info, warn};
use serde::{Deserialize, Serialize};
use std::io::Write;
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::time::Instant;

use super::ffmpeg::find_ffmpeg_path;

/// Streams audio into a fragmented MP4 so stop-time finalization is constant-time
/// and a crash leaves completed fragments recoverable.
pub struct IncrementalAudioSaver {
    encoder: Option<Child>,
    encoder_stdin: Option<ChildStdin>,
    encoder_failed: bool,
    samples_since_checkpoint: usize,
    total_samples_written: u64,
    checkpoint_interval_samples: usize, // 30s at 48kHz = 1,440,000 samples
    checkpoint_count: u32,
    checkpoints_dir: PathBuf,
    meeting_folder: PathBuf,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct AudioFinalizeDiagnostics {
    pub final_checkpoint_ms: u64,
    pub merge_ms: u64,
    pub checkpoint_cleanup_ms: u64,
}

fn elapsed_millis(started: Instant) -> u64 {
    started.elapsed().as_millis().min(u64::MAX as u128) as u64
}

impl IncrementalAudioSaver {
    /// Create a new fragmented audio saver.
    ///
    /// # Arguments
    /// * `meeting_folder` - Path to the meeting folder (contains .checkpoints/)
    /// * `sample_rate` - Sample rate of audio (typically 48000)
    pub fn new(meeting_folder: PathBuf, sample_rate: u32) -> Result<Self> {
        let checkpoints_dir = meeting_folder.join(".checkpoints");

        // Verify checkpoints directory exists
        if !checkpoints_dir.exists() {
            return Err(anyhow!("Checkpoint directory is unavailable"));
        }

        let in_progress_path = checkpoints_dir.join("audio.inprogress.mp4");
        let ffmpeg_path =
            find_ffmpeg_path().ok_or_else(|| anyhow!("FFmpeg is unavailable for recording"))?;
        let mut command = Self::build_encoder_command(&ffmpeg_path, sample_rate, &in_progress_path);
        let mut encoder = command.spawn()?;
        let Some(encoder_stdin) = encoder.stdin.take() else {
            let _ = encoder.kill();
            let _ = encoder.wait();
            return Err(anyhow!("FFmpeg recording input is unavailable"));
        };

        Ok(Self {
            encoder: Some(encoder),
            encoder_stdin: Some(encoder_stdin),
            encoder_failed: false,
            samples_since_checkpoint: 0,
            total_samples_written: 0,
            checkpoint_interval_samples: sample_rate as usize * 30, // 30 seconds
            checkpoint_count: 0,
            checkpoints_dir,
            meeting_folder,
        })
    }

    /// Stream an audio chunk to the fragmented recording file.
    pub fn add_chunk(&mut self, chunk: AudioChunk) -> Result<()> {
        if chunk.data.is_empty() || self.encoder_failed {
            return Ok(());
        }
        let write_result = self
            .encoder_stdin
            .as_mut()
            .ok_or_else(|| anyhow!("FFmpeg recording input is closed"))?
            .write_all(bytemuck::cast_slice(&chunk.data));
        if write_result.is_err() {
            self.encoder_failed = true;
            self.encoder_stdin.take();
            return Err(anyhow!("FFmpeg could not persist recording audio"));
        }
        self.total_samples_written = self
            .total_samples_written
            .saturating_add(chunk.data.len() as u64);
        self.samples_since_checkpoint = self
            .samples_since_checkpoint
            .saturating_add(chunk.data.len());
        while self.samples_since_checkpoint >= self.checkpoint_interval_samples {
            self.samples_since_checkpoint -= self.checkpoint_interval_samples;
            self.checkpoint_count = self.checkpoint_count.saturating_add(1);
        }

        Ok(())
    }

    /// Finalize the recording by closing FFmpeg and atomically promoting its output.
    ///
    /// Returns the path to the final audio.mp4 file.
    pub async fn finalize(&mut self) -> Result<PathBuf> {
        self.finalize_with_diagnostics().await.map(|(path, _)| path)
    }

    pub async fn finalize_with_diagnostics(
        &mut self,
    ) -> Result<(PathBuf, AudioFinalizeDiagnostics)> {
        info!("Finalizing incremental recording...");
        let mut diagnostics = AudioFinalizeDiagnostics::default();

        let final_checkpoint_started = Instant::now();
        if self.encoder_failed {
            self.encoder_stdin.take();
            if let Some(mut encoder) = self.encoder.take() {
                let _ = encoder.wait();
            }
            return Err(anyhow!("Recording audio persistence failed"));
        }
        if self.total_samples_written == 0 {
            self.encoder_stdin.take();
            if let Some(mut encoder) = self.encoder.take() {
                let _ = encoder.wait();
            }
            return Err(anyhow!("No audio was captured for this recording"));
        }
        if self.samples_since_checkpoint > 0 {
            self.checkpoint_count = self.checkpoint_count.saturating_add(1);
            self.samples_since_checkpoint = 0;
        }
        self.encoder_stdin.take();
        let status = self
            .encoder
            .take()
            .ok_or_else(|| anyhow!("FFmpeg recording process is unavailable"))?
            .wait()?;
        if !status.success() {
            return Err(anyhow!("FFmpeg recording process failed"));
        }
        diagnostics.final_checkpoint_ms = elapsed_millis(final_checkpoint_started);

        let final_audio_path = self.meeting_folder.join("audio.mp4");
        let merge_started = Instant::now();
        let in_progress_path = self.checkpoints_dir.join("audio.inprogress.mp4");
        if !in_progress_path.is_file() || in_progress_path.metadata()?.len() == 0 {
            return Err(anyhow!("Recorded audio file was not created"));
        }
        std::fs::rename(&in_progress_path, &final_audio_path)?;
        diagnostics.merge_ms = elapsed_millis(merge_started);

        // Clean up checkpoints directory
        info!("Cleaning up {} checkpoint files", self.checkpoint_count);
        let cleanup_started = Instant::now();
        if let Err(e) = std::fs::remove_dir_all(&self.checkpoints_dir) {
            warn!("Failed to clean up checkpoints directory: {}", e);
            // Non-fatal - user can manually delete
        }
        diagnostics.checkpoint_cleanup_ms = elapsed_millis(cleanup_started);

        info!("Finalized recording audio");

        Ok((final_audio_path, diagnostics))
    }

    fn build_encoder_command(
        ffmpeg_path: &std::path::Path,
        sample_rate: u32,
        output: &std::path::Path,
    ) -> Command {
        let mut command = Command::new(ffmpeg_path);
        command
            .args(["-hide_banner", "-loglevel", "error"])
            .args(["-f", "f32le", "-ar"])
            .arg(sample_rate.to_string())
            .args(["-ac", "1", "-i", "pipe:0"])
            .args(["-c:a", "aac", "-b:a", "192k", "-profile:a", "aac_low"])
            // A front-loaded moov plus 30-second fragments leaves bounded,
            // crash-recoverable media without a recording-length stop remux.
            .args(["-movflags", "+empty_moov+default_base_moof"])
            .args(["-frag_duration", "30000000", "-flush_packets", "1"])
            .args(["-f", "mp4", "-y"])
            .arg(output)
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null());

        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        command
    }

    fn build_merge_command(
        ffmpeg_path: &std::path::Path,
        list_file: &std::path::Path,
        output: &std::path::Path,
    ) -> std::process::Command {
        let mut command = std::process::Command::new(ffmpeg_path);
        command
            .args(["-hide_banner", "-loglevel", "error", "-nostdin"])
            .args(["-probesize", "32", "-analyzeduration", "0"])
            .args(["-f", "concat", "-safe", "0", "-i"])
            .arg(list_file)
            // Saved meetings are local files, not progressive web streams.
            // Avoid faststart's size-dependent second pass over multi-hour audio.
            .args(["-map", "0:a:0", "-c", "copy", "-y"])
            .arg(output);

        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        command
    }

    /// Get the meeting folder path
    pub fn get_meeting_folder(&self) -> &PathBuf {
        &self.meeting_folder
    }

    /// Get current checkpoint count
    pub fn get_checkpoint_count(&self) -> u32 {
        self.checkpoint_count
    }

    #[cfg(test)]
    pub(crate) fn get_total_samples_written(&self) -> u64 {
        self.total_samples_written
    }
}

impl Drop for IncrementalAudioSaver {
    fn drop(&mut self) {
        self.encoder_stdin.take();
        if let Some(mut encoder) = self.encoder.take() {
            let _ = encoder.kill();
            let _ = encoder.wait();
        }
    }
}

/// Audio recovery status for transcript recovery feature
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AudioRecoveryStatus {
    pub status: String, // "success" | "partial" | "failed" | "none"
    pub chunk_count: u32,
    pub estimated_duration_seconds: f64,
    pub audio_file_path: Option<String>,
    pub message: String,
}

/// Recover fragmented audio or legacy checkpoint files after a crash.
#[tauri::command]
pub async fn recover_audio_from_checkpoints(
    meeting_folder: String,
    _sample_rate: u32,
) -> Result<AudioRecoveryStatus, String> {
    info!("Starting local audio recovery");

    let folder_path = PathBuf::from(&meeting_folder);
    let checkpoints_dir = folder_path.join(".checkpoints");
    let finalized_audio = folder_path.join("audio.mp4");

    if finalized_audio.is_file()
        && finalized_audio
            .metadata()
            .map(|metadata| metadata.len())
            .unwrap_or(0)
            > 0
    {
        return Ok(AudioRecoveryStatus {
            status: "success".to_string(),
            chunk_count: 0,
            estimated_duration_seconds: 0.0,
            audio_file_path: finalized_audio.to_str().map(str::to_string),
            message: "Finalized local audio is already available".to_string(),
        });
    }

    // Check if checkpoints directory exists
    if !checkpoints_dir.exists() {
        info!("No audio checkpoint directory was found");
        return Ok(AudioRecoveryStatus {
            status: "none".to_string(),
            chunk_count: 0,
            estimated_duration_seconds: 0.0,
            audio_file_path: None,
            message: "No audio checkpoints found".to_string(),
        });
    }

    let in_progress_path = checkpoints_dir.join("audio.inprogress.mp4");
    if in_progress_path.is_file()
        && in_progress_path
            .metadata()
            .map(|metadata| metadata.len())
            .unwrap_or(0)
            > 0
    {
        let output_path = folder_path.join("audio.mp4");
        if output_path.exists() {
            return Err("A final audio file already exists; recovery was not applied".to_string());
        }
        std::fs::rename(&in_progress_path, &output_path)
            .map_err(|_| "Failed to recover the fragmented audio file".to_string())?;
        return Ok(AudioRecoveryStatus {
            status: "partial".to_string(),
            chunk_count: 1,
            estimated_duration_seconds: 0.0,
            audio_file_path: output_path.to_str().map(str::to_string),
            message: "Recovered audio through the last completed fragment".to_string(),
        });
    }

    // Older builds stored one MP4 per checkpoint. Keep their recovery path.
    let mut checkpoint_files: Vec<_> = std::fs::read_dir(&checkpoints_dir)
        .map_err(|_| "Failed to read audio checkpoints".to_string())?
        .filter_map(|entry| entry.ok())
        .filter(|entry| entry.path().extension().and_then(|s| s.to_str()) == Some("mp4"))
        .collect();

    if checkpoint_files.is_empty() {
        info!("No audio checkpoint files were found");
        return Ok(AudioRecoveryStatus {
            status: "none".to_string(),
            chunk_count: 0,
            estimated_duration_seconds: 0.0,
            audio_file_path: None,
            message: "No audio checkpoint files found".to_string(),
        });
    }

    // Sort by filename (audio_chunk_000.mp4, audio_chunk_001.mp4, etc.)
    checkpoint_files.sort_by_key(|entry| entry.path());

    let chunk_count = checkpoint_files.len() as u32;
    let estimated_duration = (chunk_count as f64) * 30.0; // 30 seconds per chunk

    info!(
        "Found {} checkpoint files, estimated duration: {:.2}s",
        chunk_count, estimated_duration
    );

    // Create FFmpeg concat file
    let concat_file_path = checkpoints_dir.join("concat_list.txt");
    let mut concat_content = String::new();

    for entry in &checkpoint_files {
        let path = entry
            .path()
            .canonicalize()
            .map_err(|_| "Failed to prepare an audio checkpoint".to_string())?;
        concat_content.push_str(&format!("file '{}'\n", path.display()));
    }

    std::fs::write(&concat_file_path, concat_content)
        .map_err(|_| "Failed to prepare audio recovery".to_string())?;

    // Run FFmpeg to merge chunks
    let output_path = folder_path.join("audio.mp4");
    let output_path_str = output_path
        .to_str()
        .ok_or("Invalid output path")?
        .to_string();

    let ffmpeg_path = find_ffmpeg_path()
        .ok_or_else(|| "FFmpeg not found. Please install FFmpeg to recover audio.".to_string())?;
    let mut command =
        IncrementalAudioSaver::build_merge_command(&ffmpeg_path, &concat_file_path, &output_path);

    let ffmpeg_result = command.output();

    match ffmpeg_result {
        Ok(output) if output.status.success() => {
            // Clean up concat file
            let _ = std::fs::remove_file(concat_file_path);

            info!("Successfully recovered {} audio chunks", chunk_count);

            Ok(AudioRecoveryStatus {
                status: "success".to_string(),
                chunk_count,
                estimated_duration_seconds: estimated_duration,
                audio_file_path: Some(output_path_str),
                message: format!("Successfully recovered {} audio chunks", chunk_count),
            })
        }
        Ok(_) => {
            error!("Local audio recovery failed");
            Ok(AudioRecoveryStatus {
                status: "failed".to_string(),
                chunk_count,
                estimated_duration_seconds: estimated_duration,
                audio_file_path: None,
                message: "Local audio recovery failed".to_string(),
            })
        }
        Err(_) => {
            error!("Local audio recovery could not start");
            Ok(AudioRecoveryStatus {
                status: "failed".to_string(),
                chunk_count,
                estimated_duration_seconds: estimated_duration,
                audio_file_path: None,
                message: "Local audio recovery could not start".to_string(),
            })
        }
    }
}

/// Clean up checkpoint files after successful recording or recovery
/// This command is called by the frontend after successful save to clean up checkpoint files
#[tauri::command]
pub async fn cleanup_checkpoints(meeting_folder: String) -> Result<(), String> {
    info!("Cleaning up local audio checkpoints");

    let folder_path = PathBuf::from(&meeting_folder);
    let checkpoints_dir = folder_path.join(".checkpoints");

    if checkpoints_dir.exists() {
        std::fs::remove_dir_all(&checkpoints_dir)
            .map_err(|_| "Failed to remove audio checkpoints".to_string())?;
        info!("Successfully cleaned up checkpoints directory");
    } else {
        info!("No checkpoints directory to clean up");
    }

    Ok(())
}

/// Check if a meeting folder has audio checkpoint files
/// Returns true if .checkpoints/ directory exists and contains .mp4 files
#[tauri::command]
pub async fn has_audio_checkpoints(meeting_folder: String) -> Result<bool, String> {
    let folder_path = PathBuf::from(&meeting_folder);
    let checkpoints_dir = folder_path.join(".checkpoints");

    // Check if checkpoints directory exists
    if !checkpoints_dir.exists() {
        return Ok(false);
    }

    // Scan for .mp4 checkpoint files
    let has_mp4_files = std::fs::read_dir(&checkpoints_dir)
        .map_err(|e| format!("Failed to read checkpoints directory: {}", e))?
        .filter_map(|entry| entry.ok())
        .any(|entry| entry.path().extension().and_then(|s| s.to_str()) == Some("mp4"));

    Ok(has_mp4_files)
}

/// Check whether a recovery folder contains finalized audio or recoverable
/// fragments. This intentionally preserves the folder after normal native
/// finalization has already removed checkpoints.
#[tauri::command]
pub async fn has_recoverable_audio(meeting_folder: String) -> Result<bool, String> {
    let folder_path = PathBuf::from(&meeting_folder);
    let finalized_audio = folder_path.join("audio.mp4");
    if finalized_audio.is_file()
        && finalized_audio
            .metadata()
            .map(|metadata| metadata.len())
            .unwrap_or(0)
            > 0
    {
        return Ok(true);
    }
    has_audio_checkpoints(meeting_folder).await
}

#[cfg(test)]
mod tests {
    use super::super::recording_state::DeviceType;
    use super::*;
    use tempfile::tempdir;

    #[tokio::test]
    async fn finalized_audio_remains_recoverable_after_checkpoints_are_cleaned() {
        let directory = tempdir().unwrap();
        let audio_path = directory.path().join("audio.mp4");
        std::fs::write(&audio_path, b"finalized-audio").unwrap();
        let folder = directory.path().to_string_lossy().into_owned();

        assert!(has_recoverable_audio(folder.clone()).await.unwrap());
        let result = recover_audio_from_checkpoints(folder, 48_000)
            .await
            .unwrap();
        assert_eq!(result.status, "success");
        assert_eq!(result.audio_file_path.as_deref(), audio_path.to_str());
    }

    #[test]
    fn merge_command_uses_bounded_probe_and_explicit_audio_copy() {
        let command = IncrementalAudioSaver::build_merge_command(
            std::path::Path::new("ffmpeg"),
            std::path::Path::new("concat.txt"),
            std::path::Path::new("audio.mp4"),
        );
        let args: Vec<String> = command
            .get_args()
            .map(|arg| arg.to_string_lossy().into_owned())
            .collect();

        assert!(args.windows(2).any(|pair| pair == ["-probesize", "32"]));
        assert!(args
            .windows(2)
            .any(|pair| pair == ["-analyzeduration", "0"]));
        assert!(args.windows(2).any(|pair| pair == ["-map", "0:a:0"]));
        assert!(args.windows(2).any(|pair| pair == ["-c", "copy"]));
        assert!(args.windows(2).any(|pair| pair == ["-loglevel", "error"]));
        assert!(!args.iter().any(|arg| arg == "+faststart"));
        assert_eq!(args.last().map(String::as_str), Some("audio.mp4"));
    }

    #[test]
    fn recording_encoder_writes_bounded_fragmented_mp4() {
        let command = IncrementalAudioSaver::build_encoder_command(
            std::path::Path::new("ffmpeg"),
            48_000,
            std::path::Path::new("audio.inprogress.mp4"),
        );
        let args: Vec<String> = command
            .get_args()
            .map(|arg| arg.to_string_lossy().into_owned())
            .collect();

        assert!(args
            .windows(2)
            .any(|pair| pair == ["-frag_duration", "30000000"]));
        assert!(args
            .iter()
            .any(|arg| arg == "+empty_moov+default_base_moof"));
        assert!(args.windows(2).any(|pair| pair == ["-i", "pipe:0"]));
        assert_eq!(
            args.last().map(String::as_str),
            Some("audio.inprogress.mp4")
        );
    }

    #[test]
    fn audio_checkpoint_logs_do_not_interpolate_paths_or_raw_errors() {
        let source = include_str!("incremental_saver.rs");
        let source = &source[..source.rfind("mod tests {").unwrap()];

        for forbidden in [
            "Starting audio recovery for folder",
            "No checkpoints directory found at",
            "Using FFmpeg at",
            "Successfully recovered audio:",
            "FFmpeg recovery failed:",
            "Failed to run FFmpeg:",
            "Cleaning up checkpoints for folder",
        ] {
            assert!(
                !source.contains(forbidden),
                "private log remains: {forbidden}"
            );
        }
    }

    #[tokio::test]
    async fn test_checkpoint_creation() {
        // Create temp meeting folder
        let temp_dir = tempdir().unwrap();
        let meeting_folder = temp_dir.path().join("Test_Meeting");
        std::fs::create_dir_all(&meeting_folder).unwrap();
        std::fs::create_dir_all(meeting_folder.join(".checkpoints")).unwrap();

        let mut saver = IncrementalAudioSaver::new(meeting_folder.clone(), 48000).unwrap();

        // Add 60 seconds worth of audio (should create 2 checkpoints)
        for i in 0..120 {
            // 120 chunks of 0.5s each
            let chunk = AudioChunk {
                data: vec![0.5f32; 24000], // 0.5s at 48kHz
                sample_rate: 48000,
                timestamp: i as f64 * 0.5, // timestamp in seconds
                chunk_id: i as u64,
                device_type: DeviceType::Microphone,
            };
            saver.add_chunk(chunk).unwrap();
        }

        // Verify 2 checkpoints created
        assert_eq!(saver.checkpoint_count, 2);

        // Finalize and verify the already-encoded file is promoted atomically.
        let final_path = saver.finalize().await.unwrap();
        assert!(final_path.exists());
        assert!(final_path.metadata().unwrap().len() > 0);

        // Verify checkpoints directory deleted
        assert!(!meeting_folder.join(".checkpoints").exists());
    }

    #[tokio::test]
    async fn test_empty_recording() {
        let temp_dir = tempdir().unwrap();
        let meeting_folder = temp_dir.path().join("Empty_Test");
        std::fs::create_dir_all(&meeting_folder).unwrap();
        std::fs::create_dir_all(meeting_folder.join(".checkpoints")).unwrap();

        let mut saver = IncrementalAudioSaver::new(meeting_folder.clone(), 48000).unwrap();

        // Try to finalize without adding any chunks
        let result = saver.finalize().await;
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("No audio was captured"));
    }

    #[tokio::test]
    async fn encoder_failure_is_sticky_without_repeated_chunk_errors() {
        let temp_dir = tempdir().unwrap();
        let meeting_folder = temp_dir.path().join("Failed_Encoder");
        std::fs::create_dir_all(meeting_folder.join(".checkpoints")).unwrap();
        let mut saver = IncrementalAudioSaver::new(meeting_folder, 48_000).unwrap();

        let encoder = saver.encoder.as_mut().unwrap();
        encoder.kill().unwrap();
        encoder.wait().unwrap();

        let chunk = || AudioChunk {
            data: vec![0.0; 48_000],
            sample_rate: 48_000,
            timestamp: 0.0,
            chunk_id: 0,
            device_type: DeviceType::Microphone,
        };
        assert!(saver.add_chunk(chunk()).is_err());
        assert!(saver.add_chunk(chunk()).is_ok());
        assert!(saver
            .finalize()
            .await
            .unwrap_err()
            .to_string()
            .contains("persistence failed"));
    }

    #[tokio::test]
    async fn recovers_fragmented_in_progress_audio_without_remuxing() {
        let temp_dir = tempdir().unwrap();
        let meeting_folder = temp_dir.path().join("Interrupted");
        let checkpoints = meeting_folder.join(".checkpoints");
        std::fs::create_dir_all(&checkpoints).unwrap();
        std::fs::write(
            checkpoints.join("audio.inprogress.mp4"),
            b"fragmented-audio",
        )
        .unwrap();

        let result =
            recover_audio_from_checkpoints(meeting_folder.to_string_lossy().into_owned(), 48_000)
                .await
                .unwrap();

        assert_eq!(result.status, "partial");
        assert!(meeting_folder.join("audio.mp4").is_file());
        assert!(!checkpoints.join("audio.inprogress.mp4").exists());
    }
}
