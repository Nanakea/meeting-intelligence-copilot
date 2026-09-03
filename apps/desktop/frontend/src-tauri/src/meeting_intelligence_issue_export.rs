use std::io::{Cursor, Write};

use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sqlx::FromRow;
use tauri::{AppHandle, Runtime, State};
use tauri_plugin_dialog::DialogExt;
use zip::{write::SimpleFileOptions, ZipWriter};

use crate::meeting_export::write_export;
use crate::state::AppState;

const MAX_EXPORT_DRAFTS: usize = 50;
const MAX_GOVERNANCE_RECORDS: usize = 200;
const DEFAULT_WORK_ITEM_TYPE: &str = "Task";

#[derive(Debug, Clone, Deserialize)]
pub struct IssueExportOptions {
    jira_work_type: String,
    azure_work_item_type: String,
    include_connected_excerpts: bool,
}

#[derive(Debug, Clone, FromRow, Serialize, Deserialize, PartialEq, Eq)]
pub struct IssueExportSettings {
    jira_work_type: String,
    azure_work_item_type: String,
}

fn normalized_work_item_type(value: &str) -> Result<String, String> {
    let cleaned = value.trim();
    if cleaned.is_empty()
        || cleaned.len() > 80
        || cleaned
            .chars()
            .any(|character| matches!(character, '\r' | '\n' | '\0'))
    {
        return Err("The work item type is invalid".to_string());
    }
    Ok(cleaned.to_string())
}

impl IssueExportOptions {
    fn normalized(self) -> Result<Self, String> {
        Ok(Self {
            jira_work_type: normalized_work_item_type(&self.jira_work_type)?,
            azure_work_item_type: normalized_work_item_type(&self.azure_work_item_type)?,
            include_connected_excerpts: self.include_connected_excerpts,
        })
    }
}

fn text_array(value: &Value, key: &str) -> Vec<String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect()
}

fn csv_cell(value: &str) -> String {
    let trimmed = value.trim_start();
    let safe = if trimmed.starts_with(['=', '+', '-', '@']) {
        format!("'{value}")
    } else {
        value.to_string()
    };
    format!("\"{}\"", safe.replace('"', "\"\""))
}

fn public_governance_record(mut record: Value) -> Result<Value, String> {
    let object = record
        .as_object_mut()
        .ok_or_else(|| "The governance record is invalid".to_string())?;
    for key in [
        "record_id",
        "workspace_id",
        "source_session_ids",
        "linked_pain_ids",
        "superseded_by",
    ] {
        object.remove(key);
    }
    if let Some(evidence) = object.get_mut("evidence").and_then(Value::as_array_mut) {
        for reference in evidence {
            if let Some(reference) = reference.as_object_mut() {
                reference.remove("session_id");
                reference.remove("evidence_event_ids");
            }
        }
    }
    Ok(record)
}

fn governance_statement(record: &Value) -> String {
    let payload = record.get("payload").unwrap_or(&Value::Null);
    for key in ["statement", "task", "change", "subject"] {
        if let Some(value) = payload.get(key).and_then(Value::as_str) {
            return value.to_string();
        }
    }
    String::new()
}

fn governance_owner(record: &Value) -> String {
    let payload = record.get("payload").unwrap_or(&Value::Null);
    for key in ["owner", "approver", "validation_owner"] {
        if let Some(value) = payload.get(key).and_then(Value::as_str) {
            return value.to_string();
        }
    }
    payload
        .get("accountable")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect::<Vec<_>>()
        .join("; ")
}

fn governance_markdown(record: &Value) -> String {
    let title = record
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or("Governance record");
    let kind = record
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or("record");
    let status = record
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("open");
    let statement = governance_statement(record);
    let owner = governance_owner(record);
    let mut lines = vec![
        format!("# {title}"),
        String::new(),
        format!("- Type: {kind}"),
        format!("- Status: {status}"),
    ];
    if !owner.is_empty() {
        lines.push(format!("- Owner/approver: {owner}"));
    }
    if !statement.is_empty() {
        lines.extend([String::new(), "## Detail".to_string(), statement]);
    }
    lines.extend([
        String::new(),
        "## Provenance".to_string(),
        "Confirmed by the user from local meeting evidence. Internal evidence identifiers are not included in this export."
            .to_string(),
    ]);
    lines.join("\n")
}

