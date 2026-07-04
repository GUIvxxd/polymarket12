from __future__ import annotations

from polybot.discovery import (
    UNKNOWN_ASSET,
    detect_asset,
    discover_crypto_up_down_markets,
    is_crypto_up_down_payload,
    normalize_market,
    parse_json_array,
)
from polybot.main import build_parser


class FakeDiscoveryClient:
    def __init__(self, payloads):
        self.payloads = payloads

    def public_search(self, query: str, *, limit: int | None = None):
        return {"events": self.payloads.get(query, [])}

    def fetch_markets(self, *, limit: int = 100, offset: int = 0, extra_params=None):
        return []


def test_parse_json_array_accepts_double_encoded_gamma_fields() -> None:
    assert parse_json_array('["Up", "Down"]') == ["Up", "Down"]
    assert parse_json_array('["0.42", "0.58"]') == ["0.42", "0.58"]
    assert parse_json_array('["123", "456"]') == ["123", "456"]


def test_normalize_market_parses_encoded_outcomes_prices_and_token_ids() -> None:
    market = normalize_market(
        {
            "id": "123",
            "conditionId": "0xabc",
            "slug": "btc-updown-5m-1775181000",
            "question": "Bitcoin Up or Down - April 2, 9:50PM-9:55PM ET",
            "outcomes": '["Up", "Down"]',
            "outcomePrices": '["0.42", "0.58"]',
            "clobTokenIds": '["111", "222"]',
            "bestBid": "0.41",
            "bestAsk": "0.43",
            "startDate": "2026-04-03T01:50:00Z",
            "endDate": "2026-04-03T01:55:00Z",
            "active": True,
            "closed": False,
            "resolutionSource": "https://data.chain.link/streams/btc-usd",
        }
    )

    assert market.market_id == "123"
    assert market.condition_id == "0xabc"
    assert market.asset == "BTC"
    assert market.outcomes == ["Up", "Down"]
    assert market.outcome_prices == [0.42, 0.58]
    assert market.clob_token_ids == ["111", "222"]
    assert market.best_bid == 0.41
    assert market.best_ask == 0.43


def test_market_filter_accepts_only_crypto_up_down_payloads() -> None:
    assert is_crypto_up_down_payload(
        {
            "slug": "eth-updown-15m-1771868700",
            "question": "Ethereum Up or Down - February 23, 12:45PM-1:00PM ET",
        }
    )
    assert not is_crypto_up_down_payload(
        {
            "slug": "will-btc-hit-100000-this-week",
            "question": "Will BTC hit $100,000 this week?",
        }
    )


def test_asset_detection_from_slug_and_title() -> None:
    assert detect_asset("btc-updown-5m-123", "") == "BTC"
    assert detect_asset("", "Ethereum Up or Down - 15m") == "ETH"
    assert detect_asset("sol-updown-5m-123", "") == "SOL"
    assert detect_asset("", "XRP Up or Down - 15m") == "XRP"
    assert detect_asset("will-btc-hit-100000", "Will BTC hit $100,000?") == UNKNOWN_ASSET


def test_discovery_deduplicates_and_respects_include_closed_flag() -> None:
    closed_market = {
        "id": "1",
        "conditionId": "0xclosed",
        "slug": "btc-updown-5m-1",
        "question": "Bitcoin Up or Down - Closed",
        "outcomes": '["Up", "Down"]',
        "outcomePrices": '["0", "1"]',
        "active": True,
        "closed": True,
    }
    active_market = {
        "id": "2",
        "conditionId": "0xactive",
        "slug": "eth-updown-5m-2",
        "question": "Ethereum Up or Down - Active",
        "outcomes": '["Up", "Down"]',
        "outcomePrices": '["0.51", "0.49"]',
        "active": True,
        "closed": False,
    }
    client = FakeDiscoveryClient(
        {
            "Bitcoin Up or Down": [{"markets": [closed_market, closed_market]}],
            "Ethereum Up or Down": [{"markets": [active_market]}],
        }
    )

    active_only = discover_crypto_up_down_markets(client, include_closed=False)
    with_closed = discover_crypto_up_down_markets(client, include_closed=True)

    assert [market.condition_id for market in active_only] == ["0xactive"]
    assert [market.condition_id for market in with_closed] == ["0xclosed", "0xactive"]


def test_cli_parser_includes_discover_command() -> None:
    parser = build_parser()

    help_text = parser.format_help()
    args = parser.parse_args(["discover", "--limit", "20", "--include-closed"])

    assert "discover" in help_text
    assert args.command == "discover"
    assert args.limit == 20
    assert args.include_closed is True

