from realty_signal import real_trade


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

    assert stats == {"cached": 0, "fetched": 1, "failed": 1, "requested": 2, "total": 2}
    assert (tmp_path / "11680" / "202401.json").exists()
    assert not (tmp_path / "11680" / "202402.json").exists()
    assert calls == [("11680", "202401"), ("11680", "202402")]
