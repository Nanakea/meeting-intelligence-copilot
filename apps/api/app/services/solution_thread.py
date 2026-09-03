"""Pure digital solution-thread construction and bounded graph analysis."""

from __future__ import annotations

import hashlib
import re
from collections import deque

from app.domain.assurance import AssuranceFinding
from app.domain.evidence import ArtifactSection, DocumentArtifact, DocumentKind
from app.domain.governance import GovernanceRecord
from app.domain.solution_thread import (
    SolutionEdgeKind,
    SolutionMindMap,
    SolutionMindMapEdge,
    SolutionMindMapNode,
    SolutionMindMapRequest,
    SolutionNodeKind,
    SolutionThreadEdge,
    SolutionThreadEdgeReviewRequest,
    SolutionThreadImpact,
    SolutionThreadNode,
    ThreadImpactPath,
    TraceabilityMatrix,
    TraceabilityRow,
)

_TRACE_ID = re.compile(
    r"(?<![A-Z0-9])(?P<kind>REQ|NFR|TEST|TC|要求|要件|試験)"
    r"[-_ ]?(?P<number>[0-9]{1,8})(?![A-Z0-9])",
    re.IGNORECASE,
)


def _opaque(*values: str) -> str:
    return hashlib.sha256("\0".join(values).encode()).hexdigest()


def _public_map_id(prefix: str, value: str) -> str:
    return f"{prefix}_{_opaque('solution-mind-map', value)[:16]}"


def _safe_mermaid_label(value: str) -> str:
    normalized = " ".join(value.split())[:100]
    return normalized.replace('"', "'").replace("[", "(").replace("]", ")")


def _focus_matches(node: SolutionThreadNode, focus: str) -> bool:
    terms = [value for value in re.findall(r"[\w.-]+", focus.casefold()) if value]
    if not terms:
        return True
    searchable = f"{node.label}\n{node.source_reference}".casefold()
    return all(term in searchable for term in terms)


