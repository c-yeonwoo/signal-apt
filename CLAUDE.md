# CLAUDE.md — realty-signal-map

KB 주간 시계열 기반 아파트 매수·매도 시그널 분석 서비스. (~/dev-private 하위 개인 프로젝트)

## 무엇인가

수작업 엑셀(`시그널투자맵`)로 보던 부동산 시그널을 자동화한다. 원천은 **KB부동산
주간 시계열** 엑셀(매주 공개). 이를 파싱 → 임계값 룰 적용 → 지역별 신호등.

## 기술 스택

- **Python 3.12** (3.14 금지 — pandas/pyarrow datetime 추론 세그폴트 exit 139)
- pandas / openpyxl / pyarrow / typer / rich / FastAPI + uvicorn / ECharts(CDN)
- venv: `.venv/` (3.12) — `pip install -e ".[dev]"`

## 빌드 & 실행

```bash
.venv/bin/signal fetch                # KB 데이터허브 자동 수집 → 캐시(권장)
.venv/bin/signal build <kb.xlsx>      # 수동 엑셀 파싱 → 캐시
.venv/bin/signal serve                # 대시보드 http://127.0.0.1:8765
.venv/bin/signal watch [--notify]     # 지난주 대비 등급 변화 알림 + 스냅샷 갱신
.venv/bin/signal digest [--send]      # 관심지역 주간 이메일 다이제스트(SMTP 없으면 dry-run)
.venv/bin/signal brief [--send]       # 텔레그램 Nick 데일리 브리핑(토큰 없으면 dry-run)
.venv/bin/signal report <kb.xlsx>     # 터미널 시그널 리포트
.venv/bin/pytest -q                   # 테스트
```

## 데이터 범위 / 시그널

- 24개 광역: 전 지표(매수우위·전세수급·증감·입주물량). 수도권(서울·경기·인천)은 시군구까지(증감만).
- 매수우위/전세수급은 KB 광역 단위만 → 시군구는 모멘텀+입주물량 기반.
- 입주물량(`kb_supply`): aptMovinCnt, 공급압력=향후/과거. >1.3 공급과잉, 하락동반 시 SELL_RISK.
- 변화감지(`signals/history`): snapshot.json 비교, 매주 launchd(토 09:00) `watch --notify`.
- 주간 이메일: `signal digest --send` (SMTP_HOST/SMTP_FROM). 서버 `_auto_refresh_loop` 도 7일마다 시도(SMTP 없으면 dry-run). 관심지역 ★ 유저 대상.
- 쓰기 API(경매 CRUD·급매/재건축 갱신·KB refresh·단지백테스트)는 `ADMIN_EMAILS` 만. 가입 시 ToS 동의 필수.
- 수강생 전용 가입: `INVITE_CODES` 또는 `STUDENT_ALLOWLIST`. prod에서 둘 다 비면 가입 차단. `SIGNUP_OPEN=1` 우회(비권장). UI `?invite=CODE`.
- Nick 프로필 주입: advisor `build_system(profile, favorites)`. 소프트 한도: Nick 주 15회·리포트 주 3회
  (`NICK_WEEKLY_LIMIT`/`REPORT_WEEKLY_LIMIT`). Opus 화이트리스트·관리자는 무제한.
- 동네 리포트 v2: 열면 주간 스냅샷 저장 → 지난 주차 대비 diff · 관심지역 2동네 비교.
- 대출 규제 테이블(`regulation.py`): **기준일 `AS_OF` 와 근거를 한 곳에**. 규제지역 = 서울 25개 구 +
 경기 15곳(10·15 대책 12곳 + 2026.7.1 동탄·기흥·구리). 지역명 → 규제·수도권 자동 판정(`classify`),
 동명이구(중구·강서구)는 시도 힌트로 가른다. `/api/regulation`·지도 오버레이·Nick `get_regulation` 공용 정본.
 - LTV: 규제 생애최초 70 / 서민실수요 60 / 무주택 40 / 1주택 처분조건 40 · 미약정 0 / 다주택 0.
 비규제는 80 / 70 / 70 / 70 / 60 / 60. 생애최초 우대는 **가격·소득 무관**(2022.8 감독규정).
 - 절대한도(수도권·규제): 15억↓ 6억 / 15~25억 4억 / 25억↑ 2억. 만기 최장 30년, 6개월 전입 의무.
 - 스트레스금리: 수도권·규제 3.0%p, 지방 0.75%p(2026.12.31 유예) × 금리유형(변동 100·혼합 60·주기 30·고정 0).
