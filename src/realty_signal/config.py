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


def app_base_url() -> str:
    """비밀번호 재설정·메일 링크용 공개 URL. 미설정 시 Railway 기본."""
    return (os.environ.get("APP_BASE_URL") or "https://signal-apt.up.railway.app").rstrip("/")


def opus_whitelist() -> set[str]:
    """Opus 4.8 프리미엄 모델을 쓸 계정 이메일(소문자) 집합. 그 외는 기본(저가) 모델."""
    raw = os.environ.get("AI_OPUS_WHITELIST", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def admin_whitelist() -> set[str]:
    """관리자(데이터 운영) 계정 이메일(소문자). 미설정 시 Opus 화이트리스트로 폴백."""
    raw = os.environ.get("ADMIN_EMAILS", "")
    ids = {e.strip().lower() for e in raw.split(",") if e.strip()}
    return ids or opus_whitelist()


def student_allowlist() -> set[str]:
    """수강생 이메일 화이트리스트(소문자). 있으면 초대 코드 없이도 가입 가능."""
    raw = os.environ.get("STUDENT_ALLOWLIST", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def invite_codes() -> set[str]:
    """수강생 초대 코드(소문자). 콤마 구분. 예: INVITE_CODES=cohort2026a,spring26."""
    raw = os.environ.get("INVITE_CODES", "")
    return {c.strip().lower() for c in raw.split(",") if c.strip()}


def signup_open() -> bool:
    """긴급 공개 가입. SIGNUP_OPEN=1 이면 cohort 게이트 우회(비권장)."""
    return (os.environ.get("SIGNUP_OPEN") or "").strip().lower() in {"1", "true", "yes"}


def check_cohort_access(email: str, invite_code: str | None = None) -> str | None:
    """가입 허용이면 None, 거부면 사용자용 에러 메시지.

    - SIGNUP_OPEN=1 → 항상 허용
    - STUDENT_ALLOWLIST / INVITE_CODES 중 하나라도 설정 → 이메일 또는 코드 매칭 필수
    - 둘 다 비어 있고 prod → 차단(누수 방지)
    - 둘 다 비어 있고 로컬 → 허용(개발 편의)
    """
    if signup_open():
        return None
    email = (email or "").strip().lower()
    code = (invite_code or "").strip().lower()
    allow = student_allowlist()
    codes = invite_codes()
    if not allow and not codes:
        if is_prod():
            return "현재 수강생 초대 가입만 받습니다. 안내받은 초대 코드를 확인해 주세요."
        return None
    if email and email in allow:
        return None
    if codes:
        if not code:
            return "수강생 초대 코드를 입력해 주세요."
        if code not in codes:
            return "초대 코드가 올바르지 않습니다."
        return None
    return "수강생 등록 이메일이 아닙니다. 초대를 확인해 주세요."


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
