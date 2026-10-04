-- Make room for the compaction that follows.
--
-- The next migration copies every observation into a new table before the old
-- one is dropped. With the old table's indexes still in place, that copy would
-- peak at about 520 MB, over the free plan's 500 MB, and the space held by a
-- dropped relation is only released when its transaction commits. Dropping the
-- two indexes here, in a transaction of their own, frees 227 MB first.
--
-- Collection is paused while the two migrations run, so nothing writes to the
-- table in between, and the local archive was confirmed level with the hosted
-- one (same sync_seq) before the first statement.

alter table public.observation drop constraint observation_pkey;
drop index public.observation_sync_seq_idx;
