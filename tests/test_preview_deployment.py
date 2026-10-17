"""Preview packaging and Linux public-cache behavior; dummy data only."""
import ast
from pathlib import Path
import stat
import tomllib
from types import SimpleNamespace

import pytest

from session_cloud.disk_usage import disk_bytes, model_cache_bytes

ROOT = Path(__file__).resolve().parents[1]


def test_preview_config_preserves_security_and_explicit_upload_ceiling():
    config = tomllib.loads((ROOT / '.streamlit/config.toml').read_text())
    example = tomllib.loads((ROOT / 'session_cloud/preview.config.toml.example').read_text())
    assert config == example
    assert config['server']['maxUploadSize'] == 100
    assert config['server']['enableCORS'] is True
    assert config['server']['enableXsrfProtection'] is True
    assert config['server']['enableStaticServing'] is False
    assert config['client']['showErrorDetails'] is False
    assert config['theme']['base'] == 'dark'


def test_preview_entrypoint_dependency_and_auth_assets_exist():
    from session_cloud import streamlit_app
    assert callable(streamlit_app.main) and len(streamlit_app.TABS) == 7
    folder = ROOT / 'session_cloud'
    requirements = [s for s in (folder / 'requirements.txt').read_text().splitlines()
                    if s and not s.startswith('#')]
    assert len(requirements) == 13 and all('==' in s for s in requirements)
    assert not any(provider in '\n'.join(requirements).lower()
                   for provider in ('pinecone', 'cloudinary', 'celery', 'redis', 'torch'))
    for name in ('index.html', 'auth.js', 'style.css'):
        assert (ROOT / 'authenticated_cloud/component' / name).is_file()
    for source in folder.glob('*.py'):
        tree = ast.parse(source.read_text(encoding='utf-8'))
        imports = [node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        assert not any(name.startswith(('pinecone', 'cloudinary', 'authenticated_cloud.providers')) for name in imports)


def test_public_cache_regular_files_and_workspace_accounting_unchanged(tmp_path):
    (tmp_path / 'weight').write_bytes(b'x' * 2048)
    (tmp_path / 'config.json').write_bytes(b'{}')
    assert disk_bytes(tmp_path) == model_cache_bytes(tmp_path) == 2050


def fake_link(monkeypatch, alias, target):
    """Emulate POSIX HF links on Windows without requiring symlink privilege."""
    lstat, resolve = Path.lstat, Path.resolve
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_mode=stat.S_IFLNK, st_size=12)
                        if path == alias else lstat(path))
    monkeypatch.setattr(Path, 'resolve', lambda path, **kw: target
                        if path == alias else resolve(path, **kw))


def test_hf_snapshot_link_does_not_double_count_model(monkeypatch, tmp_path):
    blob, alias = tmp_path / 'blob', tmp_path / 'snapshot-link'
    blob.write_bytes(b'x' * 2048)
    alias.write_bytes(b'symlink entry')
    fake_link(monkeypatch, alias, blob)
    assert model_cache_bytes(tmp_path) == 2048 + 12


@pytest.mark.parametrize('target_kind', ['outside', 'directory'])
def test_cache_links_cannot_escape_or_hide_directories(monkeypatch, tmp_path, target_kind):
    cache = tmp_path / 'cache'
    cache.mkdir()
    outside = tmp_path / 'preserve.txt'
    outside.write_text('Unrelated private sentinel')
    alias = cache / 'link'
    alias.write_text('dummy link')
    fake_link(monkeypatch, alias, outside if target_kind == 'outside' else cache)
    with pytest.raises(OSError):
        model_cache_bytes(cache)
    assert outside.read_text() == 'Unrelated private sentinel'


@pytest.mark.parametrize('is_link', [True, False])
def test_public_cache_rejects_link_or_junction_root(monkeypatch, tmp_path, is_link):
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == tmp_path and is_link)
    monkeypatch.setattr(Path, 'is_junction', lambda path: path == tmp_path and not is_link, raising=False)
    with pytest.raises(OSError):
        model_cache_bytes(tmp_path)


def test_cache_scan_retries_rename_but_fails_closed(monkeypatch, tmp_path):
    rglob, calls = Path.rglob, []

    def interrupted(path, pattern):
        calls.append(1)
        if len(calls) < 3:
            raise FileNotFoundError()
        return rglob(path, pattern)

    monkeypatch.setattr(Path, 'rglob', interrupted)
    assert model_cache_bytes(tmp_path) == 0 and len(calls) == 3
    monkeypatch.setattr(Path, 'rglob', lambda *args: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(OSError):
        model_cache_bytes(tmp_path)
