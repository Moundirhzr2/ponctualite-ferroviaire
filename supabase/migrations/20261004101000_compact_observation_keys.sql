-- Store observations with integer keys instead of SNCF's text identifiers.
--
-- Measured on 4 October 2026 with 646,276 rows: 409 MB in all, of which the
-- primary key index alone took 192 MB. Every row carried its trip identifier
-- (about 100 characters) and its stop identifier (about 30) twice, once in the
-- table and once in the index, and a B-tree of long text keys splits its pages
-- generously. At that rate the free plan's 500 MB held little more than a week.
--
-- The identifiers move to three dictionaries that store each string once. The
-- observation table keeps four-byte references, and its primary key shrinks to
-- a 16-byte key.
--
-- public.observation survives as a view with exactly the old table's columns,
-- so the local sync (src/sync_supabase.py) reads what it always read.

create table public.trip_key (
    id integer generated always as identity primary key,
    trip_id text not null unique,
    -- Latest service day the trip was seen on. Retention prunes the dictionary
    -- with it: every observation of a trip falls on or before that day.
    last_service_date date not null
);

create table public.stop_key (
    id integer generated always as identity primary key,
    stop_id text not null unique
);

create table public.route_key (
    id integer generated always as identity primary key,
    route_id text not null unique
);

-- Eight-byte columns first, then four-byte, then two-byte: Postgres aligns each
-- value on its own width, so this order wastes no padding.
create table public.observation_compact (
    arrival_time bigint,
    departure_time bigint,
    observed_at bigint not null,
    sync_seq bigint not null default nextval('public.observation_sync_seq'),
    updated_at timestamptz not null default now(),
    service_date date not null,
    trip integer not null,
    stop integer not null,
    stop_sequence integer not null,
    route integer,
    arrival_delay integer,
    departure_delay integer,
    -- GTFS-RT TripDescriptor.ScheduleRelationship, named back in the view
    schedule_relationship smallint not null,
    primary key (service_date, trip, stop, stop_sequence)
);

insert into public.trip_key (trip_id, last_service_date)
select trip_id, max(service_date) from public.observation group by trip_id;

insert into public.stop_key (stop_id)
select distinct stop_id from public.observation;

insert into public.route_key (route_id)
select distinct route_id from public.observation where route_id is not null;

-- sync_seq and updated_at are carried over unchanged, so the local sync's cursor
-- stays valid and no row is downloaded a second time.
insert into public.observation_compact (
    arrival_time, departure_time, observed_at, sync_seq, updated_at,
    service_date, trip, stop, stop_sequence, route,
    arrival_delay, departure_delay, schedule_relationship)
select o.arrival_time, o.departure_time, o.observed_at, o.sync_seq, o.updated_at,
       o.service_date, t.id, s.id, o.stop_sequence, r.id,
       o.arrival_delay, o.departure_delay,
       case o.schedule_relationship
           when 'SCHEDULED' then 0 when 'ADDED' then 1 when 'UNSCHEDULED' then 2
           when 'CANCELED' then 3 when 'REPLACEMENT' then 5 when 'DUPLICATED' then 6
           when 'DELETED' then 7 else -1
       end
from public.observation o
join public.trip_key t on t.trip_id = o.trip_id
join public.stop_key s on s.stop_id = o.stop_id
left join public.route_key r on r.route_id = o.route_id;

create index observation_compact_sync_seq_idx on public.observation_compact (sync_seq);

alter table public.observation_compact set (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_vacuum_threshold = 5000
);

drop table public.observation;

create view public.observation with (security_invoker = true) as
select o.service_date, t.trip_id, s.stop_id, o.stop_sequence, r.route_id,
       case o.schedule_relationship
           when 0 then 'SCHEDULED' when 1 then 'ADDED' when 2 then 'UNSCHEDULED'
           when 3 then 'CANCELED' when 5 then 'REPLACEMENT' when 6 then 'DUPLICATED'
           when 7 then 'DELETED' else 'UNKNOWN'
       end as schedule_relationship,
       o.arrival_delay, o.departure_delay, o.arrival_time, o.departure_time,
       o.observed_at, o.updated_at, o.sync_seq
from public.observation_compact o
join public.trip_key t on t.id = o.trip
join public.stop_key s on s.id = o.stop
left join public.route_key r on r.id = o.route;

-- Same exposure as before: readable by anyone holding the publishable key,
-- writable by nobody but the collector, which connects as the database owner.
alter table public.observation_compact enable row level security;
alter table public.trip_key enable row level security;
alter table public.stop_key enable row level security;
alter table public.route_key enable row level security;

create policy "observations are publicly readable" on public.observation_compact
    for select to anon, authenticated using (true);
