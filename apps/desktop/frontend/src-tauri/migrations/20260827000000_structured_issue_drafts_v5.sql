DROP INDEX IF EXISTS idx_meeting_issue_drafts_updated_at;

ALTER TABLE meeting_issue_drafts RENAME TO meeting_issue_drafts_legacy_v4;

CREATE TABLE meeting_issue_drafts (
    meeting_id TEXT NOT NULL,
    pain_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 5,
    draft_json TEXT,
    legacy_markdown TEXT,
    unresolved_count INTEGER NOT NULL DEFAULT 0,
    language TEXT NOT NULL CHECK (language IN ('ja', 'en')),
    readiness TEXT NOT NULL DEFAULT 'needs_clarification'
        CHECK (readiness IN ('needs_clarification', 'ready_for_review', 'legacy')),
    state_version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (meeting_id, pain_id),
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE,
    CHECK (draft_json IS NOT NULL OR legacy_markdown IS NOT NULL)
);

INSERT INTO meeting_issue_drafts (
    meeting_id, pain_id, schema_version, legacy_markdown, unresolved_count,
    language, readiness, state_version, updated_at
)
SELECT meeting_id, 'legacy', 4, draft_markdown, unresolved_count,
       language, 'legacy', 0, updated_at
FROM meeting_issue_drafts_legacy_v4;

DROP TABLE meeting_issue_drafts_legacy_v4;

CREATE INDEX idx_meeting_issue_drafts_updated_at
ON meeting_issue_drafts(updated_at);

CREATE TABLE meeting_issue_export_settings (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    jira_work_type TEXT NOT NULL,
    azure_work_item_type TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO meeting_issue_export_settings (
    singleton, jira_work_type, azure_work_item_type, updated_at
) VALUES (1, 'Task', 'Task', CURRENT_TIMESTAMP);
