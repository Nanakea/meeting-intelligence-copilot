use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::{FromRow, SqlitePool};

use crate::state::AppState;

const MAX_DRAFT_BYTES: usize = 64 * 1024;
const MAX_UNRESOLVED_COUNT: i64 = 64;
const REVIEW_OVERLAY_ENCRYPTION_PREFIX: &str = "dpapi-v1:";

#[derive(Debug, Clone, FromRow, Serialize, Deserialize, PartialEq, Eq)]
pub struct MeetingIssueDraft {
    pub meeting_id: String,
    pub draft_markdown: String,
    pub unresolved_count: i64,
    pub language: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, FromRow, Serialize, Deserialize, PartialEq, Eq)]
pub struct StoredStructuredIssueDraft {
    pub meeting_id: String,
    pub pain_id: String,
    pub draft_json: String,
    pub readiness: String,
    pub language: String,
    pub state_version: i64,
    pub updated_at: String,
}

#[derive(Debug, Clone, FromRow, Serialize, Deserialize, PartialEq, Eq)]
pub struct StoredIssueDraftReviewOverlay {
    pub meeting_id: String,
    pub pain_id: String,
    pub base_state_version: i64,
    pub status: String,
    pub overlay_json: String,
    pub updated_at: String,
}

fn sanitize_review_overlay(
    meeting_id: &str,
    pain_id: &str,
    overlay: Value,
) -> Result<(i64, String, String), String> {
    if meeting_id.trim().is_empty() || meeting_id.len() > 128 {
        return Err("meeting id is invalid".to_string());
    }
    if pain_id.trim().is_empty() || pain_id.len() > 512 {
        return Err("problem id is invalid".to_string());
    }
    let object = overlay
        .as_object()
        .ok_or_else(|| "review overlay must be an object".to_string())?;
    let allowed_keys = [
        "meeting_id",
        "pain_id",
        "base_state_version",
        "status",
        "field_edits",
        "accepted_semantic_suggestions",
        "base_field_hashes",
        "rebase_conflicts",
        "created_at",
        "updated_at",
    ];
    if object
        .keys()
        .any(|key| !allowed_keys.contains(&key.as_str()))
    {
        return Err("review overlay contains unsupported fields".to_string());
    }
    if object.get("meeting_id").and_then(Value::as_str) != Some(meeting_id)
        || object.get("pain_id").and_then(Value::as_str) != Some(pain_id)
    {
        return Err("review overlay identity does not match".to_string());
    }
    let base_state_version = object
        .get("base_state_version")
        .and_then(Value::as_i64)
        .filter(|version| *version >= 0)
        .ok_or_else(|| "review overlay base version is invalid".to_string())?;
    let status = object
        .get("status")
        .and_then(Value::as_str)
        .filter(|status| {
            matches!(
                *status,
                "unreviewed" | "in_review" | "reviewed" | "needs_rebase" | "exported"
            )
        })
        .ok_or_else(|| "review overlay status is invalid".to_string())?
        .to_string();
    let reviewable_fields = [
        "title",
        "problem",
        "impact",
        "affected_systems",
        "scope",
        "owner",
        "workaround",
        "acceptance_criteria",
    ];
    let edits = object
        .get("field_edits")
        .and_then(Value::as_object)
        .ok_or_else(|| "review overlay edits are invalid".to_string())?;
    if edits
        .iter()
        .any(|(field, value)| !reviewable_fields.contains(&field.as_str()) || !review_value(value))
    {
        return Err("review overlay edit is invalid".to_string());
    }
    let hashes = object
        .get("base_field_hashes")
        .and_then(Value::as_object)
        .ok_or_else(|| "review overlay hashes are invalid".to_string())?;
    if hashes.iter().any(|(field, value)| {
        !reviewable_fields.contains(&field.as_str())
            || !value
                .as_str()
                .is_some_and(|hash| hash.starts_with("fnv1a-") && hash.len() == 14)
    }) {
        return Err("review overlay field hash is invalid".to_string());
    }
    let conflicts = object
        .get("rebase_conflicts")
        .and_then(Value::as_object)
        .ok_or_else(|| "review overlay conflicts are invalid".to_string())?;
    if conflicts
        .keys()
        .any(|field| !reviewable_fields.contains(&field.as_str()))
    {
        return Err("review overlay conflict is invalid".to_string());
    }
    let suggestions = object
        .get("accepted_semantic_suggestions")
        .and_then(Value::as_array)
        .filter(|values| values.len() <= 32)
        .ok_or_else(|| "review overlay suggestions are invalid".to_string())?;
    if suggestions.iter().any(|value| {
        !value
            .as_str()
            .is_some_and(|text| !text.contains('\0') && text.len() <= 2_000)
    }) {
        return Err("review overlay suggestion is invalid".to_string());
    }
    let encoded = serde_json::to_string(&overlay)
        .map_err(|_| "review overlay could not be encoded".to_string())?;
    if encoded.len() > MAX_DRAFT_BYTES {
        return Err("review overlay exceeds the local size limit".to_string());
    }
    Ok((base_state_version, status, encoded))
}

