# Raw Tables Schema Documentation

This document defines the schema, column details, and functional relevance of the raw data tables (direct API sources) in the `dashboardbe` codebase. This context is intended for an LLM to understand the underlying data structures in order to convert natural language queries into accurate SQL statements.

---

## 1. `crew_roster_raw`
**Source API:** ARMS Roster API (`GetRoster`)  
**Functional Relevance:** This table stores the raw payload from the crew rostering system. It tracks the duty schedules for both 'AirCrew' and 'CabinCrew', including the flights they are assigned to and the nature of their duty.

### Columns:
- `id` (BIGSERIAL, PK): Unique auto-incrementing ID.
- `sync_id` (INTEGER): Foreign key linking to `crew_sync_log(id)`.
- `staff_id` (VARCHAR): The unique identifier for the crew member.
- `crew_type` (VARCHAR): E.g., 'AirCrew', 'CabinCrew'.
- `flight_date` (DATE): The date of the flight or duty.
- `role_raw` (VARCHAR): The specific role assigned (e.g., Pilot, Attendant).
- `duty_raw` (VARCHAR): The type of duty.
- `duty_start_raw` (VARCHAR): Raw string representation of duty start time.
- `duty_end_raw` (VARCHAR): Raw string representation of duty end time.
- `remarks` (TEXT): Any additional comments from the roster.
- `captured_at` (TIMESTAMPTZ): Timestamp when the data was synced.

---

## 2. `pnr_flight`
**Source API:** Sales Report Analytics/PNR Feed API  
**Functional Relevance:** This is the core raw table for all ticketing, revenue, and ancillary transactions. It records the payload for every passenger itinerary (PNR) segment. It captures the date of booking, flight date, point of sale, ticket revenue (`net_yield`), and a granular breakdown of ancillary charges (baggage, meals, seats).

### Key Columns:
- `pnr` (VARCHAR, PK): Passenger Name Record identifier.
- `flight_date`, `flight_number`, `sector` (VARCHAR, PK): Core dimensional keys.
- `fare_class`, `fare_type` (VARCHAR): Details about the ticket fare class.
- `seat_count` (INTEGER): Number of passengers booked under this PNR segment.
- `booked_date`, `time_of_booking` (VARCHAR): When the booking occurred.
- `booked_to_flight_gap_day` (INTEGER): The booking window (days prior to departure).
- `point_of_sale` (VARCHAR): The channel or agency that made the sale.
- `net_yield`, `total_amount` (DOUBLE PRECISION): Base revenue figures.
- `cancellation_fee`, `reissue_fee`, `convenience`, `xbag_3kg`, etc. (DOUBLE PRECISION): Raw ancillary revenue amounts broken down by category.
- `sync_id` (INTEGER): Foreign key to `api_sync_log(id)`.

---

## 3. `rbd_daily`
**Source API:** Sales Report Analytics API (RBD Allocation)  
**Functional Relevance:** Provides daily inventory allocations and load factors grouped by Reservation Booking Designator (RBD) / fare class. 

### Key Columns:
- `flight_date`, `flight_number`, `sector`, `fare_class` (VARCHAR, PK): Dimensional composite key.
- `booked`, `cancellation`, `net` (INTEGER): Ticket volumes for this RBD.
- `no_show_count` (INTEGER): Passengers who failed to board.
- `total_revenue`, `yield_value` (DOUBLE PRECISION): Revenue derived from this specific fare class allocation.
- `departed_load_factor` (DOUBLE PRECISION): The actual load factor representation for the segment.
- `quarter`, `month`, `day` (VARCHAR): Pre-computed temporal dimensions.
- `sync_id` (INTEGER): Foreign key to `api_sync_log(id)`.

---

## 4. `group_requests`
**Source API:** Group Quote API (`GROUP_QUOTE_API_URL`)  
**Functional Relevance:** Captures raw "group booking" quote requests submitted by agencies. Used to measure the pipeline of group sales (Window Shopping vs. Received vs. Converted).

### Key Columns:
- `external_id` (BIGINT, PK): ID from the upstream Group Quote system.
- `requested_date`, `travel_date` (DATE): Temporal dimensions of the quote.
- `status_id`, `status_name` (INTEGER/VARCHAR): Current state of the quote (e.g., pending, approved, finalized).
- `pos_name`, `agency_code` (VARCHAR): The Point of Sale or Agency requesting the quote.
- `pax_count` (INTEGER): The number of passengers requested in the group.

---

## 5. `group_request_routes`
**Source API:** Group Quote API (Child rows)  
**Functional Relevance:** Stores the specific routing information for a given group quote request (Origin to Destination). A single group request can have multiple route segments.

### Key Columns:
- `id` (SERIAL, PK): Local ID.
- `request_ext_id` (BIGINT): Foreign key to `group_requests(external_id)`.
- `origin`, `destination` (VARCHAR): Route endpoints.
- `departure_date` (DATE): Sector departure date.

---

## 6. `pp_flight_schedule`
**Source API:** Pricing Portal API (Flight Schedule Reference)  
**Functional Relevance:** Defines the stable, reference flight schedule from the upstream IBS system.

