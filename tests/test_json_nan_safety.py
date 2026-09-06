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
