// Collect the SNCF GTFS-RT trip updates into Postgres, on a schedule.
//
// The hosted counterpart of src/ingest_sncf.py, with the same storage rule:
// decode on arrival, keep only stop-level observations, upsert them on
// (service_date, trip_id, stop_id, stop_sequence), and let a fresher reading
// replace a row only when it changes something — a value, or the fact that the
// call has now happened.
//
// Rewriting unchanged rows was the original rule and is harmless in SQLite, but
// each run would rewrite every row still in the feed: a trip's stops re-written
// every five minutes for hours, mostly identical. On Postgres every rewrite
// leaves a dead row version and new index entries behind, which on the free
// plan's 500 MB is how a collector quietly runs out of disk.
//
// pg_cron invokes this every 5 minutes. Authentication is a shared token the
// database generates for itself and keeps in Vault ('ingest_token'): the cron
// job sends it in the x-ingest-token header and the function compares it with
// the Vault copy over its own database connection. No API key is needed by the
// scheduler, and the token never leaves the database or enters the repository.
//
// A run arriving within four minutes of the previous one is also refused, so
// even a caller holding the token cannot make the SNCF feed be fetched, or the
// database written, more than once per window.

import postgres from 'https://deno.land/x/postgresjs@v3.4.5/mod.js'
import GtfsRealtimeBindings from 'npm:gtfs-realtime-bindings@1'

const FEED_URL = 'https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates'
const MIN_SECONDS_BETWEEN_RUNS = 240
const BATCH_SIZE = 5000
// Any constant works; it names the lock that serialises concurrent run claims.
const CLAIM_LOCK = 72710531

// gtfs-realtime TripDescriptor.ScheduleRelationship
const RELATIONSHIP: Record<number, string> = {
  0: 'SCHEDULED', 1: 'ADDED', 2: 'UNSCHEDULED',
  3: 'CANCELED', 5: 'REPLACEMENT', 6: 'DUPLICATED', 7: 'DELETED',
}

const sql = postgres(Deno.env.get('SUPABASE_DB_URL')!, { prepare: false })

