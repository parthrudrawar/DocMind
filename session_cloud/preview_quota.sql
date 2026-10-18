-- Preview-only additive setup. Review/apply to the preview Supabase project.
-- No existing cloud_* tables, data, credentials or production RPCs are changed.
begin;
create table public.preview_ai_usage (
  owner uuid primary key references auth.users(id) on delete cascade,
  day date not null,
  requests integer not null check (requests between 0 and 25)
);
alter table public.preview_ai_usage enable row level security;
revoke all on public.preview_ai_usage from public, anon, authenticated;

create function public.preview_reserve_ai() returns integer
language plpgsql security definer set search_path = pg_catalog, pg_temp as $$
declare
  uid uuid := auth.uid();
  usage public.preview_ai_usage;
  today date;
begin
  if uid is null then raise exception 'preview_auth_required'; end if;
  insert into public.preview_ai_usage(owner, day, requests)
    values(uid, (clock_timestamp() at time zone 'UTC')::date, 0)
    on conflict(owner) do nothing;
  select * into strict usage from public.preview_ai_usage where owner=uid for update;
  -- Compute the day AFTER acquiring the account lock, including at midnight.
  today := (clock_timestamp() at time zone 'UTC')::date;
  if usage.day <> today then usage.requests := 0; end if;
  if usage.requests >= 25 then return 0; end if;
  update public.preview_ai_usage set day=today, requests=usage.requests+1 where owner=uid;
  return usage.requests+1;
end $$;
revoke all on function public.preview_reserve_ai() from public, anon;
grant execute on function public.preview_reserve_ai() to authenticated;
commit;