def solution_mind_map(
    request: SolutionMindMapRequest,
    nodes: list[SolutionThreadNode],
    edges: list[SolutionThreadEdge],
) -> SolutionMindMap:
    """Build a bounded, safe-label projection without changing graph authority."""

    allowed_kinds = set(request.kinds)
    eligible_nodes = {
        node.node_id: node
        for node in nodes
        if not allowed_kinds or node.kind in allowed_kinds
    }
    eligible_edges = [
        edge
        for edge in edges
        if edge.review_status != "dismissed"
        and (edge.confirmed or request.include_suggested)
        and edge.source_node_id in eligible_nodes
        and edge.target_node_id in eligible_nodes
    ]
    eligible_edges.sort(key=lambda value: value.edge_id)
    adjacency: dict[str, list[SolutionThreadEdge]] = {}
    for edge in eligible_edges:
        adjacency.setdefault(edge.source_node_id, []).append(edge)
        adjacency.setdefault(edge.target_node_id, []).append(edge)

    ordered_nodes = sorted(
        eligible_nodes.values(),
        key=lambda value: (
            not value.confirmed,
            value.kind.value,
            value.label.casefold(),
            value.node_id,
        ),
    )
    if request.focus:
        root_candidates = [
            node for node in ordered_nodes if _focus_matches(node, request.focus)
        ]
    else:
        preferred = {
            SolutionNodeKind.document,
            SolutionNodeKind.system,
            SolutionNodeKind.interface,
            SolutionNodeKind.erp_entity,
        }
        root_candidates = [node for node in ordered_nodes if node.kind in preferred]
        if not root_candidates:
            root_candidates = ordered_nodes
    roots = root_candidates[:64]

    selected: dict[str, int] = {}
    queue = deque((node.node_id, 0) for node in roots)
    truncated = len(root_candidates) > len(roots)
    while queue:
        node_id, depth = queue.popleft()
        if node_id in selected and selected[node_id] <= depth:
            continue
        if len(selected) >= request.max_nodes:
            truncated = True
            break
        selected[node_id] = depth
        if depth >= request.depth:
            continue
        for edge in adjacency.get(node_id, []):
            other = (
                edge.target_node_id
                if edge.source_node_id == node_id
                else edge.source_node_id
            )
            if other not in selected:
                queue.append((other, depth + 1))

    selected_edge_candidates = [
        edge
        for edge in eligible_edges
        if edge.source_node_id in selected and edge.target_node_id in selected
    ]
    if len(selected_edge_candidates) > 2_000:
        truncated = True
    selected_edges = selected_edge_candidates[:2_000]
    degree: dict[str, int] = {node_id: 0 for node_id in selected}
    for edge in selected_edges:
        degree[edge.source_node_id] += 1
        degree[edge.target_node_id] += 1

    selected_nodes = sorted(
        (eligible_nodes[node_id] for node_id in selected),
        key=lambda value: (
            selected[value.node_id],
            value.kind.value,
            value.label.casefold(),
            value.node_id,
        ),
    )
    public_ids = {
        node.node_id: _public_map_id("m", node.node_id) for node in selected_nodes
    }
    root_ids = {node.node_id for node in roots if node.node_id in selected}
    map_nodes = [
        SolutionMindMapNode(
            public_id=public_ids[node.node_id],
            kind=node.kind,
            label=node.label,
            source_reference=node.source_reference,
            source_revision=node.source_revision,
            confirmed=node.confirmed,
            root=node.node_id in root_ids,
            degree=degree[node.node_id],
        )
        for node in selected_nodes
    ]
    map_edges = [
        SolutionMindMapEdge(
            public_id=_public_map_id("e", edge.edge_id),
            source_public_id=public_ids[edge.source_node_id],
            target_public_id=public_ids[edge.target_node_id],
            relation=edge.relation,
            status="confirmed" if edge.confirmed else "suggested",
        )
        for edge in selected_edges
    ]

    counts: dict[str, int] = {}
    for node in map_nodes:
        counts[node.kind.value] = counts.get(node.kind.value, 0) + 1
    hint_candidates = [
        node.label
        for node in sorted(
            map_nodes,
            key=lambda value: (not value.root, -value.degree, value.label.casefold()),
        )
    ]
    query_hints = list(dict.fromkeys(hint_candidates))[:32]
    by_public_id = {node.public_id: node for node in map_nodes}
    outline = [
        (
            f"{by_public_id[edge.source_public_id].label} "
            f"--{edge.relation.value}--> "
            f"{by_public_id[edge.target_public_id].label} [{edge.status}]"
        )
        for edge in map_edges[:500]
    ]
    mermaid = ["flowchart LR"]
    for node in map_nodes:
        mermaid.append(
            f'  {node.public_id}["{_safe_mermaid_label(node.label)}"]'
        )
    for edge in map_edges:
        connector = "-->" if edge.status == "confirmed" else "-.->"
        mermaid.append(
            f"  {edge.source_public_id} {connector}|{edge.relation.value}| "
            f"{edge.target_public_id}"
        )

    fingerprint_parts = [
        *(f"n:{node.node_id}:{node.source_revision or ''}" for node in selected_nodes),
        *(
            f"e:{edge.edge_id}:{edge.revision}:{edge.review_status}"
            for edge in selected_edges
        ),
        f"focus:{request.focus or ''}",
        f"depth:{request.depth}",
        f"suggested:{request.include_suggested}",
    ]
    return SolutionMindMap(
        source_fingerprint=_opaque(*fingerprint_parts),
        focus=request.focus,
        root_public_ids=[public_ids[node_id] for node_id in sorted(root_ids)],
        nodes=map_nodes,
        edges=map_edges,
        counts_by_kind=counts,
        query_hints=query_hints,
        outline=outline,
        mermaid="\n".join(mermaid),
        suggested_edge_count=sum(edge.status == "suggested" for edge in map_edges),
        truncated=truncated,
    )


def artifact_nodes(artifact: DocumentArtifact) -> list[SolutionThreadNode]:
    document_node = SolutionThreadNode(
        node_id=_opaque("artifact", artifact.artifact_id),
        kind=SolutionNodeKind.document,
        label=artifact.title,
        source_reference=artifact.source_reference,
        source_revision=artifact.current_revision_id,
        confirmed=True,
    )
    revision_node = SolutionThreadNode(
        node_id=_opaque("revision", artifact.current_revision_id),
        kind=SolutionNodeKind.revision,
        label=f"{artifact.title} revision",
        source_reference=artifact.source_reference,
        source_revision=artifact.current_revision_id,
        confirmed=True,
    )
    return [document_node, revision_node]


