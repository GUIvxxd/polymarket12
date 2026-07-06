from __future__ import annotations

from polybot import gamma


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


def test_gamma_client_fetches_markets_with_public_get(monkeypatch) -> None:
    calls = []

    class FakeHttpClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def get(self, path, *, params):
            calls.append(("get", path, params))
            return FakeResponse([{"id": "1"}])

    monkeypatch.setattr(gamma.httpx, "Client", FakeHttpClient)

    markets = gamma.GammaClient().fetch_markets(limit=5)

    assert markets == [{"id": "1"}]
    assert calls[0][0] == "init"
    assert "Authorization" not in calls[0][1]["headers"]
    assert calls[1] == ("get", "/markets", {"limit": 5, "offset": 0})


def test_gamma_client_public_search_normalizes_dict_response(monkeypatch) -> None:
    class FakeHttpClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def get(self, path, *, params):
            assert path == "/public-search"
            assert params == {"q": "Bitcoin Up or Down", "limit": 3}
            return FakeResponse({"events": [{"id": "event-1"}]})

    monkeypatch.setattr(gamma.httpx, "Client", FakeHttpClient)

    payload = gamma.GammaClient().public_search("Bitcoin Up or Down", limit=3)

    assert payload == {"events": [{"id": "event-1"}]}


def test_gamma_client_fetches_market_by_slug(monkeypatch) -> None:
    calls = []

    class FakeHttpClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def get(self, path, *, params):
            calls.append(("get", path, params))
            return FakeResponse({"slug": "btc-updown-5m-1"})

    monkeypatch.setattr(gamma.httpx, "Client", FakeHttpClient)

    market = gamma.GammaClient().fetch_market_by_slug("btc-updown-5m-1")

    assert market == {"slug": "btc-updown-5m-1"}
    assert calls[1] == ("get", "/markets/slug/btc-updown-5m-1", {})