type Row = {
  service_date: string
  trip_id: string
  stop_id: string
  stop_sequence: number
  route_id: string | null
  schedule_relationship: string
  arrival_delay: number | null
  departure_delay: number | null
  arrival_time: number | null
  departure_time: number | null
  observed_at: number
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

// protobufjs decodes 64-bit fields as Long objects when long.js is present.
function toNumber(value: unknown): number | null {
  if (value === null || value === undefined) return null
  return typeof value === 'number' ? value : Number(String(value))
}

// GTFS-RT is proto2: an unset optional field reads back as its default (0 for a
// delay), so presence must be tested explicitly or "no delay reported" becomes
// "on time". protobufjs sets only the fields present in the payload as own
// properties, which is the equivalent of HasField in the Python bindings.
function present(message: object | null | undefined, field: string): boolean {
  return message != null && Object.prototype.hasOwnProperty.call(message, field)
}

function isoDate(yyyymmdd: string): string {
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`
}

// deno-lint-ignore no-explicit-any
function extractRows(feed: any, fallbackDate: string, observedAt: number): Map<string, Row> {
  // Keyed on the primary key: the feed occasionally repeats a stop within a trip,
  // and Postgres refuses to upsert the same key twice in one statement.
  const rows = new Map<string, Row>()

  for (const entity of feed.entity ?? []) {
    const update = entity.tripUpdate
    if (!update) continue

    const trip = update.trip ?? {}
    const serviceDate = isoDate(trip.startDate || fallbackDate)
    const relationship = RELATIONSHIP[trip.scheduleRelationship ?? 0] ?? 'UNKNOWN'

    for (const stop of update.stopTimeUpdate ?? []) {
      const row: Row = {
        service_date: serviceDate,
        trip_id: trip.tripId ?? '',
        stop_id: stop.stopId ?? '',
        stop_sequence: stop.stopSequence ?? 0,
        route_id: trip.routeId || null,
        schedule_relationship: relationship,
        arrival_delay: present(stop.arrival, 'delay') ? stop.arrival.delay : null,
        departure_delay: present(stop.departure, 'delay') ? stop.departure.delay : null,
        arrival_time: present(stop.arrival, 'time') ? toNumber(stop.arrival.time) : null,
        departure_time: present(stop.departure, 'time') ? toNumber(stop.departure.time) : null,
        observed_at: observedAt,
      }
      rows.set(`${row.service_date}|${row.trip_id}|${row.stop_id}|${row.stop_sequence}`, row)
    }
  }

  return rows
}

// Each batch travels as one JSON document unpacked by jsonb_to_recordset, which
// types every column explicitly and handles nulls natively. Passing one array per
// column instead would leave the driver to infer array types, which it cannot do
// reliably when a column's first value is null.
//
// The parameter is cast ::text before ::jsonb on purpose. With a bare ::jsonb the
// driver sees a jsonb parameter and JSON-encodes the already-encoded string a
// second time, so Postgres receives a single string instead of an array ("cannot
// call jsonb_to_recordset on a non-array" on the first deployment).
async function upsert(rows: Row[]): Promise<number> {
  let written = 0
  await sql.begin(async (tx) => {
    for (let start = 0; start < rows.length; start += BATCH_SIZE) {
      const batch = JSON.stringify(rows.slice(start, start + BATCH_SIZE))
      const result = await tx`
        insert into public.observation (
            service_date, trip_id, stop_id, stop_sequence, route_id,
            schedule_relationship, arrival_delay, departure_delay,
            arrival_time, departure_time, observed_at
        )
        select service_date, trip_id, stop_id, stop_sequence, route_id,
               schedule_relationship, arrival_delay, departure_delay,
               arrival_time, departure_time, observed_at
        from jsonb_to_recordset(${batch}::text::jsonb) as incoming (
            service_date date, trip_id text, stop_id text, stop_sequence integer,
            route_id text, schedule_relationship text, arrival_delay integer,
            departure_delay integer, arrival_time bigint, departure_time bigint,
            observed_at bigint
        )
        on conflict (service_date, trip_id, stop_id, stop_sequence) do update set
            route_id              = excluded.route_id,
            schedule_relationship = excluded.schedule_relationship,
            arrival_delay         = excluded.arrival_delay,
            departure_delay       = excluded.departure_delay,
            arrival_time          = excluded.arrival_time,
            departure_time        = excluded.departure_time,
            observed_at           = excluded.observed_at,
            updated_at            = now(),
            -- the local sync's cursor: every change must move the row forward
            sync_seq              = nextval('public.observation_sync_seq')
        where excluded.observed_at > observation.observed_at
          and (
                excluded.arrival_delay         is distinct from observation.arrival_delay
             or excluded.departure_delay       is distinct from observation.departure_delay
             or excluded.arrival_time          is distinct from observation.arrival_time
             or excluded.departure_time        is distinct from observation.departure_time
             or excluded.schedule_relationship is distinct from observation.schedule_relationship
             or excluded.route_id              is distinct from observation.route_id
             -- is_past compares arrival_time with observed_at, so the first reading
             -- taken after the train has passed must be kept even when nothing else
             -- changed, or the call would stay marked as not yet happened
             or (observation.observed_at < observation.arrival_time
                 and excluded.observed_at >= excluded.arrival_time)
          )`
      written += result.count
    }
  })
  return written
}

let expectedToken: string | null = null

async function isAuthorised(request: Request): Promise<boolean> {
  const supplied = request.headers.get('x-ingest-token')
  if (!supplied) return false // refuse without touching the database

  if (expectedToken === null) {
    const [secret] = await sql`
      select decrypted_secret from vault.decrypted_secrets where name = 'ingest_token'`
    if (!secret) return false
    expectedToken = secret.decrypted_secret as string
  }

  // Constant-time comparison, so response timing does not leak the token.
  const a = new TextEncoder().encode(supplied)
  const b = new TextEncoder().encode(expectedToken)
  let difference = a.length ^ b.length
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    difference |= (a[i] ?? 0) ^ (b[i] ?? 0)
  }
  return difference === 0
}

Deno.serve(async (request: Request) => {
  if (!(await isAuthorised(request))) {
    return json({ status: 'unauthorised' }, 401)
  }

  const started = Date.now()

  const claim = await sql.begin(async (tx) => {
    await tx`select pg_advisory_xact_lock(${CLAIM_LOCK})`
    return await tx`
      insert into public.ingest_run (status)
      select 'running'
      where not exists (
          select 1 from public.ingest_run
          where started_at > now() - make_interval(secs => ${MIN_SECONDS_BETWEEN_RUNS})
      )
      returning id`
  })
  if (claim.length === 0) {
    return json({ status: 'skipped', reason: `a run started less than ${MIN_SECONDS_BETWEEN_RUNS}s ago` })
  }
  const runId = claim[0].id

  try {
    const response = await fetch(FEED_URL)
    if (!response.ok) throw new Error(`feed returned HTTP ${response.status}`)
    const payload = new Uint8Array(await response.arrayBuffer())

    const feed = GtfsRealtimeBindings.transit_realtime.FeedMessage.decode(payload)
    const headerTimestamp = toNumber(feed.header?.timestamp) || Math.floor(started / 1000)
    const fallbackDate = new Date(started).toISOString().slice(0, 10).replaceAll('-', '')

    const rows = [...extractRows(feed, fallbackDate, headerTimestamp).values()]
    const stopTimeUpdates = feed.entity.reduce(
      // deno-lint-ignore no-explicit-any
      (sum: number, entity: any) => sum + (entity.tripUpdate?.stopTimeUpdate?.length ?? 0), 0,
    )
    const written = await upsert(rows)
    const durationMs = Date.now() - started

    await sql`
      update public.ingest_run set
          status = 'ok', finished_at = now(), header_timestamp = ${headerTimestamp},
          payload_bytes = ${payload.length}, entities = ${feed.entity.length},
          stop_time_updates = ${stopTimeUpdates}, rows_written = ${written},
          duration_ms = ${durationMs}
      where id = ${runId}`

    return json({
      status: 'ok', entities: feed.entity.length, stop_time_updates: stopTimeUpdates,
      rows_written: written, payload_bytes: payload.length, duration_ms: durationMs,
    })
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    await sql`
      update public.ingest_run set
          status = 'error', finished_at = now(), error = ${message},
          duration_ms = ${Date.now() - started}
      where id = ${runId}`
    console.error('ingest-sncf failed:', message)
    return json({ status: 'error', error: message }, 500)
  }
})
