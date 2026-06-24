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
  created_at timestamptz not null default now(),
  ended_at timestamptz
);

create table if not exists messages (
  id bigint generated always as identity primary key,
  session_id uuid not null references sessions (id) on delete cascade,
  role text not null,
  content text not null,
  created_at timestamptz not null default now()
);

create table if not exists evaluations (
  id bigint generated always as identity primary key,
  session_id uuid not null references sessions (id) on delete cascade,
  category text not null,
  score int not null,
  feedback text
);

-- Row level security: users can only read their own sessions/messages/evaluations.
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

-- Deletes are performed by the backend using the service role key, which bypasses RLS.
-- These policies are here for completeness in case you ever allow direct client deletes.
create policy "Users can delete their own sessions"
  on sessions for delete
  using (auth.uid() = user_id);

create index if not exists idx_messages_session_id on messages (session_id);
create index if not exists idx_evaluations_session_id on evaluations (session_id);
create index if not exists idx_sessions_user_id on sessions (user_id);

-- Phase 1 reliability and integrity migration.
alter table sessions add column if not exists updated_at timestamptz not null default now();
alter table messages add column if not exists sequence_no integer;
alter table evaluations add column if not exists created_at timestamptz not null default now();

with numbered as (
  select id, row_number() over (partition by session_id order by created_at, id)::integer as sequence_no
  from messages
)
update messages set sequence_no = numbered.sequence_no
from numbered where messages.id = numbered.id and messages.sequence_no is null;
alter table messages alter column sequence_no set not null;

delete from evaluations older using evaluations newer
where older.session_id = newer.session_id and older.category = newer.category and older.id < newer.id;

create unique index if not exists uq_messages_session_sequence on messages (session_id, sequence_no);
create unique index if not exists uq_evaluations_session_category on evaluations (session_id, category);
create index if not exists idx_sessions_user_created on sessions (user_id, created_at desc);
create index if not exists idx_sessions_user_track_created on sessions (user_id, track, created_at desc);

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

create or replace function set_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists sessions_set_updated_at on sessions;
create trigger sessions_set_updated_at before update on sessions
for each row execute function set_updated_at();

create or replace function create_interview_session(
  p_session_id uuid, p_user_id uuid, p_track text, p_role text, p_question text
) returns void language plpgsql as $$
begin
  insert into sessions (id, user_id, track, role, status)
  values (p_session_id, p_user_id, p_track, p_role, 'active');
  insert into messages (session_id, role, content, sequence_no)
  values (p_session_id, 'interviewer', p_question, 1);
end;
$$;

create or replace function append_interview_turn(
  p_session_id uuid, p_user_id uuid, p_candidate_content text, p_interviewer_content text
) returns void language plpgsql as $$
declare next_sequence integer;
begin
  perform 1 from sessions
  where id = p_session_id and user_id = p_user_id and status = 'active' for update;
  if not found then raise exception 'Active session not found'; end if;
  select coalesce(max(sequence_no), 0) + 1 into next_sequence
  from messages where session_id = p_session_id;
  insert into messages (session_id, role, content, sequence_no) values
    (p_session_id, 'candidate', p_candidate_content, next_sequence),
    (p_session_id, 'interviewer', p_interviewer_content, next_sequence + 1);
end;
$$;

create or replace function complete_interview_session(
  p_session_id uuid, p_user_id uuid, p_result jsonb
) returns void language plpgsql as $$
begin
  update sessions set
    status = 'completed',
    overall_score = (p_result->>'overall_score')::integer,
    summary = p_result->>'summary',
    star_analysis = p_result->'star_analysis',
    ended_at = coalesce(ended_at, now())
  where id = p_session_id and user_id = p_user_id and status = 'active';
  if not found then
    if exists (select 1 from sessions where id = p_session_id and user_id = p_user_id and status = 'completed') then
      return;
    end if;
    raise exception 'Active session not found';
  end if;
  insert into evaluations (session_id, category, score, feedback)
  select p_session_id, item->>'category', (item->>'score')::integer, item->>'feedback'
  from jsonb_array_elements(coalesce(p_result->'evaluations', '[]'::jsonb)) item
  on conflict (session_id, category) do update set
    score = excluded.score, feedback = excluded.feedback, created_at = now();
end;
$$;

revoke execute on function create_interview_session(uuid, uuid, text, text, text) from public, anon, authenticated;
revoke execute on function append_interview_turn(uuid, uuid, text, text) from public, anon, authenticated;
revoke execute on function complete_interview_session(uuid, uuid, jsonb) from public, anon, authenticated;
grant execute on function create_interview_session(uuid, uuid, text, text, text) to service_role;
grant execute on function append_interview_turn(uuid, uuid, text, text) to service_role;
grant execute on function complete_interview_session(uuid, uuid, jsonb) to service_role;
