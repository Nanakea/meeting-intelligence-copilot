"""Pure provider request plans used by signed read-only gateway connectors."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import quote, urlencode, urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.adapters.context.enterprise_gateway import validate_public_https_origin
from app.domain.context import ConnectorKind

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_PROJECT_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SERVICENOW_TABLES = {
    "incident",
    "problem",
    "change_request",
    "cmdb_ci",
    "cmdb_ci_service",
    "kb_knowledge",
}


def _validate_source_ids(kind: ConnectorKind, values: list[str]) -> list[str]:
    if kind is ConnectorKind.github:
        valid = all(_REPOSITORY.fullmatch(value) for value in values)
    elif kind is ConnectorKind.gitlab:
        valid = all(len(value) <= 240 and _PROJECT_PATH.fullmatch(value) for value in values)
    elif kind in {ConnectorKind.jira, ConnectorKind.confluence}:
        valid = all(_PROJECT_KEY.fullmatch(value) for value in values)
    elif kind is ConnectorKind.azure_devops:
        valid = all(_SAFE_NAME.fullmatch(value) for value in values)
    elif kind is ConnectorKind.servicenow:
        valid = all(value in _SERVICENOW_TABLES for value in values)
    elif kind is ConnectorKind.openapi:
        valid = all(_SAFE_NAME.fullmatch(value) for value in values)
    else:
        valid = False
    if not valid:
        raise ValueError("connector source identifier is not allow-listed")
    return values


class ReadOnlyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["GET", "HEAD"]
    url: str = Field(min_length=1, max_length=4_096)
    operation_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    maximum_response_bytes: int = Field(default=2_000_000, ge=1_024, le=10_000_000)

    @model_validator(mode="after")
    def request_is_public_https(self) -> ReadOnlyRequest:
        parsed = urlsplit(self.url)
        validate_public_https_origin(f"{parsed.scheme}://{parsed.netloc}")
        return self


def _url(origin: str, path: str, parameters: dict[str, str | int]) -> str:
    base = validate_public_https_origin(origin)
    target = urljoin(f"{base}/", path.lstrip("/"))
    if urlsplit(target).netloc.casefold() != urlsplit(base).netloc.casefold():
        raise ValueError("connector request escaped its configured origin")
    return f"{target}?{urlencode(parameters)}" if parameters else target


def build_search_plan(
    *,
    kind: ConnectorKind,
    base_url: str,
    terms: list[str],
    selected_external_ids: list[str],
    limit: int,
) -> list[ReadOnlyRequest]:
    phrase = " ".join(terms[:12])[:500]
    bounded_limit = max(1, min(limit, 50))
    if not phrase:
        raise ValueError("connector search requires canonical terms")
    if len(selected_external_ids) > 200:
        raise ValueError("connector search source scope is too broad")
    selected_external_ids = _validate_source_ids(kind, selected_external_ids)

    if kind is ConnectorKind.github:
        scopes = " ".join(f"repo:{value}" for value in selected_external_ids[:20])
        query = f"{phrase} {scopes}".strip()
        return [
            ReadOnlyRequest(
                method="GET",
                url=_url(base_url, "/search/code", {"q": query, "per_page": bounded_limit}),
                operation_id="github.search.code",
            ),
            ReadOnlyRequest(
                method="GET",
                url=_url(base_url, "/search/issues", {"q": query, "per_page": bounded_limit}),
                operation_id="github.search.issues",
            ),
        ]
    if kind is ConnectorKind.gitlab:
        return [
            ReadOnlyRequest(
                method="GET",
                url=_url(
                    base_url,
                    f"/api/v4/projects/{quote(project, safe='')}/search",
                    {"scope": scope, "search": phrase, "per_page": bounded_limit},
                ),
                operation_id=f"gitlab.search.{scope}",
            )
            for project in selected_external_ids[:20]
            for scope in ("blobs", "issues", "merge_requests", "wiki_blobs")
        ]
    if kind in {ConnectorKind.jira, ConnectorKind.confluence}:
        escaped = phrase.replace("\\", "\\\\").replace('"', '\\"')
        if kind is ConnectorKind.jira:
            project_filter = (
                " AND project IN ("
                + ",".join(f"'{value}'" for value in selected_external_ids)
                + ")"
                if selected_external_ids
                else ""
            )
            return [
                ReadOnlyRequest(
                    method="GET",
                    url=_url(
                        base_url,
                        "/rest/api/3/search/jql",
                        {
                            "jql": f'text ~ "{escaped}"{project_filter}',
                            "maxResults": bounded_limit,
                        },
                    ),
                    operation_id="jira.search.issues",
                )
            ]
        return [
            ReadOnlyRequest(
                method="GET",
                url=_url(
                    base_url,
                    "/wiki/rest/api/search",
                    {
                        "cql": (
                            f'text ~ "{escaped}"'
                            + (
                                " AND space IN ("
                                + ",".join(f'"{value}"' for value in selected_external_ids)
                                + ")"
                                if selected_external_ids
                                else ""
                            )
                        ),
                        "limit": bounded_limit,
                    },
                ),
                operation_id="confluence.search.content",
            )
        ]
    if kind is ConnectorKind.azure_devops:
        return [
            ReadOnlyRequest(
                method="GET",
                url=_url(
                    base_url,
                    f"/{quote(project, safe='')}/_apis/git/repositories",
                    {"api-version": "7.1"},
                ),
                operation_id="azure_devops.list.repositories",
            )
            for project in selected_external_ids[:50]
        ]
    if kind is ConnectorKind.servicenow:
        return [
            ReadOnlyRequest(
                method="GET",
                url=_url(
                    base_url,
                    f"/api/now/table/{table}",
                    {
                        "sysparm_query": f"short_descriptionLIKE{phrase}",
                        "sysparm_limit": bounded_limit,
                        "sysparm_display_value": "false",
                    },
                ),
                operation_id=f"servicenow.read.{table}",
            )
            for table in selected_external_ids[:20]
        ]
    if kind is ConnectorKind.openapi:
        return [
            ReadOnlyRequest(
                method="GET",
                url=_url(base_url, f"/{operation}", {"q": phrase, "limit": bounded_limit}),
                operation_id=f"openapi.{operation}",
            )
            for operation in selected_external_ids[:20]
        ]
    raise ValueError("connector kind requires a non-HTTP read adapter")
