//! Local, content-free usefulness feedback for ASK NOW suggestions.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, Runtime, State};

const MAX_FEEDBACK_RECORDS: usize = 10_000;
const MAX_FEEDBACK_BYTES: u64 = 2 * 1024 * 1024;
const FEEDBACK_FILE: &str = "meeting-intelligence-feedback.jsonl";

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum IntelligenceFeedbackAction {
    Useful,
    Dismissed,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct IntelligenceFeedbackRequest {
    session_id: String,
    suggestion_id: String,
    pain_template: String,
    slot: String,
    action: IntelligenceFeedbackAction,
    state_version: u64,
    reopen_count: u64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct IntelligenceFeedbackRecord {
    session_id: String,
    suggestion_id: String,
    pain_template: String,
    slot: String,
    action: IntelligenceFeedbackAction,
    state_version: u64,
    reopen_count: u64,
    recorded_at_epoch_seconds: u64,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct IntelligenceFeedbackOccurrence {
    session_id: String,
    suggestion_id: String,
    reopen_count: u64,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct IntelligenceFeedbackSummaryRow {
    pain_template: String,
    slot: String,
    useful: u64,
    dismissed: u64,
}

#[derive(Default)]
pub struct IntelligenceFeedbackState {
    records: Mutex<Vec<IntelligenceFeedbackRecord>>,
}

impl IntelligenceFeedbackState {
    pub fn load_existing<R: Runtime>(&self, app: &AppHandle<R>) -> Result<(), String> {
        let data_dir = app
            .path()
            .app_local_data_dir()
            .map_err(|_| "Meeting Intelligence feedback is unavailable".to_string())?;
        let Ok(contents) = read_feedback_tail(&data_dir.join(FEEDBACK_FILE), MAX_FEEDBACK_BYTES)
        else {
            return Ok(());
        };
        let loaded: Vec<IntelligenceFeedbackRecord> = contents
            .lines()
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect();
        let mut loaded = deduplicate_feedback_records(loaded);
        if loaded.len() > MAX_FEEDBACK_RECORDS {
            loaded.drain(..loaded.len() - MAX_FEEDBACK_RECORDS);
        }
        *self
            .records
            .lock()
            .map_err(|_| "Meeting Intelligence feedback is unavailable".to_string())? = loaded;
        Ok(())
    }

    pub fn summary(&self) -> Result<Vec<IntelligenceFeedbackSummaryRow>, String> {
        let records = self
            .records
            .lock()
            .map_err(|_| "Meeting Intelligence feedback is unavailable".to_string())?;
        let mut counts: BTreeMap<(String, String), (u64, u64)> = BTreeMap::new();
        for record in records.iter() {
            let count = counts
                .entry((record.pain_template.clone(), record.slot.clone()))
                .or_default();
            match record.action {
                IntelligenceFeedbackAction::Useful => count.0 += 1,
                IntelligenceFeedbackAction::Dismissed => count.1 += 1,
            }
        }
        Ok(counts
            .into_iter()
            .map(
                |((pain_template, slot), (useful, dismissed))| IntelligenceFeedbackSummaryRow {
                    pain_template,
                    slot,
                    useful,
                    dismissed,
                },
            )
            .collect())
    }
}

fn read_feedback_tail(path: &Path, maximum_bytes: u64) -> std::io::Result<String> {
    let mut file = OpenOptions::new().read(true).open(path)?;
    let length = file.metadata()?.len();
    let starts_mid_file = length > maximum_bytes;
    if starts_mid_file {
        file.seek(SeekFrom::End(-(maximum_bytes.min(i64::MAX as u64) as i64)))?;
    }
    let mut contents = Vec::with_capacity(length.min(maximum_bytes) as usize);
    file.take(maximum_bytes).read_to_end(&mut contents)?;
    if starts_mid_file {
        if let Some(first_newline) = contents.iter().position(|byte| *byte == b'\n') {
            contents.drain(..=first_newline);
        } else {
            contents.clear();
        }
    }
    Ok(String::from_utf8_lossy(&contents).into_owned())
}

fn deduplicate_feedback_records(
    mut records: Vec<IntelligenceFeedbackRecord>,
) -> Vec<IntelligenceFeedbackRecord> {
    let mut seen = BTreeSet::new();
    records.reverse();
    records.retain(|record| {
        seen.insert((
            record.session_id.clone(),
            record.suggestion_id.clone(),
            record.reopen_count,
            record.action,
        ))
    });
    records.reverse();
    records
}

fn valid_identifier(value: &str, max_len: usize) -> bool {
    !value.is_empty()
        && value.len() <= max_len
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(&byte))
}

fn occurrence_is_dismissed(
    mut records: impl Iterator<Item = IntelligenceFeedbackRecord>,
    occurrence: &IntelligenceFeedbackOccurrence,
) -> bool {
    records.any(|record| {
        record.action == IntelligenceFeedbackAction::Dismissed
            && record.session_id == occurrence.session_id
            && record.suggestion_id == occurrence.suggestion_id
            && record.reopen_count == occurrence.reopen_count
    })
}

fn serialized_record_line(record: &IntelligenceFeedbackRecord) -> Result<Vec<u8>, String> {
    let mut line = serde_json::to_vec(record)
        .map_err(|_| "Meeting Intelligence feedback serialization failed".to_string())?;
    line.push(b'\n');
    Ok(line)
}

fn bounded_feedback_records(
    records: &[IntelligenceFeedbackRecord],
    maximum_bytes: u64,
    maximum_records: usize,
) -> Result<(Vec<IntelligenceFeedbackRecord>, Vec<u8>), String> {
    let mut retained = Vec::new();
    let mut bytes = 0_u64;
    for record in records.iter().rev().take(maximum_records) {
        let line = serialized_record_line(record)?;
        let line_bytes = line.len() as u64;
        if line_bytes > maximum_bytes {
            return Err(
                "Meeting Intelligence feedback record exceeds the local size limit".to_string(),
            );
        }
        if bytes.saturating_add(line_bytes) > maximum_bytes {
            break;
        }
        bytes += line_bytes;
        retained.push((record.clone(), line));
    }
    retained.reverse();
    let records = retained.iter().map(|(record, _)| record.clone()).collect();
    let encoded = retained.into_iter().flat_map(|(_, line)| line).collect();
    Ok((records, encoded))
}

#[cfg(target_os = "windows")]
fn replace_feedback_file(source: &Path, destination: &Path) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source_wide: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination_wide: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    let replaced = unsafe {
        MoveFileExW(
            source_wide.as_ptr(),
            destination_wide.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if replaced == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(not(target_os = "windows"))]
fn replace_feedback_file(source: &Path, destination: &Path) -> std::io::Result<()> {
    fs::rename(source, destination)
}

fn write_feedback_snapshot(path: &Path, contents: &[u8]) -> Result<(), String> {
    let temporary = path.with_extension(format!("jsonl.{}.tmp", uuid::Uuid::new_v4().simple()));
    let result = (|| -> std::io::Result<()> {
        let mut file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)?;
        file.write_all(contents)?;
        file.sync_all()?;
        replace_feedback_file(&temporary, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
        return Err("Meeting Intelligence feedback storage is unavailable".to_string());
    }
    Ok(())
}

fn remove_feedback_files(data_dir: &Path) -> std::io::Result<()> {
    if !data_dir.exists() {
        return Ok(());
    }
    for entry in fs::read_dir(data_dir)? {
        let entry = entry?;
        let name = entry.file_name();
        let name = name.to_string_lossy();
        let is_snapshot = name == FEEDBACK_FILE
            || (name.starts_with(&format!("{FEEDBACK_FILE}.")) && name.ends_with(".tmp"));
        if is_snapshot && entry.file_type()?.is_file() {
            fs::remove_file(entry.path())?;
        }
    }
    Ok(())
}

fn persist_feedback_record(
    path: &Path,
    records: &mut Vec<IntelligenceFeedbackRecord>,
    record: IntelligenceFeedbackRecord,
) -> Result<bool, String> {
    let already_recorded = records.iter().any(|existing| {
        existing.session_id == record.session_id
            && existing.suggestion_id == record.suggestion_id
            && existing.reopen_count == record.reopen_count
            && existing.action == record.action
    });
    if already_recorded {
        return Ok(false);
    }

    let line = serialized_record_line(&record)?;
    let existing_bytes = path.metadata().map(|metadata| metadata.len()).unwrap_or(0);
    if records.len() < MAX_FEEDBACK_RECORDS
        && existing_bytes.saturating_add(line.len() as u64) <= MAX_FEEDBACK_BYTES
    {
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .map_err(|_| "Meeting Intelligence feedback storage is unavailable".to_string())?;
        file.write_all(&line)
            .and_then(|_| file.sync_data())
            .map_err(|_| "Meeting Intelligence feedback storage is unavailable".to_string())?;
        records.push(record);
        return Ok(true);
    }

    let mut candidate = records.clone();
    candidate.push(record);
    let (retained, encoded) =
        bounded_feedback_records(&candidate, MAX_FEEDBACK_BYTES, MAX_FEEDBACK_RECORDS)?;
    write_feedback_snapshot(path, &encoded)?;
    *records = retained;
    Ok(true)
}

#[tauri::command]
pub fn record_meeting_intelligence_feedback<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, IntelligenceFeedbackState>,
    feedback: IntelligenceFeedbackRequest,
) -> Result<bool, String> {
    if !valid_identifier(&feedback.session_id, 128)
        || !valid_identifier(&feedback.suggestion_id, 256)
        || !valid_identifier(&feedback.pain_template, 64)
        || !valid_identifier(&feedback.slot, 64)
    {
        return Err("Meeting Intelligence feedback is invalid".to_string());
    }
    let record = IntelligenceFeedbackRecord {
        session_id: feedback.session_id,
        suggestion_id: feedback.suggestion_id,
        pain_template: feedback.pain_template,
        slot: feedback.slot,
        action: feedback.action,
        state_version: feedback.state_version,
        reopen_count: feedback.reopen_count,
        recorded_at_epoch_seconds: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs(),
    };
    let data_dir = app
        .path()
        .app_local_data_dir()
        .map_err(|_| "Meeting Intelligence feedback storage is unavailable".to_string())?;
    std::fs::create_dir_all(&data_dir)
        .map_err(|_| "Meeting Intelligence feedback storage is unavailable".to_string())?;
    let feedback_path = data_dir.join(FEEDBACK_FILE);
    let mut records = state
        .records
        .lock()
        .map_err(|_| "Meeting Intelligence feedback is unavailable".to_string())?;
    persist_feedback_record(&feedback_path, &mut records, record)
}

#[tauri::command]
pub fn get_meeting_intelligence_feedback_summary(
    state: State<'_, IntelligenceFeedbackState>,
) -> Result<Vec<IntelligenceFeedbackSummaryRow>, String> {
    state.summary()
}

#[tauri::command]
pub fn is_meeting_intelligence_suggestion_dismissed(
    state: State<'_, IntelligenceFeedbackState>,
    occurrence: IntelligenceFeedbackOccurrence,
) -> Result<bool, String> {
    if !valid_identifier(&occurrence.session_id, 128)
        || !valid_identifier(&occurrence.suggestion_id, 256)
    {
        return Err("Meeting Intelligence feedback lookup is invalid".to_string());
    }
    let records = state
        .records
        .lock()
        .map_err(|_| "Meeting Intelligence feedback is unavailable".to_string())?;
    Ok(occurrence_is_dismissed(
        records.iter().cloned(),
        &occurrence,
    ))
}

#[tauri::command]
pub fn delete_all_meeting_intelligence_feedback<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, IntelligenceFeedbackState>,
) -> Result<bool, String> {
    let mut records = state
        .records
        .lock()
        .map_err(|_| "Meeting Intelligence feedback is unavailable".to_string())?;
    let data_dir = app
        .path()
        .app_local_data_dir()
        .map_err(|_| "Meeting Intelligence feedback could not be deleted".to_string())?;
    remove_feedback_files(&data_dir)
        .map_err(|_| "Meeting Intelligence feedback could not be deleted".to_string())?;
    records.clear();
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identifiers_reject_text_shaped_or_path_shaped_values() {
        assert!(valid_identifier("meeting-intel-abc", 128));
        assert!(!valid_identifier("customer said this hurts", 128));
        assert!(!valid_identifier("C:\\private\\meeting", 128));
        assert!(!valid_identifier("token/value", 128));
    }

    #[test]
    fn serialized_record_contains_no_transcript_or_meeting_title_field() {
        let record = IntelligenceFeedbackRecord {
            session_id: "meeting-intel-abc".into(),
            suggestion_id: "suggestion:1".into(),
            pain_template: "manual_work".into(),
            slot: "business_impact".into(),
            action: IntelligenceFeedbackAction::Useful,
            state_version: 2,
            reopen_count: 0,
            recorded_at_epoch_seconds: 1,
        };
        let json = serde_json::to_string(&record).unwrap();
        for forbidden in [
            "transcript",
            "meeting_name",
            "meetingTitle",
            "token",
            "audio",
        ] {
            assert!(!json.contains(forbidden));
        }
    }

    #[test]
    fn dismissal_is_scoped_to_suggestion_and_reopen_occurrence() {
        let record = IntelligenceFeedbackRecord {
            session_id: "meeting-intel-abc".into(),
            suggestion_id: "suggestion:1".into(),
            pain_template: "manual_work".into(),
            slot: "business_impact".into(),
            action: IntelligenceFeedbackAction::Dismissed,
            state_version: 2,
            reopen_count: 1,
            recorded_at_epoch_seconds: 1,
        };
        let current = IntelligenceFeedbackOccurrence {
            session_id: "meeting-intel-abc".into(),
            suggestion_id: "suggestion:1".into(),
            reopen_count: 1,
        };
        let reopened = IntelligenceFeedbackOccurrence {
            reopen_count: 2,
            ..current.clone()
        };

        assert!(occurrence_is_dismissed(
            [record.clone()].into_iter(),
            &current
        ));
        assert!(!occurrence_is_dismissed([record].into_iter(), &reopened));
    }

    #[test]
    fn identical_feedback_action_is_idempotent_per_occurrence() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join(FEEDBACK_FILE);
        let record = IntelligenceFeedbackRecord {
            session_id: "meeting-intel-abc".into(),
            suggestion_id: "suggestion:1".into(),
            pain_template: "manual_work".into(),
            slot: "business_impact".into(),
            action: IntelligenceFeedbackAction::Useful,
            state_version: 2,
            reopen_count: 0,
            recorded_at_epoch_seconds: 1,
        };
        let mut records = Vec::new();

        assert!(persist_feedback_record(&path, &mut records, record.clone()).unwrap());
        assert!(!persist_feedback_record(&path, &mut records, record).unwrap());
        assert_eq!(records.len(), 1);
        assert_eq!(fs::read_to_string(path).unwrap().lines().count(), 1);
    }

    #[test]
    fn feedback_recovery_reads_only_complete_records_from_a_bounded_tail() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join(FEEDBACK_FILE);
        let newest = b"{\"sessionId\":\"meeting-intel-newest\"}\n";
        let mut contents = vec![b'x'; 512];
        contents.push(b'\n');
        contents.extend_from_slice(newest);
        fs::write(&path, contents).unwrap();

        let recovered = read_feedback_tail(&path, (newest.len() + 32) as u64).unwrap();

        assert_eq!(recovered.as_bytes(), newest);
    }

    #[test]
    fn feedback_recovery_deduplicates_legacy_rows_but_keeps_distinct_actions() {
        let useful = IntelligenceFeedbackRecord {
            session_id: "meeting-intel-abc".into(),
            suggestion_id: "suggestion:1".into(),
            pain_template: "manual_work".into(),
            slot: "business_impact".into(),
            action: IntelligenceFeedbackAction::Useful,
            state_version: 2,
            reopen_count: 0,
            recorded_at_epoch_seconds: 1,
        };
        let newer_useful = IntelligenceFeedbackRecord {
            state_version: 3,
            recorded_at_epoch_seconds: 2,
            ..useful.clone()
        };
        let dismissed = IntelligenceFeedbackRecord {
            action: IntelligenceFeedbackAction::Dismissed,
            ..newer_useful.clone()
        };

        let recovered = deduplicate_feedback_records(vec![useful, newer_useful, dismissed]);

        assert_eq!(recovered.len(), 2);
        assert_eq!(recovered[0].state_version, 3);
        assert_eq!(recovered[0].action, IntelligenceFeedbackAction::Useful);
        assert_eq!(recovered[1].action, IntelligenceFeedbackAction::Dismissed);
    }

    #[test]
    fn different_actions_and_reopened_occurrences_remain_distinct() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join(FEEDBACK_FILE);
        let useful = IntelligenceFeedbackRecord {
            session_id: "meeting-intel-abc".into(),
            suggestion_id: "suggestion:1".into(),
            pain_template: "manual_work".into(),
            slot: "business_impact".into(),
            action: IntelligenceFeedbackAction::Useful,
            state_version: 2,
            reopen_count: 0,
            recorded_at_epoch_seconds: 1,
        };
        let dismissed = IntelligenceFeedbackRecord {
            action: IntelligenceFeedbackAction::Dismissed,
            ..useful.clone()
        };
        let reopened = IntelligenceFeedbackRecord {
            reopen_count: 1,
            ..useful.clone()
        };
        let mut records = Vec::new();

        assert!(persist_feedback_record(&path, &mut records, useful).unwrap());
        assert!(persist_feedback_record(&path, &mut records, dismissed).unwrap());
        assert!(persist_feedback_record(&path, &mut records, reopened).unwrap());
        assert_eq!(records.len(), 3);
    }

    #[test]
    fn compaction_retains_newest_records_in_original_order() {
        let records: Vec<_> = (0..5)
            .map(|index| IntelligenceFeedbackRecord {
                session_id: "meeting-intel-abc".into(),
                suggestion_id: format!("suggestion:{index}"),
                pain_template: "manual_work".into(),
                slot: "business_impact".into(),
                action: IntelligenceFeedbackAction::Dismissed,
                state_version: index,
                reopen_count: 0,
                recorded_at_epoch_seconds: index,
            })
            .collect();

        let (retained, encoded) = bounded_feedback_records(&records, u64::MAX, 3).unwrap();
        assert_eq!(
            retained
                .iter()
                .map(|record| record.suggestion_id.as_str())
                .collect::<Vec<_>>(),
            vec!["suggestion:2", "suggestion:3", "suggestion:4"]
        );
        assert_eq!(encoded.iter().filter(|byte| **byte == b'\n').count(), 3);

        let newest_two_bytes = serialized_record_line(&records[3]).unwrap().len()
            + serialized_record_line(&records[4]).unwrap().len();
        let (byte_bounded, _) =
            bounded_feedback_records(&records, newest_two_bytes as u64, records.len()).unwrap();
        assert_eq!(
            byte_bounded
                .iter()
                .map(|record| record.suggestion_id.as_str())
                .collect::<Vec<_>>(),
            vec!["suggestion:3", "suggestion:4"]
        );
    }

    #[test]
    fn compacted_snapshot_replaces_existing_file() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join(FEEDBACK_FILE);
        fs::write(&path, b"old\n").unwrap();

        write_feedback_snapshot(&path, b"new\nrecord\n").unwrap();

        assert_eq!(fs::read(&path).unwrap(), b"new\nrecord\n");
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
    }

    #[test]
    fn feedback_deletion_removes_main_and_abandoned_snapshots_only() {
        let directory = tempfile::tempdir().unwrap();
        fs::write(directory.path().join(FEEDBACK_FILE), b"main").unwrap();
        fs::write(
            directory
                .path()
                .join(format!("{FEEDBACK_FILE}.0123456789abcdef.tmp")),
            b"temporary",
        )
        .unwrap();
        fs::write(directory.path().join("unrelated.jsonl"), b"keep").unwrap();

        remove_feedback_files(directory.path()).unwrap();

        assert!(!directory.path().join(FEEDBACK_FILE).exists());
        assert!(directory.path().join("unrelated.jsonl").exists());
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
    }
}
