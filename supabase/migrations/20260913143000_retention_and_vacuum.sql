-- Keep the hosted database inside the free plan's 500 MB.
--
-- Measured on the first scheduled run: 526 bytes per observation row, mostly
-- SNCF's ~100-character trip identifiers stored in the table and again in the
-- primary key index. The static timetable schedules about 98,000 station calls
-- on a weekday and 52,000-58,000 on a weekend day, so a week is roughly 300 MB.
-- Unbounded, the database would fill in about ten days, and Supabase then
-- restricts the project, which stops the collector.
--
-- Supabase is therefore not the archive, data/punctuality.db is: src/sync_supabase.py
-- copies everything down, and this job deletes what is more than seven days old.
-- Running the local refresh at least once a week keeps the history complete.

select cron.schedule(
    'observation-retention',
    '30 3 * * *',
    $$
    delete from public.observation where service_date < current_date - 7;
    delete from public.ingest_run where started_at < now() - interval '60 days';
    $$
);

-- Deleted and updated rows only become reusable space once vacuumed. The defaults
-- wait until a fifth of the table is dead; on a table rewritten every 5 minutes
-- that lets it grow well past its live size before any cleanup.
alter table public.observation set (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_vacuum_threshold = 5000
);
