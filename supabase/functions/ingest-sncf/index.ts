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
//
// Storage is compact since 4 October 2026: SNCF's long text identifiers live
// once each in dictionary tables, and observations carry integer references.
// The function hands each batch to ingest.store_batch, which resolves the
// identifiers and applies the change-only rule in a single round trip; see
// supabase/migrations/20261004101000_compact_observation_keys.sql.

import postgres from 'https://deno.land/x/postgresjs@v3.4.5/mod.js'
import GtfsRealtimeBindings from 'npm:gtfs-realtime-bindings@1'

const FEED_URL = 'https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates'
const MIN_SECONDS_BETWEEN_RUNS = 240
const BATCH_SIZE = 5000
// Any constant works; it names the lock that serialises concurrent run claims.
const CLAIM_LOCK = 72710531

const sql = postgres(Deno.env.get('SUPABASE_DB_URL')!, { prepare: false })

type Row = {
  service_date: string
  trip_id: string
  stop_id: string
  stop_sequence: number
  route_id: string | null
  // the raw GTFS-RT enum value; public.observation names it
  schedule_relationship: number
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
    const relationship: number = trip.scheduleRelationship ?? 0

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

// Each batch travels as one JSON document, unpacked inside the database by
// ingest.store_batch, which types every column explicitly and handles nulls
// natively.
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
      const [result] = await tx`select ingest.store_batch(${batch}::text::jsonb) as written`
      written += result.written
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
