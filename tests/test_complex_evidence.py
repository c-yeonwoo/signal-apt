from xml.etree import ElementTree as ET
from datetime import date

from realty_signal.ingest import complex as cx


def item(**kw):
    data = dict(aptNm="테스트", umdNm="가동", jibun="1", excluUseAr="84.9",
                dealAmount="50000", deposit="30000", dealYear="2026", dealMonth="8") | kw
    node = ET.Element("item")
    for k, v in data.items():
        ET.SubElement(node, k).text = v
    return node


def test_same_name_different_addresses_never_merge(monkeypatch):
    monkeypatch.setattr(cx, "_items_parallel", lambda *_: [item(), item(jibun="2")])
    result = cx.fetch_complex("11110", "테스트", "synthetic")
    assert result["status"] == "ambiguous"
    assert not result["평형별"]
    assert "거래없음" not in result


def test_same_rounded_pyeong_different_area_never_forms_gap(monkeypatch):
    monkeypatch.setattr(cx, "_items_parallel", lambda base, *_:
                        [item(excluUseAr="83.0" if base == cx._TRADE else "84.0")])
    result = cx.fetch_complex("11110", "테스트", "synthetic")
    assert result["평형별"][0]["갭"] is None
    assert not result["평형별"][0]["비교가능"]


def test_distant_months_never_form_gap(monkeypatch):
    monkeypatch.setattr(cx, "_items_parallel", lambda base, *_:
                        [item(dealMonth="8" if base == cx._TRADE else "1")])
    result = cx.fetch_complex("11110", "테스트", "synthetic")
    assert result["평형별"][0]["갭"] is None


def test_same_area_nearby_months_have_explicit_comparison_basis(monkeypatch):
    monkeypatch.setattr(cx, "_items_parallel", lambda *_: [item()])
    row = cx.fetch_complex("11110", "테스트", "synthetic")["평형별"][0]
    assert row["갭"] == 20000 and row["비교가능"]
    assert "미통제" in row["비교기준"]


def test_recent_same_area_distribution_excludes_direct_and_marks_floor_uncontrolled():
    rows = [
        {"ym": "2026-08", "amt": 50000, "floor": 3, "dealing": "중개거래"},
        {"ym": "2026-08", "amt": 60000, "floor": 18, "dealing": "중개거래"},
        {"ym": "2026-07", "amt": 55000, "floor": None, "dealing": ""},
        {"ym": "2026-08", "amt": 10000, "floor": 1, "dealing": "직거래"},
        {"ym": "2026-02", "amt": 90000, "floor": 5, "dealing": "중개거래"},
    ]
    c = cx.comparison_evidence(rows, asof=date(2026, 9, 27))
    assert c["상태"] == "관측" and c["건수"] == 3
    assert (c["중앙값"], c["최저"], c["최고"]) == (55000, 50000, 60000)
    assert c["층범위"] == [3, 18] and c["층미상건수"] == 1 and c["층미통제"]
    assert c["직거래제외"] == 1 and c["거래유형미상건수"] == 1


def test_comparison_with_old_or_direct_only_trades_has_no_median():
    asof = date(2026, 9, 27)
    old = cx.comparison_evidence([{"ym": "2026-02", "amt": 90000}], asof=asof)
    assert old["상태"] == "최근거래없음" and old["중앙값"] is None
    direct = cx.comparison_evidence([{"ym": "2026-09", "amt": 50000, "dealing": "직거래"}], asof=asof)
    assert direct["상태"] == "직거래만" and direct["중앙값"] is None


def test_unidentified_complex_does_not_publish_comparable_price(monkeypatch):
    monkeypatch.setattr(cx, "_items_parallel", lambda *_: [item(umdNm="", jibun="", aptSeq="")])
    d = cx.fetch_complex("11110", "테스트", "synthetic")
    assert d["identity_status"] == "unverified"
    assert d["평형별"][0]["비교거래"]["상태"] == "단지미확인"
    assert d["평형별"][0]["비교거래"]["중앙값"] is None


def test_last_trade_uses_contract_day_not_upstream_order(monkeypatch):
    monkeypatch.setattr(cx, "_items_parallel", lambda base, *_: [
        item(dealDay="25", dealAmount="60000"),
        item(dealDay="03", dealAmount="30000"),
    ] if base == cx._TRADE else [])
    row = cx.fetch_complex("11110", "테스트", "synthetic")["평형별"][0]
    assert row["최근매매"] == 60000
