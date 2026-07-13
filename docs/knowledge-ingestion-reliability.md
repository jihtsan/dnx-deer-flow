# Knowledge ingestion reliability

Knowledge document ingestion uses the existing `app/knowledge/ingestion.py`
service and the SQL `knowledge_ingestion_jobs` table. SQL is the source of truth;
the in-process wake event only reduces pickup latency.

## State contract

| From | To | Trigger |
| --- | --- | --- |
| `pending` | `leased` | Atomic claim of a due job |
| `retry_wait` | `leased` | Atomic claim after `next_attempt_at` |
| expired `leased` | `leased` | Atomic recovery claim after `lease_expires_at` |
| `leased` | `leased` | Renewal by the same owner and attempt |
| `leased` | `retry_wait` | Retryable failure before `max_attempts` |
| `leased` | `succeeded` | Matching LightRAG tracking result is complete |
| `leased` | `dead` | Permanent failure or exhausted attempts |
| exhausted due/expired work | `dead` | Startup/poll reaper recovers an interrupted final attempt |
| eligible `dead` | `pending` | Idempotent operator retry of the same persisted job |
| active or `dead` | `cancelled` | Internal cancellation boundary reserved for deletion work |

`succeeded` and `cancelled` are terminal. An ordinary manual retry cannot
reactivate them. Every lease mutation is fenced by job ID, `lease_owner`,
`attempt_count`, `status = leased`, and an unexpired `lease_expires_at`. A stale
worker therefore cannot overwrite a later recovery attempt or terminal state.

The public document state remains smaller and user-oriented:

| Job state | Document state |
| --- | --- |
| `pending` | `pending` |
| `leased` / `retry_wait` before a remote tracking ID exists | `pending` |
| `leased` / `retry_wait` after a remote tracking ID exists | `indexing` |
| `succeeded` | `ready` |
| `dead` / `cancelled` | `failed` |

The API returns internal job diagnostics in a nested `ingestion` object, while
the document's business `status` keeps this four-state contract.

## Claim, retry, and restart recovery

Claiming is one SQL `UPDATE ... RETURNING` statement whose candidate and outer
update both carry the due/expiry predicate. It is not a select-then-update flow.
Only the winning update increments `attempt_count`, assigns the owner, and sets
the lease deadline. Multiple processes may run the same worker service; at most
one owns a valid lease for a job at a time. Within one Gateway process the same
service executes at most four claims concurrently, so a slow tracking request
does not starve later documents. A single attempt is also capped at 300
non-terminal tracking polls; reaching the cap is a transient timeout that
releases the lease into `retry_wait`.

Retryable failures use:

```text
delay = min(max_delay, base_delay * 2 ** (attempt_count - 1))
```

The worker persists `next_attempt_at`, the stable sanitized error code/message,
and releases the lease. Permanent failures and failures on the final allowed
attempt enter `dead`. Startup immediately scans the same SQL states used during
normal polling: `pending`, due `retry_wait`, and expired `leased`. An expired
final attempt is reaped to `dead` so it cannot remain stuck forever.

The worker resolves the current hot-reloaded LightRAG configuration before each
attempt. If no usable client is configured, it leaves eligible jobs unclaimed
and does not consume an attempt. Endpoint or credential rotation therefore
takes effect on the next automatic retry without requiring another upload
request or a process restart.

Every accepted manual retry inserts `(job_id, retry_key)` into
`knowledge_ingestion_retry_requests` in the same transaction that reactivates
the existing job. The ledger retains older keys across later retry rounds, so a
delayed replay can return the current persisted state but can never reactivate
the job again.

All repository methods use SQLAlchemy's async sessions, LightRAG uses `httpx`
async I/O, and original-file operations run through `run_file_io`; no ingestion
path performs blocking SQL, filesystem, or network work on the event loop.

## Interrupted remote operations

The three persisted interruption boundaries converge as follows:

1. **After claim commit, before a remote call:** the unrenewed lease expires and
   another worker claims the same job with a higher attempt number.
2. **After upload succeeds, before local tracking commit:** DeerFlow retries the
   same server-generated `storage_name`. LightRAG reports its filename conflict,
   then DeerFlow pages `/documents/paginated` and accepts only an exact filename
   match to recover the original `track_id`.
3. **After tracking succeeds, before local status commit:** the lease expires;
   the next owner resumes the persisted `track_id`, queries tracking again, and
   commits through its own fenced attempt.

LightRAG's pinned upload API does not accept an idempotency key and does not
promise exactly-once insertion. The stable filename/conflict lookup is a
verifiable reconciliation strategy, not an exactly-once claim. Its remaining
limits are remote list consistency and an extremely unlikely pre-existing
filename collision outside DeerFlow. DeerFlow-generated document IDs make such
collisions improbable, and a conflict that cannot yet be reconciled is retried
rather than guessed.

## Error policy

Timeouts, connection failures, HTTP 5xx, rate limits, temporary unavailability,
and unknown worker exceptions are transient. Authentication failures, malformed
or incompatible responses, rejected uploads, missing persisted files, and
LightRAG-reported processing failures are permanent. Stored messages are fixed,
sanitized Chinese descriptions; exception text, document content, API keys, and
full endpoints are never persisted. The manual-retry allowlist contains only
failures an operator can plausibly repair without replacing the source file.
