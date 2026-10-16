-- Run ONLY in a NEW dedicated Supabase Free project, once. No legacy migration.
begin;
create table public.cloud_profiles (
  owner uuid primary key references auth.users(id) on delete restrict,
  day date not null default (now() at time zone 'utc')::date,
  uploads integer not null default 0, ai integer not null default 0,
  created_at timestamptz not null default now()
);
create table public.cloud_documents (
  owner uuid not null references public.cloud_profiles(owner) on delete restrict,
  id text not null check (id ~ '^[a-f0-9]{64}$'),
  filename text not null check (length(filename) between 1 and 120),
  pages integer not null check (pages between 1 and 10),
  chunks integer not null check (chunks between 1 and 200),
  state text not null check (state in ('pending','ready','deleting')),
  lease_until timestamptz not null default now() + interval '10 minutes',
  created_at timestamptz not null default now(), primary key(owner,id)
);
create table public.cloud_turns (
  id bigint generated always as identity primary key,
  owner uuid not null references public.cloud_profiles(owner) on delete restrict,
  document_ids text[] not null,
  question text not null check (length(question) between 1 and 500),
  answer text not null check (length(answer) between 1 and 8000),
  citations jsonb not null check (jsonb_typeof(citations)='array' and pg_column_size(citations)<20000),
  created_at timestamptz not null default now()
);
create index cloud_turns_owner_id on public.cloud_turns(owner,id desc);
create table public.cloud_budget (
  singleton boolean primary key default true check(singleton),
  day date not null default (now() at time zone 'utc')::date,
  uploads integer not null default 0, ai integer not null default 0,
  lifetime_uploads integer not null default 0, lifetime_ai integer not null default 0,
  last_ai timestamptz not null default '-infinity'
);
insert into public.cloud_budget(singleton) values(true);
create table public.cloud_server_settings (
  singleton boolean primary key default true check(singleton),
  write_key_hash text not null check(write_key_hash ~ '^[a-f0-9]{64}$')
);
alter table public.cloud_server_settings enable row level security;
revoke all on public.cloud_server_settings from anon,authenticated;
alter table public.cloud_profiles enable row level security;
alter table public.cloud_documents enable row level security;
alter table public.cloud_turns enable row level security;
alter table public.cloud_budget enable row level security;
create policy cloud_own_profiles on public.cloud_profiles for select to authenticated using(owner=(select auth.uid()));
create policy cloud_own_documents on public.cloud_documents for select to authenticated using(owner=(select auth.uid()));
create policy cloud_own_turns on public.cloud_turns for select to authenticated using(owner=(select auth.uid()));
revoke all on public.cloud_profiles,public.cloud_documents,public.cloud_turns,public.cloud_budget from anon,authenticated;
grant select on public.cloud_profiles,public.cloud_documents,public.cloud_turns to authenticated;

-- Prevent direct client RPCs from forging completion or erasing cleanup records.
-- Only the HASH of the independent server write key belongs in this table.
create function public.cloud_guard() returns void language plpgsql security definer
set search_path = pg_catalog, public as $$
declare k text;
begin
  k:=current_setting('request.headers',true)::jsonb->>'x-documind-write-key';
  if auth.uid() is null or k is null or not exists(select 1 from public.cloud_server_settings
    where singleton and write_key_hash=encode(sha256(convert_to(k,'UTF8')),'hex'))
    then raise exception 'cloud_document'; end if;
end $$;

-- Atomic global + account budgets. Only callable by other functions, not REST.
create function public.cloud_charge(p_kind text) returns void language plpgsql security definer
set search_path = pg_catalog, public as $$
declare b public.cloud_budget; u public.cloud_profiles; uid uuid:=auth.uid();
begin
  perform public.cloud_guard();
  if uid is null or p_kind not in ('upload','ai') then raise exception 'cloud_document'; end if;
  select * into b from public.cloud_budget where singleton for update;
  if not found then raise exception 'cloud_document'; end if;
  select * into u from public.cloud_profiles where owner=uid for update;
  if not found then raise exception 'cloud_document'; end if;
  if b.day<>(now() at time zone 'utc')::date then
    update public.cloud_budget set day=(now() at time zone 'utc')::date, uploads=0,ai=0 where singleton;
    b.uploads:=0; b.ai:=0;
  end if;
  if u.day<>(now() at time zone 'utc')::date then
    update public.cloud_profiles set day=(now() at time zone 'utc')::date,uploads=0,ai=0 where owner=uid;
    u.uploads:=0; u.ai:=0;
  end if;
  if p_kind='upload' then
    if b.uploads>=20 or b.lifetime_uploads>=100 or u.uploads>=6 then raise exception 'cloud_limit'; end if;
    update public.cloud_budget set uploads=uploads+1,lifetime_uploads=lifetime_uploads+1 where singleton;
    update public.cloud_profiles set uploads=uploads+1 where owner=uid;
  else
    if b.ai>=40 or b.lifetime_ai>=200 or u.ai>=5 or b.last_ai>now()-interval '60 seconds'
      then raise exception 'cloud_limit'; end if;
    update public.cloud_budget set ai=ai+1,lifetime_ai=lifetime_ai+1,last_ai=now() where singleton;
    update public.cloud_profiles set ai=ai+1 where owner=uid;
  end if;
end $$;

