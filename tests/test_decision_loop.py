"""카드 네 줄, 같은 돈 보드, 보류 주간 차이."""

from datetime import date

from fastapi.testclient import TestClient

from realty_signal import api, auth, buying_power as bp, db
from realty_signal.services import buyer_decision, decision_log, levers


def test_card_lines_come_from_the_decision_and_skip_auction_cash():
    row = {"유형": "경매", "입찰상태": "needs_review", "가격출처": "매물가", "추정가": 50000,
           "자금": {"필요현금": 10000, "가능": True, "월상환": 30}}
    decision = {"feasibility": "unknown", "unknowns": ["권리 확인"], "next_action": "권리 확인 후 검토"}
    lines = buyer_decision.card_lines(row, decision)
    assert lines["cash"] == "입찰 보류 · 필요현금 산정 전"
    assert lines["unknown"] == "권리 확인"
    assert lines["next"] == "권리 확인 후 검토"
    built = buyer_decision.build(
        {"유형": "급매", "단지명": "A", "지역": "강남구", "가격출처": "매물가", "추정가": 10000,
         "fetched_at": "2026-09-19T00:00:00Z", "source": "synthetic",
         "자금": {"가능": True, "확인필요": False, "필요현금": 21000, "월상환": 135}},
        bp.Params(capital=50000), uid=1)
    assert built["lines"]["cash"].startswith("필요현금")
    assert "가정 안" in built["lines"]["cash"]


def test_same_capital_board_has_no_winner_and_blank_gap():
    params = bp.Params(capital=21000, income=13000, region="강남구", regulated=True,
                       metro=True, first_time=True)
    board = levers.compare(params, price=80000)
    assert board["winner"] is None
    by_id = {col["id"]: col for col in board["columns"]}
    assert [col["id"] for col in board["columns"]] == ["live", "gap", "auction", "rebuild"]
    assert by_id["live"]["feasibility"] == "infeasible"
    assert by_id["gap"]["feasibility"] == "unknown"
    assert "전세보증금" in by_id["gap"]["lines"]["unknown"]
    assert by_id["rebuild"]["feasibility"] == "unknown"
    assert "분담금" in by_id["rebuild"]["lines"]["unknown"]
    assert all("점수" not in col for col in board["columns"])


def test_gap_cash_uses_deposit_but_stays_unknown(monkeypatch):
    params = bp.Params(capital=21000, income=13000, region="노원구", first_time=True)
    board = levers.compare(params, price=30000, jeonse=20000, contribution=5000)
    gap = next(col for col in board["columns"] if col["id"] == "gap")
    assert gap["feasibility"] == "unknown"
    assert "보증금 반환" in gap["lines"]["unknown"]
    assert "실투자금" not in gap["lines"]["cash"]
    rebuild = next(col for col in board["columns"] if col["id"] == "rebuild")
    assert "입력한 분담금" in rebuild["lines"]["unknown"]


def test_hold_reason_diff_between_weeks(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "holds.db")
    db._migrated[0] = False
    base = buyer_decision.build(
        {"유형": "급매", "단지명": "가상", "지역": "노원구", "가격출처": "매물가",
         "추정가": 40000, "fetched_at": "2026-09-19T00:00:00Z", "source": "synthetic",
         "자금": {"가능": True, "확인필요": False, "필요현금": 12000, "월상환": 40}},
        bp.Params(capital=50000), uid=3)
    base["단지명"] = "가상"
    base["지역"] = "노원구"
    calls = {"n": 0}

    def week(day=None):
        calls["n"] += 1
        return "2026-W10" if calls["n"] == 1 else "2026-W11"

    monkeypatch.setattr(decision_log, "week_key", week)
    decision_log.remember(3, [base])
    nxt = dict(base)
    nxt["decision"] = dict(base["decision"])
    nxt["decision"]["unknowns"] = ["은행 심사·권리·입주 조건"]
    nxt["lines"] = dict(base["lines"])
    nxt["lines"]["cash"] = "필요현금 1.5억 · 월 50만 · 호가 기준 가정 안"
    decision_log.remember(3, [nxt])
    diff = decision_log.changes(3, today=date(2026, 3, 12))
    assert diff["prev_week"] == "2026-W10"
    assert diff["items"]
    text = " ".join(diff["items"][0]["changes"])
    assert "현금" in text
    assert "미확인" in text


def test_logged_in_buyer_reads_the_board(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "buyer.db")
    db._migrated[0] = False
    monkeypatch.setenv("SIGNUP_OPEN", "1")
    token, err = auth.signup("buyer-loop@example.com", "secret1", accept_tos=True)
    assert err is None and token
    user = db.user_by_email("buyer-loop@example.com")
    db.profile_set(user["id"], {
        "가용자본": 21000, "연소득": 13000, "생애최초": True, "매수지역": "강남구",
        "매수력": {"가정": {"지역": "강남구", "규제지역": True, "자기자본": 21000,
                          "연소득": 13000, "생애최초": True, "방공제적용": True}},
    })
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, token)
    res = client.get("/api/levers", params={"price": 80000})
    assert res.status_code == 200
    body = res.json()
    assert body["winner"] is None
    live = next(col for col in body["columns"] if col["id"] == "live")
    gap = next(col for col in body["columns"] if col["id"] == "gap")
    assert live["feasibility"] == "infeasible"
    assert gap["feasibility"] == "unknown"
