-- A strictly increasing number stamped on every insert and update of an
-- observation, used by the local sync as its cursor: "give me every row with
-- sync_seq greater than the last one I stored", paged in order.
--
-- updated_at cannot do this job. Every row written by one run shares the same
-- now(), so ~20,000 rows tie on it; and paging a time window with an offset skips
-- rows whenever a row inside the window changes during the sync, which with a
-- 5-minute collector is routinely. A unique, monotonic key has neither problem.

create sequence public.observation_sync_seq;

alter table public.observation
    add column sync_seq bigint not null default nextval('public.observation_sync_seq');

-- Named with a suffix: indexes and sequences share one namespace in Postgres.
create index observation_sync_seq_idx on public.observation (sync_seq);

-- The sync no longer reads updated_at, so its index is only cost on a 500 MB plan.
drop index public.observation_updated_at;
