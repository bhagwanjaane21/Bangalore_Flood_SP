# Phase 5: durable notifications

Notifications are a separate worker. API ingestion saves transitions without
waiting for any messaging provider. No hardware or Telegram credentials are
needed for the prototype demonstration. Run commands from the repository root
using the same Python environment as the backend.

## Dry-run demonstration

Start the worker in terminal A:

```powershell
python 02_backend/notify.py --base-url http://127.0.0.1:8001 --watch --poll-seconds 0.5
```

It reports an unavailable backend and keeps polling until the demo starts.
In terminal B, run an isolated Phase 4 scenario:

```powershell
python 02_backend/simulate.py progressing-surge --speed 1 --keep-server
```

The worker prints plain-text prototype cards with sensor, source, transition,
depth, reason, timestamps, suggested operator action and stream/event reference.
Every card says `DRY RUN - NO MESSAGE SENT`. Dry-run never constructs a Telegram
sender, even if live credentials happen to exist in the shell environment.
No forecasts, physical actions or safe-road assertions are made by these cards.

Inspect the ledger from another terminal:

```powershell
python 02_backend/notify.py --status
```

Stop the worker with Ctrl+C. While the same backend still runs, this performs
one bounded cycle and does not preview already-recorded events again:

```powershell
python 02_backend/notify.py --base-url http://127.0.0.1:8001 --once
```

`--once` is also the default. It processes at most one event page and the same
number of ready deliveries. A full page signals possible remaining work: run
another cycle or use `--watch`. Old, superseded or stale alerts are recorded as
skipped; running after the whole simulation is not a replay of every historic
card. A fresh scenario database receives a new stream identity automatically.

The compatibility command `python 04_notifications/telegram_bot.py` accepts the
same options. Importing either entry point performs no network I/O.

## Persistence and eligibility

Default ledger: `02_backend/data/notifications/delivery.db` (ignored by Git).
Use `--state-db PATH` for an isolated test; relative paths are anchored to
`02_backend`, independent of the working directory. Keep the ledger across
worker restarts. A different ledger has no memory of earlier deliveries.
An unavailable ledger during operation fails visibly instead of recreating it.

Delivery identity is `(event_stream_id, mode, destination, event_id)`.
Dry-run and live delivery have separate cursors/records. The live destination
is represented by a chat-ID hash, not a token or a raw chat ID. Rotating a bot
token for the same destination does not replay its delivered events.
Database backups/clones retain their stream identity and are the same logical
history; use a fresh simulation database for a separate run.

Each poll reads `/api/alerts?after_id=...&limit=...`. All events in the page
and the new ingestion cursor commit in **one SQLite transaction**. Delivery
success is tracked separately. A failed send cannot erase an ingested event or
move it into the delivered state. Stream identity is checked before/after polling
and again after reading current sensor status. A changed backend defers the cycle.

Immediately before a preview/send, the worker requires:

- The event was fresh at creation and is no older than 120 real seconds by default.
  Accelerated simulated observation times do not control this expiry.
- The current sensor status is fresh, its source/level match the event, and its
  current reading is at least as recent as the event's reading.
- The current `alert_event_id` still matches this transition. A heartbeat at the
  same level remains eligible; an older episode with the same colour does not.
- In live mode, simulated data requires `--allow-simulated`. Unknown/legacy
  sources are skipped. Source labels remain client declarations, not verified
  identities; this backend is an unauthenticated local prototype.

Skips have a durable reason and consume no provider attempt. A recovery card
means the sensor's rule recovered; it does not establish road safety.
There are no new remote notification control routes or frontend changes.

## Delivery results and retry policy

| Status | Meaning / next action |
| --- | --- |
| `pending` | Durably queued, not yet claimed. |
| `sending` | Claimed by a worker with a 120-second lease. Other workers cannot claim it. |
| `dry_run` | Preview recorded; **not a Telegram delivery**. |
| `delivered` | Telegram returned a valid positive message ID and `ok: true` with HTTP success. This confirms provider acceptance, not that a person read it. |
| `retry` | Waiting for a retry timestamp or backend availability. |
| `skipped` | Not currently eligible; see the stored reason. Terminal. |
| `failed` | Permanent provider rejection or exhausted attempt budget. Terminal; investigate the record/configuration. |
| `uncertain` | Acceptance is unknown. Inspect the demo destination before explicitly retrying. |

