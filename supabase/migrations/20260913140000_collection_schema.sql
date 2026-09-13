-- Hosted collection: raw stop-level observations and one row per collector run.
--
-- Only collection lives in Supabase. The GTFS reference, the star schema and the
-- quality gate stay in the local pipeline, which pulls these two tables down
-- (src/sync_supabase.py). Keeping the hosted side this small matters on the
-- free plan's 500 MB database.

create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;

create table public.observation (
    service_date          date        not null,
    trip_id               text        not null,
    stop_id               text        not null,
    stop_sequence         integer     not null,
    route_id              text,
    schedule_relationship text,
    arrival_delay         integer,
    departure_delay       integer,
    arrival_time          bigint,
    departure_time        bigint,
    -- Feed header timestamp of the reading; a row is only ever replaced by a fresher one.
    observed_at           bigint      not null,
    -- When this row last changed, so the local sync can fetch only what is new.
    updated_at            timestamptz not null default now(),
    primary key (service_date, trip_id, stop_id, stop_sequence)
);

create index observation_updated_at on public.observation (updated_at);

create table public.ingest_run (
    id                bigint generated always as identity primary key,
    started_at        timestamptz not null default now(),
    finished_at       timestamptz,
    status            text        not null check (status in ('running', 'ok', 'error')),
    header_timestamp  bigint,
    payload_bytes     integer,
    entities          integer,
    stop_time_updates integer,
    rows_written      integer,
    duration_ms       integer,
    error             text
);

create index ingest_run_started_at on public.ingest_run (started_at);

-- The data is derived from open data, so reading it through the API is allowed
-- to anyone holding the project's publishable key. Nothing can be written through
-- the API: there are no insert, update or delete policies. The collector writes
-- over a direct database connection as the table owner, which RLS does not restrict.
alter table public.observation enable row level security;
alter table public.ingest_run enable row level security;

create policy "observations are publicly readable"
    on public.observation for select to anon, authenticated using (true);

create policy "collector runs are publicly readable"
    on public.ingest_run for select to anon, authenticated using (true);
