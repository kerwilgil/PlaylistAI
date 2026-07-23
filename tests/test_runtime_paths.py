from pathlib import Path

from runtime_paths import ensure_env_file, env_file_path, log_file_path


def test_config_directory_override_creates_env_and_log_paths(tmp_path, monkeypatch):
    config_dir = tmp_path / "playlistai-config"
    monkeypatch.setenv("PLAYLISTAI_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("PLAYLISTAI_ENV_FILE", raising=False)

    assert env_file_path() == config_dir / ".env"
    assert ensure_env_file() is True
    assert env_file_path().read_text(encoding="utf-8") == Path(
        ".env.example"
    ).read_text(encoding="utf-8")
    assert ensure_env_file() is False
    assert log_file_path() == config_dir / "playlistai.log"


def test_env_file_override_has_priority(tmp_path, monkeypatch):
    explicit_env = tmp_path / "custom.env"
    monkeypatch.setenv("PLAYLISTAI_CONFIG_DIR", str(tmp_path / "ignored"))
    monkeypatch.setenv("PLAYLISTAI_ENV_FILE", str(explicit_env))

    assert env_file_path() == explicit_env
