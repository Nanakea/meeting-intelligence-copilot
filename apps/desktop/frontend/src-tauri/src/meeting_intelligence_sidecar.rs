//! Best-effort lifecycle for the packaged Meeting Intelligence backend.
//!
//! This is distribution plumbing only. It never owns meeting state and it is
//! enabled by default in release builds and optional in development.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Mutex as StdMutex, RwLock,
};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};
use tauri::State;
use tokio::process::Child;
use tokio::sync::{Mutex, Notify};
use url::Url;
use uuid::Uuid;

use crate::audio::transcription::local_only::local_copilot_url_or_default;

const MANAGE_SIDECAR_ENV: &str = "MEETING_INTELLIGENCE_MANAGE_SIDECAR";
const BACKEND_PATH_ENV: &str = "MEETING_INTELLIGENCE_BACKEND_PATH";
const BACKEND_PORT_ENV: &str = "MEETING_INTELLIGENCE_BACKEND_PORT";
const SEMANTIC_ANALYZER_ENV: &str = "MEETING_INTELLIGENCE_SEMANTIC_ANALYZER";
const SEMANTIC_MODEL_ENV: &str = "MEETING_INTELLIGENCE_SEMANTIC_MODEL";
pub const CAPABILITY_TOKEN_ENV: &str = "MEETING_INTELLIGENCE_TOKEN";
pub const CAPABILITY_TOKEN_HEADER: &str = "X-Meeting-Intelligence-Token";
pub const BACKEND_LIFECYCLE_EVENT: &str = "meeting-intelligence-backend-lifecycle";
const EXPECTED_PRODUCT: &str = "meeting-intelligence-copilot";
const EXPECTED_API_VERSION: u32 = 13;
const STARTUP_ATTEMPTS: usize = 40;
const STARTUP_POLL: Duration = Duration::from_millis(200);
const HEALTH_TIMEOUT: Duration = Duration::from_secs(5);
const MONITOR_POLL: Duration = Duration::from_secs(1);
const STABLE_RUNTIME: Duration = Duration::from_secs(60);
const MAX_RESTART_ATTEMPTS: usize = 3;
const RESTART_BASE_DELAY: Duration = Duration::from_secs(1);
const HEALTH_FAILURE_LIMIT: usize = 2;
const OPERATIONAL_LOG_FILE: &str = "meeting-intelligence-sidecar.log";
const OPERATIONAL_LOG_BACKUP_FILE: &str = "meeting-intelligence-sidecar.1.log";
const OPERATIONAL_LOG_MAX_BYTES: u64 = 256 * 1024;

#[derive(Debug, Clone, Copy)]
enum OperationalEvent {
    Disabled,
    Starting,
    Ready,
    BackendStopped,
    HealthProbeTimeout,
    HealthProbeConnectFailed,
    HealthProbeInvalidResponse,
    HealthProbeIncompatible,
    HealthFailed,
    StartFailed,
    RestartScheduled,
    Unavailable,
    ManualRetry,
    Stopped,
}

impl OperationalEvent {
    fn code(self) -> &'static str {
        match self {
            Self::Disabled => "disabled",
            Self::Starting => "starting",
            Self::Ready => "ready",
            Self::BackendStopped => "backend_stopped",
            Self::HealthProbeTimeout => "health_probe_timeout",
            Self::HealthProbeConnectFailed => "health_probe_connect_failed",
            Self::HealthProbeInvalidResponse => "health_probe_invalid_response",
            Self::HealthProbeIncompatible => "health_probe_incompatible",
            Self::HealthFailed => "health_failed",
            Self::StartFailed => "start_failed",
            Self::RestartScheduled => "restart_scheduled",
            Self::Unavailable => "unavailable",
            Self::ManualRetry => "manual_retry",
            Self::Stopped => "stopped",
        }
    }
}

#[derive(Debug)]
struct OperationalLog {
    current: PathBuf,
    backup: PathBuf,
    max_bytes: u64,
}

impl OperationalLog {
    fn new(directory: &Path, max_bytes: u64) -> Result<Self> {
        if max_bytes == 0 {
            return Err(anyhow!("operational log size limit must be nonzero"));
        }
        fs::create_dir_all(directory).context("failed to create operational log directory")?;
        Ok(Self {
            current: directory.join(OPERATIONAL_LOG_FILE),
            backup: directory.join(OPERATIONAL_LOG_BACKUP_FILE),
            max_bytes,
        })
    }

    fn write_event(&self, event: OperationalEvent) -> Result<()> {
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let line = format!("{timestamp} event={}\n", event.code());
        let current_bytes = fs::metadata(&self.current)
            .map(|metadata| metadata.len())
            .unwrap_or(0);

        if current_bytes.saturating_add(line.len() as u64) > self.max_bytes
            && self.current.is_file()
        {
            if self.backup.exists() {
                fs::remove_file(&self.backup)
                    .context("failed to replace operational log backup")?;
            }
            fs::rename(&self.current, &self.backup).context("failed to rotate operational log")?;
        }

        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.current)
            .context("failed to open operational log")?;
        file.write_all(line.as_bytes())
            .context("failed to append operational log")?;
        Ok(())
    }
}

#[derive(Debug, Deserialize)]
struct CompatibilityHealth {
    status: String,
    product: String,
    api_version: u32,
    backend_version: Option<String>,
    capability_auth: bool,
}

struct ManagedChild {
    child: Child,
    root_pid: u32,
}

pub struct MeetingIntelligenceSidecarState {
    child: Mutex<Option<ManagedChild>>,
    capability_token: RwLock<Option<String>>,
    owns_capability_token: AtomicBool,
    status: RwLock<MeetingIntelligenceSidecarStatus>,
    shutdown_requested: AtomicBool,
    supervisor_started: AtomicBool,
    manual_retry: Notify,
    operational_log: StdMutex<Option<OperationalLog>>,
    semantic_beta: RwLock<(bool, String)>,
}

impl Default for MeetingIntelligenceSidecarState {
    fn default() -> Self {
        let capability_token = configured_capability_token().unwrap_or_else(|error| {
            log::error!("Invalid Meeting Intelligence capability-token configuration: {error}");
            None
        });
        Self {
            child: Mutex::new(None),
            capability_token: RwLock::new(capability_token.clone()),
            owns_capability_token: AtomicBool::new(false),
            status: RwLock::new(MeetingIntelligenceSidecarStatus::stopped(
                capability_token.is_some(),
                management_enabled(std::env::var(MANAGE_SIDECAR_ENV).ok().as_deref()),
            )),
            shutdown_requested: AtomicBool::new(false),
            supervisor_started: AtomicBool::new(false),
            manual_retry: Notify::new(),
            operational_log: StdMutex::new(None),
            semantic_beta: RwLock::new((false, "qwen3:8b".to_string())),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MeetingIntelligenceSidecarPhase {
    Starting,
    Ready,
    Degraded,
    Restarting,
    Unavailable,
    Stopped,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MeetingIntelligenceSidecarFailureReason {
    MissingExecutable,
    IncompatibleBackend,
    StartupFailed,
    BackendStopped,
}

#[derive(Debug, thiserror::Error)]
enum SidecarStartFailure {
    #[error("Meeting Intelligence backend executable is missing: {0}")]
    MissingExecutable(String),
    #[error("Meeting Intelligence backend is incompatible: {0}")]
    IncompatibleBackend(String),
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MeetingIntelligenceSidecarStatus {
    phase: MeetingIntelligenceSidecarPhase,
    reason: Option<MeetingIntelligenceSidecarFailureReason>,
    retry_count: usize,
    max_retry_count: usize,
    retry_after_ms: Option<u64>,
    auth_enabled: bool,
    managed: bool,
    backend_version: Option<String>,
}

impl MeetingIntelligenceSidecarStatus {
    fn stopped(auth_enabled: bool, managed: bool) -> Self {
        Self {
            phase: MeetingIntelligenceSidecarPhase::Stopped,
            reason: None,
            retry_count: 0,
            max_retry_count: MAX_RESTART_ATTEMPTS,
            retry_after_ms: None,
            auth_enabled,
            managed,
            backend_version: None,
        }
    }

    fn new(
        phase: MeetingIntelligenceSidecarPhase,
        retry_count: usize,
        retry_after: Option<Duration>,
        auth_enabled: bool,
        managed: bool,
    ) -> Self {
        Self {
            phase,
            reason: None,
            retry_count: retry_count.min(MAX_RESTART_ATTEMPTS),
            max_retry_count: MAX_RESTART_ATTEMPTS,
            retry_after_ms: retry_after.map(|delay| delay.as_millis() as u64),
            auth_enabled,
            managed,
            backend_version: None,
        }
    }

    fn with_reason(mut self, reason: MeetingIntelligenceSidecarFailureReason) -> Self {
        self.reason = Some(reason);
        self
    }

    fn with_backend_version(mut self, backend_version: Option<String>) -> Self {
        self.backend_version = backend_version;
        self
    }
}

#[derive(Debug, Default)]
struct RestartBudget {
    failures: usize,
}

impl RestartBudget {
    fn record_failure(&mut self) -> Option<Duration> {
        self.failures += 1;
        if self.failures > MAX_RESTART_ATTEMPTS {
            return None;
        }
        Some(RESTART_BASE_DELAY * 2_u32.pow((self.failures - 1) as u32))
    }

    fn reset(&mut self) {
        self.failures = 0;
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MeetingIntelligenceTransport {
    base_url: String,
    token: Option<String>,
    auth_enabled: bool,
}

fn valid_capability_token(token: &str) -> bool {
    (32..=256).contains(&token.len())
        && token
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._~-".contains(&byte))
}

fn configured_capability_token() -> Result<Option<String>> {
    match std::env::var(CAPABILITY_TOKEN_ENV) {
        Ok(token) if valid_capability_token(&token) => Ok(Some(token)),
        Ok(_) => Err(anyhow!(
            "{} must contain 32-256 URL-safe ASCII characters",
            CAPABILITY_TOKEN_ENV
        )),
        Err(std::env::VarError::NotPresent) => Ok(None),
        Err(error) => Err(anyhow!(
            "{} is not valid Unicode: {error}",
            CAPABILITY_TOKEN_ENV
        )),
    }
}

fn generate_capability_token() -> String {
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}

fn management_enabled(value: Option<&str>) -> bool {
    match value.map(str::trim).map(str::to_ascii_lowercase) {
        Some(value) if matches!(value.as_str(), "0" | "false" | "no" | "off") => false,
        Some(value) if matches!(value.as_str(), "1" | "true" | "yes" | "on") => true,
        Some(_) => false,
        None => !cfg!(debug_assertions),
    }
}

fn target_triple() -> &'static str {
    if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        "x86_64-pc-windows-msvc"
    } else if cfg!(all(target_os = "windows", target_arch = "aarch64")) {
        "aarch64-pc-windows-msvc"
    } else if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        "aarch64-apple-darwin"
    } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
        "x86_64-apple-darwin"
    } else if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
        "x86_64-unknown-linux-gnu"
    } else if cfg!(all(target_os = "linux", target_arch = "aarch64")) {
        "aarch64-unknown-linux-gnu"
    } else {
        "unsupported-target"
    }
}

fn target_binary_name() -> String {
    if cfg!(windows) {
        format!("meeting-intelligence-backend-{}.exe", target_triple())
    } else {
        format!("meeting-intelligence-backend-{}", target_triple())
    }
}

fn packaged_binary_name() -> &'static str {
    if cfg!(windows) {
        "meeting-intelligence-backend.exe"
    } else {
        "meeting-intelligence-backend"
    }
}

pub(crate) fn resolve_backend_binary() -> Result<PathBuf> {
    if let Ok(configured) = std::env::var(BACKEND_PATH_ENV) {
        if !configured.trim().is_empty() {
            let path = PathBuf::from(configured);
            if path.is_file() {
                return Ok(path);
            }
            return Err(SidecarStartFailure::MissingExecutable(format!(
                "{} does not identify a real file: {}",
                BACKEND_PATH_ENV,
                path.display()
            ))
            .into());
        }
    }

    let mut candidates = Vec::new();
    if let Ok(executable) = std::env::current_exe() {
        if let Some(directory) = executable.parent() {
            candidates.push(directory.join(target_binary_name()));
            candidates.push(directory.join(packaged_binary_name()));
        }
    }

    let manifest_directory = Path::new(env!("CARGO_MANIFEST_DIR"));
    candidates.push(
        manifest_directory
            .join("binaries")
            .join(target_binary_name()),
    );

    candidates
        .into_iter()
        .find(|candidate| candidate.is_file())
        .ok_or_else(|| {
            SidecarStartFailure::MissingExecutable(
                "run apps/desktop/scripts/setup-meetily-sidecars.ps1 with the reviewed artifact"
                    .to_string(),
            )
            .into()
        })
}

fn start_failure_reason(error: &anyhow::Error) -> MeetingIntelligenceSidecarFailureReason {
    match error.downcast_ref::<SidecarStartFailure>() {
        Some(SidecarStartFailure::MissingExecutable(_)) => {
            MeetingIntelligenceSidecarFailureReason::MissingExecutable
        }
        Some(SidecarStartFailure::IncompatibleBackend(_)) => {
            MeetingIntelligenceSidecarFailureReason::IncompatibleBackend
        }
        None => MeetingIntelligenceSidecarFailureReason::StartupFailed,
    }
}

fn backend_port(base_url: &str) -> Result<u16> {
    let parsed = Url::parse(base_url).context("validated backend URL did not parse")?;
    parsed
        .port_or_known_default()
        .ok_or_else(|| anyhow!("validated backend URL has no usable port"))
}

fn is_compatible(health: &CompatibilityHealth) -> bool {
    health.status == "ok"
        && health.product == EXPECTED_PRODUCT
        && health.api_version == EXPECTED_API_VERSION
        && health.capability_auth
}

fn health_probe_failure_event(error: &anyhow::Error) -> OperationalEvent {
    match error.downcast_ref::<reqwest::Error>() {
        Some(error) if error.is_timeout() => OperationalEvent::HealthProbeTimeout,
        Some(error) if error.is_connect() => OperationalEvent::HealthProbeConnectFailed,
        _ => OperationalEvent::HealthProbeInvalidResponse,
    }
}

async fn compatibility_health(
    client: &reqwest::Client,
    base_url: &str,
    capability_token: Option<&str>,
) -> Result<CompatibilityHealth> {
    let mut request = client.get(format!("{base_url}/health/compatibility"));
    if let Some(token) = capability_token {
        request = request.header(CAPABILITY_TOKEN_HEADER, token);
    }
    request
        .send()
        .await?
        .error_for_status()?
        .json::<CompatibilityHealth>()
        .await
        .map_err(Into::into)
}

impl MeetingIntelligenceSidecarState {
    pub fn configure_semantic_beta(&self, enabled: bool, model: String) {
        if model == "qwen3:8b" {
            *self
                .semantic_beta
                .write()
                .unwrap_or_else(|poisoned| poisoned.into_inner()) = (enabled, model);
        }
    }

