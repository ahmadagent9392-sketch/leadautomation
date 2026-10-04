-- Opportunity Desk database (Supabase / Postgres).
-- How to use: Supabase dashboard -> SQL Editor -> paste this whole file -> Run.
-- Safe to run again: it only creates what is missing. It never deletes data.
-- The SQLite copy used by tests and demo mode is in scripts/store_sqlite.py (keep both the same).

-- ---------- tables ----------
create table if not exists companies (
  id          bigint generated always as identity primary key,
  name        text not null,
  name_key    text not null default '',           -- normalized name for duplicate checks
  domain      text unique,                         -- null for leads found on platforms (Upwork, HN...)
  country     text,
  industry    text,
  size_band   text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table if not exists opportunities (
  id              bigint generated always as identity primary key,   -- = lead ID
  company_id      bigint not null references companies(id),
  source_url      text unique,
  note            text not null default '',
  channel         text not null check (channel in
                    ('email','linkedin_message','upwork_proposal','agency_pitch','referral_ask')),
  status          text not null default 'new' check (status in
                    ('new','researched','verified','qualified','draft_ready','approved','contacted',
                     'replied','meeting','proposal','won','lost','no_response','rejected','opted_out')),
  pattern_id      text,
  fit             smallint check (fit between 0 and 3),
  value_band      smallint check (value_band between 1 and 3),
  urgency         smallint check (urgency between 0 and 2),
  priority        integer,
  owner_person_id bigint,
  why_now         text,
  unknowns        jsonb not null default '[]'::jsonb,
  closed_reason   text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create table if not exists snapshots (
  sha256      text primary key,
  url         text not null,
  fetched_at  timestamptz not null default now(),
  http_status integer,
  text_path   text not null,                      -- local file in data/snapshots/
  title       text
);

create table if not exists evidence (
  id              bigint generated always as identity primary key,
  opportunity_id  bigint not null references opportunities(id),
  claim           text not null,
  url             text not null,
  quote           text not null,
  snapshot_sha256 text references snapshots(sha256),
  observed_at     date,
  source_type     text check (source_type in
                    ('job_post','help_request','review','website','news','profile','manual')),
  topic           text not null default 'company' check (topic in      -- only 'pain' proves the problem
                    ('pain','company','why_now','owner','contact','impact')),
  grade           text not null check (grade in
                    ('CONFIRMED_FACT','STRONG_SIGNAL','WEAK_SIGNAL','INFERENCE','UNKNOWN')),
  depends_on      jsonb not null default '[]'::jsonb,
  verified        boolean not null default false,
  verifier_note   text,
  created_at      timestamptz not null default now()
);

create table if not exists people (
  id           bigint generated always as identity primary key,
  company_id   bigint not null references companies(id),
  full_name    text,
  title        text,
  role_type    text check (role_type in ('owner','technical','buyer','influencer')),
  email        text,
  email_status text check (email_status in ('published','verified','inferred','invalid')),
  profile_url  text,
  evidence_id  bigint references evidence(id),
  created_at   timestamptz not null default now()
);

create table if not exists messages (
  id               bigint generated always as identity primary key,
  opportunity_id   bigint not null references opportunities(id),
  touch_number     smallint not null default 1,
  direction        text not null check (direction in ('out','in')),
  channel          text,
  subject          text,
  body             text not null,
  angle            text,
  cta_type         text,
  evidence_ids     jsonb not null default '[]'::jsonb,
  critic           jsonb,
  gmail_draft_id   text,
  gmail_message_id text,
  thread_id        text,
  sent_at          timestamptz,
  created_at       timestamptz not null default now()
);

create table if not exists replies (
  id               bigint generated always as identity primary key,
  message_id       bigint references messages(id),
  opportunity_id   bigint not null references opportunities(id),
  body             text not null,
  category         text,
  objections       jsonb not null default '[]'::jsonb,
  requested_action text,
  follow_up_date   date,
  classified_at    timestamptz,
  created_at       timestamptz not null default now()
);

create table if not exists follow_ups (
  id             bigint generated always as identity primary key,
  opportunity_id bigint not null references opportunities(id),
  due_on         date not null,
  kind           text not null check (kind in ('followup','nurture','stale_check')),
  touch_number   smallint,
  status         text not null default 'pending' check (status in ('pending','done','cancelled')),
  created_at     timestamptz not null default now()
);

create table if not exists approvals (
  id          bigint generated always as identity primary key,
  object_type text not null,
  object_id   bigint not null,
  body_sha256 text not null,
  decision    text not null check (decision in ('approved','edited','rejected')),
  reason      text,
  decided_at  timestamptz not null default now(),
  expires_at  timestamptz
);

create table if not exists suppression (
  id         bigint generated always as identity primary key,
  value      text not null unique,                -- lower-case email or domain
  kind       text not null check (kind in ('email','domain')),
  reason     text not null,
  created_at timestamptz not null default now()
);

create table if not exists events (
  id             bigint generated always as identity primary key,
  ts             timestamptz not null default now(),
  actor          text not null,                   -- code | human | role:<name>
  opportunity_id bigint references opportunities(id),
  type           text not null,
  payload        jsonb not null default '{}'::jsonb
);

-- ---------- upgrades for databases made by an older version of this file ----------
-- Stage 3: what an evidence item is about
alter table evidence add column if not exists topic text not null default 'company';
do $$ begin
  alter table evidence add constraint evidence_topic_check
    check (topic in ('pain','company','why_now','owner','contact','impact'));
exception when duplicate_object then null;
end $$;

create index if not exists evidence_opportunity_idx on evidence(opportunity_id);
create index if not exists opportunities_status_idx on opportunities(status);
create index if not exists events_opportunity_idx on events(opportunity_id);
create index if not exists events_type_ts_idx on events(type, ts);
create index if not exists follow_ups_due_idx on follow_ups(due_on, status);
create index if not exists people_email_idx on people(email);

-- ---------- events are append-only ----------
create or replace function events_append_only() returns trigger
language plpgsql set search_path = public as $$
begin
  raise exception 'events table is append-only';
end $$;

drop trigger if exists events_no_change on events;
create trigger events_no_change before update or delete on events
  for each row execute function events_append_only();

-- ---------- add a lead: company + opportunity + event in one transaction ----------
create or replace function add_lead(
  p_company_name text, p_name_key text, p_domain text,
  p_source_url text, p_note text, p_channel text, p_actor text
) returns bigint
language plpgsql set search_path = public as $$
declare
  v_company bigint;
  v_lead    bigint;
begin
  insert into companies(name, name_key, domain) values (p_company_name, p_name_key, p_domain)
    returning id into v_company;
  insert into opportunities(company_id, source_url, note, channel) values (v_company, p_source_url, p_note, p_channel)
    returning id into v_lead;
  insert into events(actor, opportunity_id, type, payload)
    values (p_actor, v_lead, 'lead_added',
            jsonb_build_object('url', p_source_url, 'channel', p_channel, 'company', p_company_name));
  return v_lead;
end $$;

-- ---------- change status + event in one transaction ----------
-- The allowed moves are checked in scripts/db.py. Here we only make sure nobody changed the
-- status in the meantime (status must still be p_from).
create or replace function change_status(
  p_lead_id bigint, p_from text, p_to text, p_reason text, p_actor text, p_closed_reason text
) returns void
language plpgsql set search_path = public as $$
begin
  update opportunities
     set status = p_to, closed_reason = coalesce(p_closed_reason, closed_reason), updated_at = now()
   where id = p_lead_id and status = p_from;
  if not found then
    raise exception 'status_changed' using errcode = 'P0001';
  end if;
  insert into events(actor, opportunity_id, type, payload)
    values (p_actor, p_lead_id, 'status_changed',
            jsonb_build_object('from', p_from, 'to', p_to, 'reason', p_reason));
end $$;

-- ---------- security ----------
-- RLS on, no policies: the public (anon) key can read nothing. Only the secret key on Ahmad's PC works.
alter table companies     enable row level security;
alter table opportunities enable row level security;
alter table snapshots     enable row level security;
alter table evidence      enable row level security;
alter table people        enable row level security;
alter table messages      enable row level security;
alter table replies       enable row level security;
alter table follow_ups    enable row level security;
alter table approvals     enable row level security;
alter table suppression   enable row level security;
alter table events        enable row level security;

revoke execute on function add_lead(text, text, text, text, text, text, text) from public, anon, authenticated;
revoke execute on function change_status(bigint, text, text, text, text, text) from public, anon, authenticated;
grant execute on function add_lead(text, text, text, text, text, text, text) to service_role;
grant execute on function change_status(bigint, text, text, text, text, text) to service_role;

grant usage on schema public to service_role;
grant select, insert, update on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to service_role;

-- tell the REST API to see the new tables now
notify pgrst, 'reload schema';
