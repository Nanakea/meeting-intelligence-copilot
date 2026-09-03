# Realistic Meeting Scenario Matrix

Status: deterministic synthetic demo/eval coverage. These are naturalistic curated meetings, not
claims of unrestricted language understanding and not recordings of real people.

| Product scenario | Executable evidence | What it proves |
|---|---|---|
| Japanese data mismatch with owner answered | `owner-answer-ja` | source of truth and impact close first; an owner question does not self-answer; explicit split ownership retracts ASK NOW |
| English manual work with volume and impact | `manual-work-volume-en` | daily frequency, 1,200-order volume, 45-minute cost, shipment impact, and team owner promote in template order |
| Japanese directional integration failure | `integration-transfer-details-ja` | explicit EC → SAP CSV transfer fills source, target, and failure point with evidence |
| English directional integration failure | `integration-transfer-details-en` | explicit EC → SAP CSV import, impact, error code, retry, and owner fill without a model |
| Process delay | `process-delay-en` | current/normal lead time, bottleneck, impact, owner, and process step; bottleneck question is not evidence |
| Unclear ownership | `unclear-ownership-en` | unresolved ownership opens ASK NOW; stakeholders/decision/escalation do not invent an owner; explicit owner and status close the remaining gaps |
| Vague requirement | `vague-requirement-ja` | concrete current problem and expected behavior emerge from a vague improvement request |
| Small talk / no pain | `small-talk-no-pain-ja`, `small-talk-no-pain-en` | neither language fabricates a pain, fact, gap, or question |
| Mixed technical terms | `integration-failure-mixed-terms-ja` | Japanese flow preserves bounded Latin technical identifiers without corruption |
| Recovery/replay | backend recovery adapter/panel tests and `MEETILY_V1_RUNBOOK.md` | stale state clears and ordered active-recording history rebuilds deterministic state after backend restart |

All fixture facts and pains retain emitted event IDs. Every golden checkpoint reasserts one ASK NOW
maximum, two FOLLOW UP maximum, and no active suggestion for an answered gap. The four new Phase 7
meetings are registered server-side demos; arbitrary fixture paths remain rejected.

Each annotation-only semantic case now has a `related_demo_id`. This links paraphrase/negative-guard
research to a realistic scenario without promoting annotations into deterministic state goldens or
changing normal runtime behavior.
