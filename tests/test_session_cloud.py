"""Phase 1 fixtures use fresh temp roots, dummy identities and mocked Auth only."""
from dataclasses import replace
from io import BytesIO
import threading
from types import SimpleNamespace

import httpx
import pytest

from authenticated_cloud.auth import AuthBroker, CloudError
from session_cloud.adapter import LocalAPI, bind_chat_state
from session_cloud.auth_client import AuthConfig, AuthOnlyClient
from session_cloud.chat_state import complete_turn, enqueue_turn
from session_cloud.runtime import Limits, SessionError, WorkspaceStore

A, B, C = "a" * 64, "b" * 64, "c" * 64
USER_A, USER_B = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
PDF_A, PDF_B = b"%PDF-1.7\nfixture A", b"%PDF-1.7\nfixture B"


class FakeAuth:
    def __init__(self, clock):
        self.clock = clock
        self.users = {A: USER_A, B: USER_B, C: USER_A}

    def authorize(self, capability):
        if capability not in self.users:
            raise CloudError("expired")
        return SimpleNamespace(user_id=self.users[capability], deadline=50_000)

    def logout(self, capability):
        self.users.pop(capability, None)
        return True


@pytest.fixture
def environment(tmp_path):
    now = [1000.0]
    auth = FakeAuth(lambda: now[0])
    store = WorkspaceStore(auth, clock=lambda: now[0], base_dir=tmp_path)
    return store, auth, now


def test_independent_workspaces_documents_history_and_foreign_ids(environment):
    store, _, _ = environment
    wa, wb = store.open(A), store.open(B)
    assert wa != wb and store.documents(B, wb) == []
    da = store.upload(A, wa, "A.pdf", BytesIO(PDF_A))
    db = store.upload(B, wb, "B.pdf", BytesIO(PDF_B))
    assert da["status"] == "accepted" and db["status"] == "accepted"
    ca, cb = store.new_chat(A, wa), store.new_chat(B, wb)
    assert store.save_turn(A, wa, ca, "turn1", "A question", "A answer", {"citations": []})
    assert not store.save_turn(A, wa, ca, "turn1", "duplicate", "duplicate", {})
    assert store.history(B, wb, cb) == []
    for operation in [lambda: store.documents(A, wb),
                      lambda: store.delete_document(A, wb, db["id"]),
                      lambda: store.delete_document(A, wa, db["id"]),
                      lambda: store.history(A, wa, cb),
                      lambda: store.save_turn(B, wb, ca, "t", "q", "a", {})]:
        with pytest.raises(CloudError):
            operation()
    assert [d["filename"] for d in store.documents(A, wa)] == ["A.pdf"]
    assert [d["filename"] for d in store.documents(B, wb)] == ["B.pdf"]
    assert store.history(A, wa, ca)[1]["content"] == "A answer"


def test_same_account_separate_login_capabilities_are_isolated(environment):
    store, _, _ = environment
    wa, wc = store.open(A), store.open(C)
    store.upload(A, wa, "A.pdf", BytesIO(PDF_A))
    assert wc != wa and store.documents(C, wc) == []
    with pytest.raises(CloudError):
        store.documents(C, wa)


def test_invalid_token_or_changed_identity_cannot_access_workspace(environment):
    store, auth, _ = environment
    ref = store.open(A)
    with pytest.raises(CloudError):
        store.documents("0" * 64, ref)
    auth.users[A] = USER_B
    with pytest.raises(CloudError):
        store.documents(A, ref)


def test_failed_workspace_initialization_leaves_no_live_registry_entry(tmp_path):
    auth = FakeAuth(lambda: 1000)
    store = WorkspaceStore(auth, clock=auth.clock, base_dir=tmp_path,
                           limits=replace(Limits(), workspace_bytes=1))
    with pytest.raises(SessionError):
        store.open(A)
    assert not store._leases and not store._known
    assert [p.name for p in store.root.iterdir()] == [".instance"]


def test_forged_logout_cannot_grow_retired_registry_or_affect_other_user(environment):
    store, _, _ = environment
    ref = store.open(A)
    store.logout("0" * 64)
    assert not store._retired and store.documents(A, ref) == []


