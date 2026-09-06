# Fly91 Dashboard Database Context for NL2SQL

## Purpose

This is the retrieval context for generating safe, grain-correct PostgreSQL queries against the Fly91 dashboard database. It records the live schema, application-defined metric semantics, preferred analytical sources, verified and inferred joins, date/snapshot rules, bounded categorical observations, privacy restrictions, and known gaps.

The database is an application store assembled from several upstream systems. Similar-looking revenue, sold-seat, load-factor, and inventory fields are not interchangeable. Follow the source-selection and critical-instruction sections before writing SQL.

## Inspection metadata and limitations

| Item | Verified value |
|---|---|
| Inspection date | 2026-09-05 UTC |
| PostgreSQL | 15.15 (Homebrew) |
| Database | `fly91_dashboard` |
| Application environment | `APP_ENV=local`; deployment classification otherwise unconfirmed |
| Schemas inspected | `public` only; no other application schemas were visible |
| Git revision | `ee55f0cd654db80450c01fe66d6e943fd54ddd32` on `feature/group-intelligence` |
| Live objects | 123 tables; 0 views; 0 materialized views; 0 public functions |
| Detailed relations | 44 primary/relevant relations below; all 123 live tables are inventoried |
| Database comments | No live relation or column comments |
| Schema authority | Live catalog first; Python-embedded DDL and application SQL second. There is no standalone migration directory. |
| Database access | Read-only `psycopg2` transactions using only `.env` `DATABASE_URL`; `SET LOCAL statement_timeout`; catalog and bounded aggregate queries only |
| Excluded output | Credentials, raw records, PNR values, people/agency names, emails, phones, employee IDs, and other high-cardinality or identifying values |

`pg_class.reltuples` estimates are stale and are labelled **approximate**. A value of `-1` means the catalog had no usable estimate. Exact counts shown in the categorical catalogue came only from bounded `GROUP BY`/`COUNT` inspection and must not be confused with catalog estimates.

Many business dates are stored as `varchar`, not `date`. Lexical filtering is valid only for nonblank, zero-padded ISO `YYYY-MM-DD` values. Use guarded parsing if data quality is uncertain.

## Executive source-selection guide

| Analytical subject | Preferred source | Fallback / context | Grain and date | Required rule | Additivity / caveat |
|---|---|---|---|---|---|
| Displayed ticket volume | `public.pnr_flight` | None for canonical dashboard KPI | PNR-flight row; `flight_date` | `SUM(seat_count)` | Additive after selecting the requested departure window |
| Displayed booked revenue | `public.pnr_flight` | None for canonical dashboard KPI | PNR-flight row; `flight_date` | `SUM(total_amount)` | All-in application revenue; do not substitute RBD revenue |
| Displayed average yield | `public.pnr_flight` | None | Requested aggregate grain | `SUM(total_amount)/NULLIF(SUM(seat_count),0)` | Ratio; never average row yields |
| Booking/transaction timing | `public.pnr_flight` | — | `booked_date`, `time_of_booking` | Filter booking questions on `booked_date`, not `flight_date` | Strings; validate ISO date |
| Departure/RBD outcome | `public.rbd_daily` | `public.route_performance` for uploaded OTP only | flight-date-flight-sector-fare class | Aggregate fare classes before flight totals | Legacy/noncanonical for displayed sales revenue |
| Flown/realized revenue | Not governed | `rbd_daily.total_revenue` is used by Revenue Performance | Departure date | Label as RBD-derived, not canonical displayed booked revenue | Business owner must confirm “realized” semantics |
| Agency sales contribution | `public.rt_agency_daily` | `pnr_flight` only for inferred POS-text analysis | agency-day; `sale_date` | Sum FIT/group/total measures; apply hierarchy scope | Separate Sales Report pipeline |
| Sector sales contribution | `public.rt_agency_sector_daily` | Canonical PNR sector totals for dashboard sales | agency-sector-day | Pre-aggregate before hierarchy joins | Separate pipelines may not reconcile |
| Internal sales share | `rt_agency_daily` or `rt_agency_sector_daily` | `pnr_flight` by POS/sector | Same scoped denominator | entity measure / total airline measure in same source/window | Internal contribution only |
| External market share | Not available | `rt_targets.market_share_pct` is target metadata only | — | Require authoritative external denominator | Do not relabel internal share |
| Revenue targets | `public.rt_targets` | `public.revenue_perf_targets` for manual Revenue Performance planning | entity-month or planning key | Filter `entity_type`, year, month | Target, not actual |
| Ancillary revenue | `public.anc_agency_sector_daily` | Raw ancillary columns in `pnr_flight` for reconciliation | POS-sector-departure day | `ancillary_total` or sum seven `anc_*` buckets | `sale_date` is actually departure date |
| Forward bookings | `public.pnr_flight` | `public.fsi_sector_daily` for governed forward rollup | flight/sector departure date | Future `flight_date`; canonical sold/revenue formulas | Current upserted state, not historical snapshot |
| Remaining inventory | `public.pp_rbd_inventory_daily` | `public.pp_inventory` for Pricing Portal current detail | flight-date-sector-RBD | Preserve null as unknown; aggregate RBDs carefully | Current state; not observation history |
| Load factor, forward | `public.fsi_sector_daily` | `pp_rbd_inventory_daily` calculation | sector-day | `current_sold/capacity`; capacity must be nonnull | Stored as decimal 0–1 |
| Load factor, RBD month | `public.pp_rbd_inventory_monthly` | Aggregate daily table | sector-flight-month | `100*SUM(sold)/SUM(authorized)` over paired data | Stored as percent 0–100 |
| Schedule definitions | `public.pp_flight_schedule` | `public.pp_schedule_time` | effective schedule row | Apply date range and operation-frequency rules | Definitions, not proof flight operated |
| Flight operations / delay | active `dashboard_uploads` children | — | upload-specific rows | Restrict to active upload/version and deduplicate overlap | Uploaded, versioned extracts |
| Fare ladder | `public.pp_rbd_fares` | `pp_rbd_details` reference | sector-RBD-fare type | Apply validity when required | Reference price, not realized revenue |
| Point-in-time booking curve | `public.pp_fare_class_history` | `pnr_flight.booked_to_flight_gap_day` for booking-event curves | capture-flight-date-fare class | Compare equal days-to-departure; preserve missing captures | True capture history |
| Current competitor fares | `public.pp_competitor_fares` | — | logical fare identity/version | **Always `is_current = true`**; use `query_sector` for requested market | Price versions are nonadditive |
| Competitor fare history | `public.pp_competitor_fare_history` | — | capture-market-departure-airline-flight-site | Aggregate sites/flights explicitly; often `MIN(outbound_fare)` | True capture history |
| Group pipeline/conversion | `public.group_requests` | `group_metrics_*` for simple cached rollups | request; usually `requested_date` | Approved/converted is application rule `status_id=6` | Request routes are one-to-many |
| Group route demand | `public.group_request_routes` | — | request leg | Join to request; pre-aggregate legs before request totals | No unique request/leg constraint |
| Group advance receipts | `public.gr_advance_paid_pnrs` | — | parent/child PNR-flight-sale date | Aggregate restricted identifiers away | Sensitive; separate sync |
| Series commitments/utilization | Not available | — | — | Do not infer Series from `fare_type` or chart “series” | Missing agreements, commitments, releases |
| Offer definitions | `public.rm_offers` | `fsi_sector_daily` only for current context | offer version | Valid when `start_date <= date <= end_date`; latest `created_at` per flight/departure | Face value, not payout |
| Offer exposure/redemption/uplift | Not available | — | — | No exposure, redemption, offer-booking link, or control group | No causal claims |
| Crew duties | `public.crew_activity` | `crew_monthly_summary` for monthly KPI reads | staff-duty event | Filter `data_type`; suppress staff identity | Planned and actual coexist |
| Forecast/model output | No governed local forecast | `fsi_sector_daily` is deterministic heuristic; `pp_predicted_fares` is empty | — | Never call FSI a trained forecast | External proxy responses are not persisted |

## Business domains

### Agency master and hierarchy

The authoritative normalized path is `zones → territories → pos → agencies`, enforced by foreign keys. Current-state rows use `is_active`; no effective-dated normalized hierarchy history exists. Flat upload snapshot tables are supporting sources, not direct substitutes.

`pnr_flight.point_of_sale` does **not** carry an agency ID. Application scope logic normalizes text and compares it with agency name/code/channel values. That relationship is inferred, can be ambiguous, and must never be presented as a database-enforced join.

### Normal B2C sales and revenue

There is no dedicated `b2c_sales` table or governed B2C flag. The canonical displayed sales definitions are from `pnr_flight`: tickets `SUM(seat_count)`, revenue `SUM(total_amount)`, average yield revenue/tickets. `rbd_daily` and `sales_metrics_*` are legacy/supplementary aggregates with different formulas and grains.

Cancellation, no-show, and fare-class outcome fields exist in `rbd_daily`; raw PNR ancillary fee columns exist in `pnr_flight`. Refund lifecycle, payment events, passenger status history, and a governed booked-versus-flown revenue reconciliation are not present.

### Series business — not currently available

No verified Series agreement, commitment, contracted fare, consumption, release, utilization, or series-to-booking relation exists. `fare_type` has no Series value in the inspected data. Questions about Series revenue, committed seats, utilization, expiry, or release cannot be answered without a Series contract header, flight-level commitments, transaction history, and booking linkage.

### Group bookings and intelligence

`group_requests` is the request header; `group_request_routes` contains one-to-many legs. Cached group metrics summarize requests, while `gi_leg_displacement_cache` stores current algorithm output as JSON. No status-transition history, quote-version history, expiry event history, or definitive accepted-group-to-PNR linkage exists.

### Campaigns and revenue-management offers

There is no campaign model. `rm_offers` stores offer definitions/versions only. It has targeting arrays and an inclusive validity window, but no exposure, agency acknowledgement, participation, redemption, payout, offer code, booking linkage, or experiment/control assignment. Current code rejects overlapping validity windows for the same flight instance using transaction advisory locking plus `FOR UPDATE`; this enforcement is application-side, not a database exclusion constraint.

### Flight schedule, capacity, and operations

`pp_flight_schedule` and `pp_schedule_time` are schedule definitions. Flight instances are inferred from schedule dates/frequency or observed in PNR/inventory facts; there is no normalized flight-instance table. Uploaded operations tables are versioned under `dashboard_uploads`. They provide delay/OTP extracts, but not a complete aircraft, tail assignment, cancellation-event, or operated-flight ledger.

### Inventory, fares, and RBD

`pp_rbd_fares` is the published fare ladder. `pp_inventory` and `pp_rbd_inventory_daily` hold current flight/RBD state and are overwritten/upserted by refresh. `pp_fare_class_history` is the true point-in-time inventory capture source. Null sold/allocation in the RBD daily table means unknown, not zero.

### Competitor fares

Use `pp_competitor_fares` only with `is_current=true` for current pricing and filter requested market by `query_sector`. Use `pp_competitor_fare_history` for capture-time curves. Current versions and historical captures must not be summed together.

### Crew

`crew_activity` is the normalized duty fact; `crew_monthly_summary` is its monthly KPI rollup. `crew_roster_raw` is restricted raw staging. Staff IDs, names, remarks, and free text are restricted; NL2SQL should return aggregates by nonidentifying role/category/base unless specifically authorized.

### Reference and calendar data

The hierarchy, schedule, sector, and RBD tables provide limited reference data. There is no shared calendar/date dimension, holiday/festival/event calendar, airport master, timezone table, currency-rate table, or governed status lookup. Group currency was observed only as INR, but this is an observation rather than a permanent enum.

### Forecasting, scenario, and optimization inputs

`fsi_sector_daily` is a persisted deterministic revenue-gap heuristic, not a trained demand forecast. `gi_leg_displacement_cache` is a freshness cache of algorithm output. `pp_predicted_fares` exists live but is empty and has no application read/write path. External demand forecast/EMSR responses are proxied but not stored. Demand elasticity, spill/recapture, flight costs, fleet/slot constraints, intervention history, confidence intervals, and model-versioned local outputs are unavailable.

## Primary analytical relations

Column definitions below reflect the live PostgreSQL catalog. “Approximate rows” are stale catalog estimates unless stated exact.

### `public.zones`

- *Object type:* table
- *Functional relevance:* top level of normalized sales hierarchy
- *Use for:* zone scope, active hierarchy, target ownership
- *Do not use for:* effective-dated hierarchy history
- *Row grain:* one zone
- *Approximate row count:* unavailable (`reltuples=-1`)
- *Primary key:* `id`
- *Natural key:* unique `name`; unique nullable `external_id`
- *Foreign keys:* none
- *Important indexes:* PK; unique `name`; unique/partial unique `external_id`
- *Time semantics:* `last_synced_at` source refresh; `created_at` local creation
- *Snapshot behavior:* current state with soft-active flag
- *Data sensitivity:* `zone_gm_user_email` restricted
- *Known limitations:* no valid-from/to history

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | local zone key | PK | Join hierarchy by this key |
| `name` | character varying(100) | no | zone label | unique | Safe grouping; do not assume stable external ID |
| `zone_gm_user_email` | character varying(255) | yes | assigned manager identity | — | Restricted; never select by default |
| `external_id` | integer | yes | upstream key | unique when present | Preferred reconciliation key across syncs |
| `is_active` | boolean | no | current-state flag | default true | Filter true for current hierarchy |
| `last_synced_at` | timestamp with time zone | yes | upstream refresh | — | Freshness only |
| `created_at` | timestamp with time zone | no | local row creation | default now | Not hierarchy effective date |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `territories` | `territories.zone_id = zones.id` | one-to-many | physical FK | Aggregate territory facts before zone join if totals already contain zone rows |
| `rt_targets` | `rt_targets.zone_id = zones.id` | one-to-many | physical FK | Filter target month/entity type first |

### `public.territories`

- *Object type:* table
- *Functional relevance:* hierarchy level between zone and POS
- *Use for:* territory scope and rollups
- *Do not use for:* historical ownership as of a past sale
- *Row grain:* one territory within one zone
- *Approximate row count:* 46
- *Primary key:* `id`
- *Natural key:* `(zone_id,name)`; unique nullable `external_id`
- *Foreign keys:* `zone_id → zones.id ON DELETE CASCADE`
- *Important indexes:* `zone_id`; unique natural/external keys
- *Time semantics:* sync and creation timestamps
- *Snapshot behavior:* current state, soft-active
- *Data sensitivity:* ordinary reference data
- *Known limitations:* `is_aggregate` changes interpretation but has no check constraint

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | territory key | PK | Use for enforced fact joins |
| `zone_id` | integer | no | parent zone | FK | Join to `zones.id` |
| `name` | character varying(255) | no | territory label | unique within zone | Group only after parent disambiguation |
| `is_aggregate` | boolean | no | aggregate territory marker | default false | Avoid mixing aggregate and leaf rows without intent |
| `external_id` | integer | yes | upstream key | unique | Reconciliation key |
| `is_active` | boolean | no | current flag | default true | Filter true for current hierarchy |
| `last_synced_at` | timestamp with time zone | yes | source refresh | — | Freshness only |
| `created_at` | timestamp with time zone | no | local creation | default now | Not effective date |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `zones` | `territories.zone_id = zones.id` | many-to-one | physical FK | Safe dimension join |
| `pos` | `pos.territory_id = territories.id` | one-to-many | physical FK | Do not sum territory-level targets after expanding to POS |

### `public.pos`

- *Object type:* table
- *Functional relevance:* point-of-sale hierarchy level
- *Use for:* POS filtering, territory rollups
- *Do not use for:* direct canonical PNR join; `pnr_flight` has text only
- *Row grain:* one POS within one territory
- *Approximate row count:* 573
- *Primary key:* `id`
- *Natural key:* `(territory_id,name)`; unique nullable `external_id`
- *Foreign keys:* `territory_id → territories.id ON DELETE CASCADE`
- *Important indexes:* `territory_id`; unique natural/external keys
- *Time semantics:* sync and creation timestamps
- *Snapshot behavior:* current state, soft-active
- *Data sensitivity:* ordinary reference data
- *Known limitations:* no effective dates

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | POS key | PK | Use for enforced fact joins |
| `territory_id` | integer | no | parent territory | FK | Join to `territories.id` |
| `name` | character varying(255) | no | POS label | unique within territory | Do not equate automatically to `point_of_sale` text |
| `external_id` | integer | yes | upstream key | unique | Reconciliation key |
| `is_active` | boolean | no | current flag | default true | Current hierarchy filter |
| `last_synced_at` | timestamp with time zone | yes | source refresh | — | Freshness only |
| `created_at` | timestamp with time zone | no | local creation | default now | Not effective date |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `territories` | `pos.territory_id = territories.id` | many-to-one | physical FK | Safe |
| `agencies` | `agencies.pos_id = pos.id` | one-to-many | physical FK | Aggregate agency facts before joining a POS-level measure |

### `public.agencies`

