from xml.etree import ElementTree as ET

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
