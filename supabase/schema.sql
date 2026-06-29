-- Run this in the Supabase SQL editor for your project.

create table if not exists sessions (
  id uuid primary key,
  user_id uuid references auth.users (id) on delete cascade,
  track text not null,
  role text,
  status text not null default 'active',
  overall_score int,
  summary text,
  star_analysis jsonb,
  assigned_question_id text,
  created_at timestamptz not null default now(),
  ended_at timestamptz,
  updated_at timestamptz not null default now()
);

create table if not exists messages (
  id bigint generated always as identity primary key,
  session_id uuid not null references sessions (id) on delete cascade,
  role text not null,
  content text not null,
  created_at timestamptz not null default now(),
  sequence_no integer
);

create table if not exists evaluations (
  id bigint generated always as identity primary key,
  session_id uuid not null references sessions (id) on delete cascade,
  category text not null,
  score int not null,
  feedback text,
  created_at timestamptz not null default now()
);

-- Row level security: users can only read their own data.
-- Writes are performed by the backend using the service role key, which bypasses RLS.

alter table sessions enable row level security;
alter table messages enable row level security;
alter table evaluations enable row level security;

drop policy if exists "Users can view their own sessions" on sessions;
drop policy if exists "Users can view messages from their sessions" on messages;
drop policy if exists "Users can view evaluations from their sessions" on evaluations;
drop policy if exists "Users can delete their own sessions" on sessions;

create policy "Users can view their own sessions"
  on sessions for select
  using (auth.uid() = user_id);

create policy "Users can view messages from their sessions"
  on messages for select
  using (
    exists (
      select 1 from sessions
      where sessions.id = messages.session_id
        and sessions.user_id = auth.uid()
    )
  );

create policy "Users can view evaluations from their sessions"
  on evaluations for select
  using (
    exists (
      select 1 from sessions
      where sessions.id = evaluations.session_id
        and sessions.user_id = auth.uid()
    )
  );

create policy "Users can delete their own sessions"
  on sessions for delete
  using (auth.uid() = user_id);

-- Curated technical question bank. Backend reads this table first (see
-- services/question_bank.py) and falls back to the bundled JSON seed only if
-- this table is empty/unreachable — so the bank can be updated by inserting
-- rows here at any time, with no backend redeploy required.
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

-- Indexes
create index if not exists idx_messages_session_id on messages (session_id);
create index if not exists idx_evaluations_session_id on evaluations (session_id);
create index if not exists idx_sessions_user_id on sessions (user_id);
create index if not exists idx_sessions_user_created on sessions (user_id, created_at desc);

-- Database-level constraints so invalid data can never be saved
alter table sessions drop constraint if exists sessions_track_check;
alter table sessions add constraint sessions_track_check
  check (track in ('behavioral', 'technical', 'system-design'));

alter table sessions drop constraint if exists sessions_status_check;
alter table sessions add constraint sessions_status_check
  check (status in ('active', 'completed', 'abandoned'));

alter table sessions drop constraint if exists sessions_score_check;
alter table sessions add constraint sessions_score_check
  check (overall_score between 0 and 10);

alter table messages drop constraint if exists messages_role_check;
alter table messages add constraint messages_role_check
  check (role in ('interviewer', 'candidate'));

alter table evaluations drop constraint if exists evaluations_score_check;
alter table evaluations add constraint evaluations_score_check
  check (score between 0 and 10);

-- Auto-update updated_at on sessions
create or replace function set_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists sessions_set_updated_at on sessions;
create trigger sessions_set_updated_at before update on sessions
  for each row execute function set_updated_at();
