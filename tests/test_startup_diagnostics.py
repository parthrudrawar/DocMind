"""Dummy-only startup checks; no Secrets files, providers, databases or models."""
import ast
import copy
import logging
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from authenticated_cloud.auth import AuthBroker, CloudError, Supabase
from authenticated_cloud.diagnostics import startup_diagnostic
from authenticated_cloud.providers import Config, RemoteStore

ROOT = Path(__file__).resolve().parents[1]
LOGGER = "documind.cloud.startup"
SENSITIVE = "dummy-sensitive-sentinel-never-log"


def values():
    return {
        "FREE_PLANS_CONFIRMED": True,
        "SUPABASE_URL": "https://dummy.supabase.co",
        "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_dummy",
        "APP_URL": "https://dummy.streamlit.app/",
        "CLOUDINARY_CLOUD_NAME": "dummy-cloud",
        "CLOUDINARY_API_KEY": "dummy-cloud-key-" + SENSITIVE,
        "CLOUDINARY_API_SECRET": "dummy-cloud-secret-" + SENSITIVE,
        "PINECONE_API_KEY": "dummy-pinecone-" + SENSITIVE,
        "PINECONE_INDEX": "documind-cloud",
        "PINECONE_HOST": "https://dummy.svc.region.pinecone.io",
        "GROQ_API_KEY": "dummy-groq-" + SENSITIVE,
        "CLOUD_WRITE_KEY": "a" * 64,
    }


def metadata():
    return {"name": "documind-cloud", "dimension": 384, "metric": "cosine", "host": "dummy.svc.region.pinecone.io",
            "spec": {"serverless": {"cloud": "aws", "region": "us-east-1"}}, "status": {"ready": True}}


def diagnostic(caplog, stage, reason, field=None, status=None):
    records = [record for record in caplog.records if record.name == LOGGER]
    assert len(records) == 1
    record = records[0]
    message = record.getMessage()
    assert f"stage={stage}" in message and f"reason={reason}" in message
    if field:
        assert "field=" + field in message
    if status:
        assert "http_status=" + str(status) in message
    assert record.levelno == logging.WARNING
    assert record.exc_info is None and record.stack_info is None
    assert "https://" not in message and "Authorization" not in message and "Bearer " not in message
    for secret in [SENSITIVE, *[item for item in values().values() if isinstance(item, str)]]:
        assert secret not in message
    return message


@pytest.mark.parametrize("field", list(values()))
def test_each_missing_name_is_logged_without_values(caplog, field):
    supplied = values()
    del supplied[field]
    with pytest.raises(CloudError) as caught:
        Config.load(supplied)
    assert str(caught.value) == CloudError.MESSAGES["configuration"]
    diagnostic(caplog, "secrets", "missing_variable", field)


@pytest.mark.parametrize("flag", [False, "true", 1, None])
def test_free_plan_flag_requires_actual_boolean_true(caplog, flag):
    with pytest.raises(CloudError):
        Config.load({**values(), "FREE_PLANS_CONFIRMED": flag})
    diagnostic(caplog, "secrets", "boolean_true_required", "FREE_PLANS_CONFIRMED")


@pytest.mark.parametrize("field", [key for key in values() if key != "FREE_PLANS_CONFIRMED"])
@pytest.mark.parametrize("invalid", ["", None, 42])
def test_required_strings_name_the_field_only(caplog, field, invalid):
    with pytest.raises(CloudError):
        Config.load({**values(), field: invalid})
    diagnostic(caplog, "secrets", "nonempty_string_required", field)


@pytest.mark.parametrize("field,bad,reason", [
    ("CLOUDINARY_CLOUD_NAME", SENSITIVE + "/", "cloud_name_format"),
    ("PINECONE_INDEX", SENSITIVE + "/", "index_name_format"),
    ("PINECONE_HOST", "https://example.test/?key=" + SENSITIVE, "index_host_format"),
    ("SUPABASE_PUBLISHABLE_KEY", SENSITIVE, "publishable_key_required"),
])
def test_config_format_failures_redact_the_entire_value(caplog, field, bad, reason):
    with pytest.raises(CloudError):
        Config.load({**values(), field: bad})
    diagnostic(caplog, "secrets", reason, field)
    assert bad not in caplog.text