- 매수력 확정서(`buying_power.py`): 최대매수가·대출·월상환·필요현금을 한 산출물로. 대출 = **LTV·DSR·절대한도
 중 최저**(`loan_for`), 기대출 차감 + 스트레스 DSR, 주택수별 취득세, 중개보수 구간요율, 소득 30% 기준 '안정매수가'.
 `Params.region` 을 주면 규제가 자동 적용된다(없으면 ★관심지역 1순위 → 지난 확정 지역).
 `notes()` 가 "왜 이 숫자인지"를 문장으로 돌려주고 UI 가 그대로 띄운다.
 `/api/buying-power`(계산) · `/confirm`(프로필 `매수력`·`매수지역`에 확정 저장). 확정 가정은 다음 조회의 기본값.
 `api._max_purchase`(결론·갈아타기)도 이 엔진에 위임 — 예산 숫자는 앱 전체에서 하나.
- 단지 숏리스트(`services/shortlist.py`): 시군구 시그널을 '가볼 단지 3곳'으로 내리는 다리.
 후보 지역 = ★ ∪ BUY+(시그널 강도→저평가도 순, 최대 5). 적합도 = 통근·예산·시그널·저평가·급지 가중합,
 지역당 최대 2곳. 탈락은 사유별로 집계해 함께 반환. `/api/shortlist`.
 후보별 자금은 그 지역 규제 기준으로 다시 계산한다(`buying_power.params_for_region`).
 `_region_grades` 는 kv 7일 캐시 + 병렬 조회(콜드 70초 → 2초).
- 텔레그램 데일리 브리핑(`telegram.py`·`briefing.py`): 후보 3곳의 **어제 대비 변화만** 보낸다.
 변화 없는 날은 미발송, 월요일은 현황 확인용으로 1회. 기준점은 kv `briefing_snap:{uid}`,
 발송 성공 후에만 이동(실패 시 변화 유실 방지). 중복 방지는 `briefing_run:{날짜}`.
 연결은 웹훅 없이 getUpdates 폴링 — 앱에서 일회용 코드 발급 → `t.me/<bot>?start=<code>` → `/start` 매칭.
 `TELEGRAM_BOT_TOKEN`·`BRIEFING_HOUR`(KST, 기본 8). 서버 `_briefing_loop` 가 15분마다 폴링·발송 점검.
- 임장 코스(`services/imjang.py`): 후보 3곳 → 도착·출발 시각이 박힌 반나절 일정.
 순서는 거주지 중심에서 최근접 이웃, 구간 소요는 ODsay 30일 캐시(1.2km 미만은 도보 환산).
 이미 다녀온 단지는 뒤로. 체크리스트 10항목은 **현장에서만 아는 것**만
 (지역 단위 `personal_layer.IMJANG_CHECKS` 와 역할이 다름). 좋음2·보통1·나쁨0 → 100점 환산.
 방문 기록은 `imjang_visit` 테이블(uid·region·cx·visited 유니크 = 같은 날 재저장은 덮어쓰기).
 `/api/imjang/{course,visits,visit}`. 브리핑 '오늘 할 일'이 미방문 후보를 먼저 가리킨다.
- 폐기(개인용): 정책 KB 자동 소싱 · Toss 페이월 — 당분간 미추진.

## KB 데이터허브 API (자동 수집)

- base: `https://data-api.kbland.kr/bfmstat/weekMnthlyHuseTrnd/`
- `maktTrnd` (메뉴코드 01=매수우위, 03=전세수급; 월간주간 02=주간) →
  dataList 항목에 `매수우위지수`·`매수자많음`(=매수세우위)·`전세수급지수` 등
- `prcIndxInxrdcRt` (매물종별 01=아파트, 매매전세 01/02) → 증감률
- 응답: `dataBody.data.{데이터리스트[{지역명,dataList}], 날짜리스트}`, resultCode 11000
- TLS 정상(verify 불필요). 날짜는 'YYYYMMDD' → 슬라이싱 파싱(`pd.to_datetime(format=)` 세그폴트 회피).

## 핵심 개념

- KB 주간 시계열 시트: `매수매도`(매수우위지수), `전세수급`(전세수급지수),
  `매매증감`/`전세증감`(주간 가격 증감률). 모든 시트는 동일한 주(week) 행 공유(5행=첫 주).
- 매수우위/전세수급은 24개 광역 단위만, 증감은 164개 세부지역.
- 지수 = `100 + 우위 - 열위` (0~200). 매수우위 median≈46 (100은 역대급 활황).

