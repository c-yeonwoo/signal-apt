from realty_signal.auction import Listing, _p, breakdown, enrich, recommend, table


def test_breakdown_matches_excel_model():
    # 시세 3.4억, 입찰가 2.8억, 전용 59㎡, 대출 70%/5%, 6개월 보유
    lst = Listing(단지명="t", region="서울", 감정가=340000000/10000, 시세=340000000/10000,
                  전용면적=59, 대출비율=0.7, 대출금리=0.05)
    p = _p({"보유개월": 6})
    b = breakdown(lst, 28000, p)  # 입찰가 2.8억(만원)
    # 등기비 = 28000*1.1% + 100 = 408
    assert b["등기비"] == round(28000 * 0.011 + 100)
    # 명도비 = 59*0.3025*15만/만 = 약 268만
    assert b["명도비"] == round(59 * 0.3025 * 150000 / 10000)
    # 대출금 = 28000*0.7
    assert b["대출금"] == round(28000 * 0.7)
    # 시세차익 = 매매총매입 - 경매총매입
    assert b["시세차익"] == b["매매총매입"] - b["경매총매입"]


def test_table_spans_from_floor_rate():
    lst = Listing(감정가=10000, 최저매각가=6400, 시세=12000, region="서울")
    t = table(lst, _p(), span=0.30, step=0.01)
    assert abs(t[0]["낙찰가율"] - 64.0) < 0.01          # 최저가율 = 6400/10000
    assert t[0]["입찰가"] < t[-1]["입찰가"]              # 낮은→높은 입찰가
    # 입찰가 오를수록 시세차익률 하락
    assert t[0]["시세차익률"] > t[-1]["시세차익률"]


def test_recommend_picks_highest_bid_meeting_target():
    lst = Listing(감정가=10000, 최저매각가=6400, 시세=12000, region="서울")
    p = _p({"목표시세차익률": 0.10})
    rec = recommend(lst, p)
    assert rec["시세차익률"] >= 10.0
    # 권장보다 1%p 높은 입찰가는 목표 미달이어야(=최대 입찰가)
    rows = table(lst, p)
    higher = [r for r in rows if r["입찰가"] > rec["입찰가"]]
    assert all(r["시세차익률"] < 10.0 for r in higher)


def test_enrich_priority(tmp_path, monkeypatch):
    import realty_signal.auction as au
    monkeypatch.setattr(au, "AUCTION_FILE", tmp_path / "a.json")
    a = Listing(단지명="A", region="서울", 감정가=10000, 최저매각가=6400, 시세=14000)
    b = Listing(단지명="B", region="대구", 감정가=10000, 최저매각가=6400, 시세=14000)
    rows = enrich([b, a], {"서울": "STRONG_BUY", "대구": "BUY"})
    assert rows[0]["단지명"] == "A"  # STRONG_BUY 우선


def test_import_csv(tmp_path, monkeypatch):
    import realty_signal.auction as au
    monkeypatch.setattr(au, "AUCTION_FILE", tmp_path / "a.json")
    csv = "단지명,region,감정가,최저매각가,시세,입찰기일\n래미안,서울,120000,80000,140000,2026-07-10\n"
    assert au.import_csv(csv) == 1
    assert au.load()[0].단지명 == "래미안"
