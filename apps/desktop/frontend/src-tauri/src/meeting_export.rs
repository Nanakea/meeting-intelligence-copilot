//! Explicit, local export of user-facing saved meeting content.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;

use serde::Serialize;
use serde_json::Value;
use tauri::{AppHandle, Runtime, State};
use tauri_plugin_dialog::DialogExt;

use crate::database::repositories::{
    meeting::MeetingsRepository, summary::SummaryProcessesRepository,
};
use crate::meeting_intelligence_issue_draft::{get_issue_draft, MeetingIssueDraft};
use crate::state::AppState;

#[derive(Debug, Serialize)]
struct MeetingExportTranscript {
    text: String,
    timestamp: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    audio_start_time: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    audio_end_time: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    duration: Option<f64>,
}

#[derive(Debug, Serialize)]
struct MeetingExportIssueDraft {
    markdown: String,
    unresolved_count: i64,
    language: String,
    updated_at: String,
}

impl From<MeetingIssueDraft> for MeetingExportIssueDraft {
    fn from(value: MeetingIssueDraft) -> Self {
        Self {
            markdown: value.draft_markdown,
            unresolved_count: value.unresolved_count,
            language: value.language,
            updated_at: value.updated_at,
        }
    }
}

#[derive(Debug, Serialize)]
struct LocalMeetingExport {
    schema_version: u8,
    exported_at: String,
    title: String,
    created_at: String,
    updated_at: String,
    transcripts: Vec<MeetingExportTranscript>,
    #[serde(skip_serializing_if = "Option::is_none")]
    summary: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    issue_draft: Option<MeetingExportIssueDraft>,
}

#[cfg(target_os = "windows")]
fn replace_export_file(source: &Path, destination: &Path) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    let replaced = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
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
fn replace_export_file(source: &Path, destination: &Path) -> std::io::Result<()> {
    std::fs::rename(source, destination)
}

pub(crate) fn write_export(path: &Path, contents: &[u8]) -> Result<(), String> {
    let temporary = path.with_extension(format!("json.{}.tmp", uuid::Uuid::new_v4().simple()));
    let result = (|| -> std::io::Result<()> {
        let mut file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)?;
        file.write_all(contents)?;
        file.sync_all()?;
        replace_export_file(&temporary, path)
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
        return Err("The meeting export file could not be written".to_string());
    }
    Ok(())
}

#[tauri::command]
pub async fn export_local_meeting<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, AppState>,
    meeting_id: String,
) -> Result<bool, String> {
    if meeting_id.trim().is_empty() || meeting_id.len() > 128 {
        return Err("The saved meeting identifier is invalid".to_string());
    }
    let pool = state.db_manager.pool();
    let meeting = MeetingsRepository::get_meeting(pool, &meeting_id)
        .await
        .map_err(|_| "The saved meeting could not be prepared for export".to_string())?
        .ok_or_else(|| "The saved meeting was not found".to_string())?;
    let summary = SummaryProcessesRepository::get_summary_data(pool, &meeting_id)
        .await
        .map_err(|_| "The saved summary could not be prepared for export".to_string())?
        .and_then(|process| process.result)
        .map(|raw| serde_json::from_str(&raw).unwrap_or(Value::String(raw)));
    let issue_draft = get_issue_draft(pool, &meeting_id)
        .await
        .map_err(|_| "The saved issue draft could not be prepared for export".to_string())?
        .map(Into::into);

    let mut transcripts = meeting
        .transcripts
        .into_iter()
        .map(|transcript| MeetingExportTranscript {
            text: transcript.text,
            timestamp: transcript.timestamp,
            audio_start_time: transcript.audio_start_time,
            audio_end_time: transcript.audio_end_time,
            duration: transcript.duration,
        })
        .collect::<Vec<_>>();
    transcripts.sort_by(|left, right| {
        left.audio_start_time
            .partial_cmp(&right.audio_start_time)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.timestamp.cmp(&right.timestamp))
    });
    let export = LocalMeetingExport {
        schema_version: 1,
        exported_at: chrono::Utc::now().to_rfc3339(),
        title: meeting.title,
        created_at: meeting.created_at,
        updated_at: meeting.updated_at,
        transcripts,
        summary,
        issue_draft,
    };
    let encoded = serde_json::to_vec_pretty(&export)
        .map_err(|_| "The saved meeting export could not be encoded".to_string())?;

    let app_for_dialog = app.clone();
    let selected = tokio::task::spawn_blocking(move || {
        app_for_dialog
            .dialog()
            .file()
            .add_filter("Meeting Intelligence Copilot export", &["json"])
            .set_file_name("meeting-intelligence-export.json")
            .blocking_save_file()
    })
    .await
    .map_err(|_| "The meeting export dialog is unavailable".to_string())?;
    let Some(selected) = selected else {
        return Ok(false);
    };
    let selected_path = selected
        .into_path()
        .map_err(|_| "The selected meeting export location is invalid".to_string())?;
    write_export(&selected_path, &encoded)?;
    log::info!("Exported saved local meeting {}", meeting_id);
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn export_contract_contains_content_but_no_internal_identifiers_or_paths() {
        let export = LocalMeetingExport {
            schema_version: 1,
            exported_at: "2026-08-13T00:00:00Z".into(),
            title: "Pilot".into(),
            created_at: "2026-08-13T00:00:00Z".into(),
            updated_at: "2026-08-13T00:01:00Z".into(),
            transcripts: vec![MeetingExportTranscript {
                text: "Customer impact stated".into(),
                timestamp: "00:01".into(),
                audio_start_time: Some(1.0),
                audio_end_time: Some(2.0),
                duration: Some(1.0),
            }],
            summary: None,
            issue_draft: None,
        };
        let encoded = serde_json::to_string(&export).unwrap();

        assert!(encoded.contains("Customer impact stated"));
        for forbidden in [
            "meeting_id",
            "recording_session_id",
            "transcript_id",
            "evidence_event_ids",
            "folder_path",
            "capability_token",
        ] {
            assert!(!encoded.contains(forbidden));
        }
    }

    #[test]
    fn export_replaces_an_existing_file_without_leaving_temporary_data() {
        let directory = tempfile::tempdir().unwrap();
        let destination = directory.path().join("meeting.json");
        std::fs::write(&destination, b"old").unwrap();

        write_export(&destination, b"new").unwrap();

        assert_eq!(std::fs::read(&destination).unwrap(), b"new");
        assert_eq!(std::fs::read_dir(directory.path()).unwrap().count(), 1);
    }
}
