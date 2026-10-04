"""사용자 입력 호가와 관측 거래 비교는 근거 부족 시 보류한다."""

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from realty_signal import api
from realty_signal.services import quote_check
from realty_signal.time_kst import today_kst


def _detail(*, status="관측", n=4, identity="single_observed", asof=None):
    return {"identity_status": identity, "평형별": [{"전용㎡": 84.9,
            "비교거래": {"상태": status, "건수": n, "중앙값": 50000,
                     "최저": 45000, "최고": 55000, "거래월범위": "2026-08~2026-09",
                     "층별표본": [{"층": 10, "가격": 47000, "거래월": "2026-08"},
                               {"층": 11, "가격": 48000, "거래월": "2026-09"},
                               {"층": 13, "가격": 49000, "거래월": "2026-09"},
                               {"층": 20, "가격": 55000, "거래월": "2026-09"}],
                     "기준일": asof or today_kst().isoformat()}}]}


def test_quote_compares_only_separate_user_asking_against_observed_median():
    out = quote_check.assess(_detail(), asking=48000, exclusive_m2=84.9)
    assert out["상태"] == "관측비교"
    assert out["호가차액"] == -2000 and out["호가차이율"] == -4.0
    assert out["price_kind"] == "user_entered_asking" and out["표본수"] == 4
    assert "할인율" in " ".join(out["안내"])


def test_quote_holds_for_sparse_stale_unidentified_or_other_area():
    scenarios = [
        (_detail(status="표본적음", n=2), 84.9),
        (_detail(identity="unverified"), 84.9),
        (_detail(asof=(today_kst() - timedelta(days=8)).isoformat()), 84.9),
        (_detail(), 59.8),
    ]
    for detail, area in scenarios:
        out = quote_check.assess(detail, asking=48000, exclusive_m2=area)
        assert out["상태"] == "보류" and out["호가차액"] is None


def test_floor_band_uses_only_nearby_observed_floors_and_never_falls_back():
    near = quote_check.assess(_detail(), asking=46000, exclusive_m2=84.9, floor=11)
    assert near["상태"] == "관측비교" and near["중앙값"] == 48000
    assert near["표본수"] == 3 and near["입력층"] == 11
    far = quote_check.assess(_detail(), asking=46000, exclusive_m2=84.9, floor=20)
    assert far["상태"] == "보류" and far["중앙값"] is None
    assert far["표본수"] == 1 and far["호가차액"] is None


def test_quote_check_route_validates_input_and_does_not_write(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "complex_detail", lambda *_: calls.append(1) or _detail())
    monkeypatch.setattr(api, "_uid", lambda *_: 1)
    monkeypatch.setattr(api.db, "kv_set", lambda *_: (_ for _ in ()).throw(AssertionError("quote was saved")))
    client = TestClient(api.app)
    url = "/api/complex/노원구/테스트/quote-check"
    ok = client.post(url, json={"asking": 48000, "exclusive_m2": 84.9})
    assert ok.status_code == 200 and ok.json()["호가차이율"] == -4.0
    bad = client.post(url, json={"asking": -1, "exclusive_m2": 84.9})
    assert bad.status_code == 422
    bad_floor = client.post(url, json={"asking": 48000, "exclusive_m2": 84.9, "floor": 10.5})
    assert bad_floor.status_code == 422
    assert calls == [1]  # 잘못된 입력은 원천 조회 전에 거른다


def test_quote_ui_requires_explicit_input_and_discloses_limitations():
    html = (Path(__file__).resolve().parents[1] / "src/realty_signal/web/index.html").read_text()
    assert 'id="cxQuoteAmount"' in html and 'id="cxQuoteBtn"' in html
    assert 'id="cxQuoteFloor"' in html
    assert "quote-check" in html and "method:'POST'" in html
    assert "차이가 곧 할인율이나 매수 권고는 아닙니다" in html