def artifact_revision_edge(artifact: DocumentArtifact) -> SolutionThreadEdge:
    source = _opaque("artifact", artifact.artifact_id)
    target = _opaque("revision", artifact.current_revision_id)
    return SolutionThreadEdge(
        edge_id=_opaque("edge", source, target, SolutionEdgeKind.references.value),
        source_node_id=source,
        target_node_id=target,
        relation=SolutionEdgeKind.references,
        source_revision=artifact.current_revision_id,
        provenance_fields=["current_revision_id"],
        confirmed=True,
        review_status="confirmed",
    )


def traceability_candidates(
    artifact: DocumentArtifact, sections: list[ArtifactSection]
) -> tuple[list[SolutionThreadNode], list[SolutionThreadEdge]]:
    """Extract explicit trace identifiers; inferred relationships remain review-gated."""

    document_node_id = _opaque("artifact", artifact.artifact_id)
    nodes: dict[str, SolutionThreadNode] = {}
    edges: dict[str, SolutionThreadEdge] = {}
    for section in sections:
        identifiers: list[tuple[str, SolutionNodeKind]] = []
        for match in _TRACE_ID.finditer(section.content):
            prefix = match.group("kind").upper()
            value = f"{prefix}-{match.group('number')}"
            kind = (
                SolutionNodeKind.test
                if prefix in {"TEST", "TC", "試験"}
                else SolutionNodeKind.nfr
                if prefix == "NFR"
                else SolutionNodeKind.requirement
            )
            node_id = _opaque("trace", artifact.artifact_id, value.casefold())
            nodes[node_id] = SolutionThreadNode(
                node_id=node_id,
                kind=kind,
                label=value,
                source_reference=artifact.source_reference,
                source_revision=artifact.current_revision_id,
                confirmed=True,
            )
            identifiers.append((node_id, kind))
            if kind in {SolutionNodeKind.requirement, SolutionNodeKind.nfr}:
                edge_id = _opaque(
                    "edge", node_id, document_node_id, SolutionEdgeKind.implements.value
                )
                edges[edge_id] = SolutionThreadEdge(
                    edge_id=edge_id,
                    source_node_id=node_id,
                    target_node_id=document_node_id,
                    relation=SolutionEdgeKind.implements,
                    source_revision=artifact.current_revision_id,
                    provenance_fields=[section.section_id],
                )
        requirements = [
            node_id
            for node_id, kind in identifiers
            if kind in {SolutionNodeKind.requirement, SolutionNodeKind.nfr}
        ]
        tests = [
            node_id
            for node_id, kind in identifiers
            if kind is SolutionNodeKind.test
        ]
        for requirement_id in requirements:
            for test_id in tests:
                edge_id = _opaque(
                    "edge",
                    requirement_id,
                    test_id,
                    SolutionEdgeKind.validated_by.value,
                )
                edges[edge_id] = SolutionThreadEdge(
                    edge_id=edge_id,
                    source_node_id=requirement_id,
                    target_node_id=test_id,
                    relation=SolutionEdgeKind.validated_by,
                    source_revision=artifact.current_revision_id,
                    provenance_fields=[section.section_id],
                )
    return list(nodes.values()), list(edges.values())


def review_edge(
    edge: SolutionThreadEdge, request: SolutionThreadEdgeReviewRequest
) -> SolutionThreadEdge:
    if edge.revision != request.expected_revision:
        raise RuntimeError("stale_solution_edge")
    return edge.model_copy(
        update={
            "confirmed": request.action == "confirm",
            "review_status": "confirmed" if request.action == "confirm" else "dismissed",
            "revision": edge.revision + 1,
        }
    )


def governance_node(record: GovernanceRecord) -> SolutionThreadNode:
    kind = {
        "decision": SolutionNodeKind.decision,
        "risk": SolutionNodeKind.risk,
        "action": SolutionNodeKind.action,
    }.get(record.kind.value, SolutionNodeKind.governance_record)
    return SolutionThreadNode(
        node_id=_opaque("governance", record.record_id),
        kind=kind,
        label=record.title,
        source_reference=f"governance:{record.kind.value}",
        source_revision=str(record.revision),
        confirmed=True,
    )


