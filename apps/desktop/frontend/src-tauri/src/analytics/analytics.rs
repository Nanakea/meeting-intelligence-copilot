use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;
use uuid::Uuid;

/// Compatibility shell for inherited analytics call sites.
///
/// The product does not construct a network client or emit telemetry. Aggregate,
/// user-requested diagnostics are implemented separately by the pilot workspace.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct AnalyticsConfig {
    pub api_key: String,
    pub host: Option<String>,
    pub enabled: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserSession {
    pub session_id: String,
    pub user_id: String,
    pub start_time: DateTime<Utc>,
    pub is_active: bool,
}

impl UserSession {
    fn new(user_id: String) -> Self {
        Self {
            session_id: format!("session_{}", Uuid::new_v4()),
            user_id,
            start_time: Utc::now(),
            is_active: true,
        }
    }
}

pub struct AnalyticsClient {
    current_session: Arc<Mutex<Option<UserSession>>>,
}

impl AnalyticsClient {
    pub async fn new(_config: AnalyticsConfig) -> Self {
        Self {
            current_session: Arc::new(Mutex::new(None)),
        }
    }

    pub async fn identify(
        &self,
        _user_id: String,
        _properties: Option<HashMap<String, String>>,
    ) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_event(
        &self,
        _event_name: &str,
        _properties: Option<HashMap<String, String>>,
    ) -> Result<(), String> {
        Ok(())
    }

    pub async fn start_session(&self, user_id: String) -> Result<String, String> {
        let session = UserSession::new(user_id);
        let session_id = session.session_id.clone();
        *self.current_session.lock().await = Some(session);
        Ok(session_id)
    }

    pub async fn end_session(&self) -> Result<(), String> {
        *self.current_session.lock().await = None;
        Ok(())
    }

    pub async fn is_session_active(&self) -> bool {
        self.current_session.lock().await.is_some()
    }

    pub fn is_enabled(&self) -> bool {
        false
    }

    pub async fn track_daily_active_user(&self) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_user_first_launch(&self) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_meeting_started(&self, _meeting_id: &str) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_recording_started(&self, _meeting_id: &str) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_recording_stopped(
        &self,
        _meeting_id: &str,
        _duration_seconds: Option<u64>,
    ) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_meeting_deleted(&self, _meeting_id: &str) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_settings_changed(
        &self,
        _setting_type: &str,
        _new_value: &str,
    ) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_feature_used(&self, _feature_name: &str) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_summary_generation_started(
        &self,
        _model_provider: &str,
        _model_name: &str,
        _transcript_length: usize,
    ) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_summary_generation_completed(
        &self,
        _model_provider: &str,
        _model_name: &str,
        _success: bool,
        _duration_seconds: Option<u64>,
        _error_message: Option<&str>,
    ) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_summary_regenerated(
        &self,
        _model_provider: &str,
        _model_name: &str,
    ) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_model_changed(
        &self,
        _old_provider: &str,
        _old_model: &str,
        _new_provider: &str,
        _new_model: &str,
    ) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_custom_prompt_used(&self, _prompt_length: usize) -> Result<(), String> {
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn track_meeting_ended(
        &self,
        _transcription_provider: &str,
        _transcription_model: &str,
        _summary_provider: &str,
        _summary_model: &str,
        _total_duration_seconds: Option<f64>,
        _active_duration_seconds: f64,
        _pause_duration_seconds: f64,
        _microphone_device_type: &str,
        _system_audio_device_type: &str,
        _chunks_processed: u64,
        _transcript_segments_count: u64,
        _had_fatal_error: bool,
    ) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_analytics_enabled(&self) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_analytics_disabled(&self) -> Result<(), String> {
        Ok(())
    }

    pub async fn track_analytics_transparency_viewed(&self) -> Result<(), String> {
        Ok(())
    }
}
