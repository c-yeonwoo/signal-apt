from realty_signal import real_trade
import xml.etree.ElementTree as ET


def test_month_range_is_inclusive_and_validates_order():
    assert real_trade.month_range("202311", "202402") == ["202311", "202312", "202401", "202402"]
    try:
        real_trade.month_range("202402", "202401")
    except ValueError:
        pass
    else:
        raise AssertionError("역순 기간은 거절해야 한다")


def test_target_lawds_excludes_sido_and_deduplicates():
    codes = {"서울": "11000", "강남구": "11680", "서초구": "11650", "강남구 별칭": "11680"}
    assert real_trade.target_lawds(codes) == ["11650", "11680"]


def test_collect_resumes_cache_and_does_not_cache_failures(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(lawd, ym, key):
        calls.append((lawd, ym))
        return None if ym == "202402" else {"lawd": lawd, "ym": ym, "transactions": 1, "median_ppy": 100.0}

    monkeypatch.setattr(real_trade, "fetch_month", fake_fetch)
    stats = real_trade.collect(["11680"], ["202401", "202402"], "key", tmp_path, workers=1)

    assert stats == {"cached": 0, "fetched": 1, "failed": 1, "retried": 2, "requested": 2, "total": 2}
    assert (tmp_path / "11680" / "202401.json").exists()
    assert not (tmp_path / "11680" / "202402.json").exists()
    assert calls == [("11680", "202401"), ("11680", "202402"), ("11680", "202402"), ("11680", "202402")]


def test_collect_retries_transient_month_without_marking_it_failed(tmp_path, monkeypatch):
    calls = []

    def flaky_fetch(lawd, ym, key):
        calls.append((lawd, ym))
        return None if len(calls) == 1 else {"lawd": lawd, "ym": ym, "transactions": 1, "median_ppy": 100.0}

    monkeypatch.setattr(real_trade, "fetch_month", flaky_fetch)
    monkeypatch.setattr(real_trade.time, "sleep", lambda seconds: None)

    stats = real_trade.collect(["11680"], ["202401"], "key", tmp_path, workers=1, retries=2)

    assert stats == {"cached": 0, "fetched": 1, "failed": 0, "retried": 1, "requested": 1, "total": 1}
    assert len(calls) == 2


def test_public_data_success_codes_include_current_api_format(monkeypatch):
    root = ET.fromstring("<response><header><resultCode>000</resultCode></header><body><totalCount>0</totalCount></body></response>")

    class Response:
        def read(self):
            return ET.tostring(root)

    monkeypatch.setattr(real_trade.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    assert real_trade.fetch_month("11680", "202508", "key") == {
        "lawd": "11680", "ym": "202508", "transactions": 0, "median_ppy": None, "api_total": 0,
    }