## 데이터 규칙

- `data/raw/*.xlsx` 는 **커밋 금지**(저작권/용량, .gitignore 처리됨).
- 시그널 임계값은 `signals/engine.py:SignalConfig` 에 모아 둠. 하드코딩 금지.

## 지표 구분 (중요)

- **매수세우위**(raw, buyer_demand): 메모의 "5/10/15/20, 20↑ 매수" 사다리가 적용되는 값. 시그널 트리거.
- **매수우위지수**(buyer_superiority = 100+매수세우위-매도세우위): 차트용 참고값, 별개로 본다.

## 경매 탭 (`auction.py`)

- 매물 수동입력/CSV → `data/cache/auction.json`. API: `/api/auction/{listings,import,buy-regions}`.
- 입찰가 계산은 입찰가 산정표 모델(`auction.breakdown`/`table`/`recommend`):
  경매총매입(입찰가+등기비+명도비+이자 등) vs 일반매매총매입 → 시세차익률, 임대수익률, 단기매도수익률.
  낙찰가율 민감도 표 + 목표 시세차익률 만족 최대 입찰가 = 권장입찰가.
- 명도비 = 전용㎡×0.3025(평)×15만, 등기비 = 입찰가×취득세율+법무비. 파라미터는 DEFAULTS.
- 우선순위 = 지역시그널 가중(STRONG_BUY2/BUY1)×10 + 시세차익률, 목표미달 −100.
- 전략: 입찰기일 기준 이번주/2주내 그룹 + 최우선 단지. detail: `/api/auction/calc/{id}`.

## 경매 실행 (S5)

- 권리분석(`auction_rights.py`): 등기부·임차인 → 말소기준권리(= (근)저당·압류·가압류·담보가등기·
 경매개시 중 최선순위) → 인수/소멸/확인필요. 대항력은 **전입 다음날 0시** → 전입일 < 말소기준일.
 대항력+배당요구X = 전액 인수, 확정일자 없으면 안전하게 전액 인수로 잡음. 유치권·법정지상권은 순서 무관 '확인필요'.
 인수합계 → `Listing.인수보증금` → **입찰가 산정표에 자동 반영**(권장입찰가가 그만큼 내려감).
 애매한 건 단정하지 않고 사유+면책을 함께 반환. `/api/auction/rights/{preview,id}`.
- 낙찰 후 플랜(`auction.plan`): 낙찰일(없으면 입찰기일) 기준 D+0/7/14/44/45/75/105 단계와
 보증금·잔금·등기비·명도비 현금흐름. `/api/auction/plan/{id}`, 낙찰 기록은 `/api/auction/won/{id}`.
- 붙여넣기 임포트: `auction.parse_text` 규칙 파서가 먼저 돈다(AI 키 없이 동작).
 사건번호·감정가·최저가·기일·전용·유찰·시군구를 정규식으로. `parse_confidence` 가 low/medium 이면 AI 파서로 넘어가 병합.
- 기일 알림: 브리핑 `[경매]` 섹션 — 입찰기일 D-7~D-0, 낙찰 후 7일 내 다음 단계.
 D∈{7,3,1,0} 일 때만 '새 소식'으로 카운트(매일 울리지 않게). D≤1 이면 '오늘 할 일' 최상단.

## 미해결 / 확인 필요

- 취득세율(주택수/가격별 차등)·중개수수료율은 단일값 기본. 필요시 구간 테이블화.
- 방공제(소액임차보증금 최우선변제금, 서울 5,500만 등)는 미반영 — MCI/MCG 가입 전제. 미가입 시 한도가 그만큼 줄어든다.
- 정책대출(디딤돌·보금자리)의 별도 소득·주택가격 요건은 미모델링. 안내 문구로만 경고(`regulation.POLICY_LOAN_NOTE`).
- 규제지역 지정은 수시 변경 → `regulation.AS_OF` 와 목록을 대책 발표 때마다 손으로 갱신해야 한다.

- 권리분석은 등기부 요약 입력 기반 참고치. 배당표 시뮬레이션(선순위 배당 후 잔액)·소액임차인
 최우선변제 계산은 미구현 — '확인필요' 플래그로만 남김. 소액임차인 표는 담보물권 설정일 기준이라 단정 불가.
- 매도/끝물 시그널은 입주물량·시장강도 필요 → 부동산지인/아실 (회색지대 크롤링, 신중).
- 공식 보조 데이터: 국토부 실거래가 API(거래량) — 합법·무료.