    fn management_enabled(&self) -> bool {
        management_enabled(std::env::var(MANAGE_SIDECAR_ENV).ok().as_deref())
    }

    fn auth_enabled(&self) -> bool {
        self.capability_token().is_some()
    }

    pub fn configure_operational_log(&self, directory: &Path) -> Result<()> {
        let log = OperationalLog::new(directory, OPERATIONAL_LOG_MAX_BYTES)?;
        let mut configured = self
            .operational_log
            .lock()
            .map_err(|_| anyhow!("operational log configuration lock is poisoned"))?;
        *configured = Some(log);
        Ok(())
    }

    fn record_operational_event(&self, event: OperationalEvent) {
        let result = self
            .operational_log
            .lock()
            .map_err(|_| anyhow!("operational log lock is poisoned"))
            .and_then(|configured| match configured.as_ref() {
                Some(log) => log.write_event(event),
                None => Ok(()),
            });
        if result.is_err() {
            // Keep the console message fixed: paths, tokens, transcripts, and raw
            // backend errors must never become part of persistent diagnostics.
            log::warn!("Meeting Intelligence operational diagnostics could not be written");
        }
    }

    fn set_status(&self, status: MeetingIntelligenceSidecarStatus) {
        if let Ok(mut current) = self.status.write() {
            *current = status;
        }
    }

    pub fn status(&self) -> MeetingIntelligenceSidecarStatus {
        self.status
            .read()
            .map(|status| status.clone())
            .unwrap_or_else(|_| {
                MeetingIntelligenceSidecarStatus::new(
                    MeetingIntelligenceSidecarPhase::Unavailable,
                    MAX_RESTART_ATTEMPTS,
                    None,
                    self.auth_enabled(),
                    self.management_enabled(),
                )
                .with_reason(MeetingIntelligenceSidecarFailureReason::StartupFailed)
            })
    }

    pub fn capability_token(&self) -> Option<String> {
        self.capability_token.read().ok()?.clone()
    }

    fn set_capability_token(&self, token: Option<String>, owned: bool) {
        if let Ok(mut current) = self.capability_token.write() {
            *current = token;
            self.owns_capability_token.store(owned, Ordering::SeqCst);
        }
    }