def test_reused_markdown_and_citation_renderer_escapes_untrusted_html():
    from frontend.citations import render_answer_html

    html = render_answer_html("<script>dummy()</script>\n\n**Bold**\n\n| A | B |\n| --- | --- |\n| Yes | No |", [])
    assert "<script>" not in html and "<strong>Bold</strong>" in html and "<table>" in html


def test_content_duplicates_do_not_claim_indexing(environment):
    store, _, _ = environment
    ref = store.open(A)
    first = store.upload(A, ref, "one.pdf", BytesIO(PDF_A))
    second = store.upload(A, ref, "renamed.pdf", BytesIO(PDF_A))
    assert second == {"duplicate": True, "status": "accepted", "id": first["id"]}
    assert len(store.documents(A, ref)) == 1
    assert not list(store.root.rglob("*.part"))


def test_logout_removes_only_its_own_workspace(environment, tmp_path):
    store, auth, _ = environment
    sentinel = tmp_path / "preserve.txt"
    sentinel.write_text("Unrelated user data", encoding="utf-8")
    wa, wb = store.open(A), store.open(B)
    store.upload(A, wa, "a.pdf", BytesIO(PDF_A))
    store.upload(B, wb, "b.pdf", BytesIO(PDF_B))
    assert store.logout(A)
    assert A not in auth.users and not (store.root / wa).exists()
    assert (store.root / wb).is_dir() and sentinel.read_text() == "Unrelated user data"
    with pytest.raises(CloudError):
        store.documents(A, wa)
    assert store.documents(B, wb)[0]["filename"] == "b.pdf"


def test_expiry_revokes_before_reopening_and_preserves_live_peer(environment):
    store, _, now = environment
    wa = store.open(A)
    now[0] += 100
    wb = store.open(B)
    now[0] += 3501
    for operation in [lambda: store.documents(A, wa), lambda: store.open(A)]:
        with pytest.raises(CloudError):
            operation()
    assert not (store.root / wa).exists()
    assert store.documents(B, wb) == []


def test_new_process_cannot_restore_an_old_workspace(environment, tmp_path):
    store, auth, _ = environment
    ref = store.open(A)
    store.upload(A, ref, "a.pdf", BytesIO(PDF_A))
    restarted = WorkspaceStore(auth, clock=auth.clock, base_dir=tmp_path)
    new_ref = restarted.open(A)
    assert restarted.root != store.root and new_ref != ref
    with pytest.raises(CloudError):
        restarted.documents(A, ref)
    assert restarted.documents(A, new_ref) == []


@pytest.mark.parametrize("path", ["../legacy", "/tmp/legacy", "..\\legacy", "", "a" * 32])
def test_unissued_workspace_paths_are_rejected(environment, path):
    store, _, _ = environment
    store.open(A)
    with pytest.raises(CloudError):
        store.documents(A, path)


def test_symlink_cleanup_fails_closed(environment, tmp_path):
    store, _, now = environment
    ref = store.open(A)
    target = tmp_path / "legacy.txt"
    target.write_text("preserve", encoding="utf-8")
    link = store.root / ref / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("OS does not permit test symlinks")
    now[0] += 3601
    with pytest.raises(CloudError):
        store.open(B)
    assert target.read_text() == "preserve"


@pytest.mark.parametrize("data", [b"", b"not a pdf", b"%PDF-" + b"x" * 100])
def test_invalid_or_oversized_upload_is_never_partially_registered(tmp_path, data):
    auth = FakeAuth(lambda: 1000)
    store = WorkspaceStore(auth, clock=auth.clock, limits=replace(Limits(), upload_bytes=32), base_dir=tmp_path)
    ref = store.open(A)
    with pytest.raises(SessionError):
        store.upload(A, ref, "test.pdf", BytesIO(data))
    assert store.documents(A, ref) == []
    assert not list(store.root.rglob("*.part")) and not list(store.root.rglob("*.pdf"))


