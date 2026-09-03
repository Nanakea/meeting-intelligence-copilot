CREATE TABLE meeting_issue_draft_review_overlays (
    meeting_id TEXT NOT NULL,
    pain_id TEXT NOT NULL,
    base_state_version INTEGER NOT NULL CHECK (base_state_version >= 0),
    status TEXT NOT NULL CHECK (
        status IN ('unreviewed', 'in_review', 'reviewed', 'needs_rebase', 'exported')
    ),
    overlay_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (meeting_id, pain_id),
    FOREIGN KEY (meeting_id, pain_id)
        REFERENCES meeting_issue_drafts(meeting_id, pain_id) ON DELETE CASCADE
);

CREATE INDEX idx_meeting_issue_draft_review_overlays_updated_at
ON meeting_issue_draft_review_overlays(updated_at);
