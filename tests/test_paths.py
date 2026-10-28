from pathlib import Path

from server.utils import paths
from server.utils.paths import data_dir


def test_data_dir_follows_env(tmp_path, monkeypatch):
    target = tmp_path / "custom"
    monkeypatch.setenv("OPENPOKE_DATA_DIR", str(target))
    assert data_dir() == target
    assert target.is_dir()


def test_default_data_dir_is_server_data():
    assert paths._DEFAULT_DATA_DIR == Path(__file__).resolve().parent.parent / "server" / "data"
