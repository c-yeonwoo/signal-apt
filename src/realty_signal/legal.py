"""이용약관 · 개인정보 처리방침 (강의/코호트용 최소 고지)."""

from __future__ import annotations

TOS_VERSION = "2026-07"

TERMS_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>이용약관 — Signal APT</title>
<style>
body{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 20px;line-height:1.65;color:#1e293b}
h1{font-size:22px} h2{font-size:16px;margin-top:28px} p,li{font-size:14px;color:#334155}
a{color:#2563eb}
</style></head><body>
<h1>Signal APT 이용약관</h1>
<p>버전 2026-07 · 서비스명 Signal APT</p>
<h2>1. 서비스 성격</h2>
<p>본 서비스는 KB 주간 시계열 등 공개 데이터를 가공한 <b>참고용 매수·매도 타이밍 정보</b>를 제공합니다.
투자 권유·자문업·중개가 아니며, 최종 의사결정은 이용자 본인에게 있습니다.</p>
<h2>2. 계정</h2>
<p>이메일·비밀번호로 가입합니다. 계정 정보를 안전하게 관리할 책임은 이용자에게 있습니다.</p>
<h2>3. 데이터·정확성</h2>
<p>시그널·적중률·타이밍 점수는 과거 구간 검증에 기반한 규칙 결과이며 미래 수익을 보장하지 않습니다.
원천 데이터(KB·국토부 등) 지연·오류가 반영될 수 있습니다.</p>
<h2>4. 금지</h2>
<p>서비스·API의 무단 수집, 계정 공유로 인한 남용, 관리자 기능 우회를 금지합니다.</p>
<h2>5. 변경·문의</h2>
<p>약관은 필요 시 개정될 수 있으며, 개정 시 서비스 내 고지합니다.</p>
<p><a href="/">← Signal APT</a> · <a href="/legal/privacy">개인정보 처리방침</a></p>
</body></html>
"""

PRIVACY_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>개인정보 처리방침 — Signal APT</title>
<style>
body{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 20px;line-height:1.65;color:#1e293b}
h1{font-size:22px} h2{font-size:16px;margin-top:28px} p,li{font-size:14px;color:#334155}
a{color:#2563eb}
</style></head><body>
<h1>Signal APT 개인정보 처리방침</h1>
<p>버전 2026-07</p>
<h2>1. 수집 항목</h2>
<ul>
<li>계정: 이메일, 비밀번호 해시(평문 저장 안 함)</li>
<li>프로필(선택): 가용자본, 거주지, 관심 평형 등 서비스 개인화에 필요한 항목</li>
<li>이용 기록: 즐겨찾기, 알림 확인, 이벤트 로그(기능 개선용)</li>
</ul>
<h2>2. 이용 목적</h2>
<p>회원 인증, 시그널·리포트·알림·주간 이메일 제공, 서비스 안정·개선.</p>
<h2>3. 보관</h2>
<p>계정 삭제 요청 또는 서비스 종료 시까지. 비밀번호 재설정 토큰은 단시간(1시간) 보관 후 무효화됩니다.</p>
<h2>4. 제3자 제공</h2>
<p>법령에 따른 경우를 제외하고 판매·임대하지 않습니다. 이메일 발송을 위해 SMTP 제공자를 사용할 수 있습니다.</p>
<h2>5. 권리</h2>
<p>프로필 수정·계정 관련 문의는 서비스 내 계정 메뉴 또는 운영 채널로 요청할 수 있습니다.</p>
<p><a href="/">← Signal APT</a> · <a href="/legal/terms">이용약관</a></p>
</body></html>
"""
