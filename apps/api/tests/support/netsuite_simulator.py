"""Protocol-faithful, synthetic SuiteTalk transport used only by tests."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit


class SyntheticSuiteTalkTransport:
    def __init__(
        self,
        *,
        mode: str = "ready",
        review_record_type: str = "customrecord_solution_review",
    ) -> None:
        self.mode = mode
        self.review_record_type = review_record_type
        self.requests: list[tuple[str, str]] = []
        self.operational_writes = 0
        self.review_writes = 0
        self._throttled = False
        self.records = {
            "customer": [
                {
                    "internalid": "C-100",
                    "entityid": "SYNTH-CUSTOMER",
                    "lastmodifieddate": "2026-01-01T00:00:00Z",
                }
            ],
            "salesOrder": [
                {
                    "internalid": "SO-100",
                    "tranid": "SYNTH-SO-100",
                    "currency": "JPY",
                    "subsidiary": "JP",
                    "lastmodifieddate": "2026-01-02T00:00:00Z",
                }
            ],
            "invoice": [
                {
                    "internalid": "INV-100",
                    "tranid": "SYNTH-INV-100",
                    "currency": "JPY",
                    "subsidiary": "JP",
                    "lastmodifieddate": "2026-01-03T00:00:00Z",
                }
            ],
        }

    @staticmethod
    def _schema(record_type: str, fields: set[str]) -> dict[str, object]:
        return {
            "type": "object",
            "required": ["internalid"],
            "properties": {
                field: {
                    "type": "string",
                    **({"enum": ["JPY", "USD"]} if field == "currency" else {}),
                }
                for field in sorted(fields)
            },
        }

    def request_json(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, dict[str, object]]:
        parsed = urlsplit(url)
        path = parsed.path
        self.requests.append((method, path))
        if method in {"PATCH", "PUT", "DELETE"}:
            self.operational_writes += 1
            raise AssertionError("operational NetSuite mutations are prohibited")
        if path.endswith("/auth/oauth2/v1/token"):
            if self.mode in {"expired_certificate", "revoked_role"}:
                return 401, {"error": self.mode}
            return 200, {"access_token": "synthetic-token", "expires_in": 300}
        if self.mode == "malformed":
            return 200, {"components": "invalid"}
        if self.mode == "throttled" and not self._throttled:
            self._throttled = True
            return 429, {"error": "rate_limited"}
        if path.endswith("/metadata-catalog"):
            schemas = {}
            for record_type, rows in self.records.items():
                fields = {key for row in rows for key in row}
                schemas[record_type] = self._schema(record_type, fields)
            return 200, {"components": {"schemas": schemas}, "version": "synthetic-v1"}
        if path.endswith("/suiteql"):
            payload = json.loads(body or b"{}")
            query = str(payload.get("q", ""))
            match = re.search(r"\bFROM\s+([A-Za-z][A-Za-z0-9_]*)", query, re.I)
            if match is None:
                return 400, {"error": "invalid_query"}
            rows = self.records.get(match.group(1), [])
            offset = int(parse_qs(parsed.query).get("offset", ["0"])[0])
            limit = int(parse_qs(parsed.query).get("limit", ["200"])[0])
            return 200, {
                "items": rows[offset : offset + limit],
                "hasMore": offset + limit < len(rows),
            }
        if path.endswith(f"/record/v1/{self.review_record_type}") and method == "POST":
            if self.mode == "ambiguous_delivery":
                raise TimeoutError("synthetic ambiguous outcome")
            self.review_writes += 1
            return 201, {"id": "SYNTH-REVIEW-1"}
        if method == "POST":
            self.operational_writes += 1
            raise AssertionError("only the configured custom review record may be created")
        return 404, {"error": "not_found"}