- *Object type:* table
- *Functional relevance:* leaf sales hierarchy
- *Use for:* agency lookup, scope expansion, upstream reconciliation
- *Do not use for:* exposing names/contact details or assuming `code` is unique
- *Row grain:* one agency within one POS
- *Approximate row count:* 3,009
- *Primary key:* `id`
- *Natural key:* `(pos_id,name)`; unique nullable `external_id`
- *Foreign keys:* `pos_id → pos.id ON DELETE CASCADE`
- *Important indexes:* `pos_id`; unique natural/external keys
- *Time semantics:* sync and creation timestamps
- *Snapshot behavior:* current state, soft-active
- *Data sensitivity:* name/code are business-sensitive; email/mobile are restricted PII/contact data
- *Known limitations:* `code` is not unique; `channel` is mostly blank and sometimes agency-like, not a governed channel dimension

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | agency key | PK | Use where facts store `agency_id`/external mapping |
| `pos_id` | integer | no | parent POS | FK | Join to `pos.id` |
| `name` | character varying(255) | no | agency name | unique within POS | Restricted output; may participate in inferred normalized match |
| `channel` | character varying(50) | no | source channel text | default blank | Not a certified channel enum |
| `code` | character varying(50) | yes | agency/BSP code | not unique | Prefer exact code only when source semantics match |
| `email` | character varying(255) | yes | agency contact | — | Restricted; never select/sample |
| `mobile` | character varying(50) | yes | agency contact | — | Restricted; never select/sample |
| `external_id` | integer | yes | upstream agency key | unique | Preferred Group Quote reconciliation key |
| `is_active` | boolean | no | current flag | default true | Filter true for current picker/scope |
| `last_synced_at` | timestamp with time zone | yes | source refresh | — | Freshness only |
| `created_at` | timestamp with time zone | no | local creation | default now | Not effective date |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `pos` | `agencies.pos_id = pos.id` | many-to-one | physical FK | Safe |
| `group_requests` | `group_requests.agency_external_id = agencies.external_id` | many-to-one | application reconciliation, no FK | External IDs may be null; retain unmatched rows |
| `pnr_flight` | normalized `pnr_flight.point_of_sale` matched against agency name/code/channel | many-to-zero/one | inferred application scope logic | Match only after normalization and ambiguity rejection; never raw many-to-many join |

### `public.pnr_flight`

- *Object type:* table
- *Functional relevance:* canonical raw sales fact for displayed revenue/tickets/yield
- *Use for:* canonical sales KPIs, forward bookings, booking curves, ancillary reconciliation
- *Do not use for:* passenger/PNR extraction, historical snapshots of changing bookings, external market share
- *Row grain:* one PNR-flight-sector row; fare class is not part of the PK
- *Approximate row count:* 435,186
- *Primary key:* `(pnr,flight_date,flight_number,sector)`
- *Natural key:* same as PK
- *Foreign keys:* `sync_id → api_sync_log.id ON DELETE CASCADE`
- *Important indexes:* `flight_date`, `sector`, `point_of_sale`, PK
- *Time semantics:* `flight_date` is departure date; `booked_date`/`time_of_booking` are booking time; all are strings
- *Snapshot behavior:* upserted current record, not capture history
- *Data sensitivity:* PNR is restricted; revenue, route, fare, POS and future travel are confidential
- *Known limitations:* text dates; no agency FK; cancellation/refund lifecycle not modeled; current-state upserts cannot reconstruct every historical decision

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `pnr` | character varying(30) | no | booking locator | PK component | Restricted; count/aggregate only |
| `flight_date` | character varying(10) | no | departure date | PK component | Filter canonical sales by departure date; validate ISO |
| `flight_number` | character varying(20) | no | flight identifier | PK component | Normalize prefixes before cross-source joins |
| `sector` | character varying(30) | no | route/sector | PK component | Preferred route dimension |
| `fare_class` | character varying(10) | yes | booked class | observed low-cardinality | Group safely; not part of key |
| `seat_count` | integer | yes | seats sold | default 0 | Canonical tickets measure |
| `booked_date` | character varying(10) | yes | booking date | — | Use for transaction-time questions |
| `time_of_booking` | character varying(20) | yes | booking time text | — | Parse cautiously; no timezone guarantee |
| `booked_to_flight_gap_day` | integer | yes | days before departure | default 0 | DTD booking-curve axis |
| `fare_type` | character varying(50) | yes | fare category | observed values below | Not a Series marker |
| `point_of_sale` | character varying(100) | yes | agency/channel text | no FK | Sensitive high-cardinality; inferred normalized hierarchy mapping only |
| `net_yield` | double precision | yes | source net-yield value | default 0 | Do not average for canonical yield |
| `total_amount` | double precision | yes | all-in revenue | default 0 | Canonical revenue numerator |
| `cancellation_fee` | double precision | yes | cancellation ancillary amount | default 0 | Ancillary component |
| `reissue_fee` | double precision | yes | reissue ancillary amount | default 0 | Ancillary component |
| `convenience` | double precision | yes | convenience fee | default 0 | Ancillary component |
| `xbag_3kg` | double precision | yes | baggage amount source field | default 0 | Dedicated tracker groups into baggage |
| `xbag_5kg` | double precision | yes | baggage amount | default 0 | Same |
| `xbag_10kg` | double precision | yes | baggage amount | default 0 | Same |
| `xbag_15kg` | double precision | yes | baggage amount | default 0 | Same |
| `sbv1` | double precision | yes | ancillary source amount | default 0 | Meals bucket |
| `sbv2` | double precision | yes | ancillary source amount | default 0 | Meals bucket |
| `sbv4` | double precision | yes | ancillary source amount | default 0 | Seat bucket |
| `sbv5` | double precision | yes | ancillary source amount | default 0 | Meals bucket |
| `smart_ticket` | double precision | yes | smart-ticket amount | default 0 | Ancillary bucket |
| `sync_id` | integer | yes | ingest run | FK | Audit join only |
| `exit_seat` | double precision | yes | exit-seat amount | default 0 | Seat bucket |
| `premium_seat` | double precision | yes | premium-seat amount | default 0 | Seat bucket |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `api_sync_log` | `pnr_flight.sync_id = api_sync_log.id` | many-to-one | physical FK | Audit only |
| `rbd_daily` | date + normalized flight + sector, optionally fare class | many-to-many at raw grain | inferred application use, no FK | Pre-aggregate each source to flight/sector/date first |
| `pp_rbd_inventory_daily` | normalized flight + sector + typed `flight_date` | many-to-many at raw grain | inferred application use | Pre-aggregate PNR to flight-day and inventory across RBD |
| hierarchy | normalized `point_of_sale` text | uncertain | inferred application mapping | Never direct-join without deduplicated mapping |

### `public.rbd_daily`

- *Object type:* table
- *Functional relevance:* legacy fare-class departure outcomes
- *Use for:* cancellations, no-shows, departed LF, RBD-based Revenue Performance
- *Do not use for:* canonical displayed revenue/tickets/yield
- *Row grain:* flight-date-flight-sector-fare class
- *Approximate row count:* 77,587
- *Primary key:* `(flight_date,flight_number,sector,fare_class)`
- *Natural key:* same
- *Foreign keys:* `sync_id → api_sync_log.id ON DELETE CASCADE`
- *Important indexes:* `flight_date`, `sector`, PK
- *Time semantics:* departure date as `varchar(10)`
- *Snapshot behavior:* upserted current aggregate
- *Data sensitivity:* confidential commercial/flight data
- *Known limitations:* legacy formulas differ from canonical PNR metrics; text dates; no POS attribution

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `flight_date` | character varying(10) | no | departure date | PK | Validate ISO |
| `flight_number` | character varying(20) | no | flight | PK | Normalize for cross-source joins |
| `sector` | character varying(30) | no | route | PK | — |
| `fare_class` | character varying(10) | no | RBD class | PK | Aggregate before flight totals |
| `booked` | integer | yes | upstream booked count | default 0 | Legacy measure |
| `cancellation` | integer | yes | cancellation count | default 0 | Additive |
| `net` | integer | yes | net sold/departed count | default 0 | Revenue Performance sold pax |
| `no_show_count` | integer | yes | no-show count | default 0 | Additive |
| `total_revenue` | double precision | yes | upstream RBD revenue | default 0 | Noncanonical for displayed sales |
| `yield_value` | double precision | yes | upstream yield | default 0 | Do not average blindly |
| `departed_load_factor` | double precision | yes | upstream departed LF | default 0 | Confirm percent scale before use |
| `quarter` | character varying(10) | yes | reporting quarter | observed Q1–Q4 | Prefer deriving from valid date |
| `month` | character varying(10) | yes | month abbreviation | observed Jan–Dec | Prefer deriving from valid date |
| `day` | character varying(10) | yes | weekday abbreviation | observed Mon–Sun | Prefer deriving from valid date |
| `sync_id` | integer | yes | ingest run | FK | Audit only |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `api_sync_log` | `sync_id=id` | many-to-one | physical FK | Audit only |
| `pnr_flight` | date + flight + sector | many-to-many raw | inferred | Pre-aggregate both to same flight-day grain |

### `public.sales_metrics_summary`

- *Object type:* table
- *Functional relevance:* legacy/current cached multidimensional summary
- *Use for:* legacy payload fields and JSON blobs when code explicitly requests them
- *Do not use for:* authoritative canonical sales KPIs or arbitrary cross-dimension sums
- *Row grain:* one `(dimension_type,dimension_value,sub_type)` bucket
- *Approximate row count:* 208
- *Primary key:* `(dimension_type,dimension_value,sub_type)`
- *Natural key:* same
- *Foreign keys:* `sync_id → api_sync_log.id ON DELETE CASCADE`
- *Important indexes:* PK
- *Time semantics:* no date; full/current rebuild scope depends on sync
- *Snapshot behavior:* overwrite/upsert cache
- *Data sensitivity:* `extra` can contain restricted/high-cardinality blobs
- *Known limitations:* mixed measures/grains; some fields derive from RBD, some PNR; `extra` is opaque

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `dimension_type` | character varying(50) | no | metric family | observed catalogue below | Always filter |
| `dimension_value` | character varying(200) | no | member/key | default blank | Meaning depends on type |
| `sub_type` | character varying(200) | no | optional subcategory | observed blank only | Include in key |
| `revenue` | double precision | yes | cached revenue | default 0 | Formula varies by dimension |
| `tickets` | integer | yes | cached tickets | default 0 | Formula varies |
| `booked` | integer | yes | cached booked | default 0 | Legacy semantics |
| `cancellations` | integer | yes | cached cancellations | default 0 | Often RBD-derived |
| `no_show` | integer | yes | cached no-show | default 0 | Often RBD-derived |
| `avg_yield` | double precision | yes | cached ratio | default 0 | Never sum/average across rows |
| `avg_lf` | double precision | yes | cached LF | default 0 | Weight correctly |
| `ancillary_total` | double precision | yes | cached ancillary | default 0 | Confirm component set |
| `count` | integer | yes | generic count | default 0 | Meaning varies by type |
| `extra` | jsonb | yes | type-specific blob | — | Restricted; avoid generic extraction |
| `sync_id` | integer | yes | ingest run | FK | Audit only |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `api_sync_log` | `sync_id=id` | many-to-one | physical FK | Audit only |
| other facts | none certified | — | — | Do not join cached measures to raw facts |

### `public.sales_metrics_daily`

- *Object type:* table
- *Functional relevance:* cached legacy daily multidimensional sales series
- *Use for:* known dimension-specific legacy time series
- *Do not use for:* canonical totals when raw PNR is available
- *Row grain:* date-dimension type-dimension value
- *Approximate row count:* 48,473
- *Primary key:* `(date,dimension_type,dimension_value)`
- *Natural key:* same
- *Foreign keys:* `sync_id → api_sync_log.id ON DELETE CASCADE`
- *Important indexes:* PK only
- *Time semantics:* `date` is departure/reporting date text
- *Snapshot behavior:* upserted cache
- *Data sensitivity:* dimensions may contain commercial labels
- *Known limitations:* mixed metric families; `lf_sum/lf_count` require weighted recombination

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `date` | character varying(10) | no | reporting/departure date | PK | Validate ISO |
| `dimension_type` | character varying(50) | no | metric family | observed catalogue below | Always filter |
| `dimension_value` | character varying(200) | no | member | default blank | Meaning depends on type |
| `revenue` | double precision | yes | cached amount | default 0 | Sum only within one compatible type |
| `tickets` | integer | yes | cached tickets | default 0 | Same |
| `booked` | integer | yes | cached booked | default 0 | Legacy |
| `lf_sum` | double precision | yes | LF numerator accumulator | default 0 | Combine as `SUM(lf_sum)/SUM(lf_count)` |
| `lf_count` | integer | yes | LF weight | default 0 | Never average row LF |
| `count` | integer | yes | generic count | default 0 | Type-specific |
| `sync_id` | integer | yes | ingest run | FK | Audit only |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `api_sync_log` | `sync_id=id` | many-to-one | physical FK | Audit only |
| summary/raw facts | none certified | — | application rebuild only | Do not join and sum measures across grains |

### `public.rt_agency_daily`

- *Object type:* table
- *Functional relevance:* Revenue Compass agency-day actuals from Sales Report API
- *Use for:* FIT/group/total pax and revenue by agency or hierarchy
- *Do not use for:* canonical PNR dashboard totals, flight-level analysis, capacity/LF
- *Row grain:* agency key per sale date
- *Approximate row count:* 8,254
- *Primary key:* `id`
- *Natural key:* unique `(agency_key,sale_date)`
- *Foreign keys:* nullable `zone_id`, `territory_id`, `pos_id` to normalized hierarchy
- *Important indexes:* sale date; year/month with agency/POS/territory/zone; unique grain
- *Time semantics:* `sale_date` is typed reporting/sale date
- *Snapshot behavior:* current daily aggregate upserted by source
- *Data sensitivity:* agency key/name and commercial measures are restricted
- *Known limitations:* hierarchy IDs are denormalized at sync time; unmapped rows have null IDs and disappear from scoped views

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | surrogate row key | PK | Prefer natural key for reconciliation |
| `agency_key` | character varying(50) | no | upstream/BSP agency key | unique with date | Restrict output; exact join to key-partner/target keys |
| `agency_name` | character varying(255) | yes | agency label | — | Restricted; do not return/list |
| `zone_id` | integer | yes | resolved zone | FK | Scope filter; null means unmapped |
| `territory_id` | integer | yes | resolved territory | FK | Scope filter |
| `pos_id` | integer | yes | resolved POS | FK | Scope filter |
| `sale_date` | date | no | reporting date | natural key | Primary time filter |
| `year` | smallint | no | denormalized year | — | Prefer `sale_date` for ranges |
| `month` | smallint | no | denormalized month | 1–12 by application | Use with year |
| `fit_sold` | integer | no | non-group/FIT pax | default 0 | Additive |
| `group_sold` | integer | no | group pax | default 0 | Additive |
| `total_sold` | integer | no | total pax | default 0 | Use source total; do not also add components |
| `fit_revenue` | bigint | no | FIT revenue | default 0 | Additive, source currency |
| `group_revenue` | bigint | no | group revenue | default 0 | Additive |
| `total_revenue` | bigint | no | total revenue | default 0 | Use source total; avoid double counting |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `zones` / `territories` / `pos` | stored IDs to `id` | many-to-one | physical FKs | Safe dimensions; retain null/unmapped separately |
| `rt_key_partners` | `agency_key` | many-to-one | application join; key-partner PK | Filter active/ownership rules before reporting |
| `rt_targets` | agency target `entity_type='agency' AND entity_key=agency_key`, or zone target by `zone_id` | many-to-one per month | application query | Filter target year/month before joining |

### `public.rt_agency_sector_daily`

- *Object type:* table
- *Functional relevance:* Revenue Compass agency-sector-day actuals
- *Use for:* scoped sector contribution and route health
- *Do not use for:* flight/RBD detail or PNR canonical KPI reconciliation
- *Row grain:* agency key-sector-sale date
- *Approximate row count:* 40,254
- *Primary key:* `id`
- *Natural key:* unique `(agency_key,sector,sale_date)`
- *Foreign keys:* nullable hierarchy IDs to `zones`, `territories`, `pos`
- *Important indexes:* agency/month; zone/month; sale date; unique grain
- *Time semantics:* typed `sale_date`
- *Snapshot behavior:* current daily aggregate
- *Data sensitivity:* agency key and commercial measures restricted
- *Known limitations:* no flight number; hierarchy frozen at sync time

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | surrogate key | PK | — |
| `agency_key` | character varying(50) | no | upstream agency key | natural key | Restrict output |
| `zone_id` | integer | yes | resolved zone | FK | Scope filter |
| `territory_id` | integer | yes | resolved territory | FK | Scope filter |
| `pos_id` | integer | yes | resolved POS | FK | Scope filter |
| `sector` | character varying(20) | no | route | natural key | Sector contribution dimension |
| `origin` | character varying(10) | yes | origin code | — | Prefer sector when populated consistently |
| `destination` | character varying(10) | yes | destination code | — | — |
| `sale_date` | date | no | reporting date | natural key | Primary date |
| `year` | smallint | no | denormalized year | — | Use with month |
| `month` | smallint | no | denormalized month | — | Use with year |
| `fit_sold` | integer | no | FIT pax | default 0 | Additive |
| `group_sold` | integer | no | group pax | default 0 | Additive |
| `total_sold` | integer | no | total pax | default 0 | Do not add to components |
| `fit_revenue` | bigint | no | FIT revenue | default 0 | Additive |
| `group_revenue` | bigint | no | group revenue | default 0 | Additive |
| `total_revenue` | bigint | no | total revenue | default 0 | Do not add to components |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| hierarchy | stored IDs to dimension PKs | many-to-one | physical FKs | Safe |
| `rt_agency_daily` | `agency_key,sale_date` | many-sector-to-one agency-day | inferred/application | Pre-aggregate sector facts before comparing to agency total |

