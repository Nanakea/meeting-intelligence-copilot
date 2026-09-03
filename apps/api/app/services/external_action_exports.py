"""Pure rendering for review-only external action handoff."""

from __future__ import annotations

import csv
import io
import json
import zipfile

from app.domain.integrations import ExternalActionDraft


def _safe_cell(value: str) -> str:
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value


def _description(action: ExternalActionDraft) -> str:
    return (
        f"Severity: {action.severity}\n"
        f"Source: {action.source_reference}\n\n"
        f"{action.rendered_preview}"
    )


def _csv(headers: list[str], values: list[str]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerow([_safe_cell(value) for value in values])
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def _markdown(action: ExternalActionDraft) -> bytes:
    value = (
        f"# {action.rendered_preview.splitlines()[0][:200]}\n\n"
        f"- Severity: {action.severity}\n"
        f"- Source reference: {action.source_reference}\n"
        "- Delivery: reviewed draft only\n\n"
        f"{action.rendered_preview}\n"
    )
    return value.encode("utf-8")


def render_external_action_export(
    action: ExternalActionDraft, format_name: str
) -> tuple[str, str, bytes]:
    stem = f"reviewed-action-{action.fingerprint[:12]}"
    description = _description(action)
    title = action.rendered_preview.splitlines()[0][:200]
    if format_name in {"markdown", "github_issue_markdown", "gitlab_issue_markdown"}:
        return f"{stem}.md", "text/markdown; charset=utf-8", _markdown(action)
    if format_name in {"canonical_json", "netsuite_review_json"}:
        payload = {
            "schema_version": 12,
            "kind": action.kind.value,
            "title": title,
            "severity": action.severity,
            "source_reference": action.source_reference,
            "description": action.rendered_preview,
            "submission": "disabled",
        }
        return (
            f"{stem}.json",
            "application/json",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
        )
    if format_name == "jira_csv":
        payload = _csv(
            ["Summary", "Description", "Work Type", "Labels"],
            [title, description, "Task", "meeting-intelligence"],
        )
        return f"{stem}-jira.csv", "text/csv; charset=utf-8", payload
    if format_name == "azure_boards_csv":
        payload = _csv(
            ["Work Item Type", "Title", "Description", "Tags"],
            ["Task", title, description, "meeting-intelligence"],
        )
        return f"{stem}-azure.csv", "text/csv; charset=utf-8", payload
    if format_name == "servicenow_csv":
        payload = _csv(
            ["short_description", "description", "impact", "source_reference"],
            [title, description, action.severity, action.source_reference],
        )
        return f"{stem}-servicenow.csv", "text/csv; charset=utf-8", payload
    if format_name == "combined_zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{stem}.md", _markdown(action))
            archive.writestr(
                f"{stem}-jira.csv",
                _csv(
                    ["Summary", "Description", "Work Type", "Labels"],
                    [title, description, "Task", "meeting-intelligence"],
                ),
            )
            archive.writestr(
                f"{stem}-azure.csv",
                _csv(
                    ["Work Item Type", "Title", "Description", "Tags"],
                    ["Task", title, description, "meeting-intelligence"],
                ),
            )
        return f"{stem}.zip", "application/zip", buffer.getvalue()
    raise ValueError("unsupported reviewed export format")
