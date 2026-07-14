"""환경변수 로더 — .env 의 API 키를 os.environ 으로 (python-dotenv 미사용, 의존 최소화)."""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def is_prod() -> bool:
    """배포(Railway) 환경 여부. 로컬 dev 는 RAILWAY_ENVIRONMENT 가 없음."""
    return bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("APP_ENV") == "prod")


def public_data_key() -> str | None:
    return os.environ.get("PUBLIC_DATA_KEY")


def opus_whitelist() -> set[str]:
    """Opus 4.8 프리미엄 모델을 쓸 계정 이메일(소문자) 집합. 그 외는 기본(저가) 모델."""
    raw = os.environ.get("AI_OPUS_WHITELIST", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def admin_whitelist() -> set[str]:
    """관리자(데이터 운영) 계정 이메일(소문자). 미설정 시 Opus 화이트리스트로 폴백."""
    raw = os.environ.get("ADMIN_EMAILS", "")
    ids = {e.strip().lower() for e in raw.split(",") if e.strip()}
    return ids or opus_whitelist()


def odsay_key() -> str | None:
    return os.environ.get("ODSAY_KEY")


def vworld_key() -> str | None:
    """VWorld(국토부) 지도 타일 키 — 있으면 한글 지도, 없으면 CartoDB 폴백."""
    return os.environ.get("VWORLD_KEY")


def vworld_data_key() -> str | None:
    """VWorld NED 데이터 API 키(공동주택 공시가격 WFS 등). 도메인 잠금이라 vworld_domain()과 함께 사용."""
    return os.environ.get("VWORLD_DATA_KEY")


def vworld_domain() -> str:
    """VWorld 키에 등록된 도메인(잠금 해제용). 로컬 기본 localhost, prod는 배포 도메인."""
    return os.environ.get("VWORLD_DOMAIN", "localhost")


def seoul_key() -> str | None:
    return os.environ.get("SEOUL_OPENAPI_KEY")


def kakao_key() -> str | None:
    return os.environ.get("KAKAO_REST_API_KEY")


def youtube_key() -> str | None:
    return os.environ.get("YOUTUBE_API_KEY")


def naver_search() -> tuple[str | None, str | None]:
    return os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")


def nick_weekly_limit() -> int:
    """무료 티어 Nick 주간 질문 한도. Opus/관리자는 서버에서 우회."""
    try:
        return max(0, int(os.environ.get("NICK_WEEKLY_LIMIT", "15")))
    except ValueError:
        return 15


def report_weekly_limit() -> int:
    """무료 티어 AI 심층 리포트 주간 한도."""
    try:
        return max(0, int(os.environ.get("REPORT_WEEKLY_LIMIT", "3")))
    except ValueError:
        return 3