### `public.rt_targets`

- *Object type:* table
- *Functional relevance:* monthly Revenue Compass targets
- *Use for:* agency/key-partner and zone target pax/revenue
- *Do not use for:* actuals or observed external market share
- *Row grain:* entity type-key-year-month
- *Approximate row count:* catalog unavailable; exact bounded inspection saw 11 rows
- *Primary key:* `id`
- *Natural key:* unique `(entity_type,entity_key,year,month)`
- *Foreign keys:* nullable `zone_id → zones.id`
- *Important indexes:* year/month; unique natural key
- *Time semantics:* business reporting month; `synced_at` refresh
- *Snapshot behavior:* current target per entity/month
- *Data sensitivity:* entity labels/keys restricted when agency-level
- *Known limitations:* no check constraint for entity type; market-share denominator/definition is not documented

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | surrogate key | PK | — |
| `entity_type` | character varying(20) | no | target level | observed `agency`, `zone` | Always filter |
| `entity_key` | character varying(50) | no | agency key or zone key | natural key | Semantics depend on type |
| `entity_name` | character varying(255) | yes | display label | — | Do not output agency names |
| `zone_id` | integer | yes | owning/resolved zone | FK | Join/scope |
| `year` | smallint | no | target year | natural key | — |
| `month` | smallint | no | target month | natural key | — |
| `pax` | integer | yes | pax target | — | Target, not actual |
| `revenue` | bigint | yes | revenue target | — | Target, not actual |
| `market_share_pct` | numeric(5,2) | yes | source target metadata | — | Do not call observed market share |
| `mom_variance_pct` | numeric(5,2) | yes | source target metadata | — | Definition needs owner confirmation |
| `synced_at` | timestamp with time zone | yes | refresh timestamp | default now | Freshness |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `zones` | `zone_id=zones.id` | many-to-one | physical FK | Safe |
| `rt_agency_daily` | type/key plus matching year/month | target-to-many actual days | application join | Aggregate actuals to entity-month first |

### `public.rt_key_partners`

- *Object type:* table
- *Functional relevance:* curated key-partner classification
- *Use for:* key-partner grouping and network exceptions
- *Do not use for:* full agency master or exposure tracking
- *Row grain:* agency key
- *Approximate row count:* 14
- *Primary key:* `agency_key`
- *Natural key:* same
- *Foreign keys:* nullable `zone_id → zones.id`
- *Important indexes:* PK
- *Time semantics:* none
- *Snapshot behavior:* seeded/current configuration
- *Data sensitivity:* all names and agency keys restricted
- *Known limitations:* application seed; no history; brand grouping is business configuration

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `agency_key` | character varying(50) | no | agency/BSP key | PK | Join internally; suppress output |
| `agency_name` | character varying(255) | no | legal/source name | — | Restricted |
| `display_name` | character varying(255) | yes | display brand | — | Restricted |
| `zone_id` | integer | yes | target owner zone | FK | Ownership, not necessarily every sale's geography |
| `is_active` | boolean | yes | active config | default true | Filter true |
| `always_show` | boolean | no | network exception | default false | Apply application display semantics |
| `partner_group` | character varying(50) | yes | alias rollup key | — | Restricted brand-like category; do not enumerate |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `rt_agency_daily` | `agency_key` | one-to-many | application SQL | Aggregate actuals by partner/group once |
| `zones` | `zone_id=id` | many-to-one | physical FK | Safe |

### `public.anc_agency_sector_daily`

- *Object type:* table
- *Functional relevance:* dedicated ancillary tracker rollup from PNR
- *Use for:* ancillary revenue/counts by POS, sector, hierarchy, departure day
- *Do not use for:* booking-date sales or causal product uptake
- *Row grain:* POS text-sector-departure date
- *Approximate row count:* 42,693
- *Primary key:* `id`
- *Natural key:* unique `(pos_value,sector,sale_date)`
- *Foreign keys:* nullable hierarchy IDs to zone/territory/POS
- *Important indexes:* date; year/month with hierarchy/POS text/sector; unique grain
- *Time semantics:* `sale_date` is actually `pnr_flight.flight_date` (departure date)
- *Snapshot behavior:* rebuilt rollup for sync windows
- *Data sensitivity:* POS/agency labels and revenue are restricted
- *Known limitations:* unresolved POS has null hierarchy; category transaction count counts source rows with positive category, not redemptions

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | surrogate key | PK | — |
| `pos_value` | character varying(100) | no | source POS text | natural key | Restricted; not normalized agency FK |
| `agency_name` | character varying(255) | yes | resolved/fallback label | — | Restricted; never list |
| `zone_id` | integer | yes | resolved zone | FK | Null = unmapped |
| `territory_id` | integer | yes | resolved territory | FK | Scope |
| `pos_id` | integer | yes | resolved POS | FK | Scope |
| `sector` | character varying(30) | no | route | natural key | — |
| `sale_date` | date | no | departure/reporting date | natural key | Not booking/payment date |
| `year` | smallint | no | denormalized year | — | — |
| `month` | smallint | no | denormalized month | — | — |
| `net_ticket_revenue` | double precision | no | `SUM(net_yield*seat_count)` | default 0 | Different from canonical `SUM(total_amount)` |
| `pax` | integer | no | `SUM(seat_count)` | default 0 | Additive |
| `anc_cancellation` | double precision | no | cancellation fee bucket | default 0 | Additive |
| `anc_reissuance` | double precision | no | reissue bucket | default 0 | Additive |
| `anc_baggage` | double precision | no | four baggage source fields | default 0 | Additive |
| `anc_meals` | double precision | no | `sbv1+sbv2+sbv5` | default 0 | Additive |
| `anc_seat` | double precision | no | `sbv4+exit_seat+premium_seat` | default 0 | Additive |
| `anc_convenience` | double precision | no | convenience bucket | default 0 | Additive |
| `anc_smart_ticket` | double precision | no | smart-ticket bucket | default 0 | Additive |
| `cnt_cancellation` | integer | no | positive-source-row count | default 0 | Not distinct customers/bookings |
| `cnt_reissuance` | integer | no | positive-source-row count | default 0 | Same |
| `cnt_baggage` | integer | no | positive-source-row count | default 0 | Same |
| `cnt_meals` | integer | no | positive-source-row count | default 0 | Same |
| `cnt_seat` | integer | no | positive-source-row count | default 0 | Same |
| `cnt_convenience` | integer | no | positive-source-row count | default 0 | Same |
| `cnt_smart_ticket` | integer | no | positive-source-row count | default 0 | Same |
| `ancillary_total` | double precision | no | sum of seven `anc_*` buckets | default 0 | Additive; do not add buckets again |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| hierarchy | stored IDs to PKs | many-to-one | physical FKs | Safe; retain null unmapped |
| `pnr_flight` | POS text + sector + `flight_date=sale_date` | rollup-to-many | lineage, not FK | Do not join for totals; reconcile in separate aggregates |

### `public.group_requests`

- *Object type:* table
- *Functional relevance:* Group Quote request header/current state
- *Use for:* group pipeline, current outcome/status, quoted/approved values, request-level conversion
- *Do not use for:* status-transition history, quote history, passenger/contact extraction
- *Row grain:* one upstream group request
- *Approximate row count:* 18,188
- *Primary key:* `external_id`
- *Natural key:* upstream `external_id`
- *Foreign keys:* `sync_id → group_sync_log.id ON DELETE SET NULL`; hierarchy/agency columns are not FKs
- *Important indexes:* requested/travel date, status, POS name, agency code, territory, zone
- *Time semantics:* request/travel/return values are strings; `last_synced_at` is ingest time
- *Snapshot behavior:* current request state upsert; old status/quote versions not retained
- *Data sensitivity:* request codes, agency/contact fields, requested_by and raw JSON are restricted
- *Known limitations:* blank status is common; denormalized hierarchy; approved amount may fall back to quoted in application metrics

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `external_id` | bigint | no | upstream request key | PK | Use for routes |
| `request_code` | character varying(100) | yes | business request code | — | Restricted/high-cardinality |
| `status_id` | integer | yes | current upstream status | observed values below | Use code-backed conversion rule |
| `status_label` | character varying(100) | yes | current status label | observed values below | Exact-case filters |
| `requested_at` | character varying(25) | yes | request timestamp text | — | Parse cautiously |
| `requested_date` | character varying(10) | yes | request date | — | Default pipeline reporting date |
| `travel_date` | character varying(10) | yes | header travel date | — | Prefer routes for multi-leg travel |
| `return_date` | character varying(10) | yes | return date | — | — |
| `pax_count` | integer | yes | request pax | default 0 | Header pax; do not sum after route expansion |
| `total_quoted` | double precision | yes | total quote value | default 0 | Request-level money |
| `total_approved` | double precision | yes | approved value | default 0 | Current snapshot |
| `avg_ticket_value` | double precision | yes | cached average ticket value | default 0 | Ratio; do not sum |
| `lead_time_days` | integer | yes | request-to-travel lead | default 0 | Cached, nonadditive |
| `trip_type` | character varying(30) | yes | journey type | observed below | Exact values |
| `currency` | character varying(10) | yes | currency code | observed INR only | Filter/confirm before summing money |
| `agency_external_id` | bigint | yes | upstream agency ID | no FK | Inferred join to `agencies.external_id` |
| `agency_code` | character varying(50) | yes | agency code | no FK | Restricted |
| `agency_name` | character varying(255) | yes | agency name | — | Restricted |
| `pos_id` | integer | yes | denormalized POS ID | no FK | Application-resolved; validate existence |
| `pos_name` | character varying(255) | yes | POS snapshot | — | Restricted/scoping text |
| `territory_id` | integer | yes | denormalized territory | no FK | Not enforced |
| `territory_name` | character varying(255) | yes | snapshot label | — | Do not prefer over normalized dimension |
| `zone_id` | integer | yes | denormalized zone | no FK | Not enforced |
| `zone_name` | character varying(100) | yes | snapshot label | — | — |
| `contact_email` | character varying(255) | yes | request contact | — | Restricted PII; never select |
| `contact_mobile` | character varying(50) | yes | request contact | — | Restricted PII; never select |
| `requested_by` | character varying(255) | yes | requester identity | — | Restricted |
| `raw` | jsonb | yes | full upstream record | — | Restricted; never generic-extract |
| `sync_id` | integer | yes | sync audit | FK | Audit only |
| `last_synced_at` | timestamp with time zone | yes | latest refresh | — | Current-state freshness |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `group_request_routes` | `routes.request_ext_id=external_id` | one-to-many | physical FK | Pre-aggregate routes before request KPI sums |
| `group_sync_log` | `sync_id=id` | many-to-one | physical FK | Audit only |
| `agencies` | `agency_external_id=agencies.external_id` | many-to-one expected | application reconciliation, no FK | Left join; keep unmapped |
| `gi_leg_displacement_cache` | request ID + leg index | one-to-many | inferred; cache key is narrower integer | Guard bigint-to-integer range; not authoritative history |

### `public.group_request_routes`

- *Object type:* table
- *Functional relevance:* group request route legs
- *Use for:* sector/flight/travel-date demand and per-leg quoted/approved fare
- *Do not use for:* request counts without deduplicating request
- *Row grain:* route leg row
- *Approximate row count:* 21,916
- *Primary key:* `id`
- *Natural key:* intended `(request_ext_id,leg_index)`, not enforced
- *Foreign keys:* `request_ext_id → group_requests.external_id ON DELETE CASCADE`
- *Important indexes:* request ID; sector
- *Time semantics:* `travel_date` string
- *Snapshot behavior:* routes replaced for touched requests
- *Data sensitivity:* flight/route/fare commercial data
- *Known limitations:* duplicate leg indices are possible; null sector/flight/date

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | surrogate key | PK | — |
| `request_ext_id` | bigint | no | parent request | FK | Join header |
| `leg_index` | integer | no | leg order | default 0 | Not uniquely constrained |
| `origin` | character varying(10) | yes | origin | — | — |
| `destination` | character varying(10) | yes | destination | — | — |
| `sector` | character varying(30) | yes | route | — | Prefer for route filters |
| `travel_date` | character varying(10) | yes | leg departure date | — | Validate ISO |
| `flight_number` | character varying(20) | yes | intended flight | — | Normalize for inventory join |
| `fare_class` | character varying(10) | yes | quoted class | — | — |
| `seats` | integer | yes | leg seats | default 0 | Do not sum round-trip legs as request pax |
| `quoted_fare` | double precision | yes | per-leg quoted fare | default 0 | Confirm per-seat semantics by use case |
| `approved_fare` | double precision | yes | per-leg approved fare | default 0 | Current snapshot |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `group_requests` | `request_ext_id=external_id` | many-to-one | physical FK | Safe dimension join; request measures repeat |
| `pnr_flight` / inventory | normalized flight + sector + travel date | uncertain many-to-many | application analysis only | Pre-aggregate source to flight instance; no accepted-booking linkage |

### `public.group_metrics_summary`

- *Object type:* table
- *Functional relevance:* cached all-history request summaries
- *Use for:* simple overview/hierarchy group KPIs when cache matches question
- *Do not use for:* custom date ranges, leg detail, current status history
- *Row grain:* dimension type-value
- *Approximate row count:* 726
- *Primary key:* `(dimension_type,dimension_value)`
- *Natural key:* same
- *Foreign keys:* none; `sync_id` is not constrained
- *Important indexes:* PK
- *Time semantics:* no date
- *Snapshot behavior:* full rebuild after group sync
- *Data sensitivity:* `dimension_value` may identify agencies; `extra` may contain restricted data
- *Known limitations:* mixed dimensions; main page often queries raw requests directly

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `dimension_type` | character varying(50) | no | grouping level | `overview`,`zone`,`territory`,`pos`,`agency` observed | Always filter |
| `dimension_value` | character varying(255) | no | group member | default blank | Suppress agency values |
| `quote_count` | integer | yes | requests | default 0 | Additive only across disjoint members |
| `approved_count` | integer | yes | approved requests | default 0 | Status rule `status_id=6` |
| `total_quoted` | double precision | yes | quoted value | default 0 | Additive |
| `total_approved` | double precision | yes | approved value | default 0 | Additive |
| `total_pax` | integer | yes | pax | default 0 | May repeat across hierarchy dimensions |
| `avg_group_size` | double precision | yes | pax/request | default 0 | Weighted recomputation required |
| `approval_rate` | double precision | yes | approved/request rate | default 0 | Do not average |
| `avg_lead_time` | double precision | yes | mean lead days | default 0 | Nonadditive |
| `avg_ticket_val` | double precision | yes | approved value/approved pax | default 0 | Nonadditive |
| `extra` | jsonb | yes | type-specific data | — | Restricted/opaque |
| `sync_id` | integer | yes | last rebuild run | no FK | Audit hint only |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| other facts | none certified | — | cache lineage only | Do not join cached measures to requests |

### `public.group_metrics_daily`

- *Object type:* table
- *Functional relevance:* cached group request/travel daily series
- *Use for:* simple daily request or travel metrics
- *Do not use for:* both date lenses in the same sum
- *Row grain:* date-dimension type-`ALL`
- *Approximate row count:* 968
- *Primary key:* `(date,dimension_type,dimension_value)`
- *Natural key:* same
- *Foreign keys:* none
- *Important indexes:* PK
- *Time semantics:* `dimension_type='requested'` uses request date; `'travel'` uses travel date
- *Snapshot behavior:* full rebuild after sync
- *Data sensitivity:* commercial group values
- *Known limitations:* date is string; same request can appear in both lenses

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `date` | character varying(10) | no | reporting date | PK | Validate ISO |
| `dimension_type` | character varying(50) | no | date lens | observed `requested`,`travel` | Always filter one lens |
| `dimension_value` | character varying(255) | no | grouping | observed `ALL` | Filter `ALL` |
| `quote_count` | integer | yes | request count | default 0 | Additive within one lens |
| `approved_count` | integer | yes | approved count | default 0 | Additive within one lens |
| `total_quoted` | double precision | yes | quoted value | default 0 | Additive within one lens |
| `total_approved` | double precision | yes | approved value | default 0 | Additive within one lens |
| `total_pax` | integer | yes | pax | default 0 | Additive within one lens |
| `sync_id` | integer | yes | rebuild run | no FK | Audit hint |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| other facts | none certified | — | cache lineage | Reconcile by separate aggregate, not join |