fn review_value(value: &Value) -> bool {
    if value.is_null() {
        return true;
    }
    if let Some(text) = value.as_str() {
        return !text.contains('\0') && text.len() <= 8_000;
    }
    value.as_array().is_some_and(|items| {
        items.len() <= 64
            && items.iter().all(|item| {
                item.as_str()
                    .is_some_and(|text| !text.contains('\0') && text.len() <= 2_000)
            })
    })
}

#[cfg(target_os = "windows")]
fn protect_review_overlay(value: &str) -> Result<String, String> {
    use std::ptr;

    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptProtectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    let bytes = value.as_bytes();
    let input = CRYPT_INTEGER_BLOB {
        cbData: u32::try_from(bytes.len())
            .map_err(|_| "review overlay encryption failed".to_string())?,
        pbData: bytes.as_ptr() as *mut u8,
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: ptr::null_mut(),
    };
    let protected = unsafe {
        CryptProtectData(
            &input,
            ptr::null(),
            ptr::null(),
            ptr::null(),
            ptr::null(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
    };
    if protected == 0 || output.pbData.is_null() {
        return Err("review overlay encryption failed".to_string());
    }
    let encrypted =
        unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec() };
    unsafe {
        LocalFree(output.pbData.cast());
    }
    Ok(format!(
        "{REVIEW_OVERLAY_ENCRYPTION_PREFIX}{}",
        encode_hex(&encrypted)
    ))
}

#[cfg(not(target_os = "windows"))]
fn protect_review_overlay(_value: &str) -> Result<String, String> {
    Err("review overlay encryption is unavailable".to_string())
}

#[cfg(target_os = "windows")]
fn unprotect_review_overlay(value: &str) -> Result<String, String> {
    use std::ptr;

    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    let encoded = value
        .strip_prefix(REVIEW_OVERLAY_ENCRYPTION_PREFIX)
        .ok_or_else(|| "review overlay encryption is invalid".to_string())?;
    let encrypted = decode_hex(encoded)?;
    let input = CRYPT_INTEGER_BLOB {
        cbData: u32::try_from(encrypted.len())
            .map_err(|_| "review overlay decryption failed".to_string())?,
        pbData: encrypted.as_ptr() as *mut u8,
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: ptr::null_mut(),
    };
    let unprotected = unsafe {
        CryptUnprotectData(
            &input,
            ptr::null_mut(),
            ptr::null(),
            ptr::null(),
            ptr::null(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
    };
    if unprotected == 0 || output.pbData.is_null() {
        return Err("review overlay decryption failed".to_string());
    }
    let decrypted =
        unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec() };
    unsafe {
        LocalFree(output.pbData.cast());
    }
    if decrypted.len() > MAX_DRAFT_BYTES {
        return Err("review overlay exceeds the local size limit".to_string());
    }
    String::from_utf8(decrypted).map_err(|_| "review overlay decryption failed".to_string())
}

#[cfg(not(target_os = "windows"))]
fn unprotect_review_overlay(_value: &str) -> Result<String, String> {
    Err("review overlay encryption is unavailable".to_string())
}

fn encode_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        encoded.push(HEX[(byte >> 4) as usize] as char);
        encoded.push(HEX[(byte & 0x0f) as usize] as char);
    }
    encoded
}

