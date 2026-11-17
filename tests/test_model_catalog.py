from unittest.mock import Mock

import pytest
import requests

import app


@pytest.mark.parametrize("provider,key_name,base", [
    ("openai", "OPENAI_KEY", "https://api.openai.com/v1"),
    ("nvidia", "NVIDIA_KEY", "https://integrate.api.nvidia.com/v1"),
])
def test_catalog_refresh_and_retirement(monkeypatch, provider, key_name, base):
    monkeypatch.setattr(app, key_name, "synthetic")
    monkeypatch.setattr(app, "_model_cache", {})
    get = Mock(side_effect=[
        Mock(status_code=200, json=lambda: {"data": [{"id": "first"}]}),
        Mock(status_code=200, json=lambda: {"data": [{"id": "second"}]}),
        Mock(status_code=200, json=lambda: {"data": []}),
    ])
    monkeypatch.setattr(app.requests, "get", get)
    client = app.app.test_client()
    url = "/api/models/" + provider
    assert client.get(url).json["models"][0]["id"] == "first"
    assert app.ai_config_error(provider, "first") is None
    get.assert_called_once_with(
        base + "/models", headers={"Authorization": "Bearer synthetic"},
        timeout=5, allow_redirects=False,
    )
    assert client.get(url + "?refresh=1").json["models"][0]["id"] == "second"
    assert app.ai_config_error(provider, "first")
    assert app.ai_config_error(provider, "second") is None
    assert client.get(url + "?refresh=1").json["error"]


@pytest.mark.parametrize("status", [401, 403, 429, 503, None])
def test_catalog_errors(monkeypatch, status):
    monkeypatch.setattr(app, "OPENAI_KEY", "synthetic")
    monkeypatch.setattr(app, "_model_cache", {})
    get = Mock(return_value=Mock(status_code=status))
    if status is None:
        get.side_effect = requests.Timeout()
    monkeypatch.setattr(app.requests, "get", get)
    models, error = app.model_catalog("openai")
    assert models == []
    assert error
    assert app.model_catalog("openai") == (models, error)
    get.assert_called_once()


@pytest.mark.parametrize("provider", ["openai", "nvidia"])
def test_generation_rejection_is_explicit(monkeypatch, provider):
    post = Mock(return_value=Mock(status_code=400))
    monkeypatch.setattr(app.requests, "post", post)
    assert "Elige otro modelo" in app.call_ai("prompt", provider, "catalog-entry")
    post.assert_called_once()