### `public.gr_advance_paid_pnrs`

- *Object type:* table
- *Functional relevance:* advance-paid group PNR receipts
- *Use for:* aggregate advance revenue/pax by sale date, flight date, agency scope
- *Do not use for:* PNR-level output or inferred final booking conversion
- *Row grain:* parent PNR-child PNR-sector-flight-sale date
- *Approximate row count:* 1,279
- *Primary key:* `(parent_pnr,pnr,sector,flight_no,date_of_sale)`
- *Natural key:* same
- *Foreign keys:* none
- *Important indexes:* agency code, flight date, POS, sale date
- *Time semantics:* sale and flight dates are strings; `synced_at` is ingest time
- *Snapshot behavior:* current upserted receipts
- *Data sensitivity:* PNRs, child relationship, agency identity are restricted
- *Known limitations:* no enforced hierarchy/sync joins; not linked to `group_requests`

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `parent_pnr` | character varying(30) | no | parent locator | PK | Restricted; never select |
| `pnr` | character varying(30) | no | child/current locator | PK | Restricted |
| `sector` | character varying(30) | no | route | default blank | — |
| `flight_no` | character varying(20) | no | flight | default blank | Normalize for joins |
| `date_of_sale` | character varying(10) | no | receipt/report date | PK | Default Compass advance date |
| `date_of_flight` | character varying(10) | yes | departure date | — | Validate ISO |
| `child_pnrs` | jsonb | no | child locator list | default `[]` | Restricted; never extract |
| `total_advance` | double precision | no | advance received | default 0 | Additive |
| `pax_total` | integer | no | advance pax | default 0 | Additive |
| `agency_code` | character varying(50) | no | agency key | default blank | Restricted |
| `agency_name` | character varying(255) | no | agency label | default blank | Restricted |
| `zone_id` | integer | yes | denormalized zone | no FK | Validate if joined |
| `territory_id` | integer | yes | denormalized territory | no FK | Validate |
| `pos_id` | integer | yes | denormalized POS | no FK | Scope field |
| `synced_at` | timestamp with time zone | no | refresh time | default now | Freshness |
| `sync_id` | integer | yes | sync run | no FK | Audit hint |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| hierarchy | stored IDs to PKs | many-to-one expected | application-denormalized, no FKs | Left join and quantify missing keys |
| group requests | none | — | no linkage | Never claim request conversion |

### `public.rm_offers`

- *Object type:* table
- *Functional relevance:* versioned RM offer definitions
- *Use for:* live/scheduled/expired offer definitions, targeting metadata, publish cadence
- *Do not use for:* exposure, redemption, payout, attributed bookings, ROI, causal uplift
- *Row grain:* one published offer version for a flight instance and validity window
- *Approximate row count:* catalog unavailable; exact bounded boolean aggregate saw 11 rows
- *Primary key:* `id`
- *Natural key:* no database-enforced natural key; application identity is flight number + departure date + validity window
- *Foreign keys:* none, including targeting arrays
- *Important indexes:* `(flight_number,departure_date,created_at DESC)`; `(sector,departure_date)`
- *Time semantics:* departure date; inclusive offer start/end; `created_at` publish time
- *Snapshot behavior:* insert versions; current read selects latest in-window row per flight/departure
- *Data sensitivity:* creator and target names are restricted; offer terms confidential
- *Known limitations:* overlap prevention is application-only; no outcome/events; FSI enrichment is current sector-day context, not publish-time snapshot

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | offer version key | PK | Best analytical grain |
| `flight_number` | character varying(20) | no | targeted flight | — | Normalize for external joins |
| `sector` | character varying(30) | no | targeted route | — | — |
| `departure_date` | date | no | targeted departure | — | Flight-instance date |
| `amount_inr` | numeric(12,2) | no | face-value incentive per booking | application `>0`, cap ≤200 | Not payout/cost |
| `start_date` | date | no | inclusive validity start | — | Current condition lower bound |
| `end_date` | date | no | inclusive validity end | `>=start_date` in application | Current condition upper bound |
| `applies_to_all_agencies` | boolean | no | all-agency target flag | default false | Interpret with arrays |
| `territory_ids` | integer[] | no | target territory IDs | default empty | No FK; use array overlap carefully |
| `territory_names` | text[] | no | publish-time labels | default empty | Restricted; stale snapshot possible |
| `agency_ids` | integer[] | no | target agency IDs | default empty | No FK |
| `agency_names` | text[] | no | publish-time labels | default empty | Restricted |
| `created_by` | character varying(255) | no | publisher identity | — | Restricted; never select by default |
| `created_at` | timestamp with time zone | no | publish timestamp | default now | Version ordering |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `fsi_sector_daily` | normalized sector + `departure_date=flight_date` | many offers-to-one sector-day | application `list_live_offers` | FSI is current and sector-level, not publish-time/flight-level |
| hierarchy | ID is member of target arrays | many-to-many | application metadata, no FK | `unnest` causes fan-out; aggregate one side first |
| bookings/redemptions | none | — | absent | Never attribute sales to an offer |

### `public.pp_flight_schedule`

- *Object type:* table
- *Functional relevance:* effective-dated IBS flight schedule definitions
- *Use for:* planned flights, frequencies, times, aircraft/flight type
- *Do not use for:* proof of operation, final capacity, cancellations
- *Row grain:* one upstream schedule definition
- *Approximate row count:* 107
- *Primary key:* `upstream_id`
- *Natural key:* upstream ID
- *Foreign keys:* none
- *Important indexes:* flight number; origin/destination
- *Time semantics:* inclusive effective `start_date`/`end_date`; times are strings
- *Snapshot behavior:* current upstream rows upserted; disappeared rows are not explicitly retired
- *Data sensitivity:* operational schedule data
- *Known limitations:* no schedule-version history/effective deletion; frequency digits are application interpreted

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `upstream_id` | integer | no | source schedule ID | PK | Stable source key |
| `origin` | character varying(10) | no | origin airport | — | — |
| `destination` | character varying(10) | no | destination airport | — | — |
| `flight_number` | character varying(30) | no | flight number | — | Normalize prefixes when joining |
| `start_date` | date | no | effective start | — | Apply requested date |
| `end_date` | date | no | effective end | — | Apply requested date |
| `operation_freq` | character varying(10) | no | weekday digits | application Monday=1…Sunday=7 | Check membership for date DOW |
| `dep_time` | character varying(10) | yes | scheduled departure text | — | No timezone stored |
| `arr_time` | character varying(10) | yes | scheduled arrival text | — | No timezone stored |
| `aircraft_type` | character varying(20) | yes | planned aircraft type | — | Not physical capacity |
| `flight_type` | character varying(20) | yes | schedule flight type | — | Ungoverned category |
| `connection_point` | character varying(10) | yes | connection point | — | — |
| `synced_at` | timestamp with time zone | yes | source refresh | default now | Freshness |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| PNR/inventory | normalized flight + route + date within effective range/frequency | one definition-to-many instances | inferred application usage | Resolve overlapping definitions to one applicable schedule before join |
| `pp_schedule_time` | no enforced stable cross-key | uncertain | application unions sources | Do not direct-join by row IDs |

### `public.pp_schedule_time`

- *Object type:* table
- *Functional relevance:* alternate schedule-time feed
- *Use for:* schedule time/frequency fallback and unioned schedule reads
- *Do not use for:* duplicate-free schedule facts without source resolution
- *Row grain:* one upstream schedule-time record
- *Approximate row count:* 97
- *Primary key:* `upstream_id`
- *Natural key:* upstream ID
- *Foreign keys:* none
- *Important indexes:* sector
- *Time semantics:* nullable effective dates; time/frequency strings
- *Snapshot behavior:* current upsert by upstream ID
- *Data sensitivity:* operational schedule
- *Known limitations:* independent from `pp_flight_schedule`; API unions both sources

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `upstream_id` | integer | no | source ID | PK | — |
| `sector` | character varying(20) | no | route | — | — |
| `flight_no` | character varying(30) | no | flight | — | Normalize |
| `origin` | character varying(10) | yes | origin | — | — |
| `destination` | character varying(10) | yes | destination | — | — |
| `departure_time` | character varying(10) | yes | time text | — | No timezone |
| `arrival_time` | character varying(10) | yes | time text | — | No timezone |
| `frequency` | character varying(10) | yes | operation weekdays | — | Application parsing required |
| `start_date` | date | yes | effective start | — | Include null rule explicitly |
| `end_date` | date | yes | effective end | — | — |
| `synced_at` | timestamp with time zone | yes | refresh | default now | Freshness |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| flight facts | normalized flight + sector + applicable date | uncertain | inferred | Deduplicate schedule candidates first |

### `public.dashboard_uploads`

- *Object type:* table
- *Functional relevance:* parent/version for uploaded operations datasets
- *Use for:* selecting active operations extracts and provenance
- *Do not use for:* business KPI totals alone
- *Row grain:* one uploaded operations file/version
- *Approximate row count:* 1
- *Primary key:* `id`
- *Natural key:* none
- *Foreign keys:* none
- *Important indexes:* `(is_active,uploaded_at DESC)`
- *Time semantics:* upload timestamp plus covered text date range
- *Snapshot behavior:* overlapping uploads can deactivate prior ranges; active versions may coexist for nonoverlap
- *Data sensitivity:* uploader/file name restricted
- *Known limitations:* text coverage dates; child deduplication may require latest active upload

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | upload ID | PK | Parent join |
| `uploaded_by` | character varying(255) | no | uploader identity | — | Restricted |
| `uploaded_at` | timestamp with time zone | no | ingest time | default now | Version ordering |
| `file_name` | character varying(255) | yes | source file | — | Restricted metadata |
| `date_range_start` | character varying(10) | no | covered start | — | Validate ISO |
| `date_range_end` | character varying(10) | no | covered end | — | Validate ISO |
| `date_range_days` | integer | no | reported span | default 0 | Recalculate if needed |
| `version` | character varying(50) | yes | source version label | — | — |
| `data_source` | character varying(255) | yes | source label | — | — |
| `is_active` | boolean | no | active version | default true | Filter true for current ops |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| operations children | child `upload_id=id` | one-to-many | physical FKs | Filter active parents first |

### `public.daily_ops`

- *Object type:* table
- *Functional relevance:* uploaded daily operational payload
- *Use for:* application-specific daily operations detail
- *Do not use for:* generic SQL measures without known JSON keys
- *Row grain:* one upload/day payload by convention
- *Approximate row count:* 3,525
- *Primary key:* `id`
- *Natural key:* intended `(upload_id,date)`, not unique
- *Foreign keys:* `upload_id → dashboard_uploads.id ON DELETE CASCADE`
- *Important indexes:* `(upload_id,date)`
- *Time semantics:* date string
- *Snapshot behavior:* versioned by upload
- *Data sensitivity:* operational JSON may contain identifiers/free text
- *Known limitations:* opaque schema and possible duplicates

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | row key | PK | — |
| `upload_id` | integer | no | parent upload | FK | Join active upload |
| `date` | character varying(10) | no | operational date | — | Validate ISO |
| `ops_data` | jsonb | no | full daily payload | — | Use only documented keys; never generic-expand |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `dashboard_uploads` | `upload_id=id` | many-to-one | physical FK | Filter/deduplicate active version before JSON aggregation |

### `public.flight_delays`

- *Object type:* table
- *Functional relevance:* uploaded flight delay facts
- *Use for:* delay minutes/reason analysis by flight/day/sector
- *Do not use for:* complete flight schedule or canonical cancellation ledger
- *Row grain:* one uploaded flight-delay row
- *Approximate row count:* 32,860
- *Primary key:* `id`
- *Natural key:* none enforced
- *Foreign keys:* `upload_id → dashboard_uploads.id ON DELETE CASCADE`
- *Important indexes:* `(upload_id,date)`
- *Time semantics:* operational date string
- *Snapshot behavior:* versioned by upload
- *Data sensitivity:* operationally sensitive; JSON/free-text reasons
- *Known limitations:* no unique flight-instance key; reasons JSON is extractor-defined

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | row key | PK | — |
| `upload_id` | integer | no | parent upload | FK | Select active version |
| `date` | character varying(10) | no | operation date | — | Validate ISO |
| `flight` | character varying(100) | yes | flight text | — | Normalize before joins |
| `sector` | character varying(100) | yes | route text | — | — |
| `aircraft` | character varying(100) | yes | aircraft text | — | Not an aircraft master key |
| `total_minutes` | double precision | yes | delay duration | — | Additive only for explicit delay-event questions |
| `reasons` | jsonb | no | reason breakdown | default `[]` | Use documented extractor structure |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `dashboard_uploads` | `upload_id=id` | many-to-one | physical FK | Filter active upload; deduplicate overlapping versions |

### `public.route_performance`

- *Object type:* table
- *Functional relevance:* uploaded route-level OTP summary
- *Use for:* route flights/on-time/delayed counts and OTP percentages
- *Do not use for:* flight-level detail or averaging route percentages
- *Row grain:* route summary per upload
- *Approximate row count:* 1,458
- *Primary key:* `id`
- *Natural key:* none enforced
- *Foreign keys:* `upload_id → dashboard_uploads.id ON DELETE CASCADE`
- *Important indexes:* upload ID
- *Time semantics:* inherited upload coverage, no row date
- *Snapshot behavior:* versioned summary
- *Data sensitivity:* operational summary
- *Known limitations:* no route uniqueness; percentages must be weighted/recomputed

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | row key | PK | — |
| `upload_id` | integer | no | parent upload | FK | Active upload only |
| `route` | character varying(100) | yes | route label | — | — |
| `from_city` | character varying(100) | yes | origin label | — | — |
| `to_city` | character varying(100) | yes | destination label | — | — |
| `total_flights` | integer | yes | flights denominator | — | Additive for disjoint routes |
| `fly91_ontime` | integer | yes | Fly91 on-time count | — | Additive |
| `dgca_ontime` | integer | yes | DGCA on-time count | — | Additive |
| `dgca_delayed` | integer | yes | DGCA delayed count | — | Additive |
| `fly91_otp_pct` | double precision | yes | Fly91 OTP percent | — | Recompute weighted across rows |
| `dgca_otp_pct` | double precision | yes | DGCA OTP percent | — | Recompute weighted |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `dashboard_uploads` | `upload_id=id` | many-to-one | physical FK | Filter active version first |

### `public.pp_sector_details`

- *Object type:* table
- *Functional relevance:* Pricing Portal sector reference
- *Use for:* RCS/Non-RCS classification and airport endpoints
- *Do not use for:* sales facts or effective history
- *Row grain:* one sector
- *Approximate row count:* 40
- *Primary key:* `sector`
- *Natural key:* same
- *Foreign keys:* none
- *Important indexes:* PK
- *Time semantics:* refresh timestamp
- *Snapshot behavior:* current reference
- *Data sensitivity:* ordinary route reference
- *Known limitations:* no FKs from facts; column `source` means origin airport, not source system

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `sector` | character varying(20) | no | route key | PK | Join by normalized route |
| `sector_type` | character varying(20) | no | RCS class | observed `RCS`,`Non-RCS` | Exact filter |
| `source` | character varying(10) | yes | origin airport | — | Do not interpret as data source |
| `destination` | character varying(10) | yes | destination airport | — | — |
| `synced_at` | timestamp with time zone | yes | refresh | default now | Freshness |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| fare/inventory facts | normalized `sector` | one-to-many | application join, no FK | Safe only if fact sector format matches |

### `public.pp_rbd_details`

- *Object type:* table
- *Functional relevance:* RBD/fare-class reference
- *Use for:* mapping RBD to fare class/type label
- *Do not use for:* fare amount or sold inventory
- *Row grain:* one RBD
- *Approximate row count:* 25
- *Primary key:* `rbd`
- *Natural key:* same
- *Foreign keys:* none
- *Important indexes:* PK
- *Time semantics:* refresh timestamp
- *Snapshot behavior:* current reference
- *Data sensitivity:* commercial reference
- *Known limitations:* no effective history or FKs from facts

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `rbd` | character varying(20) | no | RBD key | PK | Join by exact RBD |
| `rbd_type` | character varying(30) | yes | business type label | 25 observed labels | Exact-case filters |
| `fare_class` | character varying(5) | no | one-letter class | A–Z excluding no observed `U` | Reference mapping |
| `synced_at` | timestamp with time zone | yes | refresh | default now | — |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `pp_rbd_fares` | `rbd` | one-to-many | inferred/application; no FK | Fare rows also require sector/fare type |

### `public.pp_rbd_fares`

