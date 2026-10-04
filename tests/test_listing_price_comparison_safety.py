"""Different apartment sizes must never inherit a supplier-wide discount."""

import json
from unittest.mock import patch

import pytest

from realty_signal import api
from realty_signal.ingest import baroezip
from realty_signal.services import quote_check
from realty_signal.time_kst import today_kst


def test_59_and_84_source_rows_preserve_asking_but_not_unverified_discount():
    payload = {"data": [{"apt_list": {"complex_no": "123"}, "market_data": [
        {"exclusive_use_area": 59, "deal_amount": 50000, "median_deal_amount": 80000,
         "is_urgent": True, "original": "59", "trade_type": "trade"},
        {"exclusive_use_area": 84, "deal_amount": 80000, "median_deal_amount": 80000,
         "is_urgent": False, "original": "84", "trade_type": "trade"},
    ]}]}
    with patch.object(baroezip.urllib.request, "urlopen") as response:
        response.return_value.read.return_value = json.dumps(payload).encode()
        rows, error = baroezip.fetch_market_with_status(1, 2, 3, 4)
    assert error is None
    assert [(r["전용면적"], r["호가"]) for r in rows] == [(59, 50000), (84, 80000)]
    assert rows[0]["급매"] is True  # supplier assertion, not our price judgment
    assert all(r["급매갭"] is None and r["중위시세"] is None for r in rows)


@pytest.mark.parametrize("kind", ["급매", "찐매물"])
@pytest.mark.parametrize("failed", [False, True])
def test_existing_cache_is_sanitized_without_changing_files_or_listing_keys(tmp_path, monkeypatch, kind, failed):
    path = tmp_path / "radar.json"
    field = "QUICKSALE_FILE" if kind == "급매" else "CERTIFIED_FILE"
    version = api._QUICKSALE_SCAN_VER if kind == "급매" else api._CERTIFIED_SCAN_VER
    row = {"단지명": "비교단지", "naver_id": "same-id", "전용면적": 59,
           "호가": 50000, "급매갭": -37.5, "중위시세": 80000, "급매": True}
    path.write_text(json.dumps({"ready": True, "_scan_ver": version, "listings": [row]}))
    before = path.read_bytes()
    monkeypatch.setattr(api, field, path)
    monkeypatch.setattr(api, "_radar_refresh_status", lambda _p: {"ok": not failed})
    response = api._radar_cached_response(path, version)
    assert response["state"] == ("stale_failed" if failed else "ready")
    for actual in (response["listings"][0], api._radar_verified_rows(path, version)[0]):
        assert actual["급매갭"] is None and actual["중위시세"] is None
        assert actual["호가"] == 50000 and actual["전용면적"] == 59
        assert actual["가격비교상태"] == "비교조건 미확인"
        assert api._listing_key(kind, actual, {}, None, None) == f"{kind}:same-id"
    assert path.read_bytes() == before


def test_report_uses_59_only_and_never_falls_back_to_84():
    def area(size, price):
        return {"전용㎡": size, "비교거래": {"상태": "관측", "건수": 3,
                "기준일": today_kst().isoformat(), "중앙값": price,
                "최저": price, "최고": price, "거래월범위": "최근 6개월"}}
    detail = {"identity_status": "single_observed", "평형별": [area(59, 50000), area(84, 80000)]}
    result = quote_check.assess(detail, asking=50000, exclusive_m2=59)
    assert result["상태"] == "관측비교" and result["호가차이율"] == 0
    detail["평형별"] = [area(84, 80000)]
    result = quote_check.assess(detail, asking=50000, exclusive_m2=59)
    assert result["상태"] == "보류" and result["호가차이율"] is None


def test_legacy_gap_order_is_removed_without_mutating_input():
    rows = [{"단지명": "B", "급매갭": -37.5}, {"단지명": "A", "급매갭": -1}]
    safe = baroezip.safe_market_rows(rows)
    assert [row["단지명"] for row in safe] == ["A", "B"]
    assert rows[0]["급매갭"] == -37.5
    assert baroezip.safe_market_rows({"unexpected": "object"}) == []