def test_secret_reader_exception_text_is_not_logged(caplog):
    class Broken(dict):
        def __contains__(self, _key):
            raise ValueError(SENSITIVE)
    with pytest.raises(CloudError):
        Config.load(Broken())
    diagnostic(caplog, "secrets", "secrets_parse_or_shape_error")


@pytest.mark.parametrize("field,bad,reason", [
    ("SUPABASE_URL", "https://dummy.supabase.co/", "project_url_format"),
    ("SUPABASE_URL", "https://example.test/?key=" + SENSITIVE, "project_url_format"),
    ("SUPABASE_PUBLISHABLE_KEY", "", "nonempty_string_required"),
    ("CLOUD_WRITE_KEY", "A" * 64, "write_key_format"),
    ("CLOUD_WRITE_KEY", "a" * 63, "write_key_format"),
    ("CLOUD_WRITE_KEY", SENSITIVE, "write_key_format"),
])
def test_supabase_url_and_write_key_checks(caplog, field, bad, reason):
    supplied = {**values(), field: bad}
    with pytest.raises(CloudError) as caught:
        Supabase(supplied["SUPABASE_URL"], supplied["SUPABASE_PUBLISHABLE_KEY"], supplied["CLOUD_WRITE_KEY"])
    assert caught.value.code == "configuration"
    diagnostic(caplog, "supabase", reason, field)


@pytest.mark.parametrize("bad", ["https://dummy.streamlit.app", "https://example.test/?token=" + SENSITIVE])
def test_oauth_callback_url_check(caplog, bad):
    with pytest.raises(CloudError):
        AuthBroker(object(), bad)
    diagnostic(caplog, "oauth", "app_url_format", "APP_URL")


@pytest.mark.parametrize("change,reason", [
    ({"name": SENSITIVE}, "index_name_mismatch"),
    ({"dimension": 1536}, "dimension_mismatch"),
    ({"dimension": "384"}, "dimension_mismatch"),
    ({"metric": "dotproduct"}, "metric_mismatch"),
    ({"host": "secret.example.test/?token=" + SENSITIVE}, "host_mismatch"),
    ({"spec": None}, "serverless_shape_invalid"),
    ({"spec": {"serverless": []}}, "serverless_shape_invalid"),
    ({"spec": {"serverless": {"cloud": SENSITIVE, "region": "us-east-1"}}}, "serverless_cloud_mismatch"),
    ({"spec": {"serverless": {"cloud": "aws", "region": SENSITIVE}}}, "serverless_region_mismatch"),
    ({"status": {"ready": False}}, "index_not_ready"),
    ({"status": {"ready": "true"}}, "index_not_ready"),
    ({"status": {"ready": 1}}, "index_not_ready"),
    ({"status": None}, "index_not_ready"),
])
@pytest.mark.parametrize("with_extra", [False, True])
def test_pinecone_metadata_checks_are_precise_and_do_not_log_metadata(caplog, change, reason, with_extra):
    response = copy.deepcopy({**metadata(), **change})
    spec = response.get("spec")
    if with_extra and isinstance(spec, dict) and isinstance(spec.get("serverless"), dict):
        spec["serverless"]["extra"] = SENSITIVE
    store = RemoteStore(Config.load(values()), transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json=response)))
    with pytest.raises(CloudError) as caught:
        store.validate_index()
    assert caught.value.code == "configuration"
    field = {"host_mismatch": "PINECONE_HOST", "index_name_mismatch": "PINECONE_INDEX"}.get(reason)
    diagnostic(caplog, "pinecone_index", reason, field)


@pytest.mark.parametrize("extra", [{}, {"extra": SENSITIVE},
                                 {"read_capacity": {"mode": "OnDemand"}, "future_metadata": {"value": SENSITIVE}}])
