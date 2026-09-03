use anyhow::Result;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{Sample, SampleFormat, SampleRate, StreamConfig};
use log::{debug, error, info, warn};
use serde::Serialize;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::Arc;
use std::thread;
use std::time::Duration as StdDuration;
use tauri::{AppHandle, Emitter, Runtime};
use tokio::sync::Mutex;
use tokio::time::{interval, Duration};

use super::audio_processing::audio_to_mono;

#[derive(Debug, Serialize, Clone)]
pub struct AudioLevelData {
    pub device_name: String,
    pub device_type: String, // "input" or "output"
    pub rms_level: f32,      // RMS level (0.0 to 1.0)
    pub peak_level: f32,     // Peak level (0.0 to 1.0)
    pub is_active: bool,     // Whether audio is being detected
}

#[derive(Debug, Serialize, Clone)]
pub struct AudioLevelUpdate {
    pub timestamp: u64,
    pub levels: Vec<AudioLevelData>,
}

pub struct AudioLevelMonitor {
    monitored_devices: Arc<Mutex<Vec<String>>>,
    streams: Arc<Mutex<Vec<cpal::Stream>>>,
}

impl AudioLevelMonitor {
    pub fn new() -> Self {
        Self {
            monitored_devices: Arc::new(Mutex::new(Vec::new())),
            streams: Arc::new(Mutex::new(Vec::new())),
        }
    }

    /// Start monitoring audio levels for specified devices
    pub async fn start_monitoring<R: Runtime>(
        &mut self,
        app_handle: AppHandle<R>,
        device_names: Vec<String>,
    ) -> Result<()> {
        if AUDIO_LEVEL_STATE.is_monitoring.load(Ordering::SeqCst) {
            // Stop any existing monitoring
            AUDIO_LEVEL_STATE
                .is_monitoring
                .store(false, Ordering::SeqCst);
        }

        info!(
            "Starting real audio level monitoring for {} configured device(s)",
            device_names.len()
        );

        AUDIO_LEVEL_STATE
            .is_monitoring
            .store(true, Ordering::SeqCst);
        *self.monitored_devices.lock().await = device_names.clone();

        // Clear existing streams
        {
            let mut streams = self.streams.lock().await;
            streams.clear();
        }

        let host = cpal::default_host();
        let level_data = Arc::new(Mutex::new(Vec::<AudioLevelData>::new()));

        // Create audio streams for each device
        let mut created_streams = 0usize;
        for device_name in &device_names {
            if let Ok(device) = self.find_device_by_name(&host, device_name) {
                if let Ok(stream) = self
                    .create_level_stream(&device, device_name, level_data.clone())
                    .await
                {
                    let mut streams = self.streams.lock().await;
                    streams.push(stream);
                    created_streams += 1;
                } else {
                    warn!("Failed to create audio stream for device: {}", device_name);
                }
            } else {
                warn!("Device not found: {}", device_name);
            }
        }
        if created_streams == 0 {
            AUDIO_LEVEL_STATE
                .is_monitoring
                .store(false, Ordering::SeqCst);
            return Err(anyhow::anyhow!(
                "No usable audio level device was available"
            ));
        }

        // Start emission task
        let app_handle_clone = app_handle.clone();
        let level_data_clone = level_data.clone();

        tokio::spawn(async move {
            let mut interval = interval(Duration::from_millis(100)); // Update every 100ms

            while AUDIO_LEVEL_STATE.is_monitoring.load(Ordering::SeqCst) {
                interval.tick().await;

                let levels = {
                    let mut data = level_data_clone.lock().await;
                    let current_levels = data.clone();
                    data.clear(); // Reset for next interval
                    current_levels
                };

                if !levels.is_empty() {
                    let update = AudioLevelUpdate {
                        timestamp: std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_millis() as u64,
                        levels,
                    };

                    if let Err(e) = app_handle_clone.emit("audio-levels", &update) {
                        error!("Failed to emit audio levels: {}", e);
                    }
                }
            }
        });

        Ok(())
    }

    /// Stop monitoring audio levels
    pub async fn stop_monitoring(&self) -> Result<()> {
        info!("Stopping audio level monitoring");

        AUDIO_LEVEL_STATE
            .is_monitoring
            .store(false, Ordering::SeqCst);

        // Stop all streams
        {
            let mut streams = self.streams.lock().await;
            streams.clear(); // Dropping streams stops them
        }

        self.monitored_devices.lock().await.clear();

        Ok(())
    }