    fn ensure_managed_capability_token(&self) -> Result<String> {
        if let Some(token) = self.capability_token() {
            return Ok(token);
        }
        if std::env::var(CAPABILITY_TOKEN_ENV).is_ok() {
            return Err(anyhow!(
                "{} is configured but invalid; refusing unauthenticated fallback",
                CAPABILITY_TOKEN_ENV
            ));
        }
        let token = generate_capability_token();
        self.set_capability_token(Some(token.clone()), true);
        Ok(token)
    }

    fn clear_owned_capability_token(&self) {
        if self.owns_capability_token.swap(false, Ordering::SeqCst) {
            self.set_capability_token(None, false);
        }
    }

    fn request_manual_retry(&self) -> bool {
        if !matches!(
            self.status().phase,
            MeetingIntelligenceSidecarPhase::Restarting
                | MeetingIntelligenceSidecarPhase::Unavailable
        ) {
            return false;
        }
        self.manual_retry.notify_one();
        true
    }

    pub async fn restart_for_configuration(&self) -> bool {
        if !self.management_enabled() {
            return false;
        }
        let had_child = self.child.lock().await.is_some();
        if had_child {
            self.stop_owned_child_after_failure().await;
        } else {
            self.manual_retry.notify_one();
        }
        true
    }

    /// Start or reuse the backend when lifecycle management is enabled for this build.
    pub async fn start_if_enabled(&self) -> Result<Option<String>> {
        if !management_enabled(std::env::var(MANAGE_SIDECAR_ENV).ok().as_deref()) {
            log::info!(
                "Meeting Intelligence sidecar lifecycle disabled by build mode or {}",
                MANAGE_SIDECAR_ENV
            );
            return Ok(None);
        }

        let base_url =
            local_copilot_url_or_default(std::env::var("MEETING_COPILOT_URL").ok().as_deref());
        let capability_token = self.ensure_managed_capability_token()?;
        let client = reqwest::Client::builder()
            .timeout(HEALTH_TIMEOUT)
            .build()
            .context("failed to build Meeting Intelligence health client")?;

        match compatibility_health(&client, &base_url, Some(&capability_token)).await {
            Ok(health) if is_compatible(&health) => {
                log::info!(
                    "Reusing compatible Meeting Intelligence backend (backend version {})",
                    health.backend_version.as_deref().unwrap_or("unknown")
                );
                return Ok(health.backend_version.clone());
            }
            Ok(_) => {
                return Err(SidecarStartFailure::IncompatibleBackend(format!(
                    "backend failed the expected product/API/auth contract"
                ))
                .into());
            }
            Err(_) => {
                // No compatible listener is available; try the real bundled sidecar.
            }
        }

        let binary = resolve_backend_binary()?;
        let port = backend_port(&base_url)?;
        if self.shutdown_requested.load(Ordering::SeqCst) {
            return Err(anyhow!(
                "backend sidecar startup cancelled by application shutdown"
            ));
        }
        let mut command = tokio::process::Command::new(&binary);
        command
            .env(BACKEND_PORT_ENV, port.to_string())
            .env(CAPABILITY_TOKEN_ENV, &capability_token)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        let (semantic_enabled, semantic_model) = self
            .semantic_beta
            .read()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone();
        if semantic_enabled {
            command
                .env(SEMANTIC_ANALYZER_ENV, "ollama")
                .env(SEMANTIC_MODEL_ENV, semantic_model);
        }

        #[cfg(target_os = "windows")]
        {
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            command.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = command.spawn().with_context(|| {
            format!(
                "failed to start real backend sidecar at {}",
                binary.display()
            )
        })?;
        let root_pid = child
            .id()
            .ok_or_else(|| anyhow!("spawned backend sidecar has no process id"))?;

        for _ in 0..STARTUP_ATTEMPTS {
            if self.shutdown_requested.load(Ordering::SeqCst) {
                let mut managed = ManagedChild { child, root_pid };
                terminate_process_tree(&mut managed).await?;
                return Err(anyhow!(
                    "backend sidecar startup cancelled by application shutdown"
                ));
            }
            if let Some(status) = child.try_wait()? {
                return Err(anyhow!(
                    "backend sidecar exited before compatibility health: {status}"
                ));
            }

            match compatibility_health(&client, &base_url, Some(&capability_token)).await {
                Ok(health) if is_compatible(&health) => {
                    *self.child.lock().await = Some(ManagedChild { child, root_pid });
                    log::info!(
                        "Managed Meeting Intelligence backend is compatible (root PID {root_pid}, backend version {})",
                        health.backend_version.as_deref().unwrap_or("unknown")
                    );
                    return Ok(health.backend_version.clone());
                }
                Ok(_) => {
                    let mut managed = ManagedChild { child, root_pid };
                    terminate_process_tree(&mut managed).await?;
                    return Err(SidecarStartFailure::IncompatibleBackend(
                        "spawned backend sidecar failed the expected product/API/auth contract"
                            .to_string(),
                    )
                    .into());
                }
                Err(_) => tokio::time::sleep(STARTUP_POLL).await,
            }
        }

        let mut managed = ManagedChild { child, root_pid };
        terminate_process_tree(&mut managed).await?;
        Err(anyhow!(
            "backend sidecar did not become compatible within the startup budget"
        ))
    }

    async fn stop_owned_child_after_failure(&self) {
        let Some(mut managed) = self.child.lock().await.take() else {
            return;
        };
        if let Err(error) = terminate_process_tree(&mut managed).await {
            let _ = error;
            log::warn!("Meeting Intelligence failed to clean up an unhealthy managed process tree");
        }
    }

    async fn monitor_until_unavailable(&self) -> Result<Duration> {
        let started = Instant::now();
        let base_url =
            local_copilot_url_or_default(std::env::var("MEETING_COPILOT_URL").ok().as_deref());
        let capability_token = self.ensure_managed_capability_token()?;
        let client = reqwest::Client::builder()
            .timeout(HEALTH_TIMEOUT)
            .build()
            .context("failed to build Meeting Intelligence monitor client")?;
        let mut health_failures = 0;

        loop {
            tokio::time::sleep(MONITOR_POLL).await;
            if self.shutdown_requested.load(Ordering::SeqCst) {
                return Ok(started.elapsed());
            }

            let exited = {
                let mut child = self.child.lock().await;
                let exit = match child.as_mut() {
                    Some(managed) => managed
                        .child
                        .try_wait()?
                        .map(|status| (managed.root_pid, status)),
                    None => None,
                };
                if exit.is_some() {
                    child.take();
                }
                exit
            };
            if let Some((root_pid, status)) = exited {
                log::warn!(
                    "Managed Meeting Intelligence backend exited (root PID {root_pid}, status {status})"
                );
                return Ok(started.elapsed());
            }

            let failed_probe =
                match compatibility_health(&client, &base_url, Some(&capability_token)).await {
                    Ok(health) if is_compatible(&health) => {
                        health_failures = 0;
                        if self.status().phase == MeetingIntelligenceSidecarPhase::Degraded {
                            let current = self.status();
                            self.set_status(
                                MeetingIntelligenceSidecarStatus::new(
                                    MeetingIntelligenceSidecarPhase::Ready,
                                    current.retry_count,
                                    None,
                                    self.auth_enabled(),
                                    self.management_enabled(),
                                )
                                .with_backend_version(health.backend_version.clone()),
                            );
                        }
                        None
                    }
                    Ok(_) => Some(OperationalEvent::HealthProbeIncompatible),
                    Err(error) => Some(health_probe_failure_event(&error)),
                };
            if let Some(failure_event) = failed_probe {
                self.record_operational_event(failure_event);
                health_failures += 1;
                if health_failures == 1 {
                    let current = self.status();
                    self.set_status(
                        MeetingIntelligenceSidecarStatus::new(
                            MeetingIntelligenceSidecarPhase::Degraded,
                            current.retry_count,
                            None,
                            self.auth_enabled(),
                            self.management_enabled(),
                        )
                        .with_reason(MeetingIntelligenceSidecarFailureReason::BackendStopped)
                        .with_backend_version(current.backend_version.clone()),
                    );
                }
                if health_failures >= HEALTH_FAILURE_LIMIT {
                    self.record_operational_event(OperationalEvent::HealthFailed);
                    log::warn!(
                            "Meeting Intelligence backend failed {HEALTH_FAILURE_LIMIT} consecutive local health checks"
                        );
                    self.stop_owned_child_after_failure().await;
                    return Ok(started.elapsed());
                }
            }
        }
    }

    /// Supervise the backend without ever blocking recording or local STT.
    pub async fn run_supervisor(&self) {
        if !management_enabled(std::env::var(MANAGE_SIDECAR_ENV).ok().as_deref()) {
            self.set_status(MeetingIntelligenceSidecarStatus::stopped(
                self.auth_enabled(),
                false,
            ));
            self.record_operational_event(OperationalEvent::Disabled);
            log::info!(
                "Meeting Intelligence sidecar supervisor disabled by build mode or {}",
                MANAGE_SIDECAR_ENV
            );
            return;
        }
        if self.supervisor_started.swap(true, Ordering::SeqCst) {
            log::warn!("Meeting Intelligence sidecar supervisor is already running");
            return;
        }

        self.shutdown_requested.store(false, Ordering::SeqCst);
        let mut budget = RestartBudget::default();

        loop {
            if self.shutdown_requested.load(Ordering::SeqCst) {
                break;
            }
            self.set_status(MeetingIntelligenceSidecarStatus::new(
                MeetingIntelligenceSidecarPhase::Starting,
                budget.failures,
                None,
                self.auth_enabled(),
                true,
            ));
            self.record_operational_event(OperationalEvent::Starting);

            let failure_reason = match self.start_if_enabled().await {
                Ok(backend_version) => {
                    self.set_status(
                        MeetingIntelligenceSidecarStatus::new(
                            MeetingIntelligenceSidecarPhase::Ready,
                            budget.failures,
                            None,
                            self.auth_enabled(),
                            true,
                        )
                        .with_backend_version(backend_version),
                    );
                    self.record_operational_event(OperationalEvent::Ready);
                    log::info!("Meeting Intelligence backend supervisor reports ready");
                    match self.monitor_until_unavailable().await {
                        Ok(runtime) if runtime >= STABLE_RUNTIME => budget.reset(),
                        Ok(_) => {}
                        Err(error) => {
                            let _ = error;
                            log::warn!("Meeting Intelligence backend monitor failed");
                            self.stop_owned_child_after_failure().await;
                        }
                    }
                    self.record_operational_event(OperationalEvent::BackendStopped);
                    MeetingIntelligenceSidecarFailureReason::BackendStopped
                }
                Err(error) => {
                    let reason = start_failure_reason(&error);
                    self.record_operational_event(OperationalEvent::StartFailed);
                    log::warn!("Meeting Intelligence backend startup failed ({reason:?})");
                    reason
                }
            };

            if self.shutdown_requested.load(Ordering::SeqCst) {
                break;
            }

            if let Some(delay) = budget.record_failure() {
                self.set_status(
                    MeetingIntelligenceSidecarStatus::new(
                        MeetingIntelligenceSidecarPhase::Restarting,
                        budget.failures,
                        Some(delay),
                        self.auth_enabled(),
                        true,
                    )
                    .with_reason(failure_reason),
                );
                self.record_operational_event(OperationalEvent::RestartScheduled);
                log::warn!(
                    "Meeting Intelligence restart attempt {}/{} scheduled after {} ms",
                    budget.failures,
                    MAX_RESTART_ATTEMPTS,
                    delay.as_millis()
                );
                tokio::select! {
                    _ = tokio::time::sleep(delay) => {}
                    _ = self.manual_retry.notified() => {
                        budget.reset();
                        self.record_operational_event(OperationalEvent::ManualRetry);
                        log::info!("Manual Meeting Intelligence retry requested");
                    }
                }
            } else {
                self.set_status(
                    MeetingIntelligenceSidecarStatus::new(
                        MeetingIntelligenceSidecarPhase::Unavailable,
                        MAX_RESTART_ATTEMPTS,
                        None,
                        self.auth_enabled(),
                        true,
                    )
                    .with_reason(failure_reason),
                );
                self.record_operational_event(OperationalEvent::Unavailable);
                log::error!(
                    "Meeting Intelligence backend unavailable after {MAX_RESTART_ATTEMPTS} bounded restart attempts; recording remains active and manual retry is available"
                );
                self.manual_retry.notified().await;
                budget.reset();
                if !self.shutdown_requested.load(Ordering::SeqCst) {
                    self.record_operational_event(OperationalEvent::ManualRetry);
                    log::info!("Manual Meeting Intelligence retry requested");
                }
            }
        }

        self.set_status(MeetingIntelligenceSidecarStatus::new(
            MeetingIntelligenceSidecarPhase::Stopped,
            0,
            None,
            self.auth_enabled(),
            self.management_enabled(),
        ));
        self.record_operational_event(OperationalEvent::Stopped);
        self.supervisor_started.store(false, Ordering::SeqCst);
    }

    async fn wait_for_supervisor_shutdown(&self) {
        for _ in 0..50 {
            if !self.supervisor_started.load(Ordering::SeqCst) {
                return;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        log::warn!("Meeting Intelligence supervisor did not stop within the shutdown budget");
    }

    /// Stop only a backend process tree started by this desktop process.
    pub async fn stop(&self) -> Result<()> {
        self.shutdown_requested.store(true, Ordering::SeqCst);
        // Store a permit if the supervisor has published `Unavailable` but has not
        // reached `notified().await` yet. `notify_waiters` would lose that wake-up.
        self.manual_retry.notify_one();
        let managed = self.child.lock().await.take();
        let (root_pid, stop_result) = match managed {
            Some(mut managed) => {
                let root_pid = Some(managed.root_pid);
                let result = terminate_process_tree(&mut managed).await;
                (root_pid, result)
            }
            None => (None, Ok(())),
        };
        self.clear_owned_capability_token();
        self.set_status(MeetingIntelligenceSidecarStatus::stopped(
            self.auth_enabled(),
            self.management_enabled(),
        ));
        self.record_operational_event(OperationalEvent::Stopped);
        self.wait_for_supervisor_shutdown().await;
        stop_result?;
        if let Some(root_pid) = root_pid {
            log::info!(
                "Managed Meeting Intelligence backend process tree stopped (root PID {root_pid})"
            );
        }
        Ok(())
    }
}

#[tauri::command]
pub fn get_meeting_intelligence_transport(
    state: State<'_, MeetingIntelligenceSidecarState>,
) -> std::result::Result<MeetingIntelligenceTransport, String> {
    let base_url =
        local_copilot_url_or_default(std::env::var("MEETING_COPILOT_URL").ok().as_deref());
    let token =
        if management_enabled(std::env::var(MANAGE_SIDECAR_ENV).ok().as_deref()) {
            Some(state.ensure_managed_capability_token().map_err(|_| {
                "Meeting Intelligence transport configuration is invalid".to_string()
            })?)
        } else {
            state.capability_token()
        };
    Ok(MeetingIntelligenceTransport {
        base_url,
        auth_enabled: token.is_some(),
        token,
    })
}

#[tauri::command]
pub fn get_meeting_intelligence_backend_status(
    state: State<'_, MeetingIntelligenceSidecarState>,
) -> MeetingIntelligenceSidecarStatus {
    state.status()
}

#[tauri::command]
pub fn retry_meeting_intelligence_backend(
    state: State<'_, MeetingIntelligenceSidecarState>,
) -> bool {
    state.request_manual_retry()
}

#[tauri::command]
pub async fn restart_meeting_intelligence_backend_for_configuration(
    state: State<'_, MeetingIntelligenceSidecarState>,
) -> std::result::Result<bool, String> {
    Ok(state.restart_for_configuration().await)
}

#[cfg(target_os = "windows")]
async fn terminate_process_tree(managed: &mut ManagedChild) -> Result<()> {
    const CREATE_NO_WINDOW: u32 = 0x08000000;

    let status = tokio::process::Command::new("taskkill")
        .args(["/PID", &managed.root_pid.to_string(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await
        .context("failed to invoke Windows process-tree shutdown")?;

    if !status.success() && managed.child.try_wait()?.is_none() {
        managed.child.kill().await?;
    }
    let _ = managed.child.wait().await;
    Ok(())
}

#[cfg(not(target_os = "windows"))]
async fn terminate_process_tree(managed: &mut ManagedChild) -> Result<()> {
    if managed.child.try_wait()?.is_none() {
        managed.child.kill().await?;
    }
    let _ = managed.child.wait().await;
    Ok(())
}

#[cfg(test)]
mod tests {
    #[cfg(target_os = "windows")]
    use std::sync::Arc;

    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    use super::{
        backend_port, compatibility_health, generate_capability_token, health_probe_failure_event,
        is_compatible, management_enabled, valid_capability_token, CompatibilityHealth,
        MeetingIntelligenceSidecarFailureReason, MeetingIntelligenceSidecarPhase,
        MeetingIntelligenceSidecarState, MeetingIntelligenceSidecarStatus, OperationalEvent,
        OperationalLog, RestartBudget, SidecarStartFailure, HEALTH_TIMEOUT, MAX_RESTART_ATTEMPTS,
        STARTUP_POLL,
    };
    use crate::audio::transcription::local_only::local_copilot_url_or_default;

    #[test]
    fn lifecycle_defaults_by_build_and_honors_explicit_override() {
        assert_eq!(management_enabled(None), !cfg!(debug_assertions));
        for value in [Some(""), Some("0"), Some("false"), Some("off")] {
            assert!(!management_enabled(value));
        }
        for value in [Some("1"), Some("TRUE"), Some(" yes "), Some("on")] {
            assert!(management_enabled(value));
        }
    }

    #[test]
    fn lifecycle_uses_the_validated_loopback_url_port() {
        assert_eq!(backend_port("http://127.0.0.1:8123").unwrap(), 8123);
        assert_eq!(backend_port("http://localhost").unwrap(), 80);
    }

    #[test]
    fn compatibility_requires_exact_product_and_api_version() {
        assert!(is_compatible(&CompatibilityHealth {
            status: "ok".into(),
            product: "meeting-intelligence-copilot".into(),
            api_version: 13,
            backend_version: Some("0.4.0".into()),
            capability_auth: true,
        }));
        assert!(!is_compatible(&CompatibilityHealth {
            status: "ok".into(),
            product: "meeting-intelligence-copilot".into(),
            api_version: 2,
            backend_version: Some("0.3.0".into()),
            capability_auth: true,
        }));
        assert!(!is_compatible(&CompatibilityHealth {
            status: "ok".into(),
            product: "meeting-intelligence-copilot".into(),
            api_version: 13,
            backend_version: Some("0.4.0".into()),
            capability_auth: false,
        }));
    }

    #[tokio::test]
    async fn local_health_tolerates_brief_cpu_or_replay_stalls() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = Vec::new();
            let mut buffer = [0_u8; 1024];
            while !request.windows(4).any(|window| window == b"\r\n\r\n") {
                let read = stream.read(&mut buffer).await.unwrap();
                if read == 0 {
                    break;
                }
                request.extend_from_slice(&buffer[..read]);
            }
            tokio::time::sleep(std::time::Duration::from_millis(3_200)).await;
            let body = r#"{"status":"ok","product":"meeting-intelligence-copilot","api_version":13,"backend_version":"test","capability_auth":true}"#;
            stream
                .write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                        body.len(),
                        body,
                    )
                    .as_bytes(),
                )
                .await
                .unwrap();
        });
        let client = reqwest::Client::builder()
            .timeout(HEALTH_TIMEOUT)
            .build()
            .unwrap();

        let health =
            compatibility_health(&client, &format!("http://{address}"), Some("test-token"))
                .await
                .unwrap();
        server.await.unwrap();

        assert!(is_compatible(&health));
    }

    #[tokio::test]
    async fn health_probe_timeout_has_a_privacy_safe_operational_code() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut buffer = [0_u8; 1024];
            let _ = stream.read(&mut buffer).await;
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
            let _ = stream
                .write_all(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\n\r\n")
                .await;
        });
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_millis(20))
            .build()
            .unwrap();

