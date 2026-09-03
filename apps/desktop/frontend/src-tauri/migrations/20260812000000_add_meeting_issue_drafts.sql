CREATE TABLE IF NOT EXISTS meeting_issue_drafts (
    meeting_id TEXT PRIMARY KEY NOT NULL,
    draft_markdown TEXT NOT NULL,
    unresolved_count INTEGER NOT NULL,
    language TEXT NOT NULL CHECK (language IN ('ja', 'en')),
    updated_at TEXT NOT NULL,
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_meeting_issue_drafts_updated_at
ON meeting_issue_drafts(updated_at);