fn decode_hex(value: &str) -> Result<Vec<u8>, String> {
    if value.len() % 2 != 0 || value.len() > (MAX_DRAFT_BYTES + 1024) * 2 {
        return Err("review overlay encryption is invalid".to_string());
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let high = hex_digit(pair[0])?;
            let low = hex_digit(pair[1])?;
            Ok((high << 4) | low)
        })
        .collect()
}

fn hex_digit(value: u8) -> Result<u8, String> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err("review overlay encryption is invalid".to_string()),
    }
}

fn validate_issue_draft(
    meeting_id: &str,
    draft_markdown: &str,
    unresolved_count: i64,
    language: &str,
) -> Result<(), String> {
    if meeting_id.trim().is_empty() {
        return Err("meeting_id is required".to_string());
    }
    if draft_markdown.trim().is_empty() {
        return Err("issue draft is empty".to_string());
    }
    if draft_markdown.len() > MAX_DRAFT_BYTES {
        return Err("issue draft exceeds the local size limit".to_string());
    }
    if !(0..=MAX_UNRESOLVED_COUNT).contains(&unresolved_count) {
        return Err("unresolved count is outside the supported range".to_string());
    }
    if !matches!(language, "ja" | "en" | "ko") {
        return Err("issue draft language must be ja, en, or ko".to_string());
    }
    Ok(())
}

pub async fn save_issue_draft(
    pool: &SqlitePool,
    meeting_id: &str,
    draft_markdown: &str,
    unresolved_count: i64,
    language: &str,
) -> Result<bool, sqlx::Error> {
    let result = sqlx::query(
        "INSERT INTO meeting_issue_drafts
         (meeting_id, pain_id, schema_version, legacy_markdown, unresolved_count,
          language, readiness, state_version, updated_at)
         SELECT ?, 'legacy', 4, ?, ?, ?, 'legacy', 0, ?
         FROM meetings
         WHERE id = ?
         ON CONFLICT(meeting_id, pain_id) DO UPDATE SET
           legacy_markdown = excluded.legacy_markdown,
           unresolved_count = excluded.unresolved_count,
           language = excluded.language,
           updated_at = excluded.updated_at",
    )
    .bind(meeting_id)
    .bind(draft_markdown)
    .bind(unresolved_count)
    .bind(language)
    .bind(Utc::now().to_rfc3339())
    .bind(meeting_id)
    .execute(pool)
    .await?;
    Ok(result.rows_affected() > 0)
}

pub async fn get_issue_draft(
    pool: &SqlitePool,
    meeting_id: &str,
) -> Result<Option<MeetingIssueDraft>, sqlx::Error> {
    sqlx::query_as::<_, MeetingIssueDraft>(
        "SELECT meeting_id, legacy_markdown AS draft_markdown,
                unresolved_count, language, updated_at
         FROM meeting_issue_drafts
         WHERE meeting_id = ? AND legacy_markdown IS NOT NULL
         ORDER BY updated_at DESC LIMIT 1",
    )
    .bind(meeting_id)
    .fetch_optional(pool)
    .await
}