        let error = compatibility_health(&client, &format!("http://{address}"), Some("test-token"))
            .await
            .unwrap_err();
        server.await.unwrap();

        assert_eq!(
            health_probe_failure_event(&error).code(),
            "health_probe_timeout"
        );
    }

    #[test]
    fn capability_tokens_are_strong_url_safe_and_unique() {
        let first = generate_capability_token();
        let second = generate_capability_token();
        let too_long = "x".repeat(257);

        assert!(valid_capability_token(&first));
        assert_eq!(first.len(), 64);
        assert_ne!(first, second);
        for invalid in ["", "short", too_long.as_str(), "not valid token !"] {
            assert!(!valid_capability_token(invalid));
        }
    }

    #[test]
    fn restart_backoff_is_nonzero_exponential_and_bounded() {
        let mut budget = RestartBudget::default();

        let delays = (0..MAX_RESTART_ATTEMPTS)
            .map(|_| budget.record_failure().unwrap())
            .collect::<Vec<_>>();

        assert_eq!(
            delays,
            vec![
                std::time::Duration::from_secs(1),
                std::time::Duration::from_secs(2),
                std::time::Duration::from_secs(4),
            ]
        );
        assert!(delays.iter().all(|delay| !delay.is_zero()));
        assert!(budget.record_failure().is_none());
    }

    #[test]
    fn manual_retry_resets_the_restart_budget() {
        let mut budget = RestartBudget::default();
        assert_eq!(
            budget.record_failure(),
            Some(std::time::Duration::from_secs(1))
        );
        assert_eq!(
            budget.record_failure(),
            Some(std::time::Duration::from_secs(2))
        );

        budget.reset();

        assert_eq!(
            budget.record_failure(),
            Some(std::time::Duration::from_secs(1))
        );
    }

    #[test]
    fn manual_retry_is_available_only_during_restarting_or_unavailable() {
        let state = MeetingIntelligenceSidecarState::default();
        assert!(!state.request_manual_retry());

        state.set_status(MeetingIntelligenceSidecarStatus::new(
            MeetingIntelligenceSidecarPhase::Restarting,
            1,
            Some(std::time::Duration::from_secs(1)),
            true,
            true,
        ));
        assert!(state.request_manual_retry());

        state.set_status(MeetingIntelligenceSidecarStatus::new(
            MeetingIntelligenceSidecarPhase::Unavailable,
            MAX_RESTART_ATTEMPTS,
            None,
            true,
            true,
        ));
        assert!(state.request_manual_retry());
    }

    #[test]
    fn lifecycle_status_exposes_only_a_fixed_privacy_safe_failure_reason() {
        let status = MeetingIntelligenceSidecarStatus::new(
            MeetingIntelligenceSidecarPhase::Unavailable,
            MAX_RESTART_ATTEMPTS,
            None,
            true,
            true,
        )
        .with_backend_version(Some("0.3.0".to_string()))
        .with_reason(MeetingIntelligenceSidecarFailureReason::MissingExecutable);
        let json = serde_json::to_string(&status).unwrap();

        assert!(json.contains(r#""reason":"missing_executable""#));
        assert!(json.contains(r#""backendVersion":"0.3.0""#));
        assert!(!json.contains("MEETING_INTELLIGENCE_TOKEN"));
        assert!(!json.contains("C:\\"));
    }

    #[test]
    fn typed_start_failures_map_to_actionable_status_reasons() {
        let missing = anyhow::Error::new(SidecarStartFailure::MissingExecutable(
            "local detail".to_string(),
        ));
        let incompatible = anyhow::Error::new(SidecarStartFailure::IncompatibleBackend(
            "local detail".to_string(),
        ));

        assert_eq!(
            super::start_failure_reason(&missing),
            MeetingIntelligenceSidecarFailureReason::MissingExecutable
        );
        assert_eq!(
            super::start_failure_reason(&incompatible),
            MeetingIntelligenceSidecarFailureReason::IncompatibleBackend
        );
        assert_eq!(
            super::start_failure_reason(&anyhow::anyhow!("spawn failed")),
            MeetingIntelligenceSidecarFailureReason::StartupFailed
        );
    }

    #[test]
    fn operational_log_is_bounded_rotated_and_content_free() {
        let directory = tempfile::tempdir().unwrap();
        let log = OperationalLog::new(directory.path(), 96).unwrap();

        for _ in 0..12 {
            log.write_event(OperationalEvent::RestartScheduled).unwrap();
        }

        let current = std::fs::read_to_string(&log.current).unwrap();
        let backup = std::fs::read_to_string(&log.backup).unwrap();
        let combined = format!("{backup}{current}");
        assert!(std::fs::metadata(&log.current).unwrap().len() <= 96);
        assert!(std::fs::metadata(&log.backup).unwrap().len() <= 96);
        assert!(combined.contains("event=restart_scheduled"));
        for forbidden in [
            "MEETING_INTELLIGENCE_TOKEN",
            "X-Meeting-Intelligence-Token",
            "transcript",
            "127.0.0.1",
            "C:\\\\",
        ] {
            assert!(!combined.contains(forbidden));
        }
    }

    #[tokio::test]
    async fn stop_stores_a_wakeup_for_a_supervisor_about_to_wait() {
        let state = MeetingIntelligenceSidecarState::default();

        state.stop().await.unwrap();

        tokio::time::timeout(
            std::time::Duration::from_millis(50),
            state.manual_retry.notified(),
        )
        .await
        .expect("shutdown wake-up was lost before the supervisor started waiting");
    }

    #[tokio::test]
    #[ignore = "requires a real ignored backend artifact and explicit lifecycle environment"]
    async fn real_sidecar_starts_and_stops_its_process_tree() {
        let base_url =
            local_copilot_url_or_default(std::env::var("MEETING_COPILOT_URL").ok().as_deref());
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(1))
            .build()
            .unwrap();
        let state = MeetingIntelligenceSidecarState::default();
        let test_token = "a".repeat(64);
        state.set_capability_token(Some(test_token.clone()), true);

        state.start_if_enabled().await.unwrap();
        let health = compatibility_health(&client, &base_url, Some(&test_token))
            .await
            .unwrap();
        assert!(is_compatible(&health));
        state.stop().await.unwrap();

        for _ in 0..20 {
            if compatibility_health(&client, &base_url, Some(&test_token))
                .await
                .is_err()
            {
                return;
            }
            tokio::time::sleep(STARTUP_POLL).await;
        }
        panic!("managed backend remained healthy after process-tree shutdown");
    }

    #[cfg(target_os = "windows")]
    #[tokio::test]
    #[ignore = "requires a real ignored backend artifact and explicit lifecycle environment"]
    async fn real_supervisor_restarts_once_after_owned_backend_crash() {
        let base_url =
            local_copilot_url_or_default(std::env::var("MEETING_COPILOT_URL").ok().as_deref());
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(1))
            .build()
            .unwrap();
        let state = Arc::new(MeetingIntelligenceSidecarState::default());
        let test_token = "b".repeat(64);
        state.set_capability_token(Some(test_token.clone()), true);
        let supervisor = tokio::spawn({
            let state = Arc::clone(&state);
            async move { state.run_supervisor().await }
        });

        let mut first_pid = None;
        for _ in 0..80 {
            first_pid = state
                .child
                .lock()
                .await
                .as_ref()
                .map(|managed| managed.root_pid);
            if first_pid.is_some()
                && compatibility_health(&client, &base_url, Some(&test_token))
                    .await
                    .map(|health| is_compatible(&health))
                    .unwrap_or(false)
            {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(250)).await;
        }
        let first_pid = first_pid.expect("supervisor did not start the first backend");

        let kill_status = tokio::process::Command::new("taskkill")
            .args(["/PID", &first_pid.to_string(), "/T", "/F"])
            .status()
            .await
            .unwrap();
        assert!(kill_status.success());

        let mut restarted = false;
        for _ in 0..80 {
            let current_pid = state
                .child
                .lock()
                .await
                .as_ref()
                .map(|managed| managed.root_pid);
            if current_pid.is_some_and(|pid| pid != first_pid)
                && compatibility_health(&client, &base_url, Some(&test_token))
                    .await
                    .map(|health| is_compatible(&health))
                    .unwrap_or(false)
            {
                restarted = true;
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(250)).await;
        }
        assert!(restarted, "supervisor did not restart the crashed backend");

        state.stop().await.unwrap();
        tokio::time::timeout(std::time::Duration::from_secs(3), supervisor)
            .await
            .expect("supervisor did not stop")
            .unwrap();
    }
}
