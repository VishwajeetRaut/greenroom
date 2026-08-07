-- System-design question metadata (see backend/scripts/generate_sd_metadata.py).
--
-- tags            : controlled-vocabulary characteristics ("read-heavy",
--                   "geo-distributed", ...). Lets questions be matched on what
--                   they actually exercise rather than on the single coarse
--                   `topic` column — which is what job-description-driven
--                   selection needs (see services/jd_analyzer.py).
-- core_challenge  : one sentence naming what makes the problem hard.
-- scale_tiers     : {"easy"|"medium"|"hard": {daily_active_users, writes_per_day,
--                   reads_per_day, peak_qps, data_volume, latency_slo}}.
--                   The scale numbers used to exist only as free text inside
--                   `constraints`, so nothing could read or vary them: every
--                   candidate got the scale the author happened to type. With
--                   these, one problem can be posed at three difficulties.
--
-- All nullable: technical and behavioral questions have no use for them, and a
-- system-design question without them falls back to its authored constraints.
ALTER TABLE questions ADD COLUMN IF NOT EXISTS tags JSONB;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS core_challenge TEXT;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS scale_tiers JSONB;