fn governance_raid_csv(records: &[Value]) -> String {
    let mut rows = vec!["Type,Title,Status,Owner,Detail".to_string()];
    for record in records {
        rows.push(format!(
            "{},{},{},{},{}",
            csv_cell(
                record
                    .get("kind")
                    .and_then(Value::as_str)
                    .unwrap_or("record")
            ),
            csv_cell(
                record
                    .get("title")
                    .and_then(Value::as_str)
                    .unwrap_or("Record")
            ),
            csv_cell(
                record
                    .get("status")
                    .and_then(Value::as_str)
                    .unwrap_or("open")
            ),
            csv_cell(&governance_owner(record)),
            csv_cell(&governance_statement(record)),
        ));
    }
    format!("\u{feff}{}\r\n", rows.join("\r\n"))
}

fn governance_dependency_csv(records: &[Value]) -> String {
    let mut rows =
        vec!["Title,Provider,Consumer,Deliverable,Needed By,Owner,Status,Fallback".to_string()];
    for record in records
        .iter()
        .filter(|record| record.get("kind").and_then(Value::as_str) == Some("dependency"))
    {
        let payload = record.get("payload").unwrap_or(&Value::Null);
        let value = |key: &str| payload.get(key).and_then(Value::as_str).unwrap_or("");
        rows.push(format!(
            "{},{},{},{},{},{},{},{}",
            csv_cell(
                record
                    .get("title")
                    .and_then(Value::as_str)
                    .unwrap_or("Dependency")
            ),
            csv_cell(value("provider")),
            csv_cell(value("consumer")),
            csv_cell(value("deliverable")),
            csv_cell(value("needed_by")),
            csv_cell(value("owner")),
            csv_cell(
                record
                    .get("status")
                    .and_then(Value::as_str)
                    .unwrap_or("open")
            ),
            csv_cell(value("fallback")),
        ));
    }
    format!("\u{feff}{}\r\n", rows.join("\r\n"))
}

fn governance_zip(records: &[Value]) -> Result<Vec<u8>, String> {
    let error = || "The governance review pack could not be created".to_string();
    let mut archive = ZipWriter::new(Cursor::new(Vec::new()));
    let options = SimpleFileOptions::default();
    archive
        .start_file("raid.csv", options)
        .map_err(|_| error())?;
    archive
        .write_all(governance_raid_csv(records).as_bytes())
        .map_err(|_| error())?;
    archive
        .start_file("dependencies.csv", options)
        .map_err(|_| error())?;
    archive
        .write_all(governance_dependency_csv(records).as_bytes())
        .map_err(|_| error())?;
    let public = records
        .iter()
        .cloned()
        .map(public_governance_record)
        .collect::<Result<Vec<_>, _>>()?;
    archive
        .start_file("manifest.json", options)
        .map_err(|_| error())?;
    archive
        .write_all(
            &serde_json::to_vec_pretty(&json!({"schema_version": 7, "records": public}))
                .map_err(|_| error())?,
        )
        .map_err(|_| error())?;
    for (index, record) in records.iter().enumerate() {
        archive
            .start_file(format!("records/record-{:03}.md", index + 1), options)
            .map_err(|_| error())?;
        archive
            .write_all(governance_markdown(record).as_bytes())
            .map_err(|_| error())?;
    }
    archive
        .finish()
        .map(|value| value.into_inner())
        .map_err(|_| error())
}

