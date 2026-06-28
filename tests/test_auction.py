from realty_signal.auction import Listing, bid_calc, enrich, import_csv, save


def test_bid_calc_basic():
    lst = Listing(단지명="테스트", region="서울", 감정가=100000, 유찰횟수=1, 시세=100000, 예상비용=2000)
    c = bid_calc(lst, target_return=0.12, win_rate=0.85)
    # 최저매각가 = 10만 * 0.8^1 = 80000
    assert c["최저매각가"] == 80000
    assert c["예상낙찰가"] == 85000          # 시세*0.85
    assert c["나의입찰상한"] == 100000 * 0.88 - 2000  # 86000
    assert c["수익가능"] is True
    assert c["권장입찰가"] == 85000          # min(상한86000, max(80000,85000))


def test_bid_calc_min_bid_override_and_unprofitable():
    lst = Listing(region="대구", 감정가=50000, 최저매각가=49000, 시세=50000, 예상비용=8000)
    c = bid_calc(lst, target_return=0.12, win_rate=0.85)
    assert c["최저매각가"] == 49000          # 직접 입력 우선
    # 상한 = 50000*0.88 - 8000 = 36000 < 최저가 49000 → 수익불가
    assert c["수익가능"] is False


def test_enrich_priority_orders_by_signal_and_margin():
    a = Listing(단지명="A", region="서울", 감정가=100000, 시세=100000, 유찰횟수=1)
    b = Listing(단지명="B", region="대구", 감정가=100000, 시세=100000, 유찰횟수=1)
    rows = enrich([b, a], {"서울": "STRONG_BUY", "대구": "BUY"})
    assert rows[0]["단지명"] == "A"          # STRONG_BUY 가 우선
    assert rows[0]["우선순위점수"] > rows[1]["우선순위점수"]


def test_import_csv(tmp_path, monkeypatch):
    import realty_signal.auction as au
    monkeypatch.setattr(au, "AUCTION_FILE", tmp_path / "a.json")
    csv = "단지명,region,감정가,유찰횟수,입찰기일\n래미안,서울,120000,1,2026-07-10\n자이,경기,90000,0,2026-07-15\n"
    assert au.import_csv(csv) == 2
    assert len(au.load()) == 2