def test_full_100_mib_acceptance_does_not_mean_processing(environment):
    class GeneratedPDF:
        remaining = 100 * 1024 * 1024
        first = True

        def read(self, size):
            n = min(size, self.remaining)
            self.remaining -= n
            if self.first:
                self.first = False
                return b"%PDF-" + b"0" * (n - 5)
            return b"0" * n

    store, _, _ = environment
    ref = store.open(A)
    result = store.upload(A, ref, "100mb.pdf", GeneratedPDF())
    doc = store.documents(A, ref)[0]
    assert doc["size"] == 100 * 1024 * 1024 and result["status"] == "accepted"
    assert (store.root / ref / (result["id"] + ".pdf")).stat().st_size == doc["size"]


def test_storage_capacity_is_enforced_without_partial_record(tmp_path):
    store = WorkspaceStore(FakeAuth(lambda: 1000), clock=lambda: 1000, base_dir=tmp_path,
                           limits=replace(Limits(), workspace_bytes=64 * 1024))
    ref = store.open(A)
    with pytest.raises(SessionError) as caught:
        store.upload(A, ref, "too-large.pdf", BytesIO(b"%PDF-" + b"x" * 60_000))
    assert caught.value.code == "disk" and store.documents(A, ref) == []
    assert not list(store.root.rglob("*.part"))


def test_concurrency_rejects_second_upload_and_preserves_isolation(environment):
    store, _, _ = environment
    wa, wb = store.open(A), store.open(B)
    started, release = threading.Event(), threading.Event()
    results = []

    class SlowPDF(BytesIO):
        def read(self, size):
            started.set()
            assert release.wait(5)
            return super().read(size)

    def work():
        try:
            results.append(store.upload(A, wa, "a.pdf", SlowPDF(PDF_A)))
        except Exception as error:
            results.append(error)

    thread = threading.Thread(target=work)
    thread.start()
    try:
        assert started.wait(5)
        with pytest.raises(SessionError) as caught:
            store.upload(B, wb, "b.pdf", BytesIO(PDF_B))
        assert caught.value.code == "busy" and store.documents(B, wb) == []
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and len(results) == 1 and isinstance(results[0], dict)


def test_upload_timeout_removes_partial_bytes(environment):
    store, _, _ = environment
    ref = store.open(A)
    ticks = iter([0, 31])
    store.monotonic = lambda: next(ticks)
    with pytest.raises(SessionError) as caught:
        store.upload(A, ref, "slow.pdf", BytesIO(PDF_A))
    assert caught.value.code == "busy" and store.documents(A, ref) == []
    assert not list(store.root.rglob("*.part"))


def test_workspace_document_chat_and_upload_budgets(tmp_path):
    auth = FakeAuth(lambda: 1000)
    limits = replace(Limits(), documents=1, chats=1, turns=1, user_uploads=1, global_uploads=2)
    store = WorkspaceStore(auth, clock=auth.clock, base_dir=tmp_path, limits=limits)
    wa, wb = store.open(A), store.open(B)
    with pytest.raises(SessionError):
        store.open(C)
    store.upload(A, wa, "a.pdf", BytesIO(PDF_A))
    store.logout(A)
    wc = store.open(C)  # Same verified account, fresh capability/workspace.
    with pytest.raises(SessionError):
        store.upload(C, wc, "a.pdf", BytesIO(PDF_A))
    cb = store.new_chat(B, wb)
    with pytest.raises(SessionError):
        store.new_chat(B, wb)
    assert store.save_turn(B, wb, cb, "1", "q", "a", {})
    with pytest.raises(SessionError):
        store.save_turn(B, wb, cb, "2", "q", "a", {})
    assert len(store.history(B, wb, cb)) == 2


def test_scope_changes_clear_old_ui_and_reruns_do_not_duplicate_history():
    state = {"messages": [{"content": "private A"}], "pending_turn": {"question": "private"}}
    bind_chat_state(state, "workspace-b", "chat-b", [])
    assert state["messages"] == [] and "pending_turn" not in state
    assert enqueue_turn(state, "question", "turn1")
    assert not enqueue_turn(state, "question", "turn1")
    assert complete_turn(state, "answer", {})
    assert not complete_turn(state, "answer", {})
    assert len(state["messages"]) == 2


