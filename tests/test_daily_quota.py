"""Durable account quotas, real isolated PostgreSQL and mocked provider failures."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import json
import os
import socket
import subprocess

import httpx
import pytest
import test_generation
import test_guides
import test_comparisons
import test_exploration

from session_cloud.auth_client import AuthConfig
from session_cloud.daily_quota import SupabaseDailyQuota
from session_cloud.runtime import SessionError
from session_cloud.generation_budget import ProviderBudget

env = test_generation.env
guide_env = test_guides.env
comparison_env = test_comparisons.comparison_env
exploration_env = test_exploration.exploration_env
A, B = test_generation.A, test_generation.B
USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
CONFIG = AuthConfig("https://dummy.supabase.co", "sb_publishable_dummy", "https://dummy.streamlit.app/")


def test_definer_path_excludes_public_and_places_temporary_objects_last():
    import re
    sql = (Path(__file__).resolve().parents[1] / "session_cloud/preview_quota.sql").read_text(encoding="utf-8")
    path = re.search(r"security\s+definer\s+set\s+search_path\s*=\s*(.*?)\s+as\s+\$\$", sql, re.I).group(1)
    assert [name.strip() for name in path.split(",")] == ["pg_catalog", "pg_temp"]
    assert "public.preview_ai_usage" in sql and "auth.uid()" in sql
    assert "revoke all on function public.preview_reserve_ai() from public, anon" in sql


def test_preflight_is_catalog_only_and_never_invokes_quota_or_returns_function_bodies():
    import re
    sql = (Path(__file__).resolve().parents[1] / "session_cloud/preview_quota_preflight.sql").read_text(encoding="utf-8")
    code = re.sub(r"--[^\n]*", "", sql)
    code = re.sub(r"'(?:''|[^'])*'", "''", code)
    statements = [s.strip().lower() for s in code.split(";") if s.strip()]
    assert len(statements) == 5 and all(s.startswith(("select ", "with ")) for s in statements)
    assert not re.search(r"\b(create|alter|drop|insert|update|delete|truncate|grant|revoke|call|do|execute)\b", code, re.I)
    assert not re.search(r"\bpreview_reserve_ai\s*\(", code, re.I)
    assert "pg_get_functiondef" not in code.lower()
    assert "pg_catalog.pg_proc" in code and "pg_catalog.has_function_privilege" in code


def quota_with_reply(reply, *, owner="user-a"):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.path == "/rest/v1/rpc/preview_reserve_ai"
        assert json.loads(request.content) == {}
        return reply(request) if callable(reply) else httpx.Response(200, json=reply)
    auth = SimpleNamespace(authorize=lambda _: SimpleNamespace(user_id=owner, access="dummy-access"))
    return SupabaseDailyQuota(auth, CONFIG, transport=httpx.MockTransport(handler)), requests


@pytest.mark.parametrize("reply,code", [
    (0, "daily_ai_limit"), (True, "quota_unavailable"), (26, "quota_unavailable"),
    ({"allowed": True}, "quota_unavailable"), (-1, "quota_unavailable"),
    (lambda _: httpx.Response(404, text="dummy-private-error"), "quota_unavailable"),
    (lambda _: httpx.Response(429, text="dummy-private-error"), "quota_unavailable"),
    (lambda _: httpx.Response(401), "expired"),
])
def test_quota_invalid_replies_fail_closed_and_do_not_leak(reply, code, caplog):
    quota, requests = quota_with_reply(reply)
    with pytest.raises(SessionError) as caught:
        quota.reserve(A, "user-a")
    assert caught.value.code == code and len(requests) == 1
    assert "dummy-private-error" not in str(caught.value) + caplog.text
    assert "dummy-access" not in str(caught.value) + caplog.text


def test_quota_identity_is_verified_and_no_claimed_owner_is_sent():
    quota, requests = quota_with_reply(1)
    with pytest.raises(SessionError):
        quota.reserve(A, "user-b")
    assert requests == []
    quota.reserve(A, "user-a")
    assert json.loads(requests[0].content) == {}


def test_25_account_reservations_are_atomic_and_utc_day_changes_reset_local_guard():
    now = [86399.]
    budget = ProviderBudget(clock=lambda: now[0])
    def attempt(_):
        try:
            budget.reserve("verified-owner", [{"role": "user", "content": "q"}], 10)
            return "allowed"
        except SessionError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(40)))
    assert outcomes.count("allowed") == 25 and outcomes.count("daily_ai_limit") == 15
    assert budget.calls == 25
    now[0] = 86400.
    assert attempt(0) == "allowed" and budget.calls == 1


def test_uncertain_quota_rpc_is_not_retried_and_does_not_send_groq(env):
    def timeout(_):
        raise httpx.ReadTimeout("dummy-private-token")
    quota, attempts = quota_with_reply(timeout)
    env.store.daily_quota = quota
    with pytest.raises(SessionError) as caught:
        list(env.store.generation.stream(A, env.refs[A], env.chats[A], "quota-timeout",
             "What is Amber's generating capacity?", [env.docs[A]]))
    test_generation.wait_idle(env)
    assert caught.value.code == "quota_unavailable"
    assert len(attempts) == 1 and env.requests == []
    assert "dummy-private-token" not in str(caught.value)


def test_missing_durable_quota_never_contacts_groq(env):
    env.store.daily_quota = None
    with pytest.raises(SessionError) as caught:
        list(env.store.generation.stream(A, env.refs[A], env.chats[A], "missing-quota",
             "What is Amber's generating capacity?", [env.docs[A]]))
    assert caught.value.code == "quota_unavailable" and env.requests == []


@pytest.mark.parametrize("status,code", [(401, "provider_auth"), (429, "provider_rate"), (500, "provider_unavailable")])
def test_failed_provider_attempt_is_charged_and_provider_limit_is_separate(env, status, code):
    quota, reservations = quota_with_reply(1)
    env.store.daily_quota = quota
    remote, requests, _ = test_generation.provider([status])
    env.store.generation.provider = remote
    with pytest.raises(SessionError) as caught:
        list(env.store.generation.stream(A, env.refs[A], env.chats[A], "failed-provider",
             "What is Amber's generating capacity?", [env.docs[A]]))
    test_generation.wait_idle(env)
    assert caught.value.code == code and len(reservations) == len(requests) == 1
    assert env.store.history(A, env.refs[A], env.chats[A]) == []


def test_chat_answer_followup_and_replay_share_one_account_counter(env):
    quota, reservations = quota_with_reply(1)
    env.store.daily_quota = quota
    test_generation.run(env)
    test_generation.wait_idle(env)
    assert len(reservations) == len(env.requests) == 2
    test_generation.run(env)  # Completed replay makes no new request.
    assert len(reservations) == 2


@pytest.mark.parametrize("kind", ["guide", "brief"])
def test_guides_and_briefs_share_durable_chat_quota(guide_env, kind):
    e = guide_env
    quota, reservations = quota_with_reply(1)
    e.store.daily_quota = quota
    test_guides.run(e)
    test_guides.idle(e)
    if kind == "brief":
        test_guides.run(e, kind="brief", request_id="brief")
        test_guides.idle(e)
    assert len(reservations) == len(e.requests) == (2 if kind == "brief" else 1)


@pytest.mark.parametrize("kind", ["contradictions", "gaps"])
def test_comparisons_share_durable_chat_quota(comparison_env, kind):
    e = comparison_env
    quota, reservations = quota_with_reply(1)
    e.store.daily_quota = quota
    e.responses.append(test_guides.Frames("[]"))
    test_comparisons.run(e, kind)
    test_guides.idle(e)
    assert len(reservations) == len(e.requests) == 1


@pytest.mark.parametrize("kind", ["questions", "insights", "timeline"])
def test_remaining_analyses_share_durable_chat_quota(exploration_env, kind):
    e = exploration_env
    quota, reservations = quota_with_reply(1)
    e.store.daily_quota = quota
    test_exploration.respond(e, kind)
    test_exploration.run(e, kind)
    test_guides.idle(e)
    assert len(reservations) == len(e.requests) == (2 if kind == "timeline" else 1)


@pytest.fixture(scope="module")
def postgres(tmp_path_factory):
    """Never connects to an existing cluster. Dedicated data dir + random loopback port."""
    binaries = os.environ.get("DOCUMIND_TEST_POSTGRES_BIN")
    if not binaries:
        pytest.skip("Isolated PostgreSQL binaries not configured; SQL execution is not verified")
    root = tmp_path_factory.mktemp("quota-postgres")
    data = root / "data"
    suffix = ".exe" if os.name == "nt" else ""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PG")}
    env["PGPASSFILE"] = str(root / "nonexistent-test-password-file")
    def command(name, *args, input=None):
        return subprocess.run([str(Path(binaries) / (name + suffix)), *args], input=input,
            text=True, capture_output=True, timeout=40, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    result = command("initdb", "-D", str(data), "-U", "postgres", "-A", "trust", "--no-locale", "--encoding=UTF8")
    if result.returncode != 0 and "could not create restricted token" in result.stderr:
        pytest.skip("Windows sandbox blocks PostgreSQL restricted-token startup; live SQL checks unverified")
    assert result.returncode == 0, "Isolated test database initialization failed: " + result.stderr
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = str(listener.getsockname()[1])
    result = command("pg_ctl", "-D", str(data), "-l", str(root / "postgres.log"), "-w", "start",
        "-o", f"-h 127.0.0.1 -p {port} -c max_connections=20 -c shared_buffers=16MB")
    assert result.returncode == 0, "Isolated PostgreSQL start failed: " + result.stderr
    def sql(query, *, owner=None, role=None, ok=True):
        prefix = "begin;"
        if owner:
            assert owner in (USER_A, USER_B)
            prefix += f"set local request.jwt.claim.sub='{owner}';"
        if role:
            assert role in ("authenticated", "anon")
            prefix += f"set local role {role};"
        result = command("psql", "-X", "-h", "127.0.0.1", "-p", port, "-U", "postgres", "-d", "postgres",
            "-Atq", "--set=ON_ERROR_STOP=1", input=prefix + query + ";commit;")
        if ok:
            assert result.returncode == 0, result.stderr
        else:
            assert result.returncode != 0
        return result.stdout.strip()
    try:
        sql("create role anon; create role authenticated; create schema auth;"
            "create table auth.users(id uuid primary key);"
            "create function auth.uid() returns uuid language sql as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;"
            "grant usage on schema auth to authenticated; grant execute on function auth.uid() to authenticated;"
            f"insert into auth.users values('{USER_A}'),('{USER_B}')")
        migration = Path(__file__).resolve().parents[1] / "session_cloud/preview_quota.sql"
        sql(migration.read_text(encoding="utf-8"))
        yield sql
    finally:
        # Stop only the server started above. Data is retained in the test root.
        result = command("pg_ctl", "-D", str(data), "-w", "stop", "-m", "fast")
        assert result.returncode == 0, "Isolated PostgreSQL shutdown failed"


def test_real_sql_concurrency_utc_rollover_rls_and_no_reset_api(postgres):
    sql = postgres
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: int(sql("select public.preview_reserve_ai()", owner=USER_A,
                                                role="authenticated")), range(40)))
    assert sorted(v for v in results if v) == list(range(1, 26)) and results.count(0) == 15
    assert sql("select public.preview_reserve_ai()", owner=USER_B, role="authenticated") == "1"
    for statement in ("select * from public.preview_ai_usage", "delete from public.preview_ai_usage",
                      "update public.preview_ai_usage set requests=0", "select public.preview_reserve_ai('forged')"):
        sql(statement, owner=USER_A, role="authenticated", ok=False)
    sql("select public.preview_reserve_ai()", role="anon", ok=False)
    sql("select public.preview_reserve_ai()", role="authenticated", ok=False)
    sql(f"update public.preview_ai_usage set day=(now() at time zone 'UTC')::date-1 where owner='{USER_A}'")
    assert sql("select public.preview_reserve_ai()", owner=USER_A, role="authenticated") == "1"


def test_real_sql_survives_new_runtime_and_relogin_without_refund(postgres):
    sql = postgres
    sql(f"update public.preview_ai_usage set requests=23,day=(now() at time zone 'UTC')::date where owner='{USER_A}'")
    users = {A: USER_A, B: USER_B}
    def authorize(capability):
        if capability not in users:
            raise SessionError("expired")
        return SimpleNamespace(user_id=users[capability], access="dummy-" + users[capability])
    def handler(request):
        owner = request.headers["Authorization"].removeprefix("Bearer dummy-")
        assert owner in (USER_A, USER_B)
        return httpx.Response(200, json=int(sql("select public.preview_reserve_ai()", owner=owner, role="authenticated")))
    auth = SimpleNamespace(authorize=authorize)
    quota = SupabaseDailyQuota(auth, CONFIG, transport=httpx.MockTransport(handler))
    quota.reserve(A, USER_A)  # 24; retained even if a provider later fails.
    users.pop(A)  # Logout revokes the old capability, not the account counter.
    with pytest.raises(SessionError):
        quota.reserve(A, USER_A)
    users["c" * 64] = USER_A  # New login / new runtime, same verified account.
    restarted = SupabaseDailyQuota(auth, CONFIG, transport=httpx.MockTransport(handler))
    restarted.reserve("c" * 64, USER_A)  # 25
    with pytest.raises(SessionError) as caught:
        restarted.reserve("c" * 64, USER_A)
    assert caught.value.code == "daily_ai_limit"
    restarted.reserve(B, USER_B)
