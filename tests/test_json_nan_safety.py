"""NaN 하나로 엔드포인트 전체가 500 이 되면 안 된다.

2026-09-06 prod 실측:
    ValueError: Out of range float values are not JSON compliant: nan
기본 `JSONResponse` 는 `allow_nan=False` 라 값 하나가 비면 화면 전체가 죽는다.
누락은 `null` 로 내보내고 화면이 '–' 로 처리하게 둔다.
"""

from __future__ import annotations

import json
import math

from fastapi.testclient import TestClient

from realty_signal.api import SafeJSONResponse, app


def test_nan_and_inf_become_null():
    r = SafeJSONResponse(content={"a": float("nan"), "b": float("inf"),
                                  "c": float("-inf"), "d": 1.5})
    body = json.loads(bytes(r.body))
    assert body == {"a": None, "b": None, "c": None, "d": 1.5}


def test_nested_structures_are_cleaned():
    payload = {"list": [float("nan"), {"deep": [float("inf"), 3]}],
               "tuple_like": [1, [float("nan")]]}
    body = json.loads(bytes(SafeJSONResponse(content=payload).body))
    assert body["list"][0] is None
    assert body["list"][1]["deep"][0] is None
    assert body["tuple_like"][1][0] is None


def test_output_is_standard_json():
    """`NaN`·`Infinity` 리터럴을 흘리면 표준 파서가 못 읽는다 — 그것도 장애다."""
    raw = bytes(SafeJSONResponse(content={"x": float("nan"), "y": float("inf")}).body)
    text = raw.decode()
    assert "NaN" not in text and "Infinity" not in text
    json.loads(text)  # 표준 JSON 으로 읽혀야 한다


def test_app_and_included_routers_both_use_safe_response():
    """하위 라우터는 앱 기본값을 자동 상속하지 않는다 — include_router 마다 지정해야 한다."""
    import inspect

    from realty_signal import api as app_api

    src = inspect.getsource(app_api)
    assert "default_response_class=SafeJSONResponse" in src
    # 모든 include_router 호출에 붙었는지
    total = src.count("app.include_router(")
    tagged = src.count("default_response_class=SafeJSONResponse)")
    assert tagged >= total, f"include_router {total}개 중 {tagged}개만 지정됨"


def test_live_route_returns_null_not_500():
    @app.get("/__nan_probe")
    def _probe():
        return {"v": float("nan"), "ok": 1}

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/__nan_probe")
    assert r.status_code == 200, f"NaN 이 {r.status_code} 를 만들었다"
    assert r.json() == {"v": None, "ok": 1}


def test_real_payload_with_pandas_nan():
    """pandas 계산 결과가 그대로 실려도 죽지 않아야 한다."""
    import pandas as pd

    s = pd.Series([1.0, float("nan"), 3.0])
    payload = {"values": [float(v) for v in s.values],
               "mean_of_empty": float(pd.Series([], dtype=float).mean())}
    assert math.isnan(payload["mean_of_empty"])
    body = json.loads(bytes(SafeJSONResponse(content=payload).body))
    assert body["values"] == [1.0, None, 3.0]
    assert body["mean_of_empty"] is None


# ── 근본 원인: 파이썬 json 은 비표준 NaN 리터럴을 기본 허용한다 ──────────

def test_external_nan_literal_is_parsed_as_null():
    """외부 API 가 `NaN` 을 보내도 우리 데이터에 NaN 이 들어오지 않는다.

    2026-09-06 prod 500 의 실제 경로 — 외부 응답의 `discount_min: NaN` 이
    `round(-float(d) * 100, 1)` 을 타고 응답까지 흘러갔다.
    """
    from realty_signal import jsonx

    d = jsonx.loads('{"discount_min": NaN, "avg": Infinity, "n": -Infinity, "ok": 1.5}')
    assert d == {"discount_min": None, "avg": None, "n": None, "ok": 1.5}
    # 표준 json 은 이걸 그대로 통과시킨다 — 우리가 막아야 하는 이유
    assert math.isnan(json.loads('{"x": NaN}')["x"])


def test_cache_writes_never_persist_nan_literal():
    """`json.dumps` 기본이 allow_nan=True 라 캐시 파일에 `NaN` 이 기록될 수 있었다.

    저장 자체를 실패시키면(allow_nan=False 만 걸면) 스캔 결과를 통째로 잃으므로,
    **먼저 씻고 나서** 표준 JSON 으로만 쓴다.
    """
    from realty_signal import jsonx

    text = jsonx.dumps({"listings": [{"급매갭": float("nan"), "호가": 50000}]})
    assert "NaN" not in text and "Infinity" not in text
    back = json.loads(text)          # 표준 파서로 읽혀야 한다
    assert back["listings"][0]["급매갭"] is None
    # 표준 dumps 는 NaN 리터럴을 그대로 쓴다 — 우리가 막아야 하는 이유
    assert "NaN" in json.dumps({"x": float("nan")})


def test_ingest_modules_use_safe_loader():
    """외부 응답을 파싱하는 ingest 모듈이 표준 json.loads 로 되돌아가면 안 된다."""
    import pathlib

    ing = pathlib.Path("src/realty_signal/ingest")
    offenders = []
    for f in sorted(ing.glob("*.py")):
        code = "\n".join(l for l in f.read_text(encoding="utf-8").splitlines()
                         if not l.lstrip().startswith("#"))
        if "json.loads(" in code.replace("jsonx.loads(", ""):
            offenders.append(f.name)
    assert not offenders, f"안전하지 않은 json.loads 사용: {offenders}"


def test_full_chain_external_nan_to_response():
    """외부 NaN → 파싱 → 계산 → 저장 → 응답. 어디서도 500 이 나면 안 된다."""
    from realty_signal import jsonx

    raw = '{"items": [{"discount_min": NaN, "price": 500000000}]}'
    parsed = jsonx.loads(raw)                                   # ① 파싱
    d = parsed["items"][0]["discount_min"]
    pct = None if d is None else round(-float(d) * 100, 1)      # ② 계산
    stored = jsonx.dumps({"할인율": pct})                        # ③ 저장
    assert "NaN" not in stored
    body = json.loads(bytes(SafeJSONResponse(content=json.loads(stored)).body))  # ④ 응답
    assert body == {"할인율": None}