def traceability_matrix(
    nodes: list[SolutionThreadNode], edges: list[SolutionThreadEdge]
) -> TraceabilityMatrix:
    confirmed_edges = [edge for edge in edges if edge.confirmed]
    by_id = {node.node_id: node for node in nodes if node.confirmed}
    rows: list[TraceabilityRow] = []
    for requirement in sorted(
        (node for node in by_id.values() if node.kind is SolutionNodeKind.requirement),
        key=lambda value: value.node_id,
    ):
        linked = [
            by_id.get(edge.target_node_id)
            for edge in confirmed_edges
            if edge.source_node_id == requirement.node_id
        ]
        design_ids = [
            node.node_id
            for node in linked
            if node is not None
            and node.kind
            in {SolutionNodeKind.document, SolutionNodeKind.interface, SolutionNodeKind.nfr}
        ]
        test_ids = [
            node.node_id
            for node in linked
            if node is not None and node.kind is SolutionNodeKind.test
        ]
        rows.append(
            TraceabilityRow(
                requirement_node_id=requirement.node_id,
                design_node_ids=design_ids,
                test_node_ids=test_ids,
            )
        )
    return TraceabilityMatrix(
        rows=rows,
        missing_design_count=sum(not row.design_node_ids for row in rows),
        missing_test_count=sum(not row.test_node_ids for row in rows),
    )


def bounded_impact(
    root_node_id: str,
    depth: int,
    nodes: list[SolutionThreadNode],
    edges: list[SolutionThreadEdge],
) -> SolutionThreadImpact:
    by_id = {node.node_id: node for node in nodes if node.confirmed}
    if root_node_id not in by_id:
        raise KeyError(root_node_id)
    confirmed = [edge for edge in edges if edge.confirmed]
    adjacency: dict[str, list[SolutionThreadEdge]] = {}
    for edge in confirmed:
        adjacency.setdefault(edge.source_node_id, []).append(edge)
        adjacency.setdefault(edge.target_node_id, []).append(edge)
    queue = deque([(root_node_id, [root_node_id], [])])
    visited_depth = {root_node_id: 0}
    paths: list[ThreadImpactPath] = []
    edge_ids: set[str] = set()
    while queue:
        current, node_path, edge_path = queue.popleft()
        if edge_path:
            paths.append(ThreadImpactPath(node_ids=node_path, edge_ids=edge_path))
        if len(edge_path) >= depth:
            continue
        for edge in sorted(adjacency.get(current, []), key=lambda value: value.edge_id):
            other = (
                edge.target_node_id
                if edge.source_node_id == current
                else edge.source_node_id
            )
            next_depth = len(edge_path) + 1
            if other in node_path or visited_depth.get(other, next_depth + 1) < next_depth:
                continue
            visited_depth[other] = next_depth
            edge_ids.add(edge.edge_id)
            queue.append((other, [*node_path, other], [*edge_path, edge.edge_id]))
    included_nodes = sorted(
        (by_id[node_id] for node_id in visited_depth), key=lambda value: value.node_id
    )
    included_edges = sorted(
        (edge for edge in confirmed if edge.edge_id in edge_ids),
        key=lambda value: value.edge_id,
    )
    return SolutionThreadImpact(
        root_node_id=root_node_id,
        depth=depth,
        nodes=included_nodes,
        edges=included_edges,
        paths=paths,
    )


def document_kind_node_kind(kind: DocumentKind) -> SolutionNodeKind:
    return {
        DocumentKind.requirement: SolutionNodeKind.requirement,
        DocumentKind.interface: SolutionNodeKind.interface,
        DocumentKind.data_mapping: SolutionNodeKind.data_object,
        DocumentKind.nfr_security: SolutionNodeKind.nfr,
        DocumentKind.test: SolutionNodeKind.test,
        DocumentKind.cutover: SolutionNodeKind.cutover_control,
    }.get(kind, SolutionNodeKind.document)


def finding_node(finding: AssuranceFinding) -> SolutionThreadNode:
    return SolutionThreadNode(
        node_id=_opaque("finding", finding.finding_id),
        kind=SolutionNodeKind.problem,
        label=finding.title,
        source_reference=finding.source_reference,
        source_revision=finding.revision_id,
        confirmed=finding.status.value == "confirmed",
    )