@pytest.mark.parametrize("interrupt", [False, True])
def test_stream_contract_keeps_partial_output_without_internal_errors(environment, interrupt):
    store, _, _ = environment
    ref = store.open(A)
    chat = store.new_chat(A, ref)
    api = LocalAPI(store, A, ref, chat)
    closed, output = [], []

    def events():
        try:
            yield {"type": "stage", "stage": "generating", "status": "active"}
            yield {"type": "token", "content": "Partial answer"}
            if interrupt:
                raise RuntimeError("dummy-provider-secret-never-display")
            yield {"type": "done"}
        finally:
            closed.append(True)

    text, metadata = api.parse_stream(events(), on_token=output.append)
    assert text == "Partial answer" and output == [text] and closed == [True]
    assert bool(metadata.get("stream_interrupted")) == interrupt
    assert "dummy-provider-secret" not in str(metadata)
    with pytest.raises(SessionError) as caught:
        api.query_stream("q", ref, chat)
    assert caught.value.code == "phase"


def test_stream_revalidates_auth_before_each_output(environment):
    store, auth, _ = environment
    ref = store.open(A)
    api = LocalAPI(store, A, ref, store.new_chat(A, ref))
    output = []

    def events():
        yield {"type": "token", "content": "First"}
        auth.logout(A)
        yield {"type": "token", "content": "Must not escape"}

    with pytest.raises(CloudError):
        api.parse_stream(events(), on_token=output.append)
    assert output == ["First"]


def test_existing_google_pkce_refresh_and_logout_use_auth_only_transport():
    now, paths = [1000], []
    config = AuthConfig.load({"FREE_PLANS_CONFIRMED": True, "SUPABASE_URL": "https://dummy.supabase.co",
                              "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_dummy",
                              "APP_URL": "https://dummy.streamlit.app/"})

    def handler(request):
        paths.append(request.url.path)
        assert "x-documind-write-key" not in request.headers
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": USER_A, "email_confirmed_at": "dummy",
                                              "identities": [{"provider": "google"}]})
        if request.url.path == "/auth/v1/token":
            return httpx.Response(200, json={"access_token": "dummy-access", "refresh_token": "dummy-refresh", "expires_in": 90})
        return httpx.Response(204)

    client = AuthOnlyClient(config, transport=httpx.MockTransport(handler))
    broker = AuthBroker(client, config.app_url, clock=lambda: now[0])
    flow = broker.begin()
    capability = broker.complete(flow["state"], flow["binding"], "dummy-code")
    assert broker.authorize(capability).user_id == USER_A
    now[0] += 31
    assert broker.authorize(capability).user_id == USER_A
    assert paths.count("/auth/v1/token") == 2
    assert broker.logout(capability)
    with pytest.raises(CloudError):
        broker.authorize(capability)
    with pytest.raises(CloudError):
        client.call("/rest/v1/cloud_documents", method="GET")
    assert all(path.startswith("/auth/v1/") for path in paths)


def test_original_ui_shell_and_two_independent_views(environment, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from session_cloud import streamlit_app as ui

    store, _, _ = environment
    wa = store.open(A)
    store.upload(A, wa, "Only-A.pdf", BytesIO(PDF_A))
    monkeypatch.setattr(ui, "_fixture_store", store, raising=False)
    a = AppTest.from_string(f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store, '{A}')").run(timeout=20)
    b = AppTest.from_string(f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store, '{B}')").run(timeout=20)
    assert not a.exception and not b.exception
    assert [tab.label for tab in a.tabs] == ui.TABS
    assert len(a.tabs) == len(b.tabs) == 7
    assert a.chat_input[0].disabled and b.chat_input[0].disabled
    assert any(button.label == "New Chat" for button in a.sidebar.button)
    assert "Only-A.pdf" in [expander.label for expander in a.sidebar.expander]
    assert "Only-A.pdf" not in [expander.label for expander in b.sidebar.expander]
    a.sidebar.button[0].click().run(timeout=20)
    assert not a.exception and len(store.chats(A, wa)) == 2