- *Object type:* table
- *Functional relevance:* published fare ladder
- *Use for:* sector/RBD/fare-type net and gross reference fares
- *Do not use for:* booked/realized revenue or competitor fare
- *Row grain:* sector-RBD-fare type
- *Approximate row count:* 850
- *Primary key:* `id`
- *Natural key:* unique `(sector,rbd,fare_type)`
- *Foreign keys:* none
- *Important indexes:* sector; unique grain
- *Time semantics:* nullable timestamp validity and refresh
- *Snapshot behavior:* current reference upsert
- *Data sensitivity:* commercial fare data
- *Known limitations:* no FK to reference tables; validity nullable; G-class ladder may diverge from actual revenue

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | surrogate key | PK | — |
| `sector` | character varying(20) | no | route | natural key | — |
| `rbd` | character varying(20) | no | RBD | natural key | — |
| `fare_type` | character varying(20) | no | fare source/type | observed `normal`,`via` | Always distinguish |
| `fare_class` | character varying(5) | no | fare class | 25 observed | — |
| `origin_airport` | character varying(10) | yes | origin | — | — |
| `destination_airport` | character varying(10) | yes | destination | — | — |
| `net_fare` | numeric(10,2) | no | net reference fare | default 0 | Not realized revenue |
| `gross_fare` | numeric(10,2) | no | gross reference fare | default 0 | — |
| `start_date` | timestamp with time zone | yes | validity start | — | Apply for as-of fare when populated |
| `end_date` | timestamp with time zone | yes | validity end | — | — |
| `synced_at` | timestamp with time zone | yes | refresh | default now | Current-state freshness |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `pp_sector_details` | `sector` | many-to-one | inferred, no FK | Safe normalized join |
| `pp_rbd_details` | `rbd` | many-to-one | inferred, no FK | Safe if RBD exists |

### `public.pp_inventory`

- *Object type:* table
- *Functional relevance:* Pricing Portal current per-RBD inventory
- *Use for:* current authorized/allocated/sold/remaining/available and fare detail
- *Do not use for:* historical as-of inventory or additive flight summary fields without deduplication
- *Row grain:* flight-date-RBD; sector is not in unique key
- *Approximate row count:* catalog 4,025, demonstrably stale (bounded category count covered 4,300 rows)
- *Primary key:* `id`
- *Natural key:* unique `(flight_no,flight_date,rbd)`
- *Foreign keys:* none
- *Important indexes:* flight/date; sector/date; unique grain
- *Time semantics:* departure date and refresh timestamp
- *Snapshot behavior:* current state overwritten/upserted
- *Data sensitivity:* future inventory, fare and revenue confidential
- *Known limitations:* flight-level totals repeat on every RBD row; sector absent from unique key

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | row key | PK | — |
| `flight_no` | character varying(30) | no | flight | natural key | Normalize |
| `sector` | character varying(20) | no | route | not unique key | Include in filters |
| `origin` | character varying(10) | yes | origin | — | — |
| `destination` | character varying(10) | yes | destination | — | — |
| `flight_date` | date | no | departure date | natural key | — |
| `rbd` | character varying(20) | no | RBD | natural key | — |
| `authorized_units` | integer | no | authorized inventory | default 0 | Sum only if RBD allocations are disjoint |
| `authorized_units_source` | character varying(20) | no | allocation source | observed `upstream` | Preserve provenance |
| `allocated_seats` | integer | no | allocated seats | default 0 | Current state |
| `sold_seats` | integer | no | sold in RBD | default 0 | Current state |
| `remaining_seats` | integer | no | remaining | default 0 | Current state |
| `previously_sold_seats` | integer | no | prior sold source field | default 0 | Do not infer snapshot delta |
| `net_sold_seats` | integer | no | net sold source field | default 0 | — |
| `available_seats` | integer | no | available | default 0 | — |
| `price` | numeric(10,2) | yes | source price | — | Reference/current |
| `net_fare` | numeric(10,2) | yes | net fare | — | — |
| `sold_price` | numeric(10,2) | yes | sold price | — | — |
| `synced_at` | timestamp with time zone | yes | refresh | default now | Latest-state time |
| `total_sold_seats` | integer | yes | flight-level total repeated per RBD | — | Select one per flight/date, never sum across RBD |
| `total_net_fares` | numeric(12,2) | yes | flight-level total revenue repeated per RBD | — | Deduplicate at flight/date |
| `g_class_revenue` | numeric(12,2) | yes | flight-level G-class revenue repeated per RBD | — | Deduplicate |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| schedule/PNR | normalized flight + date + route | many-to-many raw | inferred | Aggregate/deduplicate inventory by flight first |
| fare reference | sector + RBD | many-to-one/many | inferred | Include fare type selection |

### `public.pp_load_factor`

- *Object type:* table
- *Functional relevance:* current own-flight LF from RateGain block
- *Use for:* latest flight-date sold/LF context
- *Do not use for:* historical LF as of earlier captures
- *Row grain:* flight-sector-date
- *Approximate row count:* 3,572
- *Primary key:* `id`
- *Natural key:* unique `(flight_no,sector,flight_date)`
- *Foreign keys:* none
- *Important indexes:* unique natural key
- *Time semantics:* departure date, latest refresh timestamp
- *Snapshot behavior:* current refresh/delete-and-replace by scope
- *Data sensitivity:* future load confidential
- *Known limitations:* no capacity column; percent scale is source-defined

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | row key | PK | — |
| `flight_no` | character varying(30) | no | flight | natural key | Normalize |
| `sector` | character varying(20) | no | route | natural key | — |
| `flight_date` | date | no | departure | natural key | — |
| `sold_seats` | integer | yes | source sold | — | Null unknown |
| `load_factor` | numeric(5,2) | yes | source LF | — | Treat as percent only after source confirmation |
| `synced_at` | timestamp with time zone | yes | refresh | default now | Current-state timestamp |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| PNR/inventory | normalized flight + sector + date | one-to-many at RBD detail | inferred | Pre-aggregate detailed source first |

### `public.pp_rbd_inventory_daily`

- *Object type:* table
- *Functional relevance:* current sold/allocation by flight-date-sector-RBD
- *Use for:* forward capacity, sold seats, RBD availability, FSI/GI
- *Do not use for:* historical state at prior capture time
- *Row grain:* flight-date-flight-sector-RBD
- *Approximate row count:* 144,243
- *Primary key:* `(flight_date,flight_no,sector,rbd)`
- *Natural key:* same
- *Foreign keys:* none
- *Important indexes:* flight/date (including covering capacity index); sector/date
- *Time semantics:* departure date; separate sold/allocation refresh timestamps
- *Snapshot behavior:* two independent current-state upserts
- *Data sensitivity:* future capacity/fare/load confidential
- *Known limitations:* either metric may be null; empty source response does not necessarily delete stale rows

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `flight_date` | date | no | departure date | PK | — |
| `flight_no` | character varying(30) | no | flight | PK | Normalize |
| `sector` | character varying(20) | no | route | PK | — |
| `origin` | character varying(10) | yes | origin | — | — |
| `destination` | character varying(10) | yes | destination | — | — |
| `rbd` | character varying(10) | no | fare class/RBD | PK | — |
| `net_fare` | numeric(10,2) | yes | RBD net fare | — | Reference, not realized revenue |
| `sold_seats` | integer | yes | current sold | null = unknown | Do not coalesce before coverage analysis |
| `authorized_units` | integer | yes | current allocation/capacity | null = unknown | Capacity sum only across valid RBDs |
| `sold_synced_at` | timestamp with time zone | yes | sold refresh | — | Freshness by metric |
| `alloc_synced_at` | timestamp with time zone | yes | allocation refresh | — | Freshness by metric |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `fsi_sector_daily` | sector + date after per-flight/RBD aggregation | many-to-one | rollup lineage | Never raw join and sum FSI measures |
| `pnr_flight` | normalized flight + sector + typed date | many-to-many raw | application use | Pre-aggregate both |
| `pp_rbd_inventory_monthly` | sector + flight + month | many days-to-one | rollup lineage | Recompute or use monthly, not both |

### `public.pp_rbd_inventory_monthly`

- *Object type:* table
- *Functional relevance:* monthly RBD inventory rollup
- *Use for:* fast monthly sold/allocation coverage and weighted LF
- *Do not use for:* day/RBD detail or point-in-time history
- *Row grain:* sector-flight-calendar month
- *Approximate row count:* 278
- *Primary key:* `(sector,flight_no,year_month)`
- *Natural key:* same
- *Foreign keys:* none
- *Important indexes:* sector/month
- *Time semantics:* `year_month` text; `updated_at`
- *Snapshot behavior:* rebuilt from current daily table
- *Data sensitivity:* future inventory confidential
- *Known limitations:* not a historical snapshot; coverage-day counts are essential

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `sector` | character varying(20) | no | route | PK | — |
| `flight_no` | character varying(30) | no | flight | PK | — |
| `year_month` | character varying(7) | no | calendar month | `YYYY-MM` by application | Validate before lexical filter |
| `days_with_sold_data` | integer | no | sold coverage days | default 0 | Data quality denominator |
| `days_with_alloc_data` | integer | no | allocation coverage days | default 0 | Data quality |
| `total_sold_seats` | integer | no | paired-data sold total | default 0 | Additive across disjoint flights |
| `total_authorized_units` | integer | no | paired-data allocation total | default 0 | LF denominator |
| `avg_load_factor_pct` | numeric(8,2) | yes | `100*sold/authorized` | null if no denominator | Do not average across rows |
| `updated_at` | timestamp with time zone | no | rebuild time | default now | Freshness |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `pp_rbd_inventory_daily` | sector + flight + month | one-to-many lineage | application rollup | Choose one source, do not sum both |

### `public.pp_fare_class_history`

- *Object type:* table
- *Functional relevance:* true daily inventory capture history / booking curves
- *Use for:* as-of sold/authorized/remaining and equal-DTD booking curves
- *Do not use for:* current-only inventory without selecting latest capture
- *Row grain:* capture date-flight-departure-fare class
- *Approximate row count:* 46,517,832
- *Primary key:* `id`
- *Natural key:* unique `(capture_date,flight_no,flight_date,fare_class_code)`
- *Foreign keys:* none
- *Important indexes:* capture; flight/date; sector/date; covering booking-velocity index
- *Time semantics:* `capture_date` observation date; `flight_date` departure date; DTD=`flight_date-capture_date`
- *Snapshot behavior:* append-only daily captures/upserts at unique grain
- *Data sensitivity:* future inventory highly confidential
- *Known limitations:* sparse/missing capture is not zero; huge table requires selective indexed filters

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | row key | PK | Avoid scanning by ID range without date |
| `capture_date` | date | no | observation date | natural key | Always constrain |
| `flight_date` | date | no | departure date | natural key | Always constrain/window |
| `flight_no` | character varying(30) | no | flight | natural key | Normalize |
| `sector` | character varying(20) | no | route | — | Indexed with flight date |
| `fare_class_code` | character varying(10) | no | captured class | natural key | Aggregate classes for flight sold |
| `authorized_units` | integer | yes | authorized at capture | null unknown | Preserve null |
| `sold_seats` | integer | yes | cumulative sold at capture | null unknown | Sum classes at one capture only |
| `remaining_seats` | integer | yes | remaining at capture | null unknown | — |
| `synced_at` | timestamp with time zone | yes | ingest time | default now | Not observation date |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| offers | normalized flight + departure and capture on/before publish date | many captures-to-one offer | analytical reconstruction only | Pick one as-of capture before join; label estimate |
| current inventory | flight/date/class | history-to-one current | inferred | Never combine measures across captures |

### `public.pp_competitor_fares`

- *Object type:* table
- *Functional relevance:* versioned current/retired RateGain fares
- *Use for:* current competitor/own fares and price-version analysis
- *Do not use for:* current pricing without `is_current=true`
- *Row grain:* one version of logical requested-market/native-sector-flight-date-time fare
- *Approximate row count:* 40,075
- *Primary key:* `(id,version)`
- *Natural key:* current partial unique `(query_sector,sector,outbound_flight,fare_date,coalesced departure time)`
- *Foreign keys:* referenced by `pp_predicted_fares`
- *Important indexes:* requested market/date; native sector/date; partial current indexes
- *Time semantics:* fare/departure date; `synced_at` latest observation of version
- *Snapshot behavior:* price change retires prior row and inserts next version; disappearance retires current
- *Data sensitivity:* competitor/commercial pricing confidential
- *Known limitations:* `operator` is broad own/OAL label; no capture timestamp history beyond version refresh

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | logical fare identity | PK with version | Same ID can have versions |
| `sector` | character varying(20) | no | flight native sector | current unique key | Do not substitute for requested market |
| `outbound_flight` | character varying(30) | no | flight | key | — |
| `operator` | character varying(50) | yes | own/other label | observed `Fly91`,`OAL` | Exact filter |
| `outbound_departure_time` | character varying(10) | yes | departure time text | coalesced in unique key | Null and blank normalized for identity |
| `fare_date` | date | no | departure/fare date | key | Date filter |
| `price` | numeric(10,2) | yes | observed fare | — | Filter positive where metric requires |
| `is_primary_route` | boolean | no | native requested-route flag | default true | Comparison routes may be false |
| `synced_at` | timestamp with time zone | yes | observation refresh | default now | Version freshness |
| `query_sector` | character varying(20) | no | market requested from API | current unique key | Preferred browse/filter market |
| `version` | integer | no | logical price version | default 1; PK | Order versions per ID |
| `is_current` | boolean | no | live state | default true | **Mandatory current-fare filter** |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `pp_predicted_fares` | `(id,version)=(fare_id,fare_version)` | one-to-many | physical FK | Predictions may stack; aggregate explicitly |
| flight/schedule | normalized flight + sector/date/time | uncertain | inferred | `query_sector` and native `sector` differ; avoid accidental route join |

### `public.pp_competitor_fare_history`

- *Object type:* table
- *Functional relevance:* true daily competitor/own fare captures
- *Use for:* fare-vs-DTD history, direct/connecting comparisons, best fare by capture
- *Do not use for:* current state without latest-capture logic
- *Row grain:* capture-market-departure-airline-flight-site
- *Approximate row count:* 301,785
- *Primary key:* `id`
- *Natural key:* unique `(capture_date,sector,departure_date,airline_code,outbound_flight,site_code)`
- *Foreign keys:* none
- *Important indexes:* capture; sector/departure/airline; stops
- *Time semantics:* capture date versus departure date
- *Snapshot behavior:* daily point-in-time history
- *Data sensitivity:* competitor pricing and raw payload confidential
- *Known limitations:* multiple flights/sites per airline/day; payload is opaque; missing capture is not zero

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | row key | PK | — |
| `capture_date` | date | no | observation date | natural key | DTD basis |
| `departure_date` | date | no | travel date | natural key | — |
| `sector` | character varying(20) | no | requested market | natural key | — |
| `operator` | character varying(50) | yes | own/other label | — | Prefer airline code for carrier |
| `airline_code` | character varying(10) | no | carrier code | eight observed codes | Safe categorical |
| `outbound_flight` | character varying(120) | no | itinerary/flight text | natural key | High-cardinality; do not list |
| `outbound_fare` | numeric(10,2) | yes | observed fare | — | Often aggregate `MIN` per carrier/capture |
| `site_code` | character varying(20) | no | source site | one observed value | Source dimension |
| `synced_at` | timestamp with time zone | yes | ingest time | default now | Not capture date |
| `outbound_stop` | integer | yes | connection count | observed 0–3 | Filter `0` for direct comparisons |
| `payload` | jsonb | yes | raw source row | — | Restricted; avoid generic extraction |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| internal flight facts | sector + departure date; optional normalized flight | many-to-many | analytical/inferred | Aggregate competitor rows to explicit carrier/site rule first |

### `public.fsi_sector_daily`

- *Object type:* table
- *Functional relevance:* sector-day Forward Sales Index rollup
- *Use for:* forward capacity, current sold/revenue/yield, aspirational revenue gap and historical DTD benchmarks
- *Do not use for:* RBD/flight detail or combining with its source table totals
- *Row grain:* sector-flight date, with one or more flight numbers encoded in text
- *Approximate row count:* catalog estimate stale; exact bounded boolean aggregate saw 275 rows on inspection
- *Primary key:* `id`
- *Natural key:* unique `(sector,flight_date)`
- *Foreign keys:* none
- *Important indexes:* unique sector/date; flight date
- *Time semantics:* `flight_date` is departure date; `synced_at` is rollup refresh
- *Snapshot behavior:* current deterministic rollup; no persisted observation history
- *Data sensitivity:* forward inventory/performance confidential
- *Known limitations:* capacity can be null; `flight_numbers` is denormalized text; benchmark columns can be null when history is insufficient

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | integer | no | surrogate key | PK | — |
| `sector` | character varying(30) | no | route | natural key | — |
| `flight_date` | date | no | departure date | natural key | — |
| `flight_numbers` | character varying(100) | no | contributing flight list/text | — | Do not join by substring |
| `num_flights` | smallint | no | contributing flights | — | Additive only across disjoint sector/date |
| `capacity` | integer | yes | current authorized capacity | — | Null means unavailable |
| `capacity_source` | character varying(20) | no | capacity provenance | — | Retain in quality checks |
| `current_sold` | integer | no | current forward sold | — | Not canonical PNR ticket KPI |
| `current_revenue` | double precision | no | current forward revenue | — | App FSI revenue, not canonical PNR revenue |
| `current_yield` | double precision | no | `current_revenue/current_sold` | — | Do not average |
| `seats_available` | integer | yes | capacity minus sold | — | Null if capacity unknown |
| `load_factor` | double precision | yes | sold/capacity ratio | decimal 0–1 by application | Recompute weighted |
| `aspirational_yield_per_seat` | double precision | yes | target yield | — | Model/heuristic input |
| `aspirational_yield_source` | character varying(30) | yes | benchmark source | — | Label |
| `aspirational_revenue` | double precision | yes | target revenue | — | Not actual |
| `gap` | double precision | yes | aspirational minus current revenue | — | App-derived |
| `target_yield_on_remaining` | double precision | yes | yield required on remaining seats | — | Undefined/null when no denominator |
| `has_sufficient_history` | boolean | no | benchmark quality flag | — | Require true for benchmark claims |
| `synced_at` | timestamp with time zone | yes | rollup refresh | — | Freshness |
| `pooled_2yr_at_dtd` | double precision | yes | pooled two-year sold benchmark at DTD | — | Historical benchmark |
| `pooled_2yr_yield` | double precision | yes | pooled yield benchmark | — | — |
| `day_of_week_at_dtd` | double precision | yes | weekday sold benchmark at DTD | — | — |
| `day_of_week_yield` | double precision | yes | weekday yield benchmark | — | — |
| `same_date_last_year_at_dtd` | double precision | yes | prior-year sold benchmark at DTD | — | — |
| `same_date_last_year_yield` | double precision | yes | prior-year yield benchmark | — | — |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| daily RBD inventory | sector + date after aggregation | one-to-many lineage | application rollup | Choose rollup or detail, not both |

