-- Run this in the Supabase SQL editor — adds the question bank table and
-- links sessions to the assigned canonical problem (needed for the new
-- stateless session-reconstruction logic in routers/interview.py).

alter table sessions add column if not exists assigned_question_id text;
alter table messages add column if not exists sequence_no integer;

create table if not exists questions (
  id text primary key,
  track text not null,
  topic text,
  difficulty text,
  title text not null,
  prompt text not null,
  function_name text not null,
  languages text[] not null default array['python'],
  tests jsonb not null,
  created_at timestamptz not null default now()
);

alter table questions enable row level security;
drop policy if exists "Anyone can read questions" on questions;
create policy "Anyone can read questions"
  on questions for select
  using (true);

create index if not exists idx_questions_track on questions (track);

-- stdin/stdout-style problems (CodeContests-derived) have no function signature
alter table questions alter column function_name drop not null;

-- Lazily-generated, sandbox-verified Java/C++ harnesses for LeetCode-style
-- (call/expected) problems, keyed by language: {"java": {boilerplate, harness}, "cpp": {...}}.
-- See services/harness_generator.py — populated on first use, not upfront.
alter table questions add column if not exists harnesses jsonb;

-- Structured constraints/examples extracted from each problem's own prompt
-- text (not invented — see scripts/extract_constraints_examples.py), so the
-- frontend can render them as distinct panels instead of one prose blob.
alter table questions add column if not exists constraints jsonb;
alter table questions add column if not exists examples jsonb;