create policy "trip keys are publicly readable" on public.trip_key
    for select to anon, authenticated using (true);
create policy "stop keys are publicly readable" on public.stop_key
    for select to anon, authenticated using (true);
create policy "route keys are publicly readable" on public.route_key
    for select to anon, authenticated using (true);

grant select on public.observation to anon, authenticated;

-- The collector's write path, in a schema the REST API does not expose.
create schema if not exists ingest;
revoke all on schema ingest from public;

create or replace function ingest.store_batch(batch jsonb)
returns integer
language plpgsql
set search_path = ''
as $$
declare
    written integer;
begin
    update public.trip_key k
       set last_service_date = x.service_date
      from (select trip_id, max(service_date) as service_date
              from jsonb_to_recordset(batch) as r (trip_id text, service_date date)
             group by trip_id) x
     where k.trip_id = x.trip_id
       and k.last_service_date < x.service_date;

    -- Only identifiers not seen yet are inserted. INSERT ... ON CONFLICT DO
    -- NOTHING would draw an identity value for every row it skips: about 10,000
    -- known trips per run, every five minutes, exhausts a four-byte identity in
    -- under two years.
    insert into public.trip_key (trip_id, last_service_date)
    select x.trip_id, max(x.service_date)
      from jsonb_to_recordset(batch) as x (trip_id text, service_date date)
     where not exists (select 1 from public.trip_key k where k.trip_id = x.trip_id)
     group by x.trip_id;

    insert into public.stop_key (stop_id)
    select distinct x.stop_id
      from jsonb_to_recordset(batch) as x (stop_id text)
     where not exists (select 1 from public.stop_key k where k.stop_id = x.stop_id);

    insert into public.route_key (route_id)
    select distinct x.route_id
      from jsonb_to_recordset(batch) as x (route_id text)
     where x.route_id is not null
       and not exists (select 1 from public.route_key k where k.route_id = x.route_id);

    -- Same change-only rule as before: a fresher reading replaces a row only when
    -- it changes something, or when it is the first one taken after the passage.
    insert into public.observation_compact as o (
        arrival_time, departure_time, observed_at, service_date, trip, stop,
        stop_sequence, route, arrival_delay, departure_delay, schedule_relationship)
    select x.arrival_time, x.departure_time, x.observed_at, x.service_date, t.id, s.id,
           x.stop_sequence, r.id, x.arrival_delay, x.departure_delay, x.schedule_relationship
      from jsonb_to_recordset(batch) as x (
               service_date date, trip_id text, stop_id text, stop_sequence integer,
               route_id text, schedule_relationship smallint, arrival_delay integer,
               departure_delay integer, arrival_time bigint, departure_time bigint,
               observed_at bigint)
      join public.trip_key t on t.trip_id = x.trip_id
      join public.stop_key s on s.stop_id = x.stop_id
      left join public.route_key r on r.route_id = x.route_id
    on conflict (service_date, trip, stop, stop_sequence) do update set
        route                 = excluded.route,
        schedule_relationship = excluded.schedule_relationship,
        arrival_delay         = excluded.arrival_delay,
        departure_delay       = excluded.departure_delay,
        arrival_time          = excluded.arrival_time,
        departure_time        = excluded.departure_time,
        observed_at           = excluded.observed_at,
        updated_at            = now(),
        sync_seq              = nextval('public.observation_sync_seq')
    where excluded.observed_at > o.observed_at
      and (   excluded.arrival_delay         is distinct from o.arrival_delay
           or excluded.departure_delay       is distinct from o.departure_delay
           or excluded.arrival_time          is distinct from o.arrival_time
           or excluded.departure_time        is distinct from o.departure_time
           or excluded.schedule_relationship is distinct from o.schedule_relationship
           or excluded.route                 is distinct from o.route
           or (o.observed_at < o.arrival_time and excluded.observed_at >= excluded.arrival_time));

    get diagnostics written = row_count;
    return written;
end;
$$;

revoke all on function ingest.store_batch(jsonb) from public;

-- Retention, rewritten for the new tables. Observations go first, then the trip
-- identifiers no remaining observation can reference. Fourteen days instead of
-- seven: the compact table leaves room for it, and the local archive, synced
-- every four hours, no longer depends on a short window. The pg_cron run log,
-- which grows by about 600 rows a day and was never pruned, is trimmed too.
select cron.schedule(
    'observation-retention',
    '30 3 * * *',
    $cron$
    delete from public.observation_compact where service_date < current_date - 14;
    delete from public.trip_key where last_service_date < current_date - 14;
    delete from public.ingest_run where started_at < now() - interval '60 days';
    delete from cron.job_run_details where end_time < now() - interval '7 days';
    $cron$
);