    /// Check if currently monitoring
    pub fn is_monitoring(&self) -> bool {
        AUDIO_LEVEL_STATE.is_monitoring.load(Ordering::SeqCst)
    }

    /// Find a CPAL device by name
    fn find_device_by_name(&self, host: &cpal::Host, device_name: &str) -> Result<cpal::Device> {
        if device_name == "default" {
            return host
                .default_input_device()
                .ok_or_else(|| anyhow::anyhow!("Default input device is unavailable"));
        }
        let base_name = configured_device_base_name(device_name);

        // Try input devices first
        if let Ok(input_devices) = host.input_devices() {
            for device in input_devices {
                if let Ok(name) = device.name() {
                    if name == base_name {
                        return Ok(device);
                    }
                }
            }
        }

        // Try output devices
        if let Ok(output_devices) = host.output_devices() {
            for device in output_devices {
                if let Ok(name) = device.name() {
                    if name == base_name {
                        return Ok(device);
                    }
                }
            }
        }

        Err(anyhow::anyhow!("Device not found: {}", device_name))
    }

    /// Create an audio stream for level monitoring
    async fn create_level_stream(
        &self,
        device: &cpal::Device,
        device_name: &str,
        level_data: Arc<Mutex<Vec<AudioLevelData>>>,
    ) -> Result<cpal::Stream> {
        let device_name = device_name.to_string();

        // Determine if this is an input or output device and get appropriate config
        let (config, is_input) = if let Ok(input_config) = device.default_input_config() {
            (input_config, true)
        } else if let Ok(output_config) = device.default_output_config() {
            (output_config, false)
        } else {
            return Err(anyhow::anyhow!(
                "Failed to get any config for device: {}",
                device_name
            ));
        };

        let sample_rate = config.sample_rate().0;
        let channels = config.channels();
        let sample_format = config.sample_format();

        debug!(
            "Creating audio level stream for {}: {}Hz, {} channels, {:?}, is_input: {}",
            device_name, sample_rate, channels, sample_format, is_input
        );

        // Determine device type
        let device_type = if is_input { "input" } else { "output" };

        // Create stream config
        let stream_config = StreamConfig {
            channels,
            sample_rate: SampleRate(sample_rate),
            buffer_size: cpal::BufferSize::Default,
        };

        let level_data_clone = level_data.clone();
        let device_name_clone = device_name.clone();
        let device_type_clone = device_type.to_string();

        match sample_format {
            SampleFormat::F32 => {
                // The Windows CPAL/WASAPI backend exposes output loopback capture
                // through an input stream on the selected output device.
                let stream = device.build_input_stream(
                    &stream_config,
                    move |data: &[f32], _: &cpal::InputCallbackInfo| {
                        process_audio_levels(
                            data,
                            channels,
                            &device_name_clone,
                            &device_type_clone,
                            level_data_clone.clone(),
                        );
                    },
                    |err| error!("Audio stream error: {}", err),
                    None,
                )?;

                stream.play()?;
                Ok(stream)
            }
            SampleFormat::I16 => {
                let stream = device.build_input_stream(
                    &stream_config,
                    move |data: &[i16], _: &cpal::InputCallbackInfo| {
                        let f32_data: Vec<f32> = data.iter().map(|&s| s.to_sample()).collect();
                        process_audio_levels(
                            &f32_data,
                            channels,
                            &device_name_clone,
                            &device_type_clone,
                            level_data_clone.clone(),
                        );
                    },
                    |err| error!("Audio stream error: {}", err),
                    None,
                )?;

                stream.play()?;
                Ok(stream)
            }
            SampleFormat::U16 => {
                let stream = device.build_input_stream(
                    &stream_config,
                    move |data: &[u16], _: &cpal::InputCallbackInfo| {
                        let f32_data: Vec<f32> = data.iter().map(|&s| s.to_sample()).collect();
                        process_audio_levels(
                            &f32_data,
                            channels,
                            &device_name_clone,
                            &device_type_clone,
                            level_data_clone.clone(),
                        );
                    },
                    |err| error!("Audio stream error: {}", err),
                    None,
                )?;

                stream.play()?;
                Ok(stream)
            }
            _ => Err(anyhow::anyhow!(
                "Unsupported sample format: {:?}",
                sample_format
            )),
        }
    }
}