Telegram 429 uses `parameters.retry_after` (30 seconds if missing/invalid).
The cooldown persists and pauses other deliveries in the same stream/destination.
Provider 5xx and connection-establishment timeouts retry with exponential delay
(2, 4, 8, ... up to 60 seconds), capped at five attempts by default. One scheduled
5xx retry does not block another ready critical alert. Freshness/supersession is
checked again at every attempt; recovery or expiry can end a retry as skipped.
HTTP 4xx other than 429 are permanent failures; redirects are not followed.
No automatic HTTP POST retries run underneath the worker.

Read timeouts, broken connections and malformed success acknowledgements are
`uncertain`: Telegram may already have accepted the message. A live process
interrupted while holding a claim also becomes uncertain once its lease expires.
The worker never automatically resends these. An interrupted dry-run claim can
be retried; a crash between printing and saving can repeat a console preview.

Exactly-once external delivery is not guaranteed. A server error can also be
ambiguous; retrying 5xx or an operator-approved uncertain event may duplicate a
provider message. Every card includes its stable stream/event reference. Keep
one worker per destination in normal use, and inspect uncertain records before
retrying. Use a private demo destination for live tests.

Inspect `--status` to see attempts, due timestamps (Unix seconds), reason codes
and provider message IDs. It needs no backend connection or credentials and
omits stored event payloads. Provider bodies, token-bearing URLs and raw network
exceptions are never logged or saved by the worker.

Exit codes: 0 for a completed cycle/status lookup, 1 for deferred work, possible
remaining pages, retries, failed/uncertain records or storage failure; 2 for bad
CLI options/credentials; 130 for Ctrl+C. `--watch` retains failures in the ledger
and continues polling; it does not silently turn them into successes. Summary
counts cover the current stream/mode/destination, including earlier failures.

## Optional live demo configuration

Only explicitly requested live runs should use real credentials. Export them in
your shell; `.env` files are not automatically loaded. Never put actual secrets
in source files, commands saved to Git, or review reports.

```powershell
# Set STREAM_TELEGRAM_BOT_TOKEN and STREAM_TELEGRAM_CHAT_ID privately first.
python 02_backend/notify.py --base-url http://127.0.0.1:8001 --live --allow-simulated --watch
```

Use a chat where your bot is allowed to send messages. Enable simulation before
producing fresh transitions; previously skipped records are not automatically
replayed when flags change. Live requests use bounded connect/read timeouts,
plain text without HTML parsing, and cards below Telegram's 4096-character limit.

After checking a particular uncertain delivery in that destination, this
explicitly retries it (duplicates are possible; eligibility/budget still apply):

```powershell
python 02_backend/notify.py --base-url http://127.0.0.1:8001 --live --allow-simulated --retry-uncertain 12
```

Useful overrides: `--batch-size 1..1000`, `--max-attempts 1..20`,
`--max-event-age 1..86400` seconds, `--poll-seconds 0.1..60` seconds.
For corrected permanent failures, preserve the ledger for investigation and
produce a new transition in a fresh demo run; do not delete shared delivery state
to force an unnoticed replay.

Provider references: [Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage),
[response and error format](https://core.telegram.org/bots/api#making-requests),
[retry_after](https://core.telegram.org/bots/api#responseparameters).

## Verification

```powershell
python -B -m pytest 02_backend/tests/test_notifications.py -q -p no:cacheprovider
python -B -m pytest 02_backend/tests 03_dashboard/tests -q -p no:cacheprovider
node --test 03_dashboard/tests/dashboard.test.cjs
```

Tests prohibit real external POSTs. They cover mocked provider success, 429/5xx,
permanent failures and acknowledgement loss; durable retries/cursors, stream
changes, restart and claim recovery, eligibility and source guards, and a real
local-server CLI dry-run twice from an unrelated working directory. Actual
Telegram delivery is not tested by this checkpoint.