fn public_draft(mut draft: Value, include_excerpts: bool) -> Result<Value, String> {
    let object = draft
        .as_object_mut()
        .ok_or_else(|| "The issue draft is invalid".to_string())?;
    for key in ["draft_id", "session_id", "pain_id"] {
        object.remove(key);
    }
    for key in ["facts", "history_warnings"] {
        if let Some(items) = object.get_mut(key).and_then(Value::as_array_mut) {
            for item in items {
                if let Some(item) = item.as_object_mut() {
                    item.remove("evidence_event_ids");
                }
            }
        }
    }
    if let Some(citations) = object
        .get_mut("context_citations")
        .and_then(Value::as_array_mut)
    {
        for citation in citations {
            if let Some(citation) = citation.as_object_mut() {
                citation.remove("citation_id");
                citation.remove("connector_id");
                if !include_excerpts {
                    citation.remove("excerpt");
                    citation.remove("uri");
                }
            }
        }
    }
    Ok(draft)
}

fn markdown(draft: &Value, include_excerpts: bool) -> String {
    let title = draft
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or("Issue");
    let mut lines = vec![format!("# {title}"), String::new()];
    for (heading, key) in [
        ("Problem", "problem"),
        ("Impact", "impact"),
        ("Affected systems", "affected_systems"),
        ("Scope", "scope"),
        ("Workaround", "workaround"),
        ("Acceptance criteria", "acceptance_criteria"),
    ] {
        let values = text_array(draft, key);
        if !values.is_empty() {
            lines.push(format!("## {heading}"));
            lines.extend(values.into_iter().map(|value| format!("- {value}")));
            lines.push(String::new());
        }
    }
    if let Some(owner) = draft.get("owner").and_then(Value::as_str) {
        lines.extend(["## Owner".to_string(), format!("- {owner}"), String::new()]);
    }
    if let Some(fields) = draft
        .get("review_provenance")
        .and_then(|value| value.get("user_authored_fields"))
        .and_then(Value::as_array)
    {
        let labels = fields.iter().filter_map(Value::as_str).collect::<Vec<_>>();
        if !labels.is_empty() {
            lines.extend([
                "## Review provenance".to_string(),
                format!("- User-authored fields: {}", labels.join(", ")),
                String::new(),
            ]);
        }
    }
    let questions = draft
        .get("unresolved_questions")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| item.get("question").and_then(Value::as_str))
        .collect::<Vec<_>>();
    if !questions.is_empty() {
        lines.push("## Questions before filing".to_string());
        lines.extend(questions.into_iter().map(|value| format!("- {value}")));
        lines.push(String::new());
    }
    if let Some(citations) = draft.get("context_citations").and_then(Value::as_array) {
        if !citations.is_empty() {
            lines.push("## External context (not stated in the meeting)".to_string());
            for citation in citations {
                let label = citation
                    .get("source_label")
                    .and_then(Value::as_str)
                    .unwrap_or("Source");
                let reference = citation
                    .get("source_reference")
                    .and_then(Value::as_str)
                    .unwrap_or("Reference");
                let relation = citation
                    .get("relation")
                    .and_then(Value::as_str)
                    .unwrap_or("reference");
                let excerpt = if include_excerpts {
                    citation
                        .get("excerpt")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                } else {
                    ""
                };
                let suffix = if excerpt.is_empty() {
                    String::new()
                } else {
                    format!(": {excerpt}")
                };
                lines.push(format!("- {label} / {reference} ({relation}){suffix}"));
            }
        }
    }
    lines.join("\n")
}

fn jira_csv(drafts: &[Value], work_type: &str, excerpts: bool) -> String {
    let mut rows = vec!["Summary,Description,Work Type,Labels".to_string()];
    for draft in drafts {
        let title = draft
            .get("title")
            .and_then(Value::as_str)
            .unwrap_or("Issue");
        let template = draft
            .get("template_id")
            .and_then(Value::as_str)
            .unwrap_or("meeting");
        rows.push(format!(
            "{},{},{},{}",
            csv_cell(title),
            csv_cell(&markdown(draft, excerpts)),
            csv_cell(work_type),
            csv_cell(&format!("meeting-intelligence,{template}"))
        ));
    }
    format!("\u{feff}{}\r\n", rows.join("\r\n"))
}