fn configured_device_base_name(device_name: &str) -> &str {
    device_name
        .strip_suffix(" (input)")
        .or_else(|| device_name.strip_suffix(" (output)"))
        .unwrap_or(device_name)
}

/// Process audio data and calculate levels
fn process_audio_levels(
    data: &[f32],
    channels: u16,
    device_name: &str,
    device_type: &str,
    level_data: Arc<Mutex<Vec<AudioLevelData>>>,
) {
    if data.is_empty() {
        return;
    }

    // Convert to mono if needed
    let mono_data = if channels > 1 {
        audio_to_mono(data, channels)
    } else {
        data.to_vec()
    };

    // Calculate RMS level
    let rms = if !mono_data.is_empty() {
        (mono_data.iter().map(|&x| x * x).sum::<f32>() / mono_data.len() as f32).sqrt()
    } else {
        0.0
    };

    // Calculate peak level
    let peak = mono_data.iter().map(|&x| x.abs()).fold(0.0, f32::max);

    // Determine if audio is active (threshold for noise floor)
    let is_active = rms > 0.001; // Adjust threshold as needed

    let level_data_entry = AudioLevelData {
        device_name: device_name.to_string(),
        device_type: device_type.to_string(),
        rms_level: rms.min(1.0), // Clamp to 0-1 range
        peak_level: peak.min(1.0),
        is_active,
    };

    // Update level data (non-blocking)
    if let Ok(mut levels) = level_data.try_lock() {
        // Remove old entry for this device if exists
        levels.retain(|l| l.device_name != device_name);
        levels.push(level_data_entry);
    }
}

// Global state for audio level monitoring

struct AudioLevelState {
    is_monitoring: AtomicBool,
    // We'll manage streams differently to avoid Send issues
}

lazy_static::lazy_static! {
    static ref AUDIO_LEVEL_STATE: AudioLevelState = AudioLevelState {
        is_monitoring: AtomicBool::new(false),
    };
    static ref AUDIO_LEVEL_CONTROLLER: std::sync::Mutex<AudioLevelController> =
        std::sync::Mutex::new(AudioLevelController::default());
}

#[derive(Default)]
struct AudioLevelController {
    stop_sender: Option<mpsc::Sender<()>>,
    thread: Option<thread::JoinHandle<()>>,
}

pub fn start_monitoring_thread<R: Runtime>(
    app_handle: AppHandle<R>,
    device_names: Vec<String>,
) -> Result<()> {
    stop_monitoring_thread()?;
    let (stop_sender, stop_receiver) = mpsc::channel();
    let (ready_sender, ready_receiver) = mpsc::sync_channel(1);
    let handle = thread::Builder::new()
        .name("meetily-audio-level-monitor".to_string())
        .spawn(move || {
            let runtime = match tokio::runtime::Builder::new_current_thread()
                .enable_time()
                .build()
            {
                Ok(runtime) => runtime,
                Err(error) => {
                    let _ = ready_sender.send(Err(error.to_string()));
                    return;
                }
            };
            runtime.block_on(async move {
                let mut monitor = AudioLevelMonitor::new();
                match monitor.start_monitoring(app_handle, device_names).await {
                    Ok(()) => {
                        let _ = ready_sender.send(Ok(()));
                    }
                    Err(error) => {
                        let _ = ready_sender.send(Err(error.to_string()));
                        return;
                    }
                }
                loop {
                    match stop_receiver.try_recv() {
                        Ok(()) | Err(mpsc::TryRecvError::Disconnected) => break,
                        Err(mpsc::TryRecvError::Empty) => {
                            tokio::time::sleep(Duration::from_millis(50)).await;
                        }
                    }
                }
                let _ = monitor.stop_monitoring().await;
            });
        })?;

    match ready_receiver.recv_timeout(StdDuration::from_secs(3)) {
        Ok(Ok(())) => {
            let mut controller = AUDIO_LEVEL_CONTROLLER
                .lock()
                .map_err(|_| anyhow::anyhow!("Audio level monitor state is unavailable"))?;
            controller.stop_sender = Some(stop_sender);
            controller.thread = Some(handle);
            Ok(())
        }
        Ok(Err(error)) => {
            let _ = handle.join();
            Err(anyhow::anyhow!(error))
        }
        Err(_) => {
            let _ = stop_sender.send(());
            let _ = handle.join();
            Err(anyhow::anyhow!("Audio level monitor startup timed out"))
        }
    }
}