def test_valid_pinecone_index_accepts_additional_metadata_without_logging_it(caplog, extra):
    response = metadata()
    response["spec"]["serverless"].update(extra)
    def handler(request):
        assert request.url.path == "/indexes/documind-cloud"
        return httpx.Response(200, json=response)
    store = RemoteStore(Config.load(values()), transport=httpx.MockTransport(handler))
    store.validate_index()
    assert not [record for record in caplog.records if record.name == LOGGER]
    assert SENSITIVE not in caplog.text


@pytest.mark.parametrize("path,reason", [
    (("name",), "index_name_mismatch"),
    (("dimension",), "dimension_mismatch"),
    (("metric",), "metric_mismatch"),
    (("host",), "host_mismatch"),
    (("spec",), "serverless_shape_invalid"),
    (("spec", "serverless"), "serverless_shape_invalid"),
    (("spec", "serverless", "cloud"), "serverless_cloud_mismatch"),
    (("spec", "serverless", "region"), "serverless_region_mismatch"),
    (("status",), "index_not_ready"),
    (("status", "ready"), "index_not_ready"),
])
@pytest.mark.parametrize("missing", [False, True])
def test_required_index_values_cannot_be_missing_or_null(caplog, path, reason, missing):
    response = metadata()
    response["spec"]["serverless"]["extra"] = SENSITIVE
    parent = response
    for key in path[:-1]:
        parent = parent[key]
    if missing:
        del parent[path[-1]]
    else:
        parent[path[-1]] = None
    store = RemoteStore(Config.load(values()), transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json=response)))
    with pytest.raises(CloudError) as caught:
        store.validate_index()
    assert caught.value.code == "configuration"
    diagnostic(caplog, "pinecone_index", reason)


def test_wrong_configured_index_is_rejected_before_network_access(caplog):
    def handler(_request):
        pytest.fail("An unexpected index must never be requested")
    store = RemoteStore(Config.load({**values(), "PINECONE_INDEX": "other-index"}),
                        transport=httpx.MockTransport(handler))
    with pytest.raises(CloudError) as caught:
        store.validate_index()
    assert caught.value.code == "configuration"
    diagnostic(caplog, "pinecone_index", "index_name_mismatch", "PINECONE_INDEX")


def test_invalid_pinecone_response_shape(caplog):
    store = RemoteStore(Config.load(values()), transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json=[SENSITIVE])))
    with pytest.raises(CloudError):
        store.validate_index()
    diagnostic(caplog, "pinecone_index", "describe_shape_invalid")


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_pinecone_http_errors_log_only_numeric_status(caplog, status):
    def handler(request):
        assert request.headers["Api-Key"] == values()["PINECONE_API_KEY"]
        return httpx.Response(status, json={"message": SENSITIVE,
                                           "Authorization": "Bearer " + SENSITIVE,
                                           "url": "https://example.test/?key=" + SENSITIVE})
    store = RemoteStore(Config.load(values()), transport=httpx.MockTransport(handler))
    with pytest.raises(CloudError) as caught:
        store.validate_index()
    assert caught.value.code == ("limit" if status == 429 else "unavailable")
    diagnostic(caplog, "pinecone_index", "describe_rate_limited" if status == 429 else "describe_http_error", status=status)


@pytest.mark.parametrize("exception,reason", [
    (httpx.ReadTimeout, "describe_timeout"),
    (httpx.ConnectError, "describe_network_error"),
    (RuntimeError, "describe_failed"),
])
def test_provider_exception_text_and_tracebacks_never_reach_logs(caplog, exception, reason):
    def handler(_request):
        raise exception(SENSITIVE + " https://secret.example/?token=" + SENSITIVE)
    store = RemoteStore(Config.load(values()), transport=httpx.MockTransport(handler))
    with pytest.raises(CloudError):
        store.validate_index()
    diagnostic(caplog, "pinecone_index", reason)


def test_invalid_json_never_logs_response_body(caplog):
    store = RemoteStore(Config.load(values()), transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, text=SENSITIVE)))
    with pytest.raises(CloudError):
        store.validate_index()
    diagnostic(caplog, "pinecone_index", "describe_invalid_json")


