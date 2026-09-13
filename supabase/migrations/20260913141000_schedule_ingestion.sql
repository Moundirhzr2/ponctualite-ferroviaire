-- Run the ingest-sncf Edge Function every 5 minutes.
--
-- Prerequisite, once per project and deliberately not versioned, since it names
-- the project:
--   select vault.create_secret('https://<project-ref>.supabase.co', 'project_url');

-- The token the function checks. Generated inside the database, so its value is
-- never typed, logged, or committed anywhere.
select vault.create_secret(
    replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''),
    'ingest_token',
    'Shared token pg_cron sends to the ingest-sncf Edge Function'
);

select cron.schedule(
    'ingest-sncf',
    '*/5 * * * *',
    $$
    select net.http_post(
        url := (select decrypted_secret from vault.decrypted_secrets where name = 'project_url')
               || '/functions/v1/ingest-sncf',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'x-ingest-token', (select decrypted_secret from vault.decrypted_secrets where name = 'ingest_token')
        ),
        body := '{}'::jsonb,
        timeout_milliseconds := 60000
    );
    $$
);