pub fn stop_monitoring_thread() -> Result<()> {
    let (stop_sender, handle) = {
        let mut controller = AUDIO_LEVEL_CONTROLLER
            .lock()
            .map_err(|_| anyhow::anyhow!("Audio level monitor state is unavailable"))?;
        (controller.stop_sender.take(), controller.thread.take())
    };
    if let Some(sender) = stop_sender {
        let _ = sender.send(());
    }
    if let Some(handle) = handle {
        handle
            .join()
            .map_err(|_| anyhow::anyhow!("Audio level monitor thread stopped unexpectedly"))?;
    }
    AUDIO_LEVEL_STATE
        .is_monitoring
        .store(false, Ordering::SeqCst);
    Ok(())
}

/// Global function to check if monitoring is active
pub fn is_monitoring() -> bool {
    AUDIO_LEVEL_STATE.is_monitoring.load(Ordering::SeqCst)
}

/// Global function to stop monitoring
pub async fn stop_monitoring() -> Result<()> {
    AUDIO_LEVEL_STATE
        .is_monitoring
        .store(false, Ordering::SeqCst);
    info!("Audio level monitoring stopped globally");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn process_audio_levels_preserves_output_provenance() {
        let levels = Arc::new(Mutex::new(Vec::new()));

        process_audio_levels(
            &[0.25, -0.25, 0.5, -0.5],
            1,
            "meeting-output",
            "output",
            levels.clone(),
        );

        let captured = levels.lock().await;
        assert_eq!(captured.len(), 1);
        assert_eq!(captured[0].device_name, "meeting-output");
        assert_eq!(captured[0].device_type, "output");
        assert!(captured[0].is_active);
        assert!((captured[0].peak_level - 0.5).abs() < f32::EPSILON);
    }

    #[test]
    fn monitor_resolves_configured_device_suffixes() {
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

    #[cfg(target_os = "windows")]
    #[tokio::test]
    #[ignore = "requires a real Windows default output device and an installed SAPI voice"]
    async fn real_windows_output_loopback_reports_spoken_audio_level() {
        use std::os::windows::process::CommandExt;
        use std::process::{Command, Stdio};

        const CREATE_NO_WINDOW: u32 = 0x08000000;
        let host = cpal::host_from_id(cpal::HostId::Wasapi)
            .expect("WASAPI must be available for the Windows pilot");
        let configured_device_name = crate::audio::default_output_device()
            .expect("a default Windows output device is required")
            .to_string();
        let levels = Arc::new(Mutex::new(Vec::new()));
        let monitor = AudioLevelMonitor::new();
        let device = monitor
            .find_device_by_name(&host, &configured_device_name)
            .expect("the configured output name must resolve to its raw WASAPI device");
        let _stream = monitor
            .create_level_stream(&device, &configured_device_name, levels.clone())
            .await
            .expect("the output device must open in WASAPI loopback mode");

        let speech = tokio::task::spawn_blocking(|| {
            Command::new("powershell.exe")
                .args([
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "Add-Type -AssemblyName System.Speech; $s = [System.Speech.Synthesis.SpeechSynthesizer]::new(); try { $s.Speak('Meeting output capture test') } finally { $s.Dispose() }",
                ])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .creation_flags(CREATE_NO_WINDOW)
                .status()
                .expect("SAPI speech process must start")
        });

        let deadline = tokio::time::Instant::now() + Duration::from_secs(15);
        let mut maximum_rms = 0.0_f32;
        let mut saw_output_provenance = false;
        while tokio::time::Instant::now() < deadline && !speech.is_finished() {
            let captured = levels.lock().await;
            maximum_rms = captured
                .iter()
                .map(|level| level.rms_level)
                .fold(maximum_rms, f32::max);
            saw_output_provenance |= captured.iter().any(|level| level.device_type == "output");
            drop(captured);
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let status = speech.await.expect("SAPI speech task must complete");
        assert!(status.success(), "SAPI speech synthesis must succeed");
        assert!(
            maximum_rms > 0.001,
            "WASAPI loopback must report a nonzero spoken-audio level"
        );
        assert!(
            saw_output_provenance,
            "WASAPI loopback levels must retain output-device provenance"
        );
    }
}
