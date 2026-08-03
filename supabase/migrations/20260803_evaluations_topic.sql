-- Technical evaluations are now tagged with the question's topic (e.g.
-- "dynamic programming") so the Results page can group evaluation points by
-- topic instead of showing one flat, undifferentiated list. Nullable and
-- unused for behavioral/system-design evaluations.
ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS topic text;
