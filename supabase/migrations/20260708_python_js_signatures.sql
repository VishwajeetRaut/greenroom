-- Lazily-generated Python/JS ("node") function-signature boilerplate, keyed by
-- language: {"python": "def two_sum(nums, target):\n    pass", "node": "..."}.
-- Mirrors the existing `harnesses` column (Java/C++), but there is no harness
-- here — the test runner already executes candidate code directly for these
-- languages, so this is purely editor scaffolding.
-- See services/harness_generator.py.
ALTER TABLE questions
  ADD COLUMN IF NOT EXISTS signatures JSONB;