### `public.crew_master`

- *Object type:* table
- *Functional relevance:* crew directory and current employment/contract attributes
- *Use for:* authorized aggregate breakdowns by crew type, role, fleet or base
- *Do not use for:* names, staff lists or individual performance output
- *Row grain:* one staff member
- *Approximate row count:* catalog estimate should be treated as stale
- *Primary key:* `staff_id`
- *Natural key:* same
- *Foreign keys:* referenced by crew activity/monthly summary
- *Important indexes:* PK
- *Time semantics:* first/last seen and update timestamps
- *Snapshot behavior:* mutable current master, not effective-dated history
- *Data sensitivity:* **direct PII, employment and performance attributes; highest restriction**
- *Known limitations:* base is inferred from remarks; default contract hours can mask missing source values

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `staff_id` | character varying(40) | no | staff identifier | PK | Restricted; never output/sample |
| `name` | character varying(255) | yes | person name | — | Restricted; never output/sample |
| `crew_type` | character varying(20) | yes | crew family | code expects AirCrew/CabinCrew | Aggregate only |
| `primary_role` | character varying(20) | yes | primary role | source-defined | Aggregate only |
| `rank_label` | character varying(40) | yes | derived rank | — | Aggregate only |
| `fleet` | character varying(40) | yes | assigned fleet | — | Aggregate only |
| `base_station` | character varying(20) | yes | inferred base | — | Aggregate only |
| `contracted_hours` | numeric(6,2) | yes | monthly target hours | default 70 | Target, not actual |
| `is_active` | boolean | yes | active flag | default true | Filter current crew where appropriate |
| `first_seen_date` | date | yes | first source observation | — | Not hire date |
| `last_seen_date` | date | yes | latest source observation | — | Not termination date |
| `remarks` | text | yes | personnel notes | — | Restricted; never return/sample |
| `updated_at` | timestamp with time zone | no | master refresh | default now | Freshness |
| `trainer_status` | character varying(20) | yes | trainer classification | — | Sensitive attribute; aggregate only |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `crew_activity` | `staff_id` | one-to-many | physical FK | Aggregate facts; never project staff identity |
| `crew_monthly_summary` | `staff_id` | one-to-many | physical FK | Filter month/data type before aggregating |

### `public.crew_roster_raw`

- *Object type:* table
- *Functional relevance:* append-only raw upstream duty rows
- *Use for:* authorized source reconciliation and ingestion audit
- *Do not use for:* unrestricted person-level output or fast monthly summaries
- *Row grain:* one captured upstream duty element
- *Approximate row count:* 801,373
- *Primary key:* `id`
- *Natural key:* none
- *Foreign keys:* `sync_id → crew_sync_log.id ON DELETE CASCADE`
- *Important indexes:* staff ID + flight date
- *Time semantics:* nullable `flight_date`; raw duty times are strings; `captured_at` is observation/ingest time
- *Snapshot behavior:* append-only audit rows across planned/actual/backfill syncs
- *Data sensitivity:* **restricted PII and employment/operational records**
- *Known limitations:* duplicates across captures are expected; use curated activity for metrics

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | bigint | no | raw row ID | PK | — |
| `sync_id` | integer | no | source sync run | FK | Audit only |
| `staff_id` | character varying(40) | no | staff identifier | — | **Restricted; never output** |
| `crew_type` | character varying(20) | yes | upstream crew family | expected AirCrew/CabinCrew by code | Aggregate only |
| `flight_date` | date | yes | duty/flight date | — | Date filter |
| `role_raw` | character varying(20) | yes | source role | — | Ungoverned raw value |
| `duty_raw` | character varying(40) | yes | source duty code | — | Prefer normalized activity |
| `duty_start_raw` | character varying(20) | yes | raw start | — | Do not do direct time arithmetic |
| `duty_end_raw` | character varying(20) | yes | raw end | — | — |
| `remarks` | text | yes | source remarks | — | Restricted free text; never return/sample |
| `captured_at` | timestamp with time zone | no | capture time | default now | Distinguish repeated snapshots |
| `trainer_status_raw` | character varying(20) | yes | source trainer status | — | Sensitive personnel attribute; aggregate only |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `crew_sync_log` | `sync_id=id` | many-to-one | physical FK | Audit only |
| `crew_master` | `staff_id=staff_id` | many-to-one expected | application/raw relationship | Restricted |
| `crew_activity` | staff/date/sync, not a direct row ID | many-to-many possible | ETL lineage | Do not join raw for KPI totals |

### `public.crew_activity`

- *Object type:* table
- *Functional relevance:* curated crew activity event
- *Use for:* planned/actual duty classification, duration, route and productivity analysis
- *Do not use for:* public person-level schedules
- *Row grain:* one normalized employee activity event
- *Approximate row count:* stale catalogue estimate 64,163; exact grouped snapshot counted 64,984
- *Primary key:* `id`
- *Natural key:* unique `(staff_id,duty_date,data_type,duty_code,start_dt)`
- *Foreign keys:* `staff_id → crew_master.staff_id ON DELETE CASCADE`; `sync_id → crew_sync_log.id ON DELETE CASCADE`
- *Important indexes:* staff/date/type; date/type; duty category
- *Time semantics:* `duty_date`; timezone-naive start/end datetimes
- *Snapshot behavior:* first planned snapshot is retained; actual rows are upserted to latest truth
- *Data sensitivity:* **restricted PII/employment and duty records**
- *Known limitations:* planned and actual coexist; omitting `data_type` double counts; remarks are restricted

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | bigint | no | activity ID | PK | — |
| `staff_id` | character varying(40) | no | staff key | FK/natural key | **Restricted; group only** |
| `duty_date` | date | no | duty date | natural key | — |
| `data_type` | character varying(10) | no | snapshot type | `PLANNED` or `ACTUAL` by code | **Always filter one type** |
| `duty_code` | character varying(40) | no | normalized activity code | natural key | Group with duty category |
| `duty_subtype` | character varying(20) | yes | subtype | leave/ground subtype | Potentially sensitive |
| `duty_category` | character varying(20) | no | business category | Productive/Compliance/Inefficiency/Unavailable by code | Aggregate only |
| `role` | character varying(20) | yes | role at duty | — | Restricted aggregate |
| `start_dt` | timestamp without time zone | yes | duty start | natural key | Local timezone not stored |
| `end_dt` | timestamp without time zone | yes | duty end | — | — |
| `duration_hours` | numeric(6,2) | yes | activity duration | default 0 | Additive over nonoverlapping events |
| `sector` | character varying(40) | yes | parsed route | — | — |
| `flight_no` | character varying(40) | yes | parsed flight | — | Normalize |
| `station` | character varying(20) | yes | parsed station | — | — |
| `remarks` | text | yes | source remarks | — | Restricted; never return/sample |
| `sync_id` | integer | no | source sync | FK | Audit |
| `trainer_status` | character varying(20) | yes | trainer classification | — | Sensitive personnel attribute; aggregate only |

#### Verified joins

| Target | Join condition | Cardinality | Evidence | Fan-out guidance |
|---|---|---|---|---|
| `crew_master` | `staff_id=staff_id` | many-to-one | physical FK | Restricted dimension join |
| `crew_sync_log` | `sync_id=id` | many-to-one | physical FK | Audit only |
| monthly summary | staff + formatted month + data type | many-to-one rollup lineage | application ETL | Choose detail or summary, not both |

### `public.crew_monthly_summary`

- *Object type:* table
- *Functional relevance:* monthly aggregate by employee
- *Use for:* planned/actual productive hours, leave/availability days and contract variance
- *Do not use for:* person-level output or adding to underlying activity totals
- *Row grain:* staff-calendar month-data type
- *Approximate row count:* 698
- *Primary key:* `(staff_id,year_month,data_type)`
- *Natural key:* same
- *Foreign keys:* `staff_id → crew_master.staff_id ON DELETE CASCADE`
- *Important indexes:* month + data type
- *Time semantics:* `year_month` string `YYYY-MM`; `last_synced_at` is rollup refresh
- *Snapshot behavior:* rebuild/upsert aggregate
- *Data sensitivity:* **restricted employee-level aggregate**
- *Known limitations:* planned and actual are separate rows; staff ID must not be exposed

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `staff_id` | character varying(40) | no | staff key | PK/FK | Restricted; never output |
| `year_month` | character varying(7) | no | month | PK, `YYYY-MM` | Validate format |
| `data_type` | character varying(10) | no | planned/actual | PK | Always filter one or compare explicitly |
| `flying_hours` | numeric(7,2) | yes | FLT hours | default 0 | Additive across disjoint staff |
| `sim_hours` | numeric(7,2) | yes | simulator hours | default 0 | — |
| `safety_hours` | numeric(7,2) | yes | safety-pilot hours | default 0 | — |
| `total_productive_hrs` | numeric(7,2) | yes | flying + simulator + safety | default 0 | Do not add to components |
| `dh_hours` | numeric(7,2) | yes | deadhead hours | default 0 | Inefficiency |
| `office_hours` | numeric(7,2) | yes | office hours | default 0 | Inefficiency |
| `sb_days` | integer | yes | standby days | default 0 | — |
| `pl_days` | integer | yes | privilege leave days | default 0 | Sensitive; aggregate only |
| `cl_days` | integer | yes | casual leave days | default 0 | Sensitive |
| `skl_days` | integer | yes | sick leave days | default 0 | Highly sensitive; aggregate/suppress small cells |
| `leave_days_total` | integer | yes | total leave days | default 0 | Do not add to leave components |
| `block_off_days` | integer | yes | blocked-off days | default 0 | — |
| `not_avail_days` | integer | yes | unavailable days | default 0 | — |
| `off_days` | integer | yes | off days | default 0 | — |
| `blank_days` | integer | yes | days without assigned action | default 0 | Primary action metric in code |
| `contracted_hours` | numeric(6,2) | yes | monthly target | default 70 | Target |
| `variance_vs_contract` | numeric(7,2) | yes | productive minus contracted | default 0 | Derived |
| `duty_days` | integer | yes | days with duty record | default 0 | — |
| `last_synced_at` | timestamp with time zone | no | rollup refresh | default now | Freshness |

#### Verified joins

Join to `crew_master` by staff only for authorized aggregate dimensions. Joining to activities on staff/month/type causes fan-out and double counts; choose summary or detail.

### `public.users`

- *Object type:* table
- *Functional relevance:* dashboard authentication identity
- *Use for:* trusted login/RBAC checks only
- *Do not use for:* business analytics or user lists
- *Row grain:* one user email
- *Approximate row count:* catalog estimate should be treated as stale
- *Primary key:* `email`
- *Natural key:* same
- *Foreign keys:* referenced by physical `user_roles.user_email` FK; page views/scopes use logical email relationships
- *Important indexes:* PK
- *Time semantics:* account creation
- *Snapshot behavior:* current identity set
- *Data sensitivity:* **direct PII and password credential material; highest restriction**
- *Known limitations:* no enabled/deleted timestamp columns; account state semantics are application-owned

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `email` | character varying(255) | no | login identity | PK | Restricted; never return/sample |
| `name` | character varying(255) | no | display name | — | Restricted; never return/sample |
| `password` | character varying(255) | no | stored password credential/hash | — | **Never select, expose, compare, or summarize** |
| `created_at` | timestamp with time zone | no | account creation | default now by application | Administrative only |
| `created_by` | character varying(255) | yes | creator identity | — | Restricted |

#### Verified joins

`users.email` links to user-role, user-scope and page-view email fields by application identity. These are authorization/audit joins, not analytical dimensions.

### `public.user_roles`

- *Object type:* table
- *Functional relevance:* user-to-role assignment
- *Use for:* trusted RBAC checks
- *Do not use for:* business metrics
- *Row grain:* user email-role name
- *Approximate row count:* catalog estimate should be treated as stale
- *Primary key:* composite assignment key
- *Natural key:* `(user_email,role_name)`
- *Foreign keys:* `user_email → users.email ON DELETE CASCADE`; `role_name → roles.name ON DELETE CASCADE`
- *Important indexes:* composite key
- *Time semantics:* none
- *Snapshot behavior:* current assignments
- *Data sensitivity:* **identity and authorization metadata**
- *Known limitations:* a user can have multiple roles; application combines role permissions

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `user_email` | character varying(255) | no | assigned user | — | Restricted |
| `role_name` | character varying(50) | no | assigned role | — | Authorization only |

#### Verified joins

Join to `users.email` and `roles.name` inside trusted authorization logic. Multiple roles and permissions create fan-out.

### `public.user_scopes`

- *Object type:* table
- *Functional relevance:* row-scope assignment for a user
- *Use for:* trusted application authorization by typed hierarchy scope
- *Do not use for:* business hierarchy membership or untrusted ad-hoc filtering
- *Row grain:* user-scope type-scope ID
- *Approximate row count:* catalog estimate should be treated as stale
- *Primary key:* `(user_email,scope_type,scope_id)`
- *Natural key:* `(user_email,scope_type,scope_id)`
- *Foreign keys:* none; email and scope IDs are application-resolved
- *Important indexes:* key columns
- *Time semantics:* none
- *Snapshot behavior:* current assignment set
- *Data sensitivity:* **email and authorization scope are restricted**
- *Known limitations:* `scope_id` is polymorphic; interpret only through `scope_type`

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `user_email` | character varying(255) | no | scoped user | — | Restricted; authorization only |
| `scope_type` | character varying(20) | no | hierarchy level/type | check: `zone`,`territory`,`pos` | Select target dimension from this value |
| `scope_id` | integer | no | ID in the typed dimension | polymorphic | Never join simultaneously to all dimensions |

#### Verified joins

Use `user_email=users.email`; then join `scope_id` only to the table identified by `scope_type`. Multiple rows form a scope set.

### `public.page_views`

- *Object type:* table
- *Functional relevance:* dashboard daily unique page-visit marker
- *Use for:* aggregate user-days visiting each page
- *Do not use for:* business-domain metrics or user-level output
- *Row grain:* user email-page-view date
- *Approximate row count:* catalog estimate should be treated as stale
- *Primary key:* `id`
- *Natural key:* unique `(user_email,page_name,view_date)`
- *Foreign keys:* none
- *Important indexes:* user email, page name, view date (application DDL)
- *Time semantics:* `view_date` is reporting date; `created_at` is event timestamp
- *Snapshot behavior:* one stored row per user/page/day
- *Data sensitivity:* **email is direct PII**
- *Known limitations:* no enforced user FK; cannot count multiple visits/refreshes within one day

| Column | PostgreSQL type | Nullable | Meaning | Valid values or constraints | NL2SQL guidance |
|---|---|---:|---|---|---|
| `id` | bigint | no | event ID | PK | — |
| `user_email` | text | no | viewer email | — | Restricted; never return/sample |
| `page_name` | text | no | viewed page key | — | Aggregate only; bound result cardinality |
| `view_date` | date | no | view date | — | Primary time filter |
| `created_at` | timestamp with time zone | no | event timestamp | default now | — |

#### Verified joins

`user_email=users.email` is an application/logical identity join. Avoid it for ordinary usage totals and never expose identity. `COUNT(*)` measures unique user-page-days, not raw view events.

## Supporting-object inventory

The 44 detailed relations above plus the 79 supporting tables below account for all **123 live `public` tables** observed on 2026-09-05 UTC. Supporting objects are grouped to keep retrieval compact; they are not preferred facts unless a question explicitly matches their documented purpose.

