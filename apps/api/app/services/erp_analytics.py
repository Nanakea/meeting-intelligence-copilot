"""Pure deterministic aggregation of live ERP and WMS records."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import combinations

from app.domain.context import (
    ConnectorKind,
    ERPAnalyticsMapping,
    ERPMetricAggregation,
)
from app.domain.erp_analytics import (
    ERPAnalyticsBucket,
    ERPAnalyticsRequest,
    ERPAnalyticsRun,
    ERPAnalyticsWarning,
    ERPMetricDiscrepancy,
    ERPTrendDirection,
    ERPTrendPoint,
    ERPTrendSeries,
    ERPTrendSummary,
)

_MAX_SERIES = 200
_MAX_DISCREPANCIES = 1_000


@dataclass(frozen=True)
class ERPAnalyticsSource:
    connector_id: str
    source_kind: ConnectorKind
    source_label: str
    mappings: list[ERPAnalyticsMapping]
    records: dict[str, list[dict[str, object]]]


def _hash(*parts: object) -> str:
    return hashlib.sha256("\0".join(str(part) for part in parts).encode()).hexdigest()


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or abs(parsed) > Decimal("1e18"):
        return None
    return parsed


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _bucket_start(value: datetime, bucket: ERPAnalyticsBucket) -> datetime:
    value = value.astimezone(UTC)
    if bucket is ERPAnalyticsBucket.day:
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket is ERPAnalyticsBucket.week:
        return (value - timedelta(days=value.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _bucket_end(value: datetime, bucket: ERPAnalyticsBucket) -> datetime:
    if bucket is ERPAnalyticsBucket.day:
        return value + timedelta(days=1)
    if bucket is ERPAnalyticsBucket.week:
        return value + timedelta(days=7)
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1)
    return value.replace(month=value.month + 1)


def _safe_dimension(value: object) -> str:
    cleaned = " ".join(str(value or "(unspecified)").replace("\0", "").split())
    return (cleaned or "(unspecified)")[:120]


def _aggregate(
    aggregation: ERPMetricAggregation,
    samples: list[tuple[datetime, Decimal, Decimal | None]],
) -> Decimal | None:
    values = [value for _, value, _ in samples]
    if not values:
        return None
    if aggregation is ERPMetricAggregation.sum:
        return sum(values, Decimal(0))
    if aggregation is ERPMetricAggregation.minimum:
        return min(values)
    if aggregation is ERPMetricAggregation.maximum:
        return max(values)
    if aggregation is ERPMetricAggregation.latest:
        return max(samples, key=lambda sample: (sample[0], sample[1]))[1]
    if aggregation is ERPMetricAggregation.weighted_average:
        weighted = [
            (value, weight)
            for _, value, weight in samples
            if weight is not None and weight >= 0
        ]
        total_weight = sum((weight for _, weight in weighted), Decimal(0))
        if total_weight == 0:
            return None
        return sum((value * weight for value, weight in weighted), Decimal(0)) / total_weight
    return sum(values, Decimal(0)) / Decimal(len(values))


def _number(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.000001")))


def _summary(
    points: list[ERPTrendPoint], stable_threshold_percent: float
) -> ERPTrendSummary:
    if not points:
        return ERPTrendSummary(
            direction=ERPTrendDirection.insufficient_data,
            point_count=0,
            sample_count=0,
        )
    first = Decimal(str(points[0].value))
    last = Decimal(str(points[-1].value))
    change = last - first
    percent = None if first == 0 else change / abs(first) * Decimal(100)
    if len(points) < 2 or percent is None:
        direction = ERPTrendDirection.insufficient_data
    elif abs(percent) <= Decimal(str(stable_threshold_percent)):
        direction = ERPTrendDirection.stable
    elif percent > 0:
        direction = ERPTrendDirection.increasing
    else:
        direction = ERPTrendDirection.decreasing
    return ERPTrendSummary(
        direction=direction,
        first_value=_number(first),
        last_value=_number(last),
        absolute_change=_number(change),
        percent_change=_number(percent) if percent is not None else None,
        point_count=len(points),
        sample_count=sum(point.sample_count for point in points),
    )


def _warning_models(
    warnings: Counter[tuple[str, str | None, str]],
) -> list[ERPAnalyticsWarning]:
    return [
        ERPAnalyticsWarning(
            connector_id=connector_id,
            metric_id=metric_id,
            code=code,  # type: ignore[arg-type]
            skipped_records=count,
        )
        for (connector_id, metric_id, code), count in sorted(warnings.items())
    ]


def analyze_erp_records(
    request: ERPAnalyticsRequest,
    sources: list[ERPAnalyticsSource],
    *,
    unavailable_connector_ids: list[str] | None = None,
) -> ERPAnalyticsRun:
    unavailable = list(dict.fromkeys(unavailable_connector_ids or []))
    warnings: Counter[tuple[str, str | None, str]] = Counter(
        (connector_id, None, "connector_unavailable") for connector_id in unavailable
    )
    grouped: dict[
        tuple[str, str, str | None, tuple[tuple[str, str], ...], datetime],
        list[tuple[datetime, Decimal, Decimal | None]],
    ] = defaultdict(list)
    mappings_by_series: dict[
        tuple[str, str, str | None, tuple[tuple[str, str], ...]],
        tuple[ERPAnalyticsSource, ERPAnalyticsMapping],
    ] = {}
    selected_metrics = set(request.metric_ids)
    for source in sources:
        if not source.mappings:
            warnings[(source.connector_id, None, "metric_not_configured")] += 1
            continue
        configured = {mapping.metric_id for mapping in source.mappings}
        for missing in sorted(selected_metrics - configured):
            warnings[(source.connector_id, missing, "metric_not_configured")] += 1
        for mapping in source.mappings:
            if selected_metrics and mapping.metric_id not in selected_metrics:
                continue
            entity_records = next(
                (
                    records
                    for entity, records in source.records.items()
                    if entity.casefold() == mapping.entity_name.casefold()
                ),
                [],
            )
            for record in entity_records:
                timestamp = _timestamp(record.get(mapping.timestamp_field))
                if timestamp is None:
                    warnings[(source.connector_id, mapping.metric_id, "invalid_timestamp")] += 1
                    continue
                if timestamp < request.period_start or timestamp >= request.period_end:
                    warnings[(source.connector_id, mapping.metric_id, "out_of_range")] += 1
                    continue
                value = _decimal(record.get(mapping.value_field))
                if value is None:
                    warnings[(source.connector_id, mapping.metric_id, "invalid_value")] += 1
                    continue
                weight = None
                if mapping.weight_field:
                    weight = _decimal(record.get(mapping.weight_field))
                    if weight is None or weight < 0:
                        warnings[(source.connector_id, mapping.metric_id, "invalid_weight")] += 1
                        continue
                currency = mapping.fixed_currency
                if mapping.currency_field:
                    raw_currency = record.get(mapping.currency_field)
                    if raw_currency is None or not str(raw_currency).strip():
                        warnings[(source.connector_id, mapping.metric_id, "missing_currency")] += 1
                        continue
                    currency = str(raw_currency).strip().upper()
                    if len(currency) != 3 or not currency.isalpha():
                        warnings[(source.connector_id, mapping.metric_id, "invalid_currency")] += 1
                        continue
                dimensions = tuple(
                    (field, _safe_dimension(record.get(field)))
                    for field in mapping.dimension_fields
                )
                period = _bucket_start(timestamp, request.bucket)
                grouped[
                    (source.connector_id, mapping.metric_id, currency, dimensions, period)
                ].append((timestamp, value, weight))
                mappings_by_series[
                    (source.connector_id, mapping.metric_id, currency, dimensions)
                ] = (source, mapping)

    series: list[ERPTrendSeries] = []
    for key in sorted(mappings_by_series, key=lambda value: json.dumps(value, default=str)):
        connector_id, metric_id, currency, dimensions = key
        source, mapping = mappings_by_series[key]
        points: list[ERPTrendPoint] = []
        periods = sorted(
            period
            for grouped_connector, grouped_metric, grouped_currency, grouped_dimensions, period
            in grouped
            if (
                grouped_connector,
                grouped_metric,
                grouped_currency,
                grouped_dimensions,
            )
            == key
        )
        for period in periods:
            samples = grouped[(connector_id, metric_id, currency, dimensions, period)]
            value = _aggregate(mapping.aggregation, samples)
            if value is None:
                warnings[(connector_id, metric_id, "invalid_weight")] += len(samples)
                continue
            points.append(
                ERPTrendPoint(
                    period_start=period,
                    period_end=_bucket_end(period, request.bucket),
                    value=_number(value),
                    sample_count=len(samples),
                )
            )
        if not points:
            continue
        dimensions_dict = dict(dimensions)
        series.append(
            ERPTrendSeries(
                series_id=_hash(
                    connector_id,
                    metric_id,
                    currency or "",
                    json.dumps(dimensions_dict, sort_keys=True),
                    request.bucket.value,
                ),
                connector_id=connector_id,
                source_kind=source.source_kind,
                source_label=source.source_label,
                source_reference=f"{source.source_label} / {mapping.entity_name}",
                metric_id=metric_id,
                metric_kind=mapping.metric_kind,
                entity_name=mapping.entity_name,
                unit=mapping.unit,
                currency=currency,
                dimensions=dimensions_dict,
                bucket=request.bucket,
                points=points,
                summary=_summary(points, request.stable_threshold_percent),
            )
        )
    series.sort(
        key=lambda value: (
            value.metric_id,
            value.currency or "",
            json.dumps(value.dimensions, sort_keys=True),
            value.connector_id,
        )
    )
    if len(series) > _MAX_SERIES:
        for source in sources:
            warnings[(source.connector_id, None, "series_limit_reached")] += 1
        series = series[:_MAX_SERIES]

    discrepancies: list[ERPMetricDiscrepancy] = []
    comparable: dict[
        tuple[str, str, str | None, tuple[tuple[str, str], ...]], list[ERPTrendSeries]
    ] = defaultdict(list)
    for value in series:
        comparable[
            (
                value.metric_id,
                value.unit,
                value.currency,
                tuple(sorted(value.dimensions.items())),
            )
        ].append(value)
    threshold = Decimal(str(request.discrepancy_threshold_percent))
    for comparison_key, candidates in sorted(
        comparable.items(), key=lambda item: _stable_json(item[0])
    ):
        for left, right in combinations(candidates, 2):
            if left.connector_id == right.connector_id:
                continue
            left_points = {point.period_start: point for point in left.points}
            right_points = {point.period_start: point for point in right.points}
            for period in sorted(set(left_points) & set(right_points)):
                left_value = Decimal(str(left_points[period].value))
                right_value = Decimal(str(right_points[period].value))
                difference = abs(left_value - right_value)
                denominator = max(abs(left_value), abs(right_value), Decimal("0.000001"))
                percent = difference / denominator * Decimal(100)
                if percent < threshold:
                    continue
                severity = "high" if percent >= 25 else "medium" if percent >= 10 else "low"
                metric_id, unit, currency, dimensions = comparison_key
                discrepancies.append(
                    ERPMetricDiscrepancy(
                        discrepancy_id=_hash(
                            metric_id,
                            left.connector_id,
                            right.connector_id,
                            period.isoformat(),
                            currency or "",
                            json.dumps(dict(dimensions), sort_keys=True),
                        ),
                        metric_id=metric_id,
                        left_connector_id=left.connector_id,
                        right_connector_id=right.connector_id,
                        period_start=period,
                        unit=unit,
                        currency=currency,
                        dimensions=dict(dimensions),
                        left_value=_number(left_value),
                        right_value=_number(right_value),
                        absolute_difference=_number(difference),
                        difference_percent=_number(percent),
                        severity=severity,
                        explanation=(
                            f"{metric_id} differs by {_number(percent)}% between "
                            f"{left.source_label} and {right.source_label}; verify source timing, "
                            "currency, units, and mapping before acting."
                        ),
                    )
                )
                if len(discrepancies) >= _MAX_DISCREPANCIES:
                    break
            if len(discrepancies) >= _MAX_DISCREPANCIES:
                break
        if len(discrepancies) >= _MAX_DISCREPANCIES:
            break

    status = (
        "unavailable"
        if not series and unavailable
        else "partial"
        if unavailable
        else "current"
    )
    generated_at = datetime.now(UTC)
    run_id = _hash(
        "erp-analytics",
        request.model_dump_json(),
        *[value.series_id for value in series],
        *unavailable,
    )
    return ERPAnalyticsRun(
        run_id=run_id,
        status=status,
        period_start=request.period_start,
        period_end=request.period_end,
        bucket=request.bucket,
        series=series,
        discrepancies=discrepancies,
        warnings=_warning_models(warnings),
        unavailable_connector_ids=unavailable,
        generated_at=generated_at,
    )