fn azure_csv(drafts: &[Value], work_type: &str, excerpts: bool) -> String {
    let mut rows = vec!["Work Item Type,Title,Description,Tags".to_string()];
    for draft in drafts {
        let title = draft
            .get("title")
            .and_then(Value::as_str)
            .unwrap_or("Issue");
        let template = draft
            .get("template_id")
            .and_then(Value::as_str)
            .unwrap_or("meeting");
        rows.push(format!(
            "{},{},{},{}",
            csv_cell(work_type),
            csv_cell(title),
            csv_cell(&markdown(draft, excerpts)),
            csv_cell(&format!("meeting-intelligence; {template}"))
        ));
    }
    format!("\u{feff}{}\r\n", rows.join("\r\n"))
}

fn render_single(
    draft: &Value,
    format: &str,
    options: &IssueExportOptions,
) -> Result<(Vec<u8>, &'static str), String> {
    match format {
        "markdown" => Ok((
            markdown(draft, options.include_connected_excerpts).into_bytes(),
            "md",
        )),
        "json" => Ok((
            serde_json::to_vec_pretty(&public_draft(
                draft.clone(),
                options.include_connected_excerpts,
            )?)
            .map_err(|_| "The issue export could not be encoded".to_string())?,
            "json",
        )),
        "jira_csv" => Ok((
            jira_csv(
                std::slice::from_ref(draft),
                &options.jira_work_type,
                options.include_connected_excerpts,
            )
            .into_bytes(),
            "csv",
        )),
        "azure_csv" => Ok((
            azure_csv(
                std::slice::from_ref(draft),
                &options.azure_work_item_type,
                options.include_connected_excerpts,
            )
            .into_bytes(),
            "csv",
        )),
        _ => Err("The issue export format is invalid".to_string()),
    }
}

fn batch_zip(drafts: &[Value], options: &IssueExportOptions) -> Result<Vec<u8>, String> {
    let mut archive = ZipWriter::new(Cursor::new(Vec::new()));
    let zip_options = SimpleFileOptions::default();
    let error = || "The issue bundle could not be created".to_string();
    archive
        .start_file("jira.csv", zip_options)
        .map_err(|_| error())?;
    archive
        .write_all(
            jira_csv(
                drafts,
                &options.jira_work_type,
                options.include_connected_excerpts,
            )
            .as_bytes(),
        )
        .map_err(|_| error())?;
    archive
        .start_file("azure-boards.csv", zip_options)
        .map_err(|_| error())?;
    archive
        .write_all(
            azure_csv(
                drafts,
                &options.azure_work_item_type,
                options.include_connected_excerpts,
            )
            .as_bytes(),
        )
        .map_err(|_| error())?;
    let public = drafts
        .iter()
        .cloned()
        .map(|draft| public_draft(draft, options.include_connected_excerpts))
        .collect::<Result<Vec<_>, _>>()?;
    archive
        .start_file("manifest.json", zip_options)
        .map_err(|_| error())?;
    archive
        .write_all(
            &serde_json::to_vec_pretty(&json!({"schema_version": 1, "drafts": public}))
                .map_err(|_| error())?,
        )
        .map_err(|_| error())?;
    for (index, draft) in drafts.iter().enumerate() {
        archive
            .start_file(format!("issues/issue-{:02}.md", index + 1), zip_options)
            .map_err(|_| error())?;
        archive
            .write_all(markdown(draft, options.include_connected_excerpts).as_bytes())
            .map_err(|_| error())?;
    }
    archive
        .finish()
        .map(|cursor| cursor.into_inner())
        .map_err(|_| error())
}

async fn choose_and_write<R: Runtime>(
    app: AppHandle<R>,
    bytes: Vec<u8>,
    extension: &str,
    filename: &str,
) -> Result<bool, String> {
    let app_for_dialog = app.clone();
    let extension = extension.to_string();
    let filename = filename.to_string();
    let selected = tokio::task::spawn_blocking(move || {
        app_for_dialog
            .dialog()
            .file()
            .add_filter("Issue export", &[&extension])
            .set_file_name(filename)
            .blocking_save_file()
    })
    .await
    .map_err(|_| "The issue export dialog is unavailable".to_string())?;
    let Some(selected) = selected else {
        return Ok(false);
    };
    let path = selected
        .into_path()
        .map_err(|_| "The issue export location is invalid".to_string())?;
    write_export(&path, &bytes)?;
    Ok(true)
}

