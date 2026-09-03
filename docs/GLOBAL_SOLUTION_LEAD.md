# Global Solution Lead Governance Cockpit

## Daily workflow

1. Before a meeting, open the governance workspace and filter confirmed records by system,
   capability, owner/team, region, status, or due date. Review ownerless, overdue, aging-decision,
   critical-dependency, and provenance alerts. Connected citations are optional and time-bounded.
2. During the meeting, keep ASK NOW as the primary interaction. The governance tray shows compact
   transcript-backed candidates but never admits them automatically.
3. After the meeting, confirm, edit, or dismiss candidates. Assign roles/teams and link confirmed
   records to problems or glossary entities. Legacy AI summaries remain display-only.
4. Export only reviewed records as canonical JSON, ADR/Markdown, RAID CSV, dependency CSV, Jira CSV,
   Azure Boards CSV, or a ZIP review pack. Exports are drafts; no external submission is implemented.

## Authority and privacy

- `MeetingState` remains the evidence-backed meeting authority.
- Governance is a separate encrypted local SQLite/WAL aggregate with append-only revisions.
- Remote Graph, SharePoint, and ERP results remain live-only and ACL-filtered. They may support or
  conflict with a record but cannot confirm it or become transcript evidence.
- System relationships participate in impact analysis only after confirmation. Traversal is bounded
  to two hops.
- Normal recording stop retains reviewed governance. Permanent meeting deletion removes that
  meeting's candidates and provenance; multi-source records remain and are flagged for review.
- The personal workspace has no synchronization, multi-user authorization, telemetry, cloud AI, or
  autonomous writes. Portable contracts are the seam for a future company-hosted workspace.
