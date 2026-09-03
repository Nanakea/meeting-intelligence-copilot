# NetSuite, WMS, and Amazon FBA Reconciliation

## Purpose

Manual handoffs between NetSuite, a warehouse management system, and Fulfillment by Amazon can
produce records that are missing, delayed, duplicated, or keyed differently at one transition.
The reconciliation engine reads explicitly approved projections from all three systems and reports
which transition disagrees:

- NetSuite → WMS
- WMS → Amazon FBA

It does not decide which system is correct, modify a record, create an FBA shipment, update
inventory, or retry a failed business transaction.

## Amazon read boundary

The Amazon adapter uses Login with Amazon (LWA) to exchange a stored refresh token for a short-lived
access token. The only business-data operations implemented are fixed-origin GET requests for:

- FBA Inventory API v1 inventory summaries by selected marketplace.
- Fulfillment Inbound shipment headers.
- Fulfillment Inbound shipment items for a bounded number of selected recent shipments.

The token exchange is the only POST and is restricted to
`https://api.amazon.com/auth/o2/token`. No Listings mutation, Feeds upload, inventory addition,
shipment creation/update/cancellation, label operation, order PII, pricing operation, or report
creation is implemented.

SP-API hosts are fixed by selling region:

- North America: `sellingpartnerapi-na.amazon.com`
- Europe: `sellingpartnerapi-eu.amazon.com`
- Far East/Japan: `sellingpartnerapi-fe.amazon.com`

Redirects, alternate origins, embedded credentials, oversized responses, unbounded pagination,
and unrestricted shipment enumeration are rejected. Amazon records remain in memory for the
request and are not added to the local evidence index or learning examples.

## Company approval required

Request approval from the Amazon Seller Central owner, security team, ERP/WMS owners, and data
owner for the following narrowly scoped setup:

1. A private SP-API application for the company's own seller account, or the company's approved
   service-provider authorization process.
2. The non-restricted `Amazon Fulfillment` role needed for FBA Inventory and Fulfillment Inbound
   retrieval. Do not request restricted PII roles, Orders access, Pricing, Product Listing writes,
   Feeds, or other roles unless a later reviewed use case independently requires them.
3. LWA client ID, client secret, and seller authorization refresh token. The secret and refresh
   token must enter Windows Credential Manager through the connector setup and must never be placed
   in source, logs, screenshots, email, environment files, or exported diagnostics.
4. Approved seller account, selling region, marketplace IDs, and a named owner for credential
   rotation and immediate revocation. The connector does not need to expose the seller ID.
5. Approval for the specific inventory and inbound fields used in reconciliation, retention of
   safe aggregate findings, and the proposed operational review process.
6. A non-production/dynamic-sandbox and then controlled Seller Central acceptance before the
   connector is represented as production-compatible.

Amazon developer registration and application authorization are external company gates. A private
seller application can be self-authorized by the owning organization, but its security and role
approval still belong to the company.

## Canonical Amazon records

The adapter converts provider responses into three bounded canonical entities:

- `fba_inventory`: seller SKU, FNSKU, ASIN, marketplace, condition, fulfillable/inbound/reserved/
  unfulfillable/researching/total quantities, and last update time.
- `fba_inbound_shipments`: shipment reference, safe name, status, destination fulfillment center,
  preparation type, and last update time.
- `fba_inbound_items`: shipment reference, seller SKU, FNSKU, shipped quantity, received quantity,
  case quantity, and release date.

Provider-internal record IDs are hashed or omitted from public search content. Seller SKU and FBA
shipment reference are treated as approved business references because operators need them to
investigate a handoff.

## Flow mapping

Each connector declares one or more reviewed `FulfillmentRecordMapping` entries:

```json
{
  "flow_id": "fba_inbound",
  "entity_name": "outbound_shipments",
  "sku_field": "item_sku",
  "quantity_field": "shipped_quantity",
  "reference_field": "fba_shipment_reference",
  "timestamp_field": "shipped_at",
  "dimension_fields": ["marketplace"]
}
```

NetSuite, WMS, and Amazon mappings must use the same `flow_id`. Dimension fields are compared by
their configured order, allowing different source field names while preserving marketplace,
facility, subsidiary, or unit-of-measure separation. A reference should be configured for inbound
shipments; inventory snapshots can omit it when SKU plus dimensions are sufficient.

The service rejects mappings that use fields outside each connector's projected allow-list. Amazon
mappings can use only the canonical fields above.

## Findings

`POST /erp/fulfillment-reconciliation/runs` is capability-authenticated and returns transient:

- `missing_downstream_record`: present in NetSuite but absent from WMS, or present in WMS but absent
  from FBA.
- `missing_upstream_record`: present downstream with no matching upstream handoff.
- `quantity_mismatch`: both records exist but quantities exceed configured absolute and relative
  tolerances.
- `duplicate_handoff_key`: the same SKU/reference/dimension key appears more than once; quantities
  are aggregated for comparison but the duplicate requires review.

Every finding identifies the transition, flow, SKU, optional handoff reference, dimensions,
source labels, quantities, timestamps when available, deterministic explanation, and recommended
checks. Findings always require review and `persisted_remote_records` remains zero.

## Avoiding false gaps

The three systems represent different operational moments. These are not automatically errors:

- WMS shipped quantity can exceed FBA received quantity while freight is in transit or Amazon is
  checking in cartons.
- FBA total inventory includes states such as fulfillable, inbound, reserved, unfulfillable, and
  researching; it is not equivalent to WMS on-hand quantity.
- Partial receipts, cancellations, returns, case-pack conversion, units of measure, marketplace
  splits, bundles/kits, and cut-off times can create legitimate differences.
- A seller SKU may repeat across marketplaces and shipments. Duplicate checks therefore use the
  full reconciliation key and do not classify every repeated FBA SKU as an error.

Mappings and tolerances must encode the company's intended comparison, for example WMS shipped
quantity versus FBA inbound shipped quantity, or WMS available quantity versus FBA fulfillable
quantity. The engine performs no unit conversion or status inference.

## Acceptance scenarios

Before use with company data, verify at least:

1. Valid, expired, and revoked LWA credentials; role denial; marketplace removal; throttling;
   pagination; malformed payloads; and regional host enforcement.
2. Complete, missing, delayed, duplicated, partially received, cancelled, returned, bundled, and
   unit-of-measure handoffs across both transitions.
3. Multiple marketplaces and facilities using the same SKU without cross-scope contamination.
4. More than one page of inventory, shipments, and shipment items without dropped records.
5. Immediate failure after authorization revocation and proof that prior Amazon rows cannot be
   searched offline.
6. Static and runtime proof that only the exact LWA token POST and business-data GET operations are
   reachable.

Official references: [SP-API connection and LWA](https://developer-docs.amazon.com/sp-api/docs/connecting-to-the-selling-partner-api),
[regional endpoints](https://developer-docs.amazon.com/sp-api/docs/sp-api-endpoints),
[FBA Inventory API](https://developer-docs.amazon.com/sp-api/docs/fba-inventory-api), and
[Fulfillment Inbound API](https://developer-docs.amazon.com/sp-api/docs/fulfillment-inbound-api).