#[tauri::command]
pub async fn export_issue_draft<R: Runtime>(
    app: AppHandle<R>,
    draft: Value,
    format: String,
    options: IssueExportOptions,
) -> Result<bool, String> {
    let options = options.normalized()?;
    let (bytes, extension) = render_single(&draft, &format, &options)?;
    choose_and_write(app, bytes, extension, &format!("meeting-issue.{extension}")).await
}

#[tauri::command]
pub async fn export_issue_draft_batch<R: Runtime>(
    app: AppHandle<R>,
    drafts: Vec<Value>,
    options: IssueExportOptions,
) -> Result<bool, String> {
    if drafts.is_empty() || drafts.len() > MAX_EXPORT_DRAFTS {
        return Err("The issue batch export request is invalid".to_string());
    }
    let options = options.normalized()?;
    choose_and_write(
        app,
        batch_zip(&drafts, &options)?,
        "zip",
        "meeting-issues.zip",
    )
    .await
}

#[tauri::command]
pub async fn export_governance_review_pack<R: Runtime>(
    app: AppHandle<R>,
    records: Vec<Value>,
    format: String,
) -> Result<bool, String> {
    if records.is_empty() || records.len() > MAX_GOVERNANCE_RECORDS {
        return Err("The governance export request is invalid".to_string());
    }
    let (bytes, extension, filename) = match format.as_str() {
        "json" => {
            let public = records
                .into_iter()
                .map(public_governance_record)
                .collect::<Result<Vec<_>, _>>()?;
            (
                serde_json::to_vec_pretty(&json!({"schema_version": 7, "records": public}))
                    .map_err(|_| "The governance export could not be encoded".to_string())?,
                "json",
                "solution-governance.json",
            )
        }
        "raid_csv" => (
            governance_raid_csv(&records).into_bytes(),
            "csv",
            "solution-raid.csv",
        ),
        "dependency_csv" => (
            governance_dependency_csv(&records).into_bytes(),
            "csv",
            "solution-dependencies.csv",
        ),
        "zip" => (governance_zip(&records)?, "zip", "solution-review-pack.zip"),
        _ => return Err("The governance export format is invalid".to_string()),
    };
    choose_and_write(app, bytes, extension, filename).await
}

#[tauri::command]
pub async fn get_issue_export_settings(
    state: State<'_, AppState>,
) -> Result<IssueExportSettings, String> {
    sqlx::query_as::<_, IssueExportSettings>(
        "SELECT jira_work_type, azure_work_item_type
         FROM meeting_issue_export_settings WHERE singleton = 1",
    )
    .fetch_optional(state.db_manager.pool())
    .await
    .map_err(|_| "The issue export settings could not be loaded".to_string())
    .map(|settings| {
        settings.unwrap_or_else(|| IssueExportSettings {
            jira_work_type: DEFAULT_WORK_ITEM_TYPE.to_string(),
            azure_work_item_type: DEFAULT_WORK_ITEM_TYPE.to_string(),
        })
    })
}