| Object group | Live tables | Intended role | NL2SQL caution |
|---|---|---|---|
| Core sales ingest, backfill and legacy upload marts (28) | `api_sync_log`, `backfill_chunks`, `backfill_jobs`, `rbd_forward_sync_days`, `rbd_forward_sync_log`, `sales_uploads`, `sales_dm_rows`, `sales_agency_performance`, `sales_ancillary_breakdown`, `sales_booking_windows`, `sales_channel_performance`, `sales_competitor_pricing`, `sales_daily_ancillary`, `sales_daily_booking_window`, `sales_daily_channel_revenue`, `sales_daily_fare_class_revenue`, `sales_daily_fare_type`, `sales_daily_sector_revenue`, `sales_fare_class_mix`, `sales_fare_type_mix`, `sales_leader_territories`, `sales_overview`, `sales_passenger_profiles`, `sales_quarterly_data`, `sales_revenue_trend`, `sales_sector_details`, `sales_sector_summary`, `sales_team_master` | Sync audit, forward-sync coordination, uploaded dashboard-model rows and derived/cached chart tables | For canonical tickets/revenue/yield, use `pnr_flight`. Do not add cached marts to their sources. Several upload marts encode display-ready values rather than governed reusable facts. |
| Uploaded hierarchy snapshots and leadership mapping (8) | `hierarchy_agencies`, `hierarchy_lf_targets`, `hierarchy_market_leaders`, `hierarchy_revenue_targets`, `hierarchy_summary`, `hierarchy_sync_log`, `hierarchy_uploads`, `pos_leader_pos` | Flat upload/version snapshots, targets, market-leader/leader-to-POS mapping | Prefer normalized `zones → territories → pos → agencies` for current hierarchy. Upload rows can repeat entities by version; select the intended active/latest upload. |
| Revenue Tracker, ancillary and group sync/cache support (7) | `anc_sync_log`, `rt_sync_log`, `rt_agency_sector_mtd`, `group_sync_log`, `gr_advance_sync_log`, `gi_leg_displacement_cache`, `gi_sync_log` | Pipeline audits, legacy monthly sector aggregate and computed group-intelligence cache | `rt_agency_sector_mtd` is live but stale/orphaned (exact count 1,050); current reads use `rt_agency_sector_daily`. Cache JSON is derived and not booking history. |
| Pricing Portal and FSI support (6) | `pp_backfill_days`, `pp_backfill_jobs`, `pp_predicted_fares`, `pp_rbd_inventory_sync_log`, `pp_sync_log`, `fsi_sync_log` | Backfill state, synchronization audit and forecast placeholder | `pp_predicted_fares` is live but exactly empty and has no observed application read/write path. Logs are not business facts. |
| Uploaded operations derivatives (10) | `daily_delay_breakdowns`, `daily_trends`, `dashboard_summary`, `data_coverage`, `data_quality`, `delay_breakdowns`, `reason_breakdowns`, `route_delay_breakdowns`, `station_performance`, `turnaround_phases` | Upload-version-specific chart summaries, quality and delay/turnaround rollups | Join through/select the intended active `dashboard_uploads` version. Do not sum summary and detail sources together; percentage fields need denominator-weighting. |
| MIS, admin expense and Revenue Performance (13) | `admin_expense_monthly`, `admin_expense_uploads`, `admin_sectors`, `cost_impact`, `mis_financials_monthly`, `mis_metrics`, `mis_operations_monthly`, `mis_route_monthly`, `mis_sectors`, `mis_uploads`, `rcs_nrcs`, `revenue_perf_sector_class`, `revenue_perf_targets` | Uploaded finance/operations planning, cost impact, route classification and Revenue Performance targets/classes | These are separate upload/planning pipelines and are not replacements for canonical PNR sales. Select upload/version and confirm metric units before use. |
| System, RBAC, token, mail and crew audit support (7) | `app_settings`, `permissions`, `role_permissions`, `roles`, `revoked_tokens`, `rm_email_log`, `crew_sync_log` | Application configuration, permission graph, token revocation, offer-email audit and crew sync audit | Security-sensitive. Never expose token/password/email/error/free-text fields. An email log proves send processing only, not offer exposure, receipt, redemption or uplift. |

### Important supporting-object statuses

- `public.pp_predicted_fares`: live schema, exact row count **0** on 2026-09-05 UTC; do not route forecast questions here.
- `public.rt_agency_sector_mtd`: live exact row count **1,050**, but current application SQL reads/writes `rt_agency_sector_daily`; treat MTD as legacy/orphaned.
- `fareclassdetails_history` and `rate_gain_matrix_history`: optional legacy source names referenced in `app/services/competitors_db.py`, but **absent from the live catalogue**.
- `flight_ops`: **not a live relation**. The phrase is a dataframe/test variable in operations extractors.
- No live view, materialized view or public SQL function provides an additional analytical contract.

## Join map and fan-out controls

### Core join paths

```text
zones (1)
  └── territories (many)
        └── pos (many)
              └── agencies (many)

group_requests (1) ──< group_request_routes (many)
dashboard_uploads (1) ──< uploaded operations child rows (many)
crew_master (1) ──< crew_activity / crew_monthly_summary / crew_roster_raw (many)
users (1) ──< user_roles / user_scopes / page_views (many, security-only)
```

| From | To | Join key | Status | Cardinality / control |
|---|---|---|---|---|
| `territories` | `zones` | `territories.zone_id=zones.id` | Physical FK | many-to-one |
| `pos` | `territories` | `pos.territory_id=territories.id` | Physical FK | many-to-one |
| `agencies` | `pos` | `agencies.pos_id=pos.id` | Physical FK | many-to-one |
| `pnr_flight` | hierarchy/agency | normalized `point_of_sale` compared to agency code/name/channel by application rules | **Inferred, no FK** | Can match zero/multiple agencies. Resolve to one mapping table first; report unmapped/ambiguous rates. |
| `rt_agency_daily` / `rt_agency_sector_daily` | hierarchy | stored agency/zone/territory/POS keys/IDs | App-resolved; several physical dimension FKs | Daily and sector rows are different grains. Aggregate sector rows before comparison to agency-day. |
| `anc_agency_sector_daily` | hierarchy | stored nullable IDs | Physical FKs where populated | Preserve null/unmapped rows with left joins. |
| `group_request_routes` | `group_requests` | `request_ext_id=external_id` | Physical FK | Header measures repeat once per leg; aggregate legs first. |
| `group_requests` | `agencies` | `agency_external_id=agencies.external_id` | Inferred, no FK | Left join; group stores denormalized labels/IDs. |
| `gr_advance_paid_pnrs` | hierarchy | stored nullable IDs | App-resolved, no live FKs in documented DDL | Never expose PNR; aggregate first. No group-request linkage. |
| `rm_offers` | hierarchy | target ID membership in integer arrays | No FK | `unnest` multiplies offers; use `EXISTS`/array membership or deduplicated bridge CTE. |
| `rm_offers` | `fsi_sector_daily` | normalized sector + departure date | Application join | FSI is sector-day current context, not publish-time offer state. |
| PNR / inventory / schedule | each other | normalized flight number + route + typed departure date | Inferred | Both sides can be many-row; aggregate to a single flight instance first. |
| `pp_rbd_inventory_daily` | monthly inventory | sector + flight + calendar month | Rollup lineage | Use either daily or monthly source, never both in one total. |
| `pp_fare_class_history` | flight/inventory | capture + flight + departure + class | Logical history | Select exactly one capture/as-of rule before joining. |
| `pp_competitor_fares` | `pp_predicted_fares` | `(id,version)=(fare_id,fare_version)` | Physical FK | Prediction table is empty; current fare still requires `is_current=true`. |
| competitor history | internal facts | market/sector + departure date, optionally normalized flight | Analytical only | Multiple carriers/sites/itineraries: pre-aggregate to explicit comparison grain. |
| operations children | `dashboard_uploads` | `upload_id=id` | Physical FK | Filter active/version before totals; overlapping uploads can duplicate dates. |
| `crew_activity` | `crew_master` | `staff_id` | Physical FK | Identity restricted; filter one `data_type`. |
| `crew_monthly_summary` | activity | staff + month + data type | Rollup lineage | Do not join summary to detail for additive metrics. |
| RBAC tables | `users` | email/name keys | Application/DDL relationships | Authorization evaluation only; role × permission × scope fan-out is expected. |

### Fan-out checklist

1. State the target output grain before joining.
2. Aggregate every many-side to that grain in a CTE.
3. Use `COUNT(DISTINCT group_requests.external_id)` after any route join.
4. Keep PNR header measures away from route, RBD, offer-target and schedule expansions.
5. Never sum flight-level repeated fields (`pp_inventory.total_sold_seats`, `total_net_fares`, `g_class_revenue`) across RBD rows.
6. Select one upload version, competitor version, inventory capture and date lens before aggregation.
7. Recompute rates from additive numerators/denominators; do not average stored percentages.

## Metric catalogue

| Metric | Governed formula/source | Unit | Date/grain | Additivity and restrictions |
|---|---|---|---|---|
| Canonical tickets | `SUM(public.pnr_flight.seat_count)` | seats/tickets | `flight_date`; requested dimensions | Additive. This is the displayed sales definition. |
| Canonical booked revenue | `SUM(public.pnr_flight.total_amount)` | source currency amount | `flight_date` unless booking-date question | Additive. Currency is not stored on the row; deployment is assumed INR but must be confirmed. |
| Canonical average yield | `SUM(total_amount) / NULLIF(SUM(seat_count),0)` from PNR | currency/seat | requested aggregate grain | Ratio; never `AVG(net_yield)` or average subgroup yields. |
| Booking lead time | `booked_to_flight_gap_day` or guarded `flight_date-booked_date` | days | booking event/PNR-flight | Average/percentile, not additive. |
| Raw PNR ancillary revenue | Sum selected PNR fee columns (`cancellation_fee`, `reissue_fee`, `convenience`, excess-baggage, seat/smart-ticket fields) | currency | PNR-flight | Scope categories explicitly; may not reconcile to dedicated ancillary feed. |
| Dedicated ancillary revenue | `SUM(ancillary_total)` or explicit sum of `anc_*` buckets in `anc_agency_sector_daily` | currency | POS-sector-departure day | Additive across disjoint grain. `sale_date` is departure date. |
| Revenue Tracker pax | `SUM(fit_pax + group_pax)` or `SUM(total_pax)` from one RT fact | passengers | agency/day or agency/sector/day | Do not combine daily and sector pipelines. |
| Revenue Tracker revenue | `SUM(fit_revenue + group_revenue)` or `SUM(total_revenue)` from one RT fact | currency | agency/day or agency/sector/day | Separate source from canonical PNR revenue. |
| Internal contribution share | scoped entity RT measure / total measure from the same RT source/window | percent | matching source/grain | Not external market share. |
| Target attainment | `actual / NULLIF(target,0)`; variance `(actual-target)/NULLIF(target,0)` | ratio/percent | matching entity-month | Use matching metric source and target type; label assumptions. |
| Group request count | `COUNT(DISTINCT group_requests.external_id)` | requests | usually `requested_date` | Distinct required after route join. |
| Group approved/converted count | `COUNT(DISTINCT external_id) FILTER (WHERE status_id=6)` | requests | request date unless specified | Application-defined current-state rule, not conversion-event history. |
| Group approval rate | approved request count / request count | percent | same date lens and scope | Current-state ratio; do not average cached rates. |
| Average group size | `SUM(pax_count)/NULLIF(COUNT(DISTINCT external_id),0)` | passengers/request | request grain | Do not sum route seats for round trips. |
| Group approved value | `SUM(total_approved)` under explicit status/amount rule | currency | request grain | Current snapshot; application may use quoted value fallback in some pages. |
| Current RBD load factor | `SUM(sold_seats)::numeric / NULLIF(SUM(authorized_units),0)` over paired nonnull daily inventory | ratio | flight/sector/date | Current state only; preserve unknown capacity. |
| Monthly RBD load factor | `100 * SUM(total_sold_seats) / NULLIF(SUM(total_authorized_units),0)` | percent | monthly rollup grain | Stored monthly percentage is 0–100; weight by totals. |
| FSI current yield | `current_revenue/NULLIF(current_sold,0)`; stored `current_yield` | currency/seat | sector-flight date | Deterministic rollup, not canonical PNR yield. |
| FSI revenue gap | stored `gap` = aspirational revenue less current revenue by application | currency | sector-flight date | Heuristic target gap, not forecast error. Require sufficient history for benchmark claims. |
| Current competitor fare | `MIN(price)` (or explicitly requested statistic) from `pp_competitor_fares WHERE is_current=true` | currency | requested market/date/carrier | Nonadditive. Use `query_sector` for requested market. |
| Competitor historical best fare | `MIN(outbound_fare)` after explicit capture/date/carrier/site/directness scope | currency | capture-market-departure-carrier | Compare equal capture DTD. `outbound_stop=0` means direct. |
| Offer face value | `rm_offers.amount_inr` for selected valid/latest definition | INR per application offer | flight/departure/validity/version | Definition only; not expense, payout, redemption or revenue. |
| Route OTP | recompute on-time count / total flights when count fields are available | percent | selected upload/route | Weight by flight counts; do not average route percentages. |
| Crew productive hours | `SUM(total_productive_hrs)` from monthly summary or categorized detail | hours | month + one `data_type` | Identity restricted. Do not add component hours to total. |
| Crew contract variance | `SUM(total_productive_hrs)-SUM(contracted_hours)` or sum stored variance | hours | staff-month, then aggregate | Filter PLANNED or ACTUAL; suppress individual/small-cell output. |
| Legacy RBD tickets/revenue/yield | fields/aggregates in `rbd_daily` and `sales_metrics_*` | seats/currency | their documented departure grain | Label **legacy/RBD-derived**. Never silently substitute for canonical displayed PNR metrics. |

## Categorical catalogue

All counts in this section are exact bounded `GROUP BY` observations made on **2026-09-05 UTC**. They are a point-in-time data profile, not database constraints. Blank and null states are semantically distinct; no names, PNRs, emails or agency values were inspected or listed.

| Relation.column | Exact observed values and row counts | NL2SQL use |
|---|---|---|
| `pnr_flight.fare_type` | `Armed Forces` 3,855; `Standard` 431,331 | Exact-case filter; this is not a governed B2C/Series taxonomy. |
| `group_requests.status_id` | `0` 11,586; `1` 7; `2` 46; `3` 11; `4` 27; `5` 7; `6` 1,395; `7` 750; `8` 3; `9` 4,356 | Use `6` for app-defined approved/converted. Status is current snapshot. |
| `group_requests.status_label` | blank 11,586; `Cancelled` 7; `Counter quote submitted` 4; `Expired` 4,356; `Final quote submitted` 20; `Finalized` 1,395; `Negotiation` 7; `Quote sent via mail` 38; `Quoted` 8; `Rejected` 757; `Request Logged` 7; `Withdraw` 3 | Prefer numeric status for conversion; preserve blank as unknown/unpopulated. |
| `group_requests.trip_type` | blank 11,586; `One Way` 2,823; `Round Trip` 3,779 | Exact-case filter; blank is not one-way. |
| `group_requests.currency` | `INR` 18,188 | Still keep currency filter in monetary SQL where possible. |
| `group_metrics_summary.dimension_type` | `agency` 541; `overview` 1; `pos` 146; `territory` 32; `zone` 6 | Always filter one dimension type; agency values remain restricted. |
| `group_metrics_daily.dimension_type` | `requested` 342; `travel` 626 | Always choose one date lens. |
| `group_metrics_daily.dimension_value` | `ALL` 968 | Filter `ALL`; no further dimension split exists in this cache. |
| `rt_targets.entity_type` | `agency` 9; `zone` 2 | Semantics of `entity_key` depend on type. |
| `rm_offers.applies_to_all_agencies` | `false` 2; `true` 9 | For false, evaluate target arrays; empty arrays need application semantics. |
| `pp_sector_details.sector_type` | `Non-RCS` 20; `RCS` 20 | Exact filter. |
| `pp_rbd_fares.fare_type` | `normal` 800; `via` 50 | Choose explicitly; fares are not interchangeable. |
| `pp_inventory.authorized_units_source` | `upstream` 4,300 | Preserve provenance; exact aggregate also shows catalogue row estimate was stale. |
| `pp_competitor_fares.operator` | `Fly91` 15,462; `OAL` 24,613 | Broad own-versus-other label; use airline code in history for carrier detail. |
| `pp_competitor_fares.is_current` | `false` 35,641; `true` 4,434 | **Filter true for every current-fare query.** |
| `pp_competitor_fare_history.airline_code` | `6E` 145,060; `9I` 3,817; `AI` 90,031; `IC` 29,378; `IX` 26,789; `QP` 3,102; `S5` 3,189; `SG` 413 | Exact carrier filters; do not infer airline names in SQL. |
| `pp_competitor_fare_history.site_code` | `MMTD-M` 301,779 | Source/site dimension; observed rows can change during sync. |
| `pp_competitor_fare_history.outbound_stop` | `0` 78,780; `1` 186,948; `2` 35,825; `3` 226 | Use `0` for direct-only comparison. |
| `fsi_sector_daily.capacity_source` | `fallback` 28; `synced` 247 | Filter/segment quality when capacity matters. |
| `fsi_sector_daily.aspirational_yield_source` | `day_of_week` 114; `pooled_2yr` 104; `same_date_last_year` 57 | Label benchmark method. |
| `fsi_sector_daily.has_sufficient_history` | `true` 275 | No false row was observed; still keep the guard for future data. |
| `crew_activity.data_type` | `ACTUAL` 50,295; `PLANNED` 14,689 | Mandatory filter; omitting it mixes snapshots. |
| `crew_activity.duty_category` | `Compliance` 1,996; `Inefficiency` 3,827; `Productive` 39,896; `Unavailable` 19,226; `Unknown` 39 | Aggregate only; no staff-level output. |
| `crew_master.crew_type` | `AirCrew` 71; `CabinCrew` 76 | Aggregate only. |
| `crew_master.is_active` | `true` 147 | All observed master rows active; no false row observed. |

