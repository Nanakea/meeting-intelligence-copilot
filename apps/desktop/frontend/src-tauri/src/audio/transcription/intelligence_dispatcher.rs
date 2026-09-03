//! Ordered, best-effort delivery of transcript updates to the local copilot.

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use reqwest::Client;
use serde::{Deserialize, Serialize};
use tauri::{Manager, Runtime, State};
use tokio::sync::{mpsc, oneshot};
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;

use super::local_only::local_copilot_url_or_default;
use super::worker::TranscriptUpdate;

const DISPATCH_QUEUE_CAPACITY: usize = 512;
const MAX_REPLAY_HISTORY_UPDATES: usize = 16_384;
const MAX_BATCH_EVENTS: usize = 200;
const RETRY_MAX_DELAY: Duration = Duration::from_secs(8);

#[derive(Debug, Clone, Deserialize)]
struct IngestAcknowledgement {
    status: IngestStatus,
    next_expected_sequence_id: u64,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum IngestStatus {
    Applied,
    Buffered,
    Duplicate,
    Rejected,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct IntelligenceDispatcherDiagnostics {
    accepted: u64,
    retried: u64,
    duplicated: u64,
    missing: u64,
    dropped: u64,
    rejected: u64,
    current_queue_depth: usize,
    max_queue_depth: usize,
    flush_attempted: bool,
    flush_completed: bool,
    total_requests: u64,
    total_latency_ms: u64,
    max_latency_ms: u64,
    current_replay_history_updates: usize,
    max_replay_history_updates: usize,
    flush_duration_ms: u64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct IntelligenceRepairResult {
    completed: bool,
    latest_sequence_id: Option<u64>,
}

#[derive(Default)]
struct Diagnostics {
    accepted: AtomicU64,
    retried: AtomicU64,
    duplicated: AtomicU64,
    missing: AtomicU64,
    dropped: AtomicU64,
    rejected: AtomicU64,
    current_queue_depth: AtomicUsize,
    max_queue_depth: AtomicUsize,
    flush_attempted: AtomicBool,
    flush_completed: AtomicBool,
    total_requests: AtomicU64,
    total_latency_ms: AtomicU64,
    max_latency_ms: AtomicU64,
    current_replay_history_updates: AtomicUsize,
    max_replay_history_updates: AtomicUsize,
    flush_duration_ms: AtomicU64,
}

impl Diagnostics {
    fn snapshot(&self) -> IntelligenceDispatcherDiagnostics {
        IntelligenceDispatcherDiagnostics {
            accepted: self.accepted.load(Ordering::Relaxed),
            retried: self.retried.load(Ordering::Relaxed),
            duplicated: self.duplicated.load(Ordering::Relaxed),
            missing: self.missing.load(Ordering::Relaxed),
            dropped: self.dropped.load(Ordering::Relaxed),
            rejected: self.rejected.load(Ordering::Relaxed),
            current_queue_depth: self.current_queue_depth.load(Ordering::Relaxed),
            max_queue_depth: self.max_queue_depth.load(Ordering::Relaxed),
            flush_attempted: self.flush_attempted.load(Ordering::Relaxed),
            flush_completed: self.flush_completed.load(Ordering::Relaxed),
            total_requests: self.total_requests.load(Ordering::Relaxed),
            total_latency_ms: self.total_latency_ms.load(Ordering::Relaxed),
            max_latency_ms: self.max_latency_ms.load(Ordering::Relaxed),
            current_replay_history_updates: self
                .current_replay_history_updates
                .load(Ordering::Relaxed),
            max_replay_history_updates: self.max_replay_history_updates.load(Ordering::Relaxed),
            flush_duration_ms: self.flush_duration_ms.load(Ordering::Relaxed),
        }
    }

    fn record_latency(&self, elapsed: Duration) {
        let millis = elapsed.as_millis().min(u64::MAX as u128) as u64;
        self.total_requests.fetch_add(1, Ordering::Relaxed);
        self.total_latency_ms.fetch_add(millis, Ordering::Relaxed);
        self.max_latency_ms.fetch_max(millis, Ordering::Relaxed);
    }

    fn observe_queue_depth(&self, depth: usize) {
        self.max_queue_depth.fetch_max(depth, Ordering::Relaxed);
    }

    fn add_pending(&self) {
        let depth = self.current_queue_depth.fetch_add(1, Ordering::Relaxed) + 1;
        self.observe_queue_depth(depth);
    }

    fn remove_pending(&self) {
        self.current_queue_depth
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |depth| {
                Some(depth.saturating_sub(1))
            })
            .expect("pending depth update is infallible");
    }

    fn begin_session(&self) {
        self.current_queue_depth.store(0, Ordering::Relaxed);
        self.flush_attempted.store(false, Ordering::Relaxed);
        self.flush_completed.store(false, Ordering::Relaxed);
        self.current_replay_history_updates
            .store(0, Ordering::Relaxed);
        self.max_replay_history_updates.store(0, Ordering::Relaxed);
        self.flush_duration_ms.store(0, Ordering::Relaxed);
    }

    fn observe_replay_history(&self, updates: usize) {
        self.current_replay_history_updates
            .store(updates, Ordering::Relaxed);
        self.max_replay_history_updates
            .fetch_max(updates, Ordering::Relaxed);
    }

    fn record_flush(&self, completed: bool, elapsed: Duration) {
        self.current_queue_depth.store(0, Ordering::Relaxed);
        self.current_replay_history_updates
            .store(0, Ordering::Relaxed);
        self.flush_attempted.store(true, Ordering::Relaxed);
        self.flush_completed.store(completed, Ordering::Relaxed);
        self.flush_duration_ms.store(
            elapsed.as_millis().min(u64::MAX as u128) as u64,
            Ordering::Relaxed,
        );
    }
}

enum DispatchMessage {
    Update(TranscriptUpdate),
    Repair(oneshot::Sender<bool>),
}

struct ActiveDispatcher {
    sender: mpsc::Sender<DispatchMessage>,
    cancellation: CancellationToken,
    replay: Arc<Mutex<BTreeMap<u64, TranscriptUpdate>>>,
    acknowledged_next: Arc<AtomicU64>,
    transport: SessionTransport,
    handle: JoinHandle<()>,
}

pub struct IntelligenceDispatcherState {
    active: Mutex<Option<ActiveDispatcher>>,
    diagnostics: Arc<Diagnostics>,
}

impl Default for IntelligenceDispatcherState {
    fn default() -> Self {
        Self {
            active: Mutex::new(None),
            diagnostics: Arc::new(Diagnostics::default()),
        }
    }
}

impl IntelligenceDispatcherState {
    pub async fn begin<R: Runtime>(
        &self,
        app: &tauri::AppHandle<R>,
        session_id: String,
        lang: String,
    ) {
        self.stop(Duration::from_secs(1)).await;

        let base_url =
            local_copilot_url_or_default(std::env::var("MEETING_COPILOT_URL").ok().as_deref());
        let capability_token = app
            .try_state::<crate::meeting_intelligence_sidecar::MeetingIntelligenceSidecarState>()
            .and_then(|state| state.capability_token());
        let (sender, receiver) = mpsc::channel(DISPATCH_QUEUE_CAPACITY);
        let cancellation = CancellationToken::new();
        let replay = Arc::new(Mutex::new(BTreeMap::new()));
        let acknowledged_next = Arc::new(AtomicU64::new(0));
        self.diagnostics.begin_session();
        let transport = SessionTransport {
            session_id,
            lang,
            base_url,
            capability_token,
        };
        let handle = tokio::spawn(run_dispatcher(
            receiver,
            cancellation.clone(),
            Arc::clone(&replay),
            Arc::clone(&acknowledged_next),
            Arc::clone(&self.diagnostics),
            transport.clone(),
        ));

        let mut active = self.active.lock().unwrap();
        *active = Some(ActiveDispatcher {
            sender,
            cancellation,
            replay,
            acknowledged_next,
            transport,
            handle,
        });
    }

    pub fn enqueue(&self, update: TranscriptUpdate) -> bool {
        let active = self.active.lock().unwrap();
        let Some(active) = active.as_ref() else {
            return false;
        };
        {
            let mut replay = active.replay.lock().unwrap();
            // "Applied" means the live engine accepted the event, not that its
            // recovery database is durable. Retain bounded session history so
            // a backend restart can request any lost durable suffix.
            if replay.len() >= MAX_REPLAY_HISTORY_UPDATES
                && !replay.contains_key(&update.sequence_id)
            {
                self.diagnostics.dropped.fetch_add(1, Ordering::Relaxed);
                return false;
            }
            replay.insert(update.sequence_id, update.clone());
            self.diagnostics.observe_replay_history(replay.len());
        }
        // Reserve the pending slot before publishing the message. Otherwise the
        // consumer can finish first and wrap the unsigned depth counter.
        self.diagnostics.add_pending();
        match active.sender.try_send(DispatchMessage::Update(update)) {
            Ok(()) => true,
            Err(mpsc::error::TrySendError::Full(_)) => {
                self.diagnostics.remove_pending();
                // The replay buffer remains authoritative and will be drained by
                // a later acknowledgement or the explicit stop flush. Do not
                // report retained work as a dropped transcript event.
                true
            }
            Err(mpsc::error::TrySendError::Closed(_)) => {
                self.diagnostics.remove_pending();
                false
            }
        }
    }

    pub async fn flush_and_stop(&self, timeout: Duration) -> bool {
        let active = self.active.lock().unwrap().take();
        let Some(active) = active else {
            return true;
        };
        let flush_started = Instant::now();
        let acknowledged_next = active.acknowledged_next.load(Ordering::Acquire);
        // Stop the serial sender before flushing. Otherwise a saturated queue
        // can leave the control message behind hundreds of slow single sends.
        // The retained replay map is authoritative, and duplicate delivery is
        // idempotent, so aborting an in-flight send before batch replay is safe.
        active.cancellation.cancel();
        drop(active.sender);
        let handle = active.handle;
        if !handle.is_finished() {
            handle.abort();
        }
        let _ = handle.await;
        let flushed = flush_replay_history(
            &active.transport,
            &active.replay,
            acknowledged_next,
            &self.diagnostics,
            timeout.saturating_sub(flush_started.elapsed()),
        )
        .await;
        self.diagnostics
            .record_flush(flushed, flush_started.elapsed());
        flushed
    }

    pub async fn repair(&self, timeout: Duration) -> IntelligenceRepairResult {
        let (sender, latest_sequence_id) = {
            let active = self.active.lock().unwrap();
            let Some(active) = active.as_ref() else {
                return IntelligenceRepairResult {
                    completed: false,
                    latest_sequence_id: None,
                };
            };
            let sender = active.sender.clone();
            let latest_sequence_id = active
                .replay
                .lock()
                .unwrap()
                .last_key_value()
                .map(|(seq, _)| *seq);
            (sender, latest_sequence_id)
        };
        let (done_tx, done_rx) = oneshot::channel();
        let completed = tokio::time::timeout(timeout, async {
            sender
                .send(DispatchMessage::Repair(done_tx))
                .await
                .map_err(|_| ())?;
            done_rx.await.map_err(|_| ())
        })
        .await
        .ok()
        .and_then(Result::ok)
        .unwrap_or(false);
        IntelligenceRepairResult {
            completed,
            latest_sequence_id,
        }
    }

    async fn stop(&self, timeout: Duration) {
        let _ = self.flush_and_stop(timeout).await;
    }

    pub fn diagnostics(&self) -> IntelligenceDispatcherDiagnostics {
        self.diagnostics.snapshot()
    }
}

#[derive(Clone)]
struct SessionTransport {
    session_id: String,
    lang: String,
    base_url: String,
    capability_token: Option<String>,
}

async fn run_dispatcher(
    mut receiver: mpsc::Receiver<DispatchMessage>,
    cancellation: CancellationToken,
    replay: Arc<Mutex<BTreeMap<u64, TranscriptUpdate>>>,
    acknowledged_next: Arc<AtomicU64>,
    diagnostics: Arc<Diagnostics>,
    transport: SessionTransport,
) {
    let client = match Client::builder().timeout(Duration::from_secs(2)).build() {
        Ok(client) => client,
        Err(_) => return,
    };
    while let Some(message) = receiver.recv().await {
        match message {
            DispatchMessage::Update(update) => {
                if let Some(ack) =
                    send_with_retry(&client, &transport, &update, &cancellation, &diagnostics).await
                {
                    let mut next_expected = ack.next_expected_sequence_id;
                    record_ack(&ack, update.sequence_id, &diagnostics);
                    if ack.status == IngestStatus::Buffered
                        || ack.next_expected_sequence_id <= update.sequence_id
                    {
                        diagnostics.missing.fetch_add(1, Ordering::Relaxed);
                        next_expected = replay_missing(
                            &client,
                            &transport,
                            next_expected,
                            &replay,
                            &cancellation,
                            &diagnostics,
                        )
                        .await;
                    }
                    acknowledged_next.store(next_expected, Ordering::Release);
                    prune_acknowledged(&replay, next_expected, &diagnostics);
                }
                diagnostics.remove_pending();
                // A full bounded channel deliberately leaves the update in the
                // replay map instead of blocking recording. Once queued sends
                // are exhausted, drain that retained suffix so live delivery
                // cannot remain stalled until stop or a manual repair.
                if receiver.is_empty() {
                    drain_retained_replay(
                        &client,
                        &transport,
                        &replay,
                        &acknowledged_next,
                        &cancellation,
                        &diagnostics,
                    )
                    .await;
                }
            }
            DispatchMessage::Repair(done) => {
                let final_next =
                    replay_missing(&client, &transport, 0, &replay, &cancellation, &diagnostics)
                        .await;
                acknowledged_next.store(final_next, Ordering::Release);
                prune_acknowledged(&replay, final_next, &diagnostics);
                let _ = done.send(replay_is_fully_acknowledged(&replay, final_next));
            }
        }
        if cancellation.is_cancelled() {
            break;
        }
    }
}

async fn drain_retained_replay(
    client: &Client,
    transport: &SessionTransport,
    replay: &Mutex<BTreeMap<u64, TranscriptUpdate>>,
    acknowledged_next: &AtomicU64,
    cancellation: &CancellationToken,
    diagnostics: &Diagnostics,
) {
    let mut delay = Duration::from_secs(1);
    loop {
        if cancellation.is_cancelled() {
            return;
        }
        let starting_next = acknowledged_next.load(Ordering::Acquire);
        let first_retained = replay
            .lock()
            .unwrap()
            .first_key_value()
            .map(|(sequence_id, _)| *sequence_id);
        if first_retained.is_none_or(|sequence_id| sequence_id > starting_next) {
            // The missing prefix may already be waiting in the actor channel.
            // Never hold the serial consumer in a retry loop that prevents it
            // from receiving the event needed to close this gap.
            return;
        }
        let final_next = replay_missing(
            client,
            transport,
            starting_next,
            replay,
            cancellation,
            diagnostics,
        )
        .await;
        if final_next > starting_next {
            acknowledged_next.store(final_next, Ordering::Release);
            prune_acknowledged(replay, final_next, diagnostics);
            delay = Duration::from_secs(1);
        }
        if replay_is_fully_acknowledged(replay, final_next) {
            return;
        }
        diagnostics.retried.fetch_add(1, Ordering::Relaxed);
        tokio::select! {
            _ = cancellation.cancelled() => return,
            _ = tokio::time::sleep(delay) => {}
        }
        delay = (delay * 2).min(RETRY_MAX_DELAY);
    }
}

async fn flush_replay_history(
    transport: &SessionTransport,
    replay: &Mutex<BTreeMap<u64, TranscriptUpdate>>,
    next_expected_sequence_id: u64,
    diagnostics: &Diagnostics,
    timeout: Duration,
) -> bool {
    if timeout.is_zero() {
        return false;
    }
    let client = match Client::builder().timeout(Duration::from_secs(2)).build() {
        Ok(client) => client,
        Err(_) => return false,
    };
    let cancellation = CancellationToken::new();
    tokio::time::timeout(timeout, async {
        let final_next = replay_missing(
            &client,
            transport,
            next_expected_sequence_id,
            replay,
            &cancellation,
            diagnostics,
        )
        .await;
        prune_acknowledged(replay, final_next, diagnostics);
        replay_is_fully_acknowledged(replay, final_next)
    })
    .await
    .unwrap_or(false)
}

fn prune_acknowledged(
    replay: &Mutex<BTreeMap<u64, TranscriptUpdate>>,
    next_expected_sequence_id: u64,
    diagnostics: &Diagnostics,
) {
    let mut replay = replay.lock().unwrap();
    while replay
        .first_key_value()
        .is_some_and(|(sequence_id, _)| *sequence_id < next_expected_sequence_id)
    {
        replay.pop_first();
    }
    diagnostics.observe_replay_history(replay.len());
}

async fn send_with_retry(
    client: &Client,
    transport: &SessionTransport,
    update: &TranscriptUpdate,
    cancellation: &CancellationToken,
    diagnostics: &Diagnostics,
) -> Option<IngestAcknowledgement> {
    let mut delay = Duration::from_secs(1);
    loop {
        if let Some(ack) = send_single(client, transport, update, diagnostics).await {
            return Some(ack);
        }
        diagnostics.retried.fetch_add(1, Ordering::Relaxed);
        tokio::select! {
            _ = cancellation.cancelled() => return None,
            _ = tokio::time::sleep(delay) => {}
        }
        delay = (delay * 2).min(RETRY_MAX_DELAY);
    }
}

async fn send_single(
    client: &Client,
    transport: &SessionTransport,
    update: &TranscriptUpdate,
    diagnostics: &Diagnostics,
) -> Option<IngestAcknowledgement> {
    let session = percent_encoding::utf8_percent_encode(
        &transport.session_id,
        percent_encoding::NON_ALPHANUMERIC,
    );
    let started = Instant::now();
    let mut request = client
        .post(format!("{}/ingest/live/{}", transport.base_url, session))
        .json(&serde_json::json!({
            "adapter": "meetily",
            "lang": transport.lang,
            "payload": update,
        }));
    if let Some(token) = &transport.capability_token {
        request = request.header(
            crate::meeting_intelligence_sidecar::CAPABILITY_TOKEN_HEADER,
            token,
        );
    }
    let response = request.send().await.ok()?;
    diagnostics.record_latency(started.elapsed());
    if !response.status().is_success() {
        return None;
    }
    response.json().await.ok()
}

async fn replay_missing(
    client: &Client,
    transport: &SessionTransport,
    mut next_expected: u64,
    replay: &Mutex<BTreeMap<u64, TranscriptUpdate>>,
    cancellation: &CancellationToken,
    diagnostics: &Diagnostics,
) -> u64 {
    loop {
        if cancellation.is_cancelled() {
            return next_expected;
        }
        let batch: Vec<TranscriptUpdate> = {
            replay
                .lock()
                .unwrap()
                .range(next_expected..)
                .take(MAX_BATCH_EVENTS)
                .map(|(_, update)| update.clone())
                .collect()
        };
        if batch.is_empty() {
            return next_expected;
        }
        let Some(ack) = send_batch(client, transport, &batch, diagnostics).await else {
            return next_expected;
        };
        record_ack(&ack, batch.last().unwrap().sequence_id, diagnostics);
        if ack.next_expected_sequence_id <= next_expected {
            return next_expected;
        }
        next_expected = ack.next_expected_sequence_id;
    }
}

async fn send_batch(
    client: &Client,
    transport: &SessionTransport,
    updates: &[TranscriptUpdate],
    diagnostics: &Diagnostics,
) -> Option<IngestAcknowledgement> {
    let session = percent_encoding::utf8_percent_encode(
        &transport.session_id,
        percent_encoding::NON_ALPHANUMERIC,
    );
    let started = Instant::now();
    let mut request = client
        .post(format!(
            "{}/ingest/live/{}/batch",
            transport.base_url, session
        ))
        .json(&serde_json::json!({
            "adapter": "meetily",
            "lang": transport.lang,
            "payloads": updates,
        }));
    if let Some(token) = &transport.capability_token {
        request = request.header(
            crate::meeting_intelligence_sidecar::CAPABILITY_TOKEN_HEADER,
            token,
        );
    }
    let response = request.send().await.ok()?;
    diagnostics.record_latency(started.elapsed());
    if !response.status().is_success() {
        return None;
    }
    response.json().await.ok()
}

fn record_ack(ack: &IngestAcknowledgement, sequence_id: u64, diagnostics: &Diagnostics) {
    match ack.status {
        IngestStatus::Applied => {
            diagnostics.accepted.fetch_add(1, Ordering::Relaxed);
        }
        IngestStatus::Duplicate => {
            diagnostics.duplicated.fetch_add(1, Ordering::Relaxed);
        }
        IngestStatus::Buffered => {
            if ack.next_expected_sequence_id <= sequence_id {
                diagnostics.missing.fetch_add(1, Ordering::Relaxed);
            }
        }
        IngestStatus::Rejected => {
            diagnostics.rejected.fetch_add(1, Ordering::Relaxed);
        }
    }
}

fn replay_is_fully_acknowledged(
    replay: &Mutex<BTreeMap<u64, TranscriptUpdate>>,
    next_expected_sequence_id: u64,
) -> bool {
    replay
        .lock()
        .unwrap()
        .last_key_value()
        .is_none_or(|(sequence_id, _)| next_expected_sequence_id > *sequence_id)
}

#[tauri::command]
pub fn get_meeting_intelligence_diagnostics(
    state: State<'_, IntelligenceDispatcherState>,
) -> IntelligenceDispatcherDiagnostics {
    state.diagnostics()
}

#[tauri::command]
pub async fn repair_meeting_intelligence_dispatcher(
    state: State<'_, IntelligenceDispatcherState>,
) -> Result<IntelligenceRepairResult, String> {
    Ok(state.repair(Duration::from_secs(10)).await)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    fn update(sequence_id: u64) -> TranscriptUpdate {
        TranscriptUpdate {
            text: "test".into(),
            timestamp: "00:00:00".into(),
            source: "Audio".into(),
            sequence_id,
            chunk_start_time: sequence_id as f64,
            is_partial: false,
            confidence: 1.0,
            audio_start_time: sequence_id as f64,
            audio_end_time: sequence_id as f64 + 1.0,
            duration: 1.0,
        }
    }

    #[test]
    fn acknowledged_updates_are_pruned_while_pending_suffix_is_retained() {
        let replay = Mutex::new(BTreeMap::from([
            (0, update(0)),
            (1, update(1)),
            (2, update(2)),
        ]));
        let diagnostics = Diagnostics::default();

        prune_acknowledged(&replay, 2, &diagnostics);
        assert_eq!(
            replay.lock().unwrap().keys().copied().collect::<Vec<_>>(),
            [2]
        );
        assert_eq!(diagnostics.snapshot().current_replay_history_updates, 1);
        prune_acknowledged(&replay, 3, &diagnostics);
        assert!(replay_is_fully_acknowledged(&replay, 3));
        assert_eq!(diagnostics.snapshot().current_replay_history_updates, 0);
    }

    #[test]
    fn acknowledged_stream_does_not_exhaust_the_replay_history_limit() {
        let replay = Mutex::new(BTreeMap::new());
        let diagnostics = Diagnostics::default();

        for sequence_id in 0..=(MAX_REPLAY_HISTORY_UPDATES as u64) {
            replay
                .lock()
                .unwrap()
                .insert(sequence_id, update(sequence_id));
            diagnostics.observe_replay_history(replay.lock().unwrap().len());
            prune_acknowledged(&replay, sequence_id + 1, &diagnostics);
        }

        assert!(replay.lock().unwrap().is_empty());
        let snapshot = diagnostics.snapshot();
        assert_eq!(snapshot.current_replay_history_updates, 0);
        assert_eq!(snapshot.max_replay_history_updates, 1);
    }

    #[test]
    fn empty_replay_history_is_already_fully_acknowledged() {
        assert!(replay_is_fully_acknowledged(
            &Mutex::new(BTreeMap::new()),
            0
        ));
    }

    #[tokio::test]
    async fn retained_history_repairs_a_restarted_backend_from_sequence_zero() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = Vec::new();
            let mut buffer = [0_u8; 4096];
            loop {
                let read = stream.read(&mut buffer).await.unwrap();
                if read == 0 {
                    break;
                }
                request.extend_from_slice(&buffer[..read]);
                if request.windows(4).any(|window| window == b"\r\n\r\n")
                    && request
                        .windows(15)
                        .any(|window| window == b"\"sequence_id\":2")
                {
                    break;
                }
            }
            let request = String::from_utf8(request).unwrap();
            let request_line = request.lines().next().unwrap();
            assert_eq!(
                request_line,
                "POST /ingest/live/test%2Dsession/batch HTTP/1.1"
            );
            for sequence_id in 0..=2 {
                assert!(request.contains(&format!("\"sequence_id\":{sequence_id}")));
            }
            let body = r#"{"status":"applied","next_expected_sequence_id":3}"#;
            stream
                .write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                        body.len(),
                        body
                    )
                    .as_bytes(),
                )
                .await
                .unwrap();
        });
        let replay = Mutex::new(BTreeMap::from([
            (0, update(0)),
            (1, update(1)),
            (2, update(2)),
        ]));
        let transport = SessionTransport {
            session_id: "test-session".into(),
            lang: "en".into(),
            base_url: format!("http://{address}"),
            capability_token: None,
        };

        let next_expected = replay_missing(
            &Client::new(),
            &transport,
            0,
            &replay,
            &CancellationToken::new(),
            &Diagnostics::default(),
        )
        .await;
        server.await.unwrap();

        assert_eq!(next_expected, 3);
        assert!(replay_is_fully_acknowledged(&replay, next_expected));
        assert_eq!(replay.lock().unwrap().len(), 3);
    }

    #[tokio::test]
    async fn idle_dispatcher_drains_updates_retained_after_queue_saturation() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = Vec::new();
            let mut buffer = [0_u8; 4096];
            loop {
                let read = stream.read(&mut buffer).await.unwrap();
                assert!(read > 0);
                request.extend_from_slice(&buffer[..read]);
                if request
                    .windows(15)
                    .any(|window| window == b"\"sequence_id\":2")
                {
                    break;
                }
            }
            let body = r#"{"status":"applied","next_expected_sequence_id":3}"#;
            stream
                .write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                        body.len(),
                        body
                    )
                    .as_bytes(),
                )
                .await
                .unwrap();
        });
        let replay = Mutex::new(BTreeMap::from([(1, update(1)), (2, update(2))]));
        let acknowledged_next = AtomicU64::new(1);
        let diagnostics = Diagnostics::default();
        diagnostics.observe_replay_history(2);
        let transport = SessionTransport {
            session_id: "test-session".into(),
            lang: "en".into(),
            base_url: format!("http://{address}"),
            capability_token: None,
        };

        drain_retained_replay(
            &Client::new(),
            &transport,
            &replay,
            &acknowledged_next,
            &CancellationToken::new(),
            &diagnostics,
        )
        .await;
        server.await.unwrap();

        assert_eq!(acknowledged_next.load(Ordering::Acquire), 3);
        assert!(replay.lock().unwrap().is_empty());
        assert_eq!(diagnostics.snapshot().current_replay_history_updates, 0);
    }

    #[tokio::test]
    async fn idle_dispatcher_never_blocks_on_a_missing_local_prefix() {
        let replay = Mutex::new(BTreeMap::from([(2, update(2))]));
        let acknowledged_next = AtomicU64::new(0);
        let diagnostics = Diagnostics::default();
        let transport = SessionTransport {
            session_id: "test-session".into(),
            lang: "en".into(),
            base_url: "http://127.0.0.1:1".into(),
            capability_token: None,
        };

        tokio::time::timeout(
            Duration::from_millis(100),
            drain_retained_replay(
                &Client::new(),
                &transport,
                &replay,
                &acknowledged_next,
                &CancellationToken::new(),
                &diagnostics,
            ),
        )
        .await
        .expect("a local gap must return control to the actor");

        assert_eq!(acknowledged_next.load(Ordering::Acquire), 0);
        assert_eq!(
            replay.lock().unwrap().keys().copied().collect::<Vec<_>>(),
            [2]
        );
    }

    #[tokio::test]
    async fn saturated_single_send_queue_does_not_block_batch_flush() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = Vec::new();
            let mut buffer = [0_u8; 4096];
            loop {
                let read = stream.read(&mut buffer).await.unwrap();
                assert!(read > 0);
                request.extend_from_slice(&buffer[..read]);
                if request
                    .windows(15)
                    .any(|window| window == b"\"sequence_id\":2")
                {
                    break;
                }
            }
            let body = r#"{"status":"applied","next_expected_sequence_id":3}"#;
            stream
                .write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                        body.len(),
                        body
                    )
                    .as_bytes(),
                )
                .await
                .unwrap();
        });

        let replay = Arc::new(Mutex::new(BTreeMap::from([
            (0, update(0)),
            (1, update(1)),
            (2, update(2)),
        ])));
        let (sender, receiver) = mpsc::channel(1);
        assert!(sender.try_send(DispatchMessage::Update(update(0))).is_ok());
        let handle = tokio::spawn(async move {
            let _receiver = receiver;
            std::future::pending::<()>().await;
        });
        let diagnostics = Arc::new(Diagnostics::default());
        diagnostics.begin_session();
        diagnostics.observe_replay_history(3);
        let state = IntelligenceDispatcherState {
            active: Mutex::new(Some(ActiveDispatcher {
                sender,
                cancellation: CancellationToken::new(),
                replay,
                acknowledged_next: Arc::new(AtomicU64::new(0)),
                transport: SessionTransport {
                    session_id: "test-session".into(),
                    lang: "en".into(),
                    base_url: format!("http://{address}"),
                    capability_token: None,
                },
                handle,
            })),
            diagnostics,
        };

        assert!(state.flush_and_stop(Duration::from_secs(2)).await);
        server.await.unwrap();
        let snapshot = state.diagnostics();
        assert!(snapshot.flush_completed);
        assert_eq!(snapshot.current_queue_depth, 0);
        assert_eq!(snapshot.current_replay_history_updates, 0);
    }

    #[test]
    fn diagnostics_do_not_contain_content_or_transport_secrets() {
        let json = serde_json::to_string(&Diagnostics::default().snapshot()).unwrap();
        for forbidden in ["text", "token", "speaker", "meetingTitle", "path"] {
            assert!(!json.contains(forbidden));
        }
    }

    #[test]
    fn diagnostics_distinguish_attempted_and_completed_flushes() {
        let diagnostics = Diagnostics::default();
        diagnostics.begin_session();
        diagnostics.remove_pending();
        assert_eq!(diagnostics.snapshot().current_queue_depth, 0);
        diagnostics.add_pending();
        diagnostics.add_pending();
        diagnostics.add_pending();

        let active = diagnostics.snapshot();
        assert_eq!(active.current_queue_depth, 3);
        assert!(!active.flush_attempted);
        assert!(!active.flush_completed);

        diagnostics.record_flush(true, Duration::from_millis(37));
        let stopped = diagnostics.snapshot();
        assert_eq!(stopped.current_queue_depth, 0);
        assert_eq!(stopped.current_replay_history_updates, 0);
        assert!(stopped.flush_attempted);
        assert!(stopped.flush_completed);
        assert_eq!(stopped.flush_duration_ms, 37);
    }
}
