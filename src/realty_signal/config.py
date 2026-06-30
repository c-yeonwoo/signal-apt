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


def public_data_key() -> str | None:
    return os.environ.get("PUBLIC_DATA_KEY")


def odsay_key() -> str | None:
    return os.environ.get("ODSAY_KEY")


def seoul_key() -> str | None:
    return os.environ.get("SEOUL_OPENAPI_KEY")


def kakao_key() -> str | None:
    return os.environ.get("KAKAO_REST_API_KEY")


def youtube_key() -> str | None:
    return os.environ.get("YOUTUBE_API_KEY")


def naver_search() -> tuple[str | None, str | None]:
    return os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
