"""상한 안 호가는 확인된 매물가이고, 추정 단지와 경매 최저가는 빠진다."""

from realty_signal.services.asks import known_asks, within_ceiling


def _row(kind, name, region, price, **extra):
    row = {"유형": kind, "단지명": name, "지역": region, "총액": price, "price_kind": "asking"}
    row.update(extra)
    return row


def test_within_ceiling_keeps_known_asks_under_the_cap():
    rows = [
        _row("급매", "상한근처", "노원구", 60000),
        _row("찐매물", "초과", "강남구", 80000),
        _row("단지", "추정", "중랑구", 17000, price_kind="modeled"),
        _row("경매", "최저가", "노원구", 30000),
        _row("일반매물", "같은단지", "노원구", 59000),
        _row("일반매물", "상한근처", "노원구", 55000),
    ]
    names = [row["단지명"] for row in within_ceiling(rows, 61000)]
    assert names[0] == "상한근처"
    assert "초과" not in names and "추정" not in names and "최저가" not in names
    assert names.index("상한근처") < names.index("같은단지")


def test_known_asks_drops_cash_that_does_not_fit(monkeypatch):
    def fake_annotate(row, params, uid=None, sido_of=None):
        item = dict(row)
        item["자금"] = {"가능": row["총액"] <= 30000, "확인필요": False}
        item["lines"] = {"price": "원천 호가 · 유효성 확인"}
        return item

    monkeypatch.setattr("realty_signal.services.asks.buyer_decision.annotate", fake_annotate)
    rows = [
        _row("급매", "비쌈", "노원구", 50000),
        _row("급매", "맞음", "중랑구", 20000),
        _row("단지", "추정", "노원구", 15000, price_kind="modeled"),
    ]
    out = known_asks(rows, object(), 61000)
    assert [row["단지명"] for row in out] == ["맞음"]
    assert out[0]["가격출처"] == "매물가"
