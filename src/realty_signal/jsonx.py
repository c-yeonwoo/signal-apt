"""NaN 안전 JSON — 외부 응답 파싱과 캐시 저장의 공통 관문.

파이썬 `json` 은 표준 JSON 에 없는 `NaN`·`Infinity` 리터럴을 **기본으로 허용**한다.

  · `json.loads('{"x": NaN}')` → `{'x': nan}`   (외부 API 가 보내면 그대로 유입)
  · `json.dumps({'x': nan})`   → `'{"x": NaN}'` (우리 캐시 파일에 그대로 기록)

그리고 응답 직렬화는 `allow_nan=False` 라 거기서 **500** 이 난다:
    ValueError: Out of range float values are not JSON compliant: nan

2026-09-06 prod 실측이 정확히 이 경로였다 — 외부 API 가 준 `NaN` 이
할인율 계산(`round(-float(d) * 100, 1)`)을 타고 응답까지 흘러갔다.

그래서 **들어올 때와 나갈 때 양쪽에서** 막는다. 누락은 `None`(=`null`) 으로 통일한다.
"""

from __future__ import annotations

import json
import math
from typing import Any

__all__ = ["clean", "loads", "dumps"]


def clean(o: Any) -> Any:
    """NaN·±Infinity 를 None 으로. 중첩 dict/list 까지 재귀."""
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def loads(text: str | bytes, **kw: Any) -> Any:
    """외부 응답 파싱. `NaN`·`Infinity` 리터럴은 `None` 으로 받는다."""
    kw.setdefault("parse_constant", lambda _c: None)
    return json.loads(text, **kw)


def dumps(obj: Any, **kw: Any) -> str:
    """캐시·파일 저장. NaN 을 리터럴로 흘리지 않는다(표준 JSON 만 쓴다).

    `allow_nan=False` 만 걸면 NaN 이 하나 있을 때 **저장 자체가 실패**해
    스캔 결과를 통째로 잃는다. 그래서 먼저 씻고 나서 끈다.
    """
    kw.setdefault("ensure_ascii", False)
    return json.dumps(clean(obj), allow_nan=False, **kw)
