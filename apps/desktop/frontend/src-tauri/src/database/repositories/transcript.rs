use crate::api::{TranscriptSearchResult, TranscriptSegment};
use chrono::Utc;
use sqlx::{Connection, Error as SqlxError, SqlitePool};
use tracing::{error, info};
use uuid::Uuid;

pub struct TranscriptsRepository;

impl TranscriptsRepository {
    /// Saves a new meeting and its associated transcript segments.
    /// This function uses a transaction to ensure that either both the meeting
    /// and all its transcripts are saved, or none of them are.
    pub async fn save_transcript(
        pool: &SqlitePool,
        meeting_title: &str,
        transcripts: &[TranscriptSegment],
        folder_path: Option<String>,
        recording_session_id: Option<String>,
    ) -> Result<String, SqlxError> {
        let meeting_id =
            recording_session_id.unwrap_or_else(|| format!("meeting-{}", Uuid::new_v4()));

        let mut conn = pool.acquire().await?;
        let mut transaction = conn.begin().await?;

        let now = Utc::now();

        // 1. Create the new meeting
        let result = sqlx::query(
            "INSERT INTO meetings (id, title, created_at, updated_at, folder_path) VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
        )
        .bind(&meeting_id)
        .bind(meeting_title)
        .bind(now)
        .bind(now)
        .bind(&folder_path)
        .execute(&mut *transaction)
        .await;

        match result {
            Ok(result) if result.rows_affected() == 0 => {
                transaction.commit().await?;
                return Ok(meeting_id);
            }
            Ok(_) => {}
            Err(error) => {
                error!("Failed to create saved local meeting");
                transaction.rollback().await?;
                return Err(error);
            }
        }

        info!("Successfully created meeting with id: {}", meeting_id);

        // 2. Save each transcript segment with audio timing fields
        for segment in transcripts {
            let transcript_id = format!("transcript-{}", Uuid::new_v4());
            let result = sqlx::query(
                "INSERT INTO transcripts (id, meeting_id, transcript, timestamp, audio_start_time, audio_end_time, duration)
                 VALUES (?, ?, ?, ?, ?, ?, ?)"
            )
            .bind(&transcript_id)
            .bind(&meeting_id)
            .bind(&segment.text)
            .bind(&segment.timestamp)
            .bind(segment.audio_start_time)
            .bind(segment.audio_end_time)
            .bind(segment.duration)
            .execute(&mut *transaction)
            .await;

            if let Err(e) = result {
                error!(
                    "Failed to save transcript segment for meeting {}",
                    meeting_id
                );
                transaction.rollback().await?;
                return Err(e);
            }
        }

        info!(
            "Successfully saved {} transcript segments for meeting {}",
            transcripts.len(),
            meeting_id
        );

        // Commit the transaction
        transaction.commit().await?;

        Ok(meeting_id)
    }

    /// Searches for a query string within the transcripts.
    /// It returns a list of matching transcripts with context.
    pub async fn search_transcripts(
        pool: &SqlitePool,
        query: &str,
    ) -> Result<Vec<TranscriptSearchResult>, SqlxError> {
        if query.trim().is_empty() {
            return Ok(Vec::new());
        }

        let search_query = format!("%{}%", query.to_lowercase());

        let rows = sqlx::query_as::<_, (String, String, String, String)>(
            "SELECT m.id, m.title, t.transcript, t.timestamp
             FROM meetings m
             JOIN transcripts t ON m.id = t.meeting_id
             WHERE LOWER(t.transcript) LIKE ?",
        )
        .bind(&search_query)
        .fetch_all(pool)
        .await?;

        let results = rows
            .into_iter()
            .map(|(id, title, transcript, timestamp)| {
                let match_context = Self::get_match_context(&transcript, query);
                TranscriptSearchResult {
                    id,
                    title,
                    match_context,
                    timestamp,
                }
            })
            .collect();

        Ok(results)
    }

    /// Helper function to extract a snippet of text around the first match of a query.
    fn get_match_context(transcript: &str, query: &str) -> String {
        let transcript_lower = transcript.to_lowercase();
        let query_lower = query.to_lowercase();

        match transcript_lower.find(&query_lower) {
            Some(match_index) => {
                let mut start_index = match_index.saturating_sub(100).min(transcript.len());
                while start_index < transcript.len() && !transcript.is_char_boundary(start_index) {
                    start_index += 1;
                }
                let mut end_index = (match_index + query.len() + 100).min(transcript.len());
                while end_index > start_index && !transcript.is_char_boundary(end_index) {
                    end_index -= 1;
                }

                let mut context = String::new();
                if start_index > 0 {
                    context.push_str("...");
                }
                context.push_str(&transcript[start_index..end_index]);
                if end_index < transcript.len() {
                    context.push_str("...");
                }
                context
            }
            None => transcript.chars().take(200).collect(), // Fallback to the start of the transcript
        }
    }
}

#[cfg(test)]
mod tests {
    use super::TranscriptsRepository;
    use crate::api::TranscriptSegment;
    use sqlx::sqlite::SqlitePoolOptions;

    #[tokio::test]
    async fn recording_session_id_makes_transcript_save_idempotent() {
        let pool = SqlitePoolOptions::new()
            .max_connections(1)
            .connect("sqlite::memory:")
            .await
            .unwrap();
        sqlx::query(
            "CREATE TABLE meetings (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, folder_path TEXT)",
        )
        .execute(&pool)
        .await
        .unwrap();
        sqlx::query(
            "CREATE TABLE transcripts (id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, transcript TEXT NOT NULL, timestamp TEXT NOT NULL, audio_start_time REAL, audio_end_time REAL, duration REAL)",
        )
        .execute(&pool)
        .await
        .unwrap();

        let session_id = "meeting-intel-0123456789abcdef0123456789abcdef";
        let segments = vec![TranscriptSegment {
            id: "source-1".to_string(),
            text: "private transcript".to_string(),
            timestamp: "2026-08-13T00:00:00Z".to_string(),
            audio_start_time: Some(0.0),
            audio_end_time: Some(1.0),
            duration: Some(1.0),
        }];

        for _ in 0..2 {
            let saved_id = TranscriptsRepository::save_transcript(
                &pool,
                "Pilot meeting",
                &segments,
                Some("recordings/session".to_string()),
                Some(session_id.to_string()),
            )
            .await
            .unwrap();
            assert_eq!(saved_id, session_id);
        }

        let meetings: (i64,) = sqlx::query_as("SELECT COUNT(*) FROM meetings")
            .fetch_one(&pool)
            .await
            .unwrap();
        let transcripts: (i64,) = sqlx::query_as("SELECT COUNT(*) FROM transcripts")
            .fetch_one(&pool)
            .await
            .unwrap();
        assert_eq!(meetings.0, 1);
        assert_eq!(transcripts.0, 1);
    }

    #[test]
    fn match_context_never_slices_inside_japanese_text() {
        let transcript = format!("{}在庫差異{}", "あ".repeat(80), "い".repeat(80));
        let context = TranscriptsRepository::get_match_context(&transcript, "在庫差異");
        assert!(context.contains("在庫差異"));
    }
}
