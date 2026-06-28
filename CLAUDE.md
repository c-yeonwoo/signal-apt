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
.venv/bin/signal report <kb.xlsx>     # 터미널 시그널 리포트
.venv/bin/pytest -q                   # 테스트
```

## 데이터 범위 / 시그널

- 24개 광역: 전 지표(매수우위·전세수급·증감·입주물량). 수도권(서울·경기·인천)은 시군구까지(증감만).
- 매수우위/전세수급은 KB 광역 단위만 → 시군구는 모멘텀+입주물량 기반.
- 입주물량(`kb_supply`): aptMovinCnt, 공급압력=향후/과거. >1.3 공급과잉, 하락동반 시 SELL_RISK.
- 변화감지(`signals/history`): snapshot.json 비교, 매주 launchd(토 09:00) `watch --notify`.

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
- 입찰가 공식은 `auction.bid_calc` 한 곳. **현재 기본식(목표수익률·낙찰가율·저감률 조정 가능),
  사용자 확정 공식으로 교체 예정** — 이 함수만 바꾸면 됨.
- 우선순위 = 지역시그널 가중(STRONG_BUY2/BUY1)×10 + 안전마진, 수익불가 −100.
- 전략: 입찰기일 기준 이번주/2주내 그룹 + 최우선 단지.

## 미해결 / 확인 필요

- **입찰가 산정 공식**: 사용자 고유 공식 미수령 → `auction.bid_calc` 기본식 사용 중. 받으면 교체.

- 매도/끝물 시그널은 입주물량·시장강도 필요 → 부동산지인/아실 (회색지대 크롤링, 신중).
- 공식 보조 데이터: 국토부 실거래가 API(거래량) — 합법·무료.