def test_emitter_rejects_arbitrary_labels_and_non_numeric_status(caplog):
    startup_diagnostic(SENSITIVE, SENSITIVE, field=SENSITIVE, http_status=SENSITIVE)
    diagnostic(caplog, "startup", "unexpected_exception")


def test_auth_keeps_diagnostic_export_compatible():
    from authenticated_cloud.auth import startup_diagnostic as exported
    assert exported is startup_diagnostic


@pytest.mark.parametrize("cached_auth", [False, True])
def test_standalone_startup_imports_with_or_without_older_cached_auth(cached_auth):
    # A fresh child process prevents this test from depending on pytest's import order.
    code = """
import authenticated_cloud.auth as auth
if CACHED_AUTH:
    del auth.startup_diagnostic
    del auth.configuration_error
from authenticated_cloud import providers, streamlit_app
from authenticated_cloud.diagnostics import startup_diagnostic
assert streamlit_app.startup_diagnostic is startup_diagnostic
try:
    providers.Config.load({})
except auth.CloudError as error:
    assert error.code == 'configuration'
else:
    raise AssertionError('Missing configuration must fail closed')
""".replace("CACHED_AUTH", repr(cached_auth))
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, "Standalone startup import failed"
    assert "documind_startup stage=secrets reason=missing_variable" in result.stderr
    assert SENSITIVE not in result.stdout + result.stderr


def test_valid_startup_does_not_log_any_configuration(caplog, monkeypatch):
    from authenticated_cloud import streamlit_app as ui
    assert Path(ui.__file__).resolve().is_relative_to(ROOT)
    store = RemoteStore(Config.load(values()), transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json=metadata())))
    monkeypatch.setattr(ui, "st", SimpleNamespace(secrets=values()))
    monkeypatch.setattr(ui, "RemoteStore", lambda _config: store)
    service = ui.get_service.__wrapped__()
    assert service.auth.app_url == values()["APP_URL"]
    assert not [record for record in caplog.records if record.name == LOGGER]


@pytest.mark.parametrize("failed_stage", ["secrets", "supabase", "pinecone_index", "oauth", "service"])
def test_unexpected_startup_errors_identify_stage_without_exception_details(caplog, monkeypatch, failed_stage):
    from authenticated_cloud import streamlit_app as ui
    def fail():
        raise RuntimeError(SENSITIVE)
    config = Config.load(values())
    monkeypatch.setattr(ui, "st", SimpleNamespace(secrets=values()))
    monkeypatch.setattr(ui.Config, "load", lambda _: fail() if failed_stage == "secrets" else config)
    monkeypatch.setattr(ui, "Supabase", lambda *_: fail() if failed_stage == "supabase" else object())
    monkeypatch.setattr(ui, "RemoteStore", lambda _: SimpleNamespace(
        validate_index=lambda: fail() if failed_stage == "pinecone_index" else None))
    monkeypatch.setattr(ui, "AuthBroker", lambda *_: fail() if failed_stage == "oauth" else object())
    monkeypatch.setattr(ui, "CloudService", lambda *_: fail() if failed_stage == "service" else object())
    with pytest.raises(RuntimeError):
        ui.get_service.__wrapped__()
    diagnostic(caplog, failed_stage, "unexpected_exception")


@pytest.mark.parametrize("exception,expected", [
    (CloudError("configuration"), CloudError.MESSAGES["configuration"]),
    (RuntimeError(SENSITIVE), "The cloud demo is temporarily unavailable. Please contact its owner."),
])
def test_public_ui_remains_generic(exception, expected):
    tree = ast.parse((ROOT / "authenticated_cloud/streamlit_app.py").read_text(encoding="utf-8"))
    guard = copy.deepcopy(tree.body[-1])
    module = ast.fix_missing_locations(ast.Module(body=[guard], type_ignores=[]))
    messages = []
    def main():
        raise exception
    exec(compile(module, "public-error-handler", "exec"), {
        "__name__": "__main__", "main": main, "CloudError": CloudError,
        "st": SimpleNamespace(warning=messages.append, error=messages.append)})
    assert messages == [expected] and SENSITIVE not in messages[0]