### Key Columns:
- `upstream_id` (INTEGER, PK): Stable upstream ID.
- `origin`, `destination`, `flight_number` (VARCHAR): Routing data.
- `start_date`, `end_date` (DATE): Validity window of the schedule.
- `operation_freq` (VARCHAR): Days of the week the flight operates.
- `dep_time`, `arr_time` (VARCHAR): Expected timing.

---

## 7. `pp_schedule_time`
**Source API:** Pricing Portal API (Schedule Time Reference)  
**Functional Relevance:** Specific scheduling time records per sector and flight.

### Key Columns:
- `upstream_id` (INTEGER, PK): Upstream record ID.
- `sector`, `flight_no` (VARCHAR): Target flight.
- `departure_time`, `arrival_time` (VARCHAR): Stated timing.
- `frequency` (VARCHAR): Operational cadence.

---

## 8. `pp_rbd_fares`
**Source API:** Pricing Portal API (RBD Fares)  
**Functional Relevance:** Flattened reference table mapping a Sector + RBD + Fare Type to the actual Net and Gross monetary fare amounts.

### Key Columns:
- `sector`, `rbd`, `fare_type`, `fare_class` (VARCHAR): Dimensions.
- `net_fare`, `gross_fare` (NUMERIC): Authorized pricing amounts.

---

## 9. `pp_competitor_fares`
**Source API:** RateGain API (via Pricing Portal)  
**Functional Relevance:** Operational, frequently-updated snapshot of competitor pricing for specific sectors/flights/dates. Preserves price-change history using a `version` / `is_current` strategy.

### Key Columns:
- `id`, `version` (PK): Composite primary key to retain historical fare changes.
- `sector`, `outbound_flight`, `fare_date` (VARCHAR/DATE): Natural key.
- `price` (NUMERIC): The scraped competitor price.
- `query_sector` (VARCHAR): The original requested sector (to handle lookalike routes).
- `is_current` (BOOLEAN): Flag indicating if this is the live, active price (TRUE) or a historical retired price (FALSE).

---

## 10. `pp_competitor_fare_history`
**Source API:** Pricing Portal API (Competitor Fares payload)  
**Functional Relevance:** Deep historical storage of competitor fares. Gives a real fare-vs-days-to-departure curve by maintaining raw, point-in-time API payload rows per `capture_date`.

### Key Columns:
- `capture_date` (DATE): When the data was scraped.
- `departure_date`, `sector`, `airline_code`, `outbound_flight` (DATE/VARCHAR): Flight dimensions.
- `outbound_fare` (NUMERIC): Minimum recorded fare.
- `payload` (JSONB): The complete, raw upstream API JSON response for downstream schema flexibility.

---

## 11. `pp_fare_class_history`
**Source API:** Pricing Portal API (`fare_class_details`)  
**Functional Relevance:** Daily point-in-time snapshots of authorized / sold / remaining seat inventory broken down by fare class. This is the **source system** for tracking booking-velocity curves for current and future departures.

### Key Columns:
- `capture_date` (DATE): Date the snapshot was taken.
- `flight_date`, `flight_no` (DATE/VARCHAR): Flight details.
- `fare_class_code` (VARCHAR): The specific inventory bucket.
- `authorized_seats`, `sold_seats`, `remaining_seats` (INTEGER): Inventory metrics at the time of capture.

---

## 12. `rate_gain_matrix_history`
**Source API:** RateGain History API  
**Functional Relevance:** Captures deep historical fare matrices for competitor pricing velocity analysis. Similar to `pp_competitor_fare_history` but often holds raw JSONB snapshots.

*(Includes JSONB payloads containing matrix arrays)*

---

## 13. `pp_inventory` & 14. `pp_rbd_inventory_daily`
**Source API:** Pricing Portal API / Real RBD Allocation API  
**Functional Relevance:** Additional real-time inventory and RBD allocation snapshots synchronized directly from the operational pricing portal layer. Used to contrast expected inventory allocations vs. actual flown availability.

### Key Columns:
- Typical temporal keys (`flight_date`, `sector`, `flight_no`).
- Inventory state counters (`available`, `allocated`, `sold`). 

---

### Tips for the NL2SQL LLM:
1. **Aggregations vs. Raw Feeds:** The tables documented here are unaggregated. Most end-user questions about "Overall Revenue" or "Total Passengers" should query the rolled-up metric tables (e.g., `rt_agency_sector_daily` or `anc_agency_sector_daily`) rather than manually summing `pnr_flight`, unless they need highly specific filtering (e.g., "Revenue just from 15kg extra baggage").
2. **Current vs History:** When queried for "competitor pricing", ensure you filter `pp_competitor_fares` by `is_current = TRUE` to avoid summing over historical price fluctuations of the same flight.
3. **Joins:** `pnr_flight` handles ticketing. To join ticketing data against inventory limits, you must map via `flight_date`, `flight_number`, `sector`, and `fare_class` to `rbd_daily` or `pp_fare_class_history`.

