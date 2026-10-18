-- READ ONLY. Run each numbered SELECT block separately in the PREVIEW project's
-- Supabase SQL Editor. This file neither invokes the quota RPC nor reads user
-- records, tokens, settings values or function bodies. It applies no migration.

-- 1. Prerequisites and migration-role privileges. Expected: editor_role postgres;
-- all boolean fields true. Missing prerequisites or other roles require review.
select current_user as editor_role,
  pg_catalog.to_regclass('auth.users') is not null as auth_users_exists,
  pg_catalog.to_regprocedure('auth.uid()') is not null as auth_uid_exists,
  exists(select 1 from pg_catalog.pg_proc
    where oid=pg_catalog.to_regprocedure('auth.uid()') and not proretset
      and prorettype='pg_catalog.uuid'::pg_catalog.regtype) as auth_uid_returns_uuid,
  exists(select 1 from pg_catalog.pg_attribute
    where attrelid=pg_catalog.to_regclass('auth.users') and attname='id'
      and not attisdropped and atttypid='pg_catalog.uuid'::pg_catalog.regtype) as auth_id_is_uuid,
  exists(select 1 from pg_catalog.pg_constraint k
    join pg_catalog.pg_attribute a on a.attrelid=k.conrelid and a.attname='id' and not a.attisdropped
    where k.conrelid=pg_catalog.to_regclass('auth.users') and k.contype='p'
      and k.conkey=array[a.attnum]) as auth_id_is_primary_key,
  pg_catalog.has_schema_privilege(current_user,
    pg_catalog.to_regnamespace('public'), 'USAGE') as can_use_public,
  pg_catalog.has_schema_privilege(current_user,
    pg_catalog.to_regnamespace('auth'), 'USAGE') as can_use_auth,
  pg_catalog.has_schema_privilege(current_user,
    pg_catalog.to_regnamespace('public'), 'CREATE') as can_create_in_public,
  case when exists(select 1 from pg_catalog.pg_attribute
      where attrelid=pg_catalog.to_regclass('auth.users') and attname='id' and not attisdropped)
    then pg_catalog.has_column_privilege(current_user,
      pg_catalog.to_regclass('auth.users'), 'id', 'REFERENCES')
    else false end as can_reference_auth_id;

-- 2. Relation/type-name collisions. Expected: ZERO rows before first setup.
-- If rows exist, STOP and review; do not drop/replace objects or reset counters.
select 'relation' as object_kind, c.relname as object_name,
  pg_catalog.pg_get_userbyid(c.relowner) as owner_role, c.relkind::text as subtype
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid=c.relnamespace
where n.nspname='public' and c.relname in ('preview_ai_usage','preview_ai_usage_pkey')
union all
select 'type', t.typname, pg_catalog.pg_get_userbyid(t.typowner), t.typtype::text
from pg_catalog.pg_type t
join pg_catalog.pg_namespace n on n.oid=t.typnamespace
where n.nspname='public' and t.typname in ('preview_ai_usage','_preview_ai_usage');

-- 3. ALL matching function/procedure signatures, including overloads/default
-- arguments. Expected: ZERO rows in public before first setup. Other schemas
-- do not collide with the qualified RPC, but should be reviewed for confusion.
-- Only search_path is displayed from function configuration; no bodies/secrets.
select n.nspname as schema_name, p.proname as function_name,
  pg_catalog.pg_get_function_identity_arguments(p.oid) as identity_arguments,
  p.pronargdefaults as default_argument_count,
  pg_catalog.pg_get_function_result(p.oid) as result_type,
  pg_catalog.pg_get_userbyid(p.proowner) as owner_role,
  p.prosecdef as security_definer,
  (select setting from pg_catalog.unnest(p.proconfig) as cfg(setting)
    where setting like 'search_path=%' limit 1) as search_path_setting,
  exists(select 1 from pg_catalog.aclexplode(
      coalesce(p.proacl, pg_catalog.acldefault('f',p.proowner))) a
    where a.grantee=0 and a.privilege_type='EXECUTE') as public_can_execute,
  exists(select 1 from pg_catalog.pg_roles r where r.rolname='anon'
    and pg_catalog.has_function_privilege(r.oid,p.oid,'EXECUTE')) as anon_can_execute,
  exists(select 1 from pg_catalog.pg_roles r where r.rolname='authenticated'
    and pg_catalog.has_function_privilege(r.oid,p.oid,'EXECUTE')) as authenticated_can_execute
from pg_catalog.pg_proc p
join pg_catalog.pg_namespace n on n.oid=p.pronamespace
where p.proname='preview_reserve_ai'
order by n.nspname, identity_arguments;

-- 4. Client roles. Expected: both exist, can use public, and neither is
-- superuser/bypasses RLS.
-- Public CREATE is informational: if true, review it, but do not change shared
-- schema/role grants here. The hardened function excludes public from its path.
with wanted(role_name) as (values ('anon'), ('authenticated'))
select w.role_name, r.oid is not null as role_exists,
  r.rolsuper as is_superuser, r.rolbypassrls as bypasses_rls,
  pg_catalog.has_schema_privilege(r.oid,pg_catalog.to_regnamespace('public'),'USAGE') as can_use_public,
  pg_catalog.has_schema_privilege(r.oid,pg_catalog.to_regnamespace('public'),'CREATE') as can_create_in_public
from wanted w left join pg_catalog.pg_roles r on r.rolname=w.role_name
order by w.role_name;

-- 5. Existing quota-table permissions (optional inspection, also read only).
-- Expected: ZERO rows before setup. For an already installed table, RLS should
-- be true and all displayed client read/write privileges should be false.
-- Existing objects still require migration review, never destructive reset.
select r.rolname as role_name, c.relrowsecurity as rls_enabled,
  pg_catalog.has_table_privilege(r.oid,c.oid,'SELECT') as table_select,
  pg_catalog.has_any_column_privilege(r.oid,c.oid,'SELECT') as any_column_select,
  pg_catalog.has_table_privilege(r.oid,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') as any_table_write_or_control,
  pg_catalog.has_any_column_privilege(r.oid,c.oid,'INSERT,UPDATE,REFERENCES') as any_column_write_or_reference
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid=c.relnamespace
cross join pg_catalog.pg_roles r
where n.nspname='public' and c.relname='preview_ai_usage' and c.relkind in ('r','p')
  and r.rolname in ('anon','authenticated')
order by r.rolname;