#[tauri::command]
pub async fn set_issue_export_settings(
    settings: IssueExportSettings,
    state: State<'_, AppState>,
) -> Result<IssueExportSettings, String> {
    let normalized = IssueExportSettings {
        jira_work_type: normalized_work_item_type(&settings.jira_work_type)?,
        azure_work_item_type: normalized_work_item_type(&settings.azure_work_item_type)?,
    };
    sqlx::query(
        "INSERT INTO meeting_issue_export_settings
         (singleton, jira_work_type, azure_work_item_type, updated_at)
         VALUES (1, ?, ?, ?)
         ON CONFLICT(singleton) DO UPDATE SET
           jira_work_type = excluded.jira_work_type,
           azure_work_item_type = excluded.azure_work_item_type,
           updated_at = excluded.updated_at",
    )
    .bind(&normalized.jira_work_type)
    .bind(&normalized.azure_work_item_type)
    .bind(Utc::now().to_rfc3339())
    .execute(state.db_manager.pool())
    .await
    .map_err(|_| "The issue export settings could not be saved".to_string())?;
    Ok(normalized)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn draft() -> Value {
        json!({
            "draft_id":"internal","session_id":"session","pain_id":"pain","state_version":2,"template_id":"data_mismatch","language":"ja","title":"=SAP,在庫差異",
            "problem":["在庫が一致しない"],"impact":[],"affected_systems":["SAP","EC"],"scope":[],"owner":null,"workaround":[],"acceptance_criteria":[],
            "facts":[{"slot":"owner","value":"山田","kind":"evidence","evidence_event_ids":["event"]}],"history_warnings":[],"unresolved_questions":[],
            "context_citations":[{"citation_id":"cid","connector_id":"erp","source_label":"SAP","source_reference":"ITEM-1","relation":"reference","excerpt":"secret","uri":"https://erp.example/item"}],
            "context_status":"current","readiness":"needs_clarification","missing_required_fields":["owner"]
        })
    }

    #[test]
    fn public_export_strips_internal_data_and_unapproved_excerpts() {
        let encoded = serde_json::to_string(&public_draft(draft(), false).unwrap()).unwrap();
        for forbidden in [
            "internal",
            "session_id",
            "pain_id",
            "evidence_event_ids",
            "connector_id",
            "secret",
            "erp.example",
        ] {
            assert!(!encoded.contains(forbidden));
        }
        assert!(encoded.contains("ITEM-1"));
    }

    #[test]
    fn csv_is_bom_quoted_and_formula_safe() {
        let csv = jira_csv(&[draft()], "Task", false);
        assert!(csv.starts_with('\u{feff}'));
        assert!(csv.contains("\"'=SAP,在庫差異\""));
    }

    #[test]
    fn batch_contains_all_manual_import_artifacts() {
        use std::io::Read;

        let bytes = batch_zip(
            &[draft()],
            &IssueExportOptions {
                jira_work_type: "Task".to_string(),
                azure_work_item_type: "Issue".to_string(),
                include_connected_excerpts: false,
            },
        )
        .unwrap();
        let mut archive = zip::ZipArchive::new(Cursor::new(bytes)).unwrap();
        for name in [
            "jira.csv",
            "azure-boards.csv",
            "manifest.json",
            "issues/issue-01.md",
        ] {
            assert!(archive.by_name(name).is_ok());
        }
        let mut jira = String::new();
        archive
            .by_name("jira.csv")
            .unwrap()
            .read_to_string(&mut jira)
            .unwrap();
        let mut azure = String::new();
        archive
            .by_name("azure-boards.csv")
            .unwrap()
            .read_to_string(&mut azure)
            .unwrap();
        assert!(jira.contains("\"Task\""));
        assert!(azure.contains("\"Issue\""));
    }

    #[test]
    fn governance_export_strips_internal_identity_and_quotes_csv() {
        let record = json!({
            "record_id":"gr-secret","workspace_id":"local-personal","kind":"risk",
            "title":"=Critical, risk","payload":{"payload_type":"risk","statement":"Customer data risk","owner":"Team A"},
            "status":"open","revision":1,"field_provenance":{"statement":"transcript"},
            "evidence":[{"session_id":"session-secret","evidence_event_ids":["event-secret"]}],
            "source_session_ids":["session-secret"],"linked_pain_ids":["pain-secret"],
            "linked_entity_ids":[],"superseded_by":null,"needs_provenance_review":false,
            "created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z"
        });
        let public =
            serde_json::to_string(&public_governance_record(record.clone()).unwrap()).unwrap();
        for forbidden in ["gr-secret", "session-secret", "event-secret", "pain-secret"] {
            assert!(!public.contains(forbidden));
        }
        let csv = governance_raid_csv(&[record]);
        assert!(csv.starts_with('\u{feff}'));
        assert!(csv.contains("\"'=Critical, risk\""));
    }

    #[test]
    fn work_item_types_are_trimmed_and_single_line() {
        assert_eq!(normalized_work_item_type("  Task  ").unwrap(), "Task");
        assert!(normalized_work_item_type("Task\nInjected").is_err());
        assert!(normalized_work_item_type(" ").is_err());
    }
}