create function public.cloud_bootstrap() returns void language plpgsql security definer
set search_path = pg_catalog, public as $$
begin
  perform public.cloud_guard();
  if auth.uid() is null then raise exception 'cloud_document'; end if;
  perform 1 from public.cloud_budget where singleton for update;
  if not found then raise exception 'cloud_document'; end if;
  if not exists(select 1 from public.cloud_profiles where owner=auth.uid()) then
    -- Starter 100 namespaces: cap at 50 permanent accounts; no paid upgrade.
    if (select count(*) from public.cloud_profiles)>=50 then raise exception 'cloud_limit'; end if;
    insert into public.cloud_profiles(owner) values(auth.uid());
  end if;
end $$;

create function public.cloud_begin(p_id text,p_filename text,p_pages integer,p_chunks integer)
returns jsonb language plpgsql security definer set search_path = pg_catalog, public as $$
declare d public.cloud_documents; uid uuid:=auth.uid();
begin
  perform public.cloud_guard();
  if uid is null then raise exception 'cloud_document'; end if;
  perform 1 from public.cloud_budget where singleton for update;
  if not found then raise exception 'cloud_document'; end if;
  select * into d from public.cloud_documents where owner=uid and id=p_id;
  if found then return to_jsonb(d); end if;
  if (select count(*) from public.cloud_documents where owner=uid)>=3
    or (select coalesce(sum(pages),0) from public.cloud_documents where owner=uid)+p_pages>20
    or (select coalesce(sum(chunks),0) from public.cloud_documents where owner=uid)+p_chunks>200
    or (select count(*) from public.cloud_documents)>=24 then raise exception 'cloud_limit'; end if;
  perform public.cloud_charge('upload');
  insert into public.cloud_documents(owner,id,filename,pages,chunks,state)
    values(uid,p_id,p_filename,p_pages,p_chunks,'pending') returning * into d;
  return to_jsonb(d)||jsonb_build_object('created',true);
end $$;

create function public.cloud_finish(p_id text) returns void language plpgsql security definer
set search_path = pg_catalog, public as $$
begin
  perform public.cloud_guard();
  update public.cloud_documents set state='ready' where owner=auth.uid() and id=p_id
    and state='pending' and lease_until>now();
  if not found then raise exception 'cloud_document'; end if;
end $$;

create function public.cloud_delete(p_id text) returns void language plpgsql security definer
set search_path = pg_catalog, public as $$
declare d public.cloud_documents;
begin
  perform public.cloud_guard();
  select * into d from public.cloud_documents where owner=auth.uid() and id=p_id for update;
  if not found then raise exception 'cloud_document'; end if;
  if d.state='pending' and d.lease_until>now() then raise exception 'cloud_busy'; end if;
  update public.cloud_documents set state='deleting' where owner=auth.uid() and id=p_id;
end $$;

create function public.cloud_forget(p_id text) returns void language plpgsql security definer
set search_path = pg_catalog, public as $$
begin
  perform public.cloud_guard();
  delete from public.cloud_documents where owner=auth.uid() and id=p_id and state='deleting';
  if not found then raise exception 'cloud_document'; end if;
  delete from public.cloud_turns where owner=auth.uid() and p_id=any(document_ids);
end $$;

create function public.cloud_ai(p_ids text[]) returns void language plpgsql security definer
set search_path = pg_catalog, public as $$
begin
  perform public.cloud_guard();
  if cardinality(p_ids) not between 1 and 3 or (select count(*) from public.cloud_documents
    where owner=auth.uid() and id=any(p_ids) and state='ready')<>cardinality(p_ids)
    then raise exception 'cloud_document'; end if;
  perform public.cloud_charge('ai');
end $$;

create function public.cloud_save_turn(p_ids text[],p_question text,p_answer text,p_citations jsonb)
returns void language plpgsql security definer set search_path = pg_catalog, public as $$
begin
  perform public.cloud_guard();
  perform 1 from public.cloud_profiles where owner=auth.uid() for update;
  if auth.uid() is null or cardinality(p_ids) not between 1 and 3 or
    (select count(*) from public.cloud_documents where owner=auth.uid() and id=any(p_ids) and state='ready')<>cardinality(p_ids)
    then raise exception 'cloud_document'; end if;
  if (select count(*) from public.cloud_turns where owner=auth.uid())>=50 then raise exception 'cloud_limit'; end if;
  insert into public.cloud_turns(owner,document_ids,question,answer,citations)
    values(auth.uid(),p_ids,p_question,p_answer,p_citations);
end $$;

-- SECURITY DEFINER functions have explicit auth.uid() predicates and fixed paths.
revoke all on function public.cloud_charge(text) from public,anon,authenticated;
revoke all on function public.cloud_guard() from public,anon,authenticated;
revoke all on function public.cloud_bootstrap(),public.cloud_begin(text,text,integer,integer),
 public.cloud_finish(text),public.cloud_delete(text),public.cloud_forget(text),
 public.cloud_ai(text[]),public.cloud_save_turn(text[],text,text,jsonb) from public,anon;
grant execute on function public.cloud_bootstrap(),public.cloud_begin(text,text,integer,integer),
 public.cloud_finish(text),public.cloud_delete(text),public.cloud_forget(text),
 public.cloud_ai(text[]),public.cloud_save_turn(text[],text,text,jsonb) to authenticated;
commit;
