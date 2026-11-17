"""Pruebas locales del ciclo OAuth sin conexión ni credenciales reales."""

import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import app as playlist_app
from spotify_session import SpotifySessions


def test_oauth_state_login_refresh_and_logout(monkeypatch):
    token = {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "expires_at": 1}
    refreshed = {**token, "access_token": "synthetic-refreshed", "expires_at": 9999999999}
    oauth = Mock()
    oauth.get_authorize_url.return_value = "https://example.invalid/authorize"
    oauth.get_access_token.return_value = token
    oauth.is_token_expired.return_value = True
    oauth.refresh_access_token.return_value = refreshed
    monkeypatch.setattr(playlist_app, "spotify_config_error", lambda: None)
    monkeypatch.setattr(playlist_app, "spotify_oauth", lambda state=None: oauth)
    monkeypatch.setattr(playlist_app, "spotify_sessions", SpotifySessions())

    client = playlist_app.app.test_client()
    assert client.get("/login").status_code == 302
    assert client.get("/callback?code=synthetic&state=wrong").status_code == 302
    oauth.get_access_token.assert_not_called()
    client.get("/login")
    with client.session_transaction() as session:
        state = session["spotify_auth_state"]
    assert client.get("/callback", query_string={"code": "synthetic", "state": state}).status_code == 302
    oauth.get_access_token.assert_called_once_with("synthetic", as_dict=True)
    with client:
        with client.session_transaction() as session:
            sid = session["spotify_sid"]
            assert "token_info" not in session
        cookie = client.get_cookie("session").value
        payload = playlist_app.app.session_interface.get_signing_serializer(playlist_app.app).loads(cookie)
        assert payload == {"spotify_sid": sid}
        spotify_client = Mock()
        spotify_client.me.return_value = {"display_name": "Test"}
        spotify_factory = Mock(return_value=spotify_client)
        monkeypatch.setattr(playlist_app.spotipy, "Spotify", spotify_factory)
        assert client.get("/api/me").status_code == 200
        oauth.refresh_access_token.assert_called_once_with("synthetic-refresh")
        spotify_factory.assert_called_once_with(auth="synthetic-refreshed", retries=0, requests_timeout=12)
        with client.session_transaction() as session:
            assert session["spotify_sid"] == sid
        assert client.get("/logout").status_code == 302
        assert client.get("/api/me").status_code == 401
        client.set_cookie("session", cookie)
        assert client.get("/api/me").status_code == 401


def test_state_replay_and_expiry():
    now = [0]
    store = SpotifySessions(ttl=10, state_ttl=5, clock=lambda: now[0])
    state = store.new_state()
    assert store.consume_state(state)
    assert not store.consume_state(state)
    state = store.new_state()
    sid = store.create({"access_token": "test"})
    now[0] = 5
    assert not store.consume_state(state)
    assert store.contains(sid)
    now[0] = 10
    assert not store.contains(sid)


def test_concurrent_refresh_occurs_once_and_logout_invalidates():
    store = SpotifySessions()
    sid = store.create({"access_token": "old", "refresh_token": "refresh", "expired": True})
    oauth = Mock()
    oauth.is_token_expired.side_effect = lambda token: token["expired"]
    oauth.refresh_access_token.return_value = {"access_token": "new", "refresh_token": "refresh", "expired": False}
    gate = threading.Barrier(8)

    def resolve():
        gate.wait()
        return store.resolve(sid, oauth)["access_token"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda _: resolve(), range(8))) == ["new"] * 8
    oauth.refresh_access_token.assert_called_once()
    store.discard(sid)
    assert store.resolve(sid, oauth) is None


def test_restart_refresh_failure_and_capacity():
    store = SpotifySessions(capacity=1)
    old = store.create({"access_token": "old"})
    current = store.create({"access_token": "current", "refresh_token": "refresh"})
    assert not store.contains(old)
    assert not SpotifySessions().contains(current)
    oauth = Mock()
    oauth.is_token_expired.return_value = True
    oauth.refresh_access_token.side_effect = RuntimeError("synthetic failure")
    assert store.resolve(current, oauth) is None
    assert not store.contains(current)


def test_logout_waits_for_refresh_and_prevents_replay():
    store = SpotifySessions()
    sid = store.create({"access_token": "old", "refresh_token": "refresh"})
    entered, release = threading.Event(), threading.Event()
    oauth = Mock()
    oauth.is_token_expired.return_value = True

    def refresh(_):
        entered.set()
        assert release.wait(5)
        return {"access_token": "new", "refresh_token": "refresh"}

    oauth.refresh_access_token.side_effect = refresh
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = pool.submit(store.resolve, sid, oauth)
        assert entered.wait(5)
        logout = pool.submit(store.discard, sid)
        release.set()
        assert result.result(timeout=5)["access_token"] == "new"
        logout.result(timeout=5)
    assert store.resolve(sid, oauth) is None


def test_cookie_is_rejected_after_store_restart(monkeypatch):
    monkeypatch.setattr(playlist_app, "spotify_config_error", lambda: None)
    monkeypatch.setattr(playlist_app, "spotify_oauth", Mock())
    monkeypatch.setattr(playlist_app, "spotify_sessions", SpotifySessions())
    client = playlist_app.app.test_client()
    with client.session_transaction() as session:
        session["spotify_sid"] = playlist_app.spotify_sessions.create({"access_token": "test"})
    monkeypatch.setattr(playlist_app, "spotify_sessions", SpotifySessions())
    assert client.get("/api/me").status_code == 401


def test_cookie_rejected_by_new_process(monkeypatch, tmp_path):
    monkeypatch.setattr(playlist_app, "spotify_sessions", SpotifySessions())
    client = playlist_app.app.test_client()
    with client.session_transaction() as session:
        session["spotify_sid"] = playlist_app.spotify_sessions.create({"access_token": "synthetic"})
    cookie = client.get_cookie("session").value
    env = {key: value for key, value in os.environ.items() if not any(
        name in key.upper() for name in ("SPOTIFY", "SPOTIPY", "API_KEY")
    )}
    env["PLAYLISTAI_ENV_FILE"] = str(tmp_path / "missing.env")
    script = (
        "import sys, app; app.spotify_config_error=lambda: None; "
        "c=app.app.test_client(); c.set_cookie('session', sys.stdin.read()); "
        "print(c.get('/api/me').status_code)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], input=cookie, text=True, capture_output=True,
        cwd=Path(playlist_app.__file__).parent, env=env, check=True, timeout=15,
    )
    assert result.stdout.strip() == "401"


def test_callback_state_cannot_be_replayed(monkeypatch):
    oauth = Mock()
    oauth.get_authorize_url.return_value = "https://example.invalid/authorize"
    oauth.get_access_token.return_value = {"access_token": "synthetic"}
    monkeypatch.setattr(playlist_app, "spotify_config_error", lambda: None)
    monkeypatch.setattr(playlist_app, "spotify_oauth", lambda state=None: oauth)
    monkeypatch.setattr(playlist_app, "spotify_sessions", SpotifySessions())
    client = playlist_app.app.test_client()
    client.get("/login")
    cookie = client.get_cookie("session").value
    with client.session_transaction() as session:
        state = session["spotify_auth_state"]
    client.get("/callback", query_string={"code": "synthetic", "state": state})
    client.set_cookie("session", cookie)
    response = client.get("/callback", query_string={"code": "synthetic", "state": state})
    assert "auth_error" in response.location
    oauth.get_access_token.assert_called_once()