## Date, period and snapshot guide

| Source | Business date / verified live coverage | Storage | Observation/version rule |
|---|---|---|---|
| `pnr_flight` | `flight_date` 2024-11-01..2027-02-12 | `varchar(10)` | Current PNR-flight state; use `booked_date` for booking-date questions. |
| `rbd_daily` | departure date 2024-11-01..2027-02-12 | `varchar` date fields | Departure/RBD fact; separate from PNR canonical metrics. |
| `sales_metrics_daily` | daily coverage 2024-11-01..2027-02-12 | string date | Legacy cached daily metric. |
| RT agency facts | `sale_date` 2026-02-01..2026-08-31 | `date` | Current daily rollups; agency and sector tables are different grains. |
| `anc_agency_sector_daily` | `sale_date` 2026-01-01..2026-08-23 | `date` | Despite name, this is PNR departure date. |
| `group_requests` | `requested_date` 2025-08-26..2026-09-04 | `varchar(10)` | Current request state, not status event history. |
| `group_request_routes` | `travel_date` through 2027-05-31 | `varchar(10)` | Multi-leg travel; route date is preferred over header for leg analysis. |
| `group_metrics_daily` | 2025-08-26..2027-05-31 | `varchar(10)` | Filter `dimension_type='requested'` or `'travel'`, never both. |
| `gr_advance_paid_pnrs` | sales/departure coverage 2026-04-01..2026-07-20 | documented relation fields | Separate restricted sales sync; no request conversion link. |
| `rm_offers` | departures 2026-08-30..2026-09-11 | `date` | Validity is inclusive; latest `created_at` per flight/departure is current definition. |
| `pp_inventory` | `flight_date` 2026-08-10..2026-09-24 | `date` | Current upsert, no capture history. |
| `pp_load_factor` | `flight_date` 2026-09-04..2026-11-19 | `date` | Current latest state. |
| `pp_rbd_inventory_daily` | `flight_date` 2026-04-01..2027-02-27 | `date` | “Daily” means departure-day grain, not daily capture history. |
| `pp_fare_class_history` | capture/departure range not certified in this document | two `date` columns | True observation history. Compute DTD as `flight_date-capture_date`; compare equal DTD. |
| `pp_competitor_fares` | current fare/departure dates 2026-08-10..2026-10-19 | `date` | Versioned current state; filter `is_current=true`. |
| competitor fare history | captures 2026-08-10..2026-09-04 | capture and departure `date` | True daily captures; `synced_at` is ingest time. |
| schedule tables | effective ranges, not certified operation coverage | `date` ranges plus time strings | Apply inclusive effective range/frequency; definition is not proof of operation. |
| active operations uploads | coverage inherited from selected upload | parent coverage strings + child dates | Select active/intended upload and resolve overlap before totaling. |
| crew facts | 2026-01..2026-09 | `date` and `YYYY-MM` | `PLANNED` first-capture lock; `ACTUAL` latest upsert. Filter one type. |
| `page_views` | `view_date` plus `created_at` | `date`, `timestamptz` | Append event log; not a business-event source. |

For string dates, use guarded conversion when validity is not guaranteed:

```sql
CASE
  WHEN date_text ~ '^\d{4}-\d{2}-\d{2}$' THEN date_text::date
END
```

Do not compare capture/ingest timestamps with business dates as though they were the same event.

## Privacy, security and authorization

### Restricted fields and relations

- **Booking identifiers:** `pnr_flight.pnr` and identifiers in `gr_advance_paid_pnrs` must never be returned, sampled or used as display dimensions. `COUNT(DISTINCT pnr)` is allowed only when authorized and aggregate output is sufficiently broad.
- **Group PII:** `group_requests.contact_email`, `contact_mobile`, `requested_by`, request codes, agency names and raw JSON are restricted. Use request counts and business aggregates.
- **Agency/commercial identity:** agency names/codes, POS text, key-partner labels and offer target-name arrays are restricted. Prefer normalized numeric geography levels and aggregate results; the document intentionally contains no agency names.
- **Crew/HR:** `crew_master.staff_id`, `name`, remarks, trainer status and all person-level roster/activity fields are restricted. Never return staff schedules or performance. Sick/leave data requires extra care and small-cell suppression.
- **Authentication:** `users.password` must never appear in any `SELECT`, comparison, export or aggregate. Emails/names, revoked tokens, roles, permissions, scopes and audit identity are security-sensitive.
- **Raw/open payloads:** group `raw`, competitor `payload`, operations JSON and cache JSON can contain undocumented sensitive/high-cardinality fields. Do not use `jsonb_each`, broad path discovery or generic expansion in NL2SQL.
- **Commercially sensitive:** future fares, competitor fares, inventory, load factors, offer values, targets and forecasts/heuristics must follow application authorization.

### Authorization model

`users`, `roles`, `permissions`, `user_roles`, `role_permissions` and `user_scopes` form the application access model. `scope_type` determines which dimension `scope_id` references. The database context does not grant access: the caller's authenticated role, permission and scope filters must be applied by the application before or inside generated SQL.

Do not infer “unrestricted” merely because a user has no `user_scopes` row; that default is application logic and must be evaluated by the trusted authorization service. Never let user-provided text choose or bypass scope predicates.

### Safe output policy for NL2SQL

Return aggregate business results only. Default to bounded grouped output, omit direct identifiers, suppress sensitive small groups, and never display free text. Parameterize user values. Refuse requests for credentials, token material, names, emails, phones, PNRs, staff IDs, raw JSON or individual employee/agency performance.

## Unsupported and missing-data matrix

| Requested concept | Availability | What exists | Required response |
|---|---|---|---|
| Series agreements, commitments, allotments, releases, utilization | **Absent** | No governed Series entity/fact | State unavailable; do not infer from `fare_type`, group requests or chart-series terminology. |
| Campaign/offer exposure | **Absent** | `rm_offers` definitions; `rm_email_log` send processing | Do not claim recipient exposure/view. |
| Offer redemption/payout/booking attribution | **Absent** | Offer face-value definition only | No redemption, payout, ROI or attributed revenue metric. |
| Causal uplift/control group | **Absent** | No treatment assignment or experiment table | Never make causal claims; at most label a noncausal before/after estimate if explicitly requested and approved. |
| Holiday/event calendar | **Absent** | Ordinary dates only | No holiday effect unless external governed calendar is supplied. |
| External market share | **Absent** | Internal sales contribution; target metadata `market_share_pct` | Do not label internal share as market share. |
| Persisted trained demand forecast | **Absent** | FSI heuristic rollup; empty `pp_predicted_fares` | State unavailable; do not call FSI a trained forecast. |
| Price optimization recommendations/rules/overrides | **Absent live** | Offer definitions and operational fare/inventory references | Do not invent optimization-run relations. |
| `pp_predicted_fares` observations | **Schema-only/empty** | Live table with 0 rows | Return no-data/schema-only status. |
| Optional legacy competitor source tables | **Absent live** | Code references `fareclassdetails_history`, `rate_gain_matrix_history` | Use live `pp_*` history tables instead where semantically appropriate. |
| `flight_ops` relation | **Absent** | Dataframe/test variable in upload extractor | Use active upload child tables; do not query `flight_ops`. |
| Current sector MTD source | **Legacy/orphaned** | `rt_agency_sector_mtd` has 1,050 rows | Use `rt_agency_sector_daily`; aggregate to month. |
| Normalized PNR→agency FK | **Absent** | Inferred normalized POS-text application match | Label inferred; quantify ambiguous/unmapped results. |
| Effective-dated sales hierarchy | **Absent** | Current normalized hierarchy and upload snapshots | Cannot reconstruct exact historical ownership without governed snapshots. |
| Group status/quote history | **Absent** | Current upserted request state | No transition time, time-to-stage or historical funnel reconstruction. |
| Offer targeting outcome | **Absent** | Territory/agency target arrays | Target eligibility is not receipt, redemption or response. |
| Complete refund/payment/passenger lifecycle | **Absent** | Fees and current sale/RBD aggregates | Do not infer payment settlement, refund status or passenger history. |
| Governed flown-versus-booked reconciliation | **Not established** | PNR booked revenue and RBD-derived revenue | Present separately and label; owner must define reconciliation. |
| Views/materialized views/public analytical functions | **Absent** | 123 base tables | Query documented base tables only. |

## Critical NL2SQL instructions

1. Generate only a single read-only `SELECT` statement (CTEs are allowed). Never emit DDL, DML, `COPY`, administrative functions or transaction-setting SQL to end users.
2. Fully qualify relations with `public.` and use only live objects documented here. Never invent a table from a UI label or code-only optional name.
3. For displayed sales tickets/revenue/yield, use `pnr_flight` and the canonical formulas. Label `rbd_daily`, `sales_metrics_*`, RT, FSI and upload metrics as separate pipelines.
4. Choose the correct date lens: departure, booking, request, travel, sale/reporting, capture, validity or ingest. State it in the answer.
5. Guard `varchar` dates before casting. Use half-open date windows where practical: `date >= :start AND date < :end_exclusive`.
6. Current competitor fare SQL must contain `pp_competitor_fares.is_current = true`.
7. For current offers, require `start_date <= :as_of_date AND end_date >= :as_of_date`, then select the latest `created_at` per flight/departure if multiple definitions qualify.
8. Filter exactly one crew `data_type` (`PLANNED` or `ACTUAL`) and one group daily date lens (`requested` or `travel`).
9. State the target grain, pre-aggregate every many-side and verify that joins do not multiply facts. Use distinct request IDs after route joins.
10. Recompute yields, load factors, rates, shares and OTP from summed numerators/denominators. Never average percentages or average yields.
11. Preserve null as unknown for inventory/capacity/benchmark fields. Do not convert missing observations to zero unless the business definition explicitly says zero.
12. Select one snapshot/version: active upload, one competitor version/current row, one fare-class capture/as-of row, or one current rollup.
13. Do not sum repeated flight-level fields across RBD rows. Do not combine detail and rollup/cached sources in the same total.
14. Treat PNR POS→agency and cross-pipeline flight/route joins as inferred. Normalize case/spacing/flight prefixes in a CTE and report mapping assumptions.
15. Never `SELECT *`. Name only necessary columns, avoid raw JSON/free text, and apply a result limit to nonaggregate detail/dimension queries.
16. Never expose names, agency labels, emails, phones, PNRs, staff IDs, passwords, tokens, request codes or raw payloads. Refuse individual-level crew/user/agency requests.
17. Apply caller RBAC and typed scope predicates from trusted application context. Generated SQL must not decide that absence of a scope means unrestricted access.
18. Do not claim unavailable constructs: Series, exposure, redemption, payout, causal uplift, holidays, external market share, persisted forecast or optimization runs.
19. Monetary totals from sources without row-level currency must be labelled with the deployment assumption; do not combine currencies without conversion.
20. Include source, formula, date lens, filters, grain and major caveat in the natural-language result explanation.

## Evidence appendix

### Live inspection

Read-only live inspection used only the application's `.env` `DATABASE_URL` with `psycopg2`, explicit read-only transactions and `SET LOCAL statement_timeout`. Queries were restricted to PostgreSQL catalog metadata, aggregate date bounds, exact bounded category counts and exceptional table counts. No credentials, direct identifiers, raw rows or high-cardinality values were printed or copied into this document.

Live evidence established:

- PostgreSQL 15.15 Homebrew, database `fly91_dashboard`, `public` schema.
- 123 tables, zero views, zero materialized views, no public analytical function and no relation/column comments.
- Column types/nullability, keys and indexes used in detailed sections.
- The date windows and approximate catalogue sizes recorded above.
- Exact bounded category profiles, `pp_predicted_fares=0`, `rt_agency_sector_mtd=1,050`, and current/noncurrent competitor version counts.

### Repository evidence

| Evidence location | Contract evidenced |
|---|---|
| `app/services/metrics.py`, `_METRIC_AGGREGATES` and sales query helpers | Canonical displayed tickets `SUM(seat_count)`, revenue `SUM(total_amount)`, average yield revenue/tickets; POS scope behavior. |
| `app/services/sales_db.py`, embedded `_DDL` and save/load functions | `pnr_flight`, `rbd_daily`, sales metric tables, sync lineage and string-date schemas. |
| `app/services/revenue_tracker_schema.py` | Current RT daily/sector-daily tables, target/key-partner DDL and indexes. |
| `app/services/revenue_tracker_sync.py` | Current writes to `rt_agency_sector_daily`; daily rollup behavior. |
| `app/services/revenue_tracker_queries.py`, `app/services/agency_analysis_queries.py` | Current reads, hierarchy filtering, daily-to-month aggregation and contribution semantics. |
| `app/services/ancillary_tracker_schema.py` and ancillary tracker sync/query services | Dedicated ancillary grain, hierarchy resolution and PNR-derived departure-date lineage. |
| `app/services/group_quote_db.py`, `_DDL`, sync and aggregation functions | Group request/route schema, current-state upsert, status-6 approval rule and caches. |
| `app/services/group_intelligence_queries.py` | Group joins to RT/targets and group-intelligence cache usage. |
| `app/services/rm_offer_schema.py` | Offer-definition-only schema, arrays, indexes and version rows. |
| RM offer route/service SQL | Inclusive validity, latest created definition and FSI context join; no outcome event schema. |
| `app/services/pricing_portal_schema.py` | Pricing reference, schedule, inventory, competitor versioning and true capture-history DDL. |
| Pricing Portal sync/route services | Current-state upserts, `is_current` lifecycle, query-sector/native-sector distinction and current fare reads. |
| `app/services/competitors_db.py` | Optional legacy table names and diagnostic expectations; live catalogue confirmed both optional relations absent. |
| `app/services/forward_sales_intelligence_schema.py` | Live FSI sector-day columns, indexes and sync table. |
| FSI calculation/query services | Capacity provenance, current sold/revenue/yield, aspirational benchmark sources and gap semantics. |
| `app/services/crew_db.py`, `_DDL` and summary functions | Crew master/raw/activity/monthly schema; planned first-capture lock, actual upsert and monthly formulas. |
| `app/services/auth_service.py`, schema initialization | `users`, roles, permissions, assignments/scopes and credential fields. |
| `app/services/analytics_db.py`, page-view DDL | Page-view event schema and usage tracking. |
| `app/services/data_extractor.py` and `app/services/extractors/` | Operations workbook extraction; `flight_ops` is an in-memory dataframe, not a table. |
| Live git state | Revision `ee55f0cd654db80450c01fe66d6e943fd54ddd32`, branch `feature/group-intelligence`. |

When live DDL differs from embedded create-if-missing code, this document follows the live PostgreSQL catalogue and records code semantics only where compatible.

## Open questions for data owners

1. Does `pnr_flight.total_amount` always represent INR, and exactly which taxes, fees, cancellations, reissues and refunds are included?
2. What business event does `rbd_daily.total_revenue` represent, and may it be called realized/flown revenue?
3. What is the authoritative normalization and tie-break rule for `pnr_flight.point_of_sale` to agency/POS/hierarchy?
4. Are all string business dates guaranteed valid ISO values, or should invalid values be quarantined?
5. What is the external denominator and period definition for `rt_targets.market_share_pct` and the meaning of `mom_variance_pct`?
6. Is group conversion permanently defined as current `status_id=6`, and when should quoted value substitute for approved value?
7. Are group route fares per passenger, per leg total, or another unit?
8. What do `pp_inventory` flight-level total fields represent, and why are they repeated at RBD grain?
9. Are competitor fares tax-inclusive and comparable across sites/operators; what direct/connecting itinerary rule is governed?
10. What timezone applies to schedule time strings and crew timezone-naive datetimes?
11. Can effective schedule rows overlap for the same flight/route, and what is the precedence rule?
12. What are the governed formulas/windows for FSI historical benchmarks and capacity fallback?
13. Does `rm_offers.amount_inr` mean per booking, per passenger or another payout basis, and what does an empty target array mean?
14. How should overlapping active operations uploads be prioritized?
15. Should `rt_agency_sector_mtd` be removed/migrated, and will `pp_predicted_fares` become an active persisted model output?
16. What minimum group size/suppression policy is required for crew, user, agency and other sensitive aggregates?
