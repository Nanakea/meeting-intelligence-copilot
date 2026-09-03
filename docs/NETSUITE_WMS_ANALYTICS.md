# NetSuite and WMS Read-Only Analytics

## Scope

The connected evidence layer can read explicitly approved NetSuite and WMS projections and
calculate deterministic price, inventory, order-volume, fulfillment, and service-level trends.
It is descriptive decision support, not accounting, forecasting, replenishment, or execution.

NetSuite uses its dedicated SuiteTalk REST, Records Catalog, and bounded SuiteQL adapter. WMS uses
a vendor-neutral HTTPS/JSON profile whose entity endpoints and projected fields must be configured
for the company's WMS. A real tenant or vendor sandbox acceptance is required before either source
is represented as production-compatible.

## Security boundary

- NetSuite and WMS credentials are stored through the configured secret store; non-secret profiles
  remain encrypted with the current Windows user's DPAPI boundary.
- NetSuite permits metadata reads, selected record reads, and bounded SuiteQL only. SuiteQL uses
  the required read-query POST, but no operational record writer is composed.
- WMS transport exposes GET only. It has no generic method argument and cannot express POST,
  PATCH, PUT, or DELETE.
- WMS origins must be HTTPS, contain no credentials/query/fragment, and use explicit mapped paths.
  Pagination is bounded to 200 rows per page and 10 pages per entity.
- Only projected fields are returned. Unknown response fields are discarded before analytics.
- Remote business rows are held in memory for the request and are never persisted or used as
  meeting evidence, learning examples, or connector cache content.
- Connector failure returns an unavailable warning and never interrupts recording or ASK NOW.

## Company permissions to request

### NetSuite

Request a dedicated integration/application and role, not an employee administrator role:

- OAuth 2.0 client-credentials access with a company-managed certificate and rotation owner.
- REST Web Services and SuiteAnalytics/Records Catalog permissions needed for approved reads.
- View access only to explicitly mapped records and fields, normally items, customers, vendors,
  sales/purchase orders, inventory locations, projects, subsidiaries, currencies, and approved
  custom records.
- SuiteQL access restricted by the role's record and field permissions.
- No Full Access, transaction edit/approve/post, inventory adjustment, record deletion, script
  deployment, user administration, or operational custom-record creation permissions.

### WMS

Request a dedicated read-only service identity and a documented vendor API profile:

- HTTPS API hostname, authentication method, certificate/token rotation, and network allow-list.
- Read-only endpoints for approved facilities and entities such as inventory balances, item master
  references, receipts, orders, allocations, picks, shipments, and service-level timestamps.
- Facility/site and business-unit scoping appropriate to the user.
- Field-level approval for price, cost, margin, customer/vendor, and inventory-value data because
  those values may be commercially sensitive.
- No inventory adjustment, allocation/release, wave creation, pick/pack/ship confirmation, master
  data change, file upload, administrative, webhook-management, or delete permission.

Security and ERP owners should approve the exact endpoint/field matrix and validate audit logs,
revocation, expiry, and incident-response ownership before enabling real data.

## Mapping model

Each source declares version-controlled entity and analytics mappings. An analytics mapping names
one projected value field, one timestamp, an aggregation, optional dimensions, and either a fixed
currency or projected currency field. Weighted prices must declare a quantity/weight field.

Example intent:

```json
{
  "metric_id": "sales_order_unit_price",
  "metric_kind": "unit_price",
  "entity_name": "sales_orders",
  "value_field": "unit_price",
  "timestamp_field": "transaction_date",
  "aggregation": "weighted_average",
  "weight_field": "quantity",
  "currency_field": "currency",
  "dimension_fields": ["item", "location"]
}
```

The configuration is rejected if any analytics field is absent from the entity's projected field
allow-list. WMS endpoint paths are relative and reject traversal, origins, queries, and fragments.

## Analytics behavior

`POST /erp/analytics/runs` accepts approved connector IDs, optional metric IDs, a bounded period,
and day/week/month buckets. The authenticated response contains:

- time series with count, value, unit, currency, and safe configured dimensions;
- first/latest/minimum/maximum/average values and deterministic trend direction/percentage;
- NetSuite-to-WMS discrepancies only when metric, dimensions, unit, currency, and time bucket match;
- aggregate warning counts for invalid values, timestamps, currencies, weights, missing entities,
  unavailable connectors, and bounded-output truncation;
- `persisted_remote_records: 0` as a wire-level privacy assertion.

Prices in USD, JPY, or another currency remain separate series. The service performs no foreign
exchange conversion. A difference is a review item, not proof that either source is wrong.

## Interpretation limits

- A price trend can reflect discounts, returns, taxes, units of measure, currency, customer tiers,
  timing, or source-system posting rules. Those semantics must be encoded in reviewed mappings.
- Snapshot inventory is not transaction history unless the source supplies historical timestamps.
- Missing rows are not treated as zero. Invalid or incomplete rows are skipped and counted.
- The engine does not forecast, recommend prices, calculate financial statements, or initiate
  purchasing/fulfillment actions.
- Cross-system differences require aligned cut-off time, environment, item/location keys, unit of
  measure, status filters, and currency before operational conclusions are made.

## Acceptance gate

Before company use, run a non-production acceptance with representative JA/EN labels and at least:

1. Valid and revoked credentials, least-privilege field denial, throttling, pagination, timeout,
   malformed payload, and audit-log verification.
2. Price/currency/unit-of-measure, inventory/location, order status, partial shipment, return, and
   back-order scenarios with independently calculated expected results.
3. NetSuite/WMS cut-off-time and environment mismatch cases proving that discrepancies are review
   items rather than automatic findings.
4. Source revocation proving subsequent reads fail and no remote rows remain searchable offline.
5. Static and runtime proof that no WMS mutation method or NetSuite operational writer is reachable.

Oracle references: [SuiteQL over REST](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_157909186990.html),
[account-specific domains](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_1546938065.html),
and [REST metadata](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_1540810174.html).