fn sanitize_structured_draft(
    mut draft: Value,
) -> Result<(String, String, String, i64, String), String> {
    let object = draft
        .as_object_mut()
        .ok_or_else(|| "structured issue draft must be an object".to_string())?;
    let pain_id = object
        .get("pain_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= 512)
        .ok_or_else(|| "structured issue draft pain id is invalid".to_string())?
        .to_string();
    let language = object
        .get("language")
        .and_then(Value::as_str)
        .filter(|value| matches!(*value, "ja" | "en" | "ko"))
        .ok_or_else(|| "structured issue draft language is invalid".to_string())?
        .to_string();
    let readiness = object
        .get("readiness")
        .and_then(Value::as_str)
        .filter(|value| matches!(*value, "needs_clarification" | "ready_for_review"))
        .ok_or_else(|| "structured issue draft readiness is invalid".to_string())?
        .to_string();
    let state_version = object
        .get("state_version")
        .and_then(Value::as_i64)
        .filter(|value| *value >= 0)
        .ok_or_else(|| "structured issue draft state version is invalid".to_string())?;
    if let Some(citations) = object
        .get_mut("context_citations")
        .and_then(Value::as_array_mut)
    {
        for citation in citations {
            if let Some(citation) = citation.as_object_mut() {
                citation.retain(|key, _| {
                    matches!(
                        key.as_str(),
                        "source_label"
                            | "source_reference"
                            | "relation"
                            | "retrieved_at"
                            | "updated_at"
                            | "stale"
                    )
                });
            }
        }
    }
    let encoded = serde_json::to_string(&draft)
        .map_err(|_| "structured issue draft could not be encoded".to_string())?;
    if encoded.len() > MAX_DRAFT_BYTES {
        return Err("structured issue draft exceeds the local size limit".to_string());
    }
    Ok((pain_id, language, readiness, state_version, encoded))
}

#[tauri::command]
pub async fn api_save_meeting_issue_drafts(
    meeting_id: String,
    drafts: Vec<Value>,
    state: tauri::State<'_, AppState>,
) -> Result<usize, String> {
    if meeting_id.trim().is_empty() || meeting_id.len() > 128 || drafts.len() > 50 {
        return Err("structured issue draft request is invalid".to_string());
    }
    let mut sanitized = Vec::with_capacity(drafts.len());
    for draft in drafts {
        let (pain_id, language, readiness, state_version, encoded) =
            sanitize_structured_draft(draft)?;
        sanitized.push((pain_id, encoded, language, readiness, state_version));
    }
    let pool = state.db_manager.pool();
    let mut transaction = pool
        .begin()
        .await
        .map_err(|_| "failed to save structured issue drafts".to_string())?;
    let existing_pain_ids = sqlx::query_scalar::<_, String>(
        "SELECT pain_id FROM meeting_issue_drafts
         WHERE meeting_id = ? AND pain_id <> 'legacy'",
    )
    .bind(&meeting_id)
    .fetch_all(&mut *transaction)
    .await
    .map_err(|_| "failed to save structured issue drafts".to_string())?;
    let now = Utc::now().to_rfc3339();
    for (pain_id, encoded, language, readiness, state_version) in &sanitized {
        sqlx::query(
            "INSERT INTO meeting_issue_drafts
             (meeting_id, pain_id, schema_version, draft_json, unresolved_count,
              language, readiness, state_version, updated_at)
             SELECT ?, ?, 5, ?, 0, ?, ?, ?, ? FROM meetings WHERE id = ?
             ON CONFLICT(meeting_id, pain_id) DO UPDATE SET
               schema_version = excluded.schema_version,
               draft_json = excluded.draft_json,
               language = excluded.language,
               readiness = excluded.readiness,
               state_version = excluded.state_version,
               updated_at = excluded.updated_at",
        )
        .bind(&meeting_id)
        .bind(pain_id)
        .bind(encoded)
        .bind(language)
        .bind(readiness)
        .bind(state_version)
        .bind(&now)
        .bind(&meeting_id)
        .execute(&mut *transaction)
        .await
        .map_err(|_| "failed to save structured issue drafts".to_string())?;
    }
    for stale_pain_id in existing_pain_ids
        .iter()
        .filter(|pain_id| !sanitized.iter().any(|draft| &draft.0 == *pain_id))
    {
        sqlx::query(
            "DELETE FROM meeting_issue_draft_review_overlays
             WHERE meeting_id = ? AND pain_id = ?",
        )
        .bind(&meeting_id)
        .bind(stale_pain_id)
        .execute(&mut *transaction)
        .await
        .map_err(|_| "failed to save structured issue drafts".to_string())?;
        sqlx::query("DELETE FROM meeting_issue_drafts WHERE meeting_id = ? AND pain_id = ?")
            .bind(&meeting_id)
            .bind(stale_pain_id)
            .execute(&mut *transaction)
            .await
            .map_err(|_| "failed to save structured issue drafts".to_string())?;
    }
    transaction
        .commit()
        .await
        .map_err(|_| "failed to save structured issue drafts".to_string())?;
    Ok(sanitized.len())
}

pub async fn get_structured_issue_drafts(
    pool: &SqlitePool,
    meeting_id: &str,
) -> Result<Vec<StoredStructuredIssueDraft>, sqlx::Error> {
    sqlx::query_as::<_, StoredStructuredIssueDraft>(
        "SELECT meeting_id, pain_id, draft_json, readiness, language,
                state_version, updated_at
         FROM meeting_issue_drafts
         WHERE meeting_id = ? AND draft_json IS NOT NULL
         ORDER BY updated_at, pain_id",
    )
    .bind(meeting_id)
    .fetch_all(pool)
    .await
}

#[tauri::command]
pub async fn api_get_meeting_issue_drafts(
    meeting_id: String,
    state: tauri::State<'_, AppState>,
) -> Result<Vec<StoredStructuredIssueDraft>, String> {
    if meeting_id.trim().is_empty() {
        return Err("meeting_id is required".to_string());
    }
    get_structured_issue_drafts(state.db_manager.pool(), &meeting_id)
        .await
        .map_err(|_| "failed to load structured issue drafts".to_string())
}

#[tauri::command]
pub async fn api_get_issue_draft_review_overlays(
    meeting_id: String,
    state: tauri::State<'_, AppState>,
) -> Result<Vec<StoredIssueDraftReviewOverlay>, String> {
    if meeting_id.trim().is_empty() {
        return Err("meeting id is required".to_string());
    }
    let mut overlays = sqlx::query_as::<_, StoredIssueDraftReviewOverlay>(
        "SELECT meeting_id, pain_id, base_state_version, status, overlay_json, updated_at
         FROM meeting_issue_draft_review_overlays WHERE meeting_id = ? ORDER BY pain_id",
    )
    .bind(meeting_id)
    .fetch_all(state.db_manager.pool())
    .await
    .map_err(|_| "failed to load issue draft reviews".to_string())?;
    for overlay in &mut overlays {
        overlay.overlay_json = unprotect_review_overlay(&overlay.overlay_json)?;
    }
    Ok(overlays)
}

#[tauri::command]
pub async fn api_save_issue_draft_review_overlay(
    meeting_id: String,
    pain_id: String,
    overlay: Value,
    state: tauri::State<'_, AppState>,
) -> Result<bool, String> {
    let (base_state_version, status, encoded) =
        sanitize_review_overlay(&meeting_id, &pain_id, overlay)?;
    let protected = protect_review_overlay(&encoded)?;
    let result = sqlx::query(
        "INSERT INTO meeting_issue_draft_review_overlays
         (meeting_id, pain_id, base_state_version, status, overlay_json, updated_at)
         SELECT ?, ?, ?, ?, ?, ? FROM meeting_issue_drafts
         WHERE meeting_id = ? AND pain_id = ? AND draft_json IS NOT NULL
         ON CONFLICT(meeting_id, pain_id) DO UPDATE SET
           base_state_version = excluded.base_state_version,
           status = excluded.status,
           overlay_json = excluded.overlay_json,
           updated_at = excluded.updated_at",
    )
    .bind(&meeting_id)
    .bind(&pain_id)
    .bind(base_state_version)
    .bind(status)
    .bind(protected)
    .bind(Utc::now().to_rfc3339())
    .bind(&meeting_id)
    .bind(&pain_id)
    .execute(state.db_manager.pool())
    .await
    .map_err(|_| "failed to save issue draft review".to_string())?;
    Ok(result.rows_affected() > 0)
}

#[tauri::command]
pub async fn api_save_meeting_issue_draft(
    meeting_id: String,
    draft_markdown: String,
    unresolved_count: i64,
    language: String,
    state: tauri::State<'_, AppState>,
) -> Result<bool, String> {
    validate_issue_draft(&meeting_id, &draft_markdown, unresolved_count, &language)?;
    let saved = save_issue_draft(
        state.db_manager.pool(),
        &meeting_id,
        &draft_markdown,
        unresolved_count,
        &language,
    )
    .await
    .map_err(|_| "failed to save the local issue draft".to_string())?;
    if saved {
        log::info!(
            "Saved local issue draft for meeting {} (language={}, unresolved={})",
            meeting_id,
            language,
            unresolved_count
        );
    }
    Ok(saved)
}

#[tauri::command]
pub async fn api_get_meeting_issue_draft(
    meeting_id: String,
    state: tauri::State<'_, AppState>,
) -> Result<Option<MeetingIssueDraft>, String> {
    if meeting_id.trim().is_empty() {
        return Err("meeting_id is required".to_string());
    }
    get_issue_draft(state.db_manager.pool(), &meeting_id)
        .await
        .map_err(|_| "failed to load the local issue draft".to_string())
}

#[cfg(test)]
mod tests {
    use serde_json::json;
    use sqlx::{sqlite::SqlitePoolOptions, SqlitePool};

    use super::{
        get_issue_draft, sanitize_review_overlay, sanitize_structured_draft, save_issue_draft,
        validate_issue_draft, MAX_DRAFT_BYTES,
    };

    async fn pool() -> SqlitePool {
        let pool = SqlitePoolOptions::new()
            .max_connections(1)
            .connect("sqlite::memory:")
            .await
            .unwrap();
        sqlx::query("PRAGMA foreign_keys = ON")
            .execute(&pool)
            .await
            .unwrap();
        sqlx::query("CREATE TABLE meetings (id TEXT PRIMARY KEY NOT NULL)")
            .execute(&pool)
            .await
            .unwrap();
        sqlx::query(
            "CREATE TABLE meeting_issue_drafts (
              meeting_id TEXT NOT NULL,
              pain_id TEXT NOT NULL,
              schema_version INTEGER NOT NULL,
              draft_json TEXT,
              legacy_markdown TEXT,
              unresolved_count INTEGER NOT NULL,
              language TEXT NOT NULL,
              readiness TEXT NOT NULL,
              state_version INTEGER NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (meeting_id, pain_id),
              FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
            )",
        )
        .execute(&pool)
        .await
        .unwrap();
        pool
    }

    #[test]
    fn validation_bounds_content_without_inspecting_it() {
        assert!(validate_issue_draft("meeting", "# Issue", 2, "en").is_ok());
        assert!(validate_issue_draft("", "# Issue", 2, "en").is_err());
        assert!(validate_issue_draft("meeting", " ", 2, "en").is_err());
        assert!(validate_issue_draft("meeting", "# Issue", -1, "en").is_err());
        assert!(validate_issue_draft("meeting", "# Issue", 65, "en").is_err());
        assert!(validate_issue_draft("meeting", "# Issue", 2, "auto").is_err());
        assert!(
            validate_issue_draft("meeting", &"x".repeat(MAX_DRAFT_BYTES + 1), 2, "ja").is_err()
        );
    }

    #[test]
    fn review_overlay_is_identity_bound_and_status_bounded() {
        let overlay = json!({
            "meeting_id": "meeting-1",
            "pain_id": "pain-1",
            "base_state_version": 4,
            "status": "in_review",
            "field_edits": {"impact": ["Reviewed impact"]},
            "accepted_semantic_suggestions": [],
            "base_field_hashes": {"impact": "fnv1a-00000000"},
            "rebase_conflicts": {},
            "created_at": "2026-08-28T00:00:00Z",
            "updated_at": "2026-08-28T00:00:00Z"
        });
        assert!(sanitize_review_overlay("meeting-1", "pain-1", overlay.clone()).is_ok());
        assert!(sanitize_review_overlay("meeting-2", "pain-1", overlay.clone()).is_err());
        let mut invalid_status = overlay;
        invalid_status["status"] = json!("approved_without_review");
        assert!(sanitize_review_overlay("meeting-1", "pain-1", invalid_status).is_err());
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn review_overlay_uses_current_user_dpapi_encryption() {
        let plaintext = r#"{"impact":"private reviewer note"}"#;

        let protected = super::protect_review_overlay(plaintext).unwrap();

        assert!(protected.starts_with(super::REVIEW_OVERLAY_ENCRYPTION_PREFIX));
        assert!(!protected.contains("private reviewer note"));
        assert_eq!(
            super::unprotect_review_overlay(&protected).unwrap(),
            plaintext
        );
        assert!(super::unprotect_review_overlay(plaintext).is_err());
    }

    #[test]
    fn structured_auto_save_keeps_only_safe_citation_metadata() {
        let (_, _, _, _, encoded) = sanitize_structured_draft(json!({
            "pain_id": "pain",
            "language": "en",
            "readiness": "needs_clarification",
            "state_version": 1,
            "context_citations": [{
                "citation_id": "private-citation",
                "connector_id": "private-connector",
                "source_kind": "sap",
                "source_label": "SAP",
                "source_reference": "ORDER-1",
                "entity_type": "order",
                "excerpt": "private excerpt",
                "uri": "https://erp.example/order/1",
                "retrieved_at": "2026-08-27T00:00:00Z",
                "updated_at": null,
                "stale": false,
                "relation": "reference",
                "rank": 1
            }]
        }))
        .unwrap();
        for forbidden in [
            "private-citation",
            "private-connector",
            "private excerpt",
            "erp.example",
            "source_kind",
            "entity_type",
            "rank",
        ] {
            assert!(!encoded.contains(forbidden));
        }
        assert!(encoded.contains("ORDER-1"));
        assert!(encoded.contains("retrieved_at"));
    }

    #[tokio::test]
    async fn save_upserts_and_meeting_delete_cascades() {
        let pool = pool().await;
        sqlx::query("INSERT INTO meetings (id) VALUES ('meeting-1')")
            .execute(&pool)
            .await
            .unwrap();

        assert!(save_issue_draft(&pool, "meeting-1", "# First", 2, "en")
            .await
            .unwrap());
        assert!(save_issue_draft(&pool, "meeting-1", "# Updated", 0, "ja")
            .await
            .unwrap());
        let stored = get_issue_draft(&pool, "meeting-1").await.unwrap().unwrap();
        assert_eq!(stored.draft_markdown, "# Updated");
        assert_eq!(stored.unresolved_count, 0);
        assert_eq!(stored.language, "ja");

        sqlx::query("DELETE FROM meetings WHERE id = 'meeting-1'")
            .execute(&pool)
            .await
            .unwrap();
        assert!(get_issue_draft(&pool, "meeting-1").await.unwrap().is_none());
    }

    #[tokio::test]
    async fn save_rejects_unknown_meeting_without_orphaning() {
        let pool = pool().await;
        assert!(!save_issue_draft(&pool, "missing", "# Draft", 1, "en")
            .await
            .unwrap());
        assert!(get_issue_draft(&pool, "missing").await.unwrap().is_none());
    }
}
