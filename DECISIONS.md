#### `DECISIONS.md`
```markdown
# Architectural Decisions

## 1. Safety Classifier Failure Policy: Fail-Closed vs. Fail-Open

- **Choice**: **Fail-closed** (`safety_node` catches timeouts/provider errors, increments `classifier.failure_count`, logs `safety_classifier_failure`, and routes to `medical_advice_node` without executing any tools).
- **Rejected Option**: **Fail-open** (allowing unverified messages through to `router_node` and tools when the classifier times out or errors).
- **What Our Choice Costs**: During a classifier outage, legitimate `SAFE` users trying to book a routine appointment or check clinic hours are temporarily blocked and asked to retry or call the wellness room. In a healthcare triage setting, however, blocking a benign booking is far safer than letting an undetected emergency or medication-dosage query bypass the safety gate.

## 2. Preventing Double-Booking in the Database

- **Choice**: A PostgreSQL **partial unique index** (`CREATE UNIQUE INDEX uq_clinician_active_slot ON service_requests (clinician_id, scheduled_for) WHERE status IN ('CONFIRMED', 'PENDING')`), paired with `IntegrityError` handling in `book_service` that rolls back and suggests nearest free slots.
- **Rejected Option**: Application-level `SELECT ... WHERE clinician_id = X AND scheduled_for = Y` check-then-insert in Python.
- **What Our Choice Costs**: It requires catching database-specific `IntegrityError` exceptions, rolling back the failed transaction, and running a follow-up query to compute nearest open slots, while tying concurrency enforcement to PostgreSQL partial index semantics. Unlike a Python check, however, a database unique index is atomic under simultaneous requests and allows cancelled slots (`status = 'CANCELLED'`) to be rebooked cleanly.

## 3. Crash-After-Write Idempotency Analysis

If `book_service` commits the `service_requests` row to PostgreSQL and the FastAPI worker dies before streaming the SSE `final` event back to the client, the patient's retry sends the exact same message with the unchanged client-generated `request_id` UUID. In our system, `book_service` queries `service_requests WHERE request_id = :request_id` first (and is also protected by the `UNIQUE` constraint on `service_requests.request_id` if a race occurs); it finds the already-committed row, skips inserting a duplicate booking, and returns the exact original booking details (`idempotent_replay: True`) so the patient receives confirmation of that single appointment. This is the right outcome because network timeouts or mid-stream crashes never create duplicate appointments or lock out the patient's own slot.