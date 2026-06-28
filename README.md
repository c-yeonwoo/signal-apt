# realty-signal-map

KB 주간 시계열 기반 **아파트 매수·매도 시그널** 분석 엔진.

기존에 수작업 엑셀(`시그널투자맵`)로 보던 매수우위·전세수급·매매/전세 증감 지표를
자동으로 파싱해 지역별 신호등(STRONG_BUY ~ SELL_RISK)으로 변환한다.

## 현재 상태 (v0.1 — 데이터 엔진 MVP)

- ✅ KB 주간 시계열 엑셀 파서 (`매수매도`·`전세수급`·`매매증감`·`전세증감`)
  - 176개 지역 · 2008~현재 주간 · 축약 날짜 자동 복원
- ✅ 임계값 기반 시그널 엔진 (전세수급 밴드 / 매수우위 / 모멘텀 / 종합 시그널)
- ✅ CLI 리포트 (지역별 시그널 테이블 + 지역 추이)
- ✅ 웹 대시보드 (FastAPI + ECharts) — 지역별 시계열 + 시그널 + 임계선
- ✅ **KB 데이터허브 자동 수집** (`signal fetch`) — 엑셀 없이 최신 주간 지표 pull
- ✅ **수도권 시군구 확장** (서울·경기·인천 증감률 드릴다운) — 112개 지역
- ✅ **입주물량 매도 시그널** — 공급압력(향후/과거) → 공급과잉+하락 시 SELL_RISK
- ✅ **주간 변화 알림** (`signal watch`) — 지난주 대비 등급 변화 + macOS 알림 + launchd 스케줄
- ✅ **경매 탭** — BUY+ 지역 매물 관리(수동/CSV) + 입찰가 계산기 + 우선순위 비교 + 입찰기일 전략
- ⬜ 입찰가 공식 — 사용자 확정 공식으로 교체 예정 (현재 `auction.bid_calc` 기본식)
- ⬜ 경매 매물 자동수집(유료 API) / 시장강도(부동산지인·아실) — 예정
- ⬜ React 전환 — 예정

## 설치 & 실행

> ⚠️ **Python 3.12 권장.** 3.14 에서는 pandas/pyarrow C 확장이 datetime 추론 시
> 세그폴트(exit 139). `python3.12 -m venv .venv` 로 생성할 것.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 데이터 준비 — 둘 중 하나
.venv/bin/signal fetch                            # KB 데이터허브에서 최신 자동 수집(권장)
.venv/bin/signal build data/raw/kb_weekly.xlsx    # 수동 엑셀 파싱

# 시그널 리포트 (KB 주간 시계열 엑셀 경로)
.venv/bin/signal report data/raw/kb_weekly.xlsx

# 매수 후보만
.venv/bin/signal report data/raw/kb_weekly.xlsx --only STRONG_BUY,BUY

# 특정 지역 추이 (터미널)
.venv/bin/signal report data/raw/kb_weekly.xlsx --region 서울

# 웹 대시보드 (시계열 시각화) — fetch/build 로 캐시 생성 후
.venv/bin/signal serve                            # http://127.0.0.1:8765

# 주간 변화 알림 (지난주 대비 등급 변화)
.venv/bin/signal watch                            # 변화 지역 표 출력
.venv/bin/signal watch --notify                   # + macOS 알림 센터 푸시

# 테스트
.venv/bin/pytest -q
```

### 주간 자동 실행 (launchd, 매주 토 09:00)

```bash
PROJECT=$(pwd)
sed "s#__PROJECT__#$PROJECT#g" scripts/com.realtysignal.weekly.plist.template \
  > ~/Library/LaunchAgents/com.realtysignal.weekly.plist
launchctl load ~/Library/LaunchAgents/com.realtysignal.weekly.plist
```

## 시그널 로직

| 지표 | 의미 | 룰 |
|---|---|---|
| 전세수급지수 (0~200) | 100+수요-공급 | <100 공급우위 / 140~ 타이트 / 170~ 전세난 / 190~ 매매전이 |
| **매수세우위** (raw) | 매수자 동향 원값 | **5/10/15/20 사다리, 20↑ = 매수신호** (median≈3~5) |
| 매수우위지수 (0~200) | 100+매수세우위-매도세우위 | 차트용 **참고값**(별개), 시그널 트리거 아님 |
| 매매·전세 증감률 (%) | 전주대비 | 최근 4주 평균 → 상승/보합/하락 |

**종합 시그널**: 전세난 + 매수세우위 20↑ + 매매상승 = `STRONG_BUY` / 매수세우위 20↑ 단독 = `BUY` / …

> 매수세우위와 매수우위지수는 **별개 지표**로 본다. 매수 판단은 매수세우위(사다리),
> 매수우위지수는 차트 흐름 참고용.

### ⚠️ 데이터 한계 (KB 원본 기인)

- 매수우위·전세수급은 KB가 **24개 광역 단위로만** 조사 → 세부 164개 시군구는
  매매/전세 증감(모멘텀)만 사용 가능.
- **매도/끝물 시그널**(입주물량↑, 상급지→하급지 유동성, 신축→구축 전이)은
  KB 데이터에 없음 → 부동산지인/아실 연동 시 보강.

## 구조

```
src/realty_signal/
├── ingest/kb_weekly.py   # KB 주간 시계열 엑셀 → tidy long DataFrame
├── signals/engine.py     # 임계값 룰 → 지역별 시그널
└── cli.py                # typer CLI 리포트
```
