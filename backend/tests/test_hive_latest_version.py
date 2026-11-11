"""The latest SwarmAI release is read from the release's own latest.json, never from the
REST API (60 unauthenticated calls an hour per public IP, used up on a shared network)."""

import httpx
import pytest

import hive.provisioner as prov
from hive.provisioner import HiveProvisioner

_REAL_CLIENT = httpx.AsyncClient


def _serve(monkeypatch, handler):
    seen = []

    def wrapped(request):
        seen.append(str(request.url))
        return handler(request)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(wrapped)
        return _REAL_CLIENT(*args, **kwargs)

    monkeypatch.setattr(prov.httpx, "AsyncClient", factory)
    return seen


def _provisioner():
    return HiveProvisioner(prov.Path("/tmp/unused.db"))


async def test_reads_the_version_from_latest_json_through_its_redirect(monkeypatch):
    def handler(request):
        if request.url.host == "api.github.com":
            return httpx.Response(403, json={"message": "API rate limit exceeded"})
        if request.url.path.endswith("/releases/latest/download/latest.json"):
            return httpx.Response(302, headers={"location": "https://assets.example/latest.json"})
        return httpx.Response(200, json={"version": "2.5.1", "platforms": {}})

    seen = _serve(monkeypatch, handler)
    assert await _provisioner()._resolve_version(None) == "2.5.1"
    assert await _provisioner()._resolve_version("latest") == "2.5.1"
    assert not any("api.github.com" in u for u in seen)


async def test_an_explicit_version_makes_no_request(monkeypatch):
    seen = _serve(monkeypatch, lambda r: httpx.Response(500))
    assert await _provisioner()._resolve_version("2.4.4") == "2.4.4"
    assert seen == []


@pytest.mark.parametrize("response", [
    httpx.Response(404, text="Not Found"),
    httpx.Response(200, text="<html>not json</html>"),
    httpx.Response(200, json={"platforms": {}}),
    httpx.Response(200, json={"version": "2.5.1; rm -rf /"}),
    httpx.Response(200, json=["2.5.1"]),
])
async def test_no_valid_version_raises(monkeypatch, response):
    _serve(monkeypatch, lambda r: response)
    with pytest.raises(RuntimeError, match="latest SwarmAI release"):
        await _provisioner()._resolve_version(None)
