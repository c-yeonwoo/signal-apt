# Signal APT — Architecture (Engine v1)

> KB 주간 시계열 기반 아파트 매수·매도 **타이밍** 의사결정 엔진.  
> 최종 갱신: 2026-07 · **Timing v1** · **Brain Phase 0–3** (Phase 4 로드맵)

브랜드 한 줄: **「근거가 증명되는 매수 타이밍」** — [DESIGN.md](../DESIGN.md) 톤·UI 토큰은 별도.

---

## 1. 시스템 개요

Signal APT는 **4계층**으로 구성된다. MVP(룰 엔진 + SPA)에서 **진화형 두뇌**로 확장 중이다.

```
┌─────────────────────────────────────────────────────────────────┐
│ L4 Brain     Nick · events · outcome labels · calibration(수동)   │
├─────────────────────────────────────────────────────────────────┤
│ L3 Decision  TimingScore · Alert Engine · 기회도/단지 시그널     │
├─────────────────────────────────────────────────────────────────┤
│ L2 Knowledge snapshots · backtest · policy KB · nbhd_snap     │
├─────────────────────────────────────────────────────────────────┤
│ L1 Data      KB · 국토부 · 급매 · 청약 · SQLite/parquet 캐시     │
└─────────────────────────────────────────────────────────────────┘
         FastAPI (api.py + routes/brain.py)  ←→  SPA (web/index.html)
```

| 계층 | 역할 | 핵심 모듈 |
|------|------|-----------|
| **L1 Data** | 외부 소스 수집·정규화·캐시 | `ingest/*`, `store.py`, `db.py` |
| **L2 Knowledge** | 시계열·스냅샷·과거 적중률 | `signals/engine.py`, `brain/snapshots.py`, `brain/outcomes.py` |
| **L3 Decision** | 타이밍·알림·매물/단지 점수 | `signals/timing.py`, `brain/alerts.py`, `api.py` |
| **L4 Brain** | 설명·개인화·학습 연료·보정 제안 | `advisor.py`, `ai_report.py`, `brain/calibrate.py`, `brain/config_store.py`, `events` |

---

## 2. 데이터 플레인 (L1)

### 2.1 Canonical 시계열 — KB 주간

| 파일 | 스키마 | 출처 |
|------|--------|------|
| `data/cache/long.parquet` | `(date, region, metric, value)` | KB DataHub / 엑셀 |
| `data/cache/supply.parquet` | 입주물량·공급압력 | KB land API |
| `data/cache/codes.json` | 지역명→법정동코드 | KB |
| `data/cache/macro.json` | 금리·구매력 | KB HAI |
| `data/cache/volume.json` | 월별 거래량·거래량비 | 국토부 |

**지표 구분 (혼동 금지)**

| 필드 | 의미 | 시그널에서의 역할 |
|------|------|-------------------|
| `buyer_superiority` | 매수우위지수 (100=중립) | **차트 트리거** (강세≥70 등) |
| `jeonse_supply` | 전세수급지수 | **차트 트리거** (밴드 100/140/170/190) |
| `sale_change` / `jeonse_change` | 주간 증감% | **차트 트리거** (4주 모멘텀) |
| `buyer_demand` | 매수세우위 (raw) | **참고만** (사다리 5/10/15/20 → 등급 결정에 미사용) |

### 2.2 엔티티·매물

| 저장 | 내용 |
|------|------|
| SQLite `kv: complex:{lawd5}:{name}` | 단지 실거래·전세가율·단지시그널 |
| `auction.json` | 경매 매물 (수동/CSV) |
| `quicksale.json` | 급매 스캔 (baroezip) |
| ODcloud / redev 캐시 | 청약·재건축 |

### 2.3 메타 필드 (API 공통)

응답에 점진 적용 중:

| 필드 | 의미 |
|------|------|
| `asof` | KB 분석 기준일 |
| `source` | `kb_weekly`, `listings_merged`, … |
| `confidence` | 0–1, 데이터 완전성·현실성 |
| `timing_version` | Decision 규칙 버전 (현재 `v1`) |

---

## 3. 시그널 엔진 (L2 → L3)

**파일:** `src/realty_signal/signals/engine.py`

### 3.1 SignalConfig

임계값은 `SignalConfig` dataclass에 기본 정의 (코드 하드코딩 산재 금지).  
운영 적용값은 `brain/config_store` 의 **active version** (`db.kv signal_config_active`).  
보정은 `CalibrationProposal` → **수동 승인 apply** 만 (자동 적용 없음).

### 3.2 등급

`STRONG_BUY` · `BUY` · `WATCH` · `NEUTRAL` · `SELL_RISK`

- 차트 우선: 전세난/매매전이 + 매수우위지수 + 매매 모멘텀 (OR)
- 입주물량 공급압력 보정
- 시군구: 광역·강남11/강북14 상속

### 3.3 보조 국면

| 모듈 | 역할 |
|------|------|
| `signals/regime.py` | 급지역전·β·막차 |
| `signals/cycle.py` | 벌집순환 4국면 |
| `ingest/locality.py` | 저평가·입지 |

### 3.4 백테스트

`backtest_summary()` — 과거 시그널 구간 vs 12주 가격 방향 적중률.  
Nick `get_backtest`, region `TimingScore`에 **조건부 확률** 근거로 사용.

---

## 4. Timing Engine v1 (L3)

**파일:** `src/realty_signal/signals/timing.py`  
**버전:** `v1`

### 4.1 3층 타이밍 모델

| Layer | 함수 | 입력 |
|-------|------|------|
| **Region** | `region_timing()` | KB 시그널, 전세수급, 모멘텀, 백테스트 적중률 |
| **Complex** | `api._complex_signal()` | 사이클·지역·단지·가격 가중 (백테스트 전) |
| **Listing** | `listing_timing()` | 급매갭/시세차익/청약D-day + 시그널·급지 |

### 4.2 ListingTiming 규칙 (v1)

- 급매: `급매갭` −1~−30% → 0–60점; ≤−35% → 신뢰도↓
- 경매: `시세차익률` × 1.8 (cap 60)
- 청약: 상태 + D-day 임박 가점
- 공통: `_SIG_BONUS` + `_GRADE_BONUS` → 0–100 clamp

### 4.3 API

| Endpoint | 설명 |
|----------|------|
| `GET /api/timing?region=` | 지역 타이밍 |
| `GET /api/listings/all` | 매물 + `타이밍점수`·`confidence`·`meta` |
| Nick `get_timing` | region / listing layer 조회 |

**하위 호환:** `기회도` = `타이밍점수`, `기회도근거` = `타이밍근거`

---

## 5. Alert Engine v1 (L3)

**파일:** `src/realty_signal/brain/alerts.py`

### 5.1 규칙 (사용자 prefs, 기본 ON)

| Rule | 트리거 |
|------|--------|
| `signal_upgrade` | ★ 지역 시그널 **승급** 또는 **SELL_RISK 강등** (전역 `signal_changes` 필터; 단순 하향은 제외) |
| `high_timing` | ★ 지역 매물 `타이밍점수 ≥ timing_min` (기본 70) |
| `nbhd_change` | 동네 리포트 **주간 스냅샷 diff** (시그널·평단·급매·거래량비) |

### 5.2 API

| Method | Path |
|--------|------|
| GET | `/api/alerts` |
| POST | `/api/alerts/seen` |
| GET/PUT | `/api/alerts/prefs` |

### 5.3 스케줄

- KB 갱신 (`_do_refresh` / `signal watch`) → `brain/snapshots` diff → `signal_changes` 로그
- digest 이메일: `digest.py` (SMTP optional)

---

## 6. Brain Layer (L4) — Phase 0–2

### 6.1 스냅샷 통합

**파일:** `brain/snapshots.py`

| 저장소 | 형식 |
|--------|------|
| `data/cache/snapshot.json` | `{as_of, signals:{region→grade}}` (CLI watch) |
| `db.kv signal_snapshot` | `{region→grade}` (API) |

→ **단일 read/write 경로**로 통합 (watch / digest / refresh 공용).

### 6.2 Outcome 스냅샷

**파일:** `brain/outcomes.py`  
**KV:** `outcome_snapshots` (최대 52주)

KB 갱신(`_snapshot_signals`) 시 지역 feature append.  
N주 후 라벨링은 §6.5 (`signal outcomes-label` / admin API).

| Admin | `GET /api/brain/outcomes` |

### 6.3 이벤트 (학습 연료)

SQLite `events` — whitelist:

`signup` · `profile_complete` · `fav_add` · `report_open` · `nick_ask` · `nbhd_open` ·  
`listing_detail_open` · `listing_click` · `timing_card_expand`

### 6.4 Nick (Advisor)

**파일:** `advisor.py` — Claude tool-use, **데이터 조회만** 근거.

| Tool | 용도 |
|------|------|
| `get_region_signal` | KB 지역 시그널 |
| `get_complex` | 실거래·전세가율·단지시그널 |
| `get_timing` | **v1** 타이밍 점수 |
| `get_backtest` | 적중률 |
| `get_policy` | 정책 KB (BM25) |
| … | listings, presale, redev, news, freshness |

원칙: 확신형 예측·매수 지시 금지 → **조건부·확률·근거**.

### 6.5 Outcome 라벨링 (Phase 2)

**파일:** `brain/outcomes.py`

| KV | 내용 |
|----|------|
| `outcome_snapshots` | 주간 region feature |
| `outcome_labels` | asof → 4/12/26주 후 `up_5pct` / `flat` / `down_5pct` |

```bash
signal outcomes-label    # KB long + snapshots → 라벨 갱신
signal calibrate [--save]  # CalibrationProposal 생성
```

| API (admin) | 설명 |
|-------------|------|
| `POST /api/brain/outcomes/label` | 라벨 배치 실행 |
| `GET /api/brain/outcomes/labels` | 분포·샘플 |
| `GET /api/brain/calibration` | 제안 조회 |
| `POST /api/brain/calibration/run` | 제안 재생성 |
| `POST /api/brain/calibration/apply` | **수동** config 적용 |
| `GET /api/brain/config` | active + history |

### 6.6 SignalConfig 버전 (Phase 2)

**파일:** `brain/config_store.py`, `brain/calibrate.py`

- Active: `db.kv signal_config_active` `{version, params, applied_ts}`
- History: `signal_config_history` (최근 20)
- Proposal: `signal_config_proposal` (자동 적용 **금지**)
- `/api/meta` → `signal_config_version` 노출

### 6.7 진화 루프

```
Observe → Evaluate → Calibrate → Explain
   ↑         ↑           ↑
 events  outcomes   SignalConfig vN
 nbhd_snap backtest  (수동 승인 merge)
```

| 단계 | Phase 2 상태 | 남은 것 |
|------|--------------|---------|
| Observe | ✅ events · outcome_snapshots · nbhd_snap | listing-level outcome |
| Evaluate | ✅ backtest · outcome_labels | 단지 시그널 outcome |
| Calibrate | ✅ CalibrationProposal + 수동 apply | 자동 적용 금지 유지 |
| Explain | ✅ Nick get_timing / get_backtest | Nick episodic memory (Phase 4) |

---

## 7. 저장소 전략

| Tier | 기술 | 용도 |
|------|------|------|
| Parquet | pandas/pyarrow | KB long, supply, locality |
| JSON | 파일 | macro, volume, quicksale, auction |
| SQLite | `app.db` | users, favorites, events, kv, nbhd_snap, policy, geocode |

프로덕션: Railway 볼륨 + S3 백업 (`backup.py`).

---

## 8. API · 프론트

### 8.1 백엔드

- **앱:** `api.py` (~2700줄) + `routes/brain.py` (`/api/brain/*` 분리 완료)
- 나머지 도메인 router 분리·`MarketDataService` 추출은 아직 TODO
- **인증:** 쿠키 `rsm_session`, `/api/*` 게이트
- **캐시:** `@lru_cache` on `_kb()`, `_signals_df()`, `_regime()`, `_backtest()`  
  → config apply 시 `_clear_signal_caches()` 로 무효화

### 8.2 프론트

- **단일 SPA:** `web/index.html` (Vanilla JS + ECharts + Leaflet)
- **IA:** 홈 / 시장·지역 / 매물 / 내 집찾기 + Nick FAB
- **UI 변경 원칙:** 레이아웃 유지, 카드 내 **근거·기준일·confidence**만 additive

---

## 9. CLI

```bash
signal fetch               # KB 수집
signal watch [--notify]    # 스냅샷 diff + macOS 알림
signal digest [--send]     # 주간 이메일
signal outcomes-label      # outcome → N주 후 가격 라벨
signal calibrate [--save]  # CalibrationProposal (자동 적용 없음)
signal strength [--rebuild] # 시장강도 프록시 TOP
signal serve               # FastAPI + SPA
```

---

## 10. 테스트

| 파일 | 영역 |
|------|------|
| `test_engine.py` | 시그널 룰 |
| `test_timing.py` | Timing v1 |
| `test_snapshots.py` | 스냅샷 통합 |
| `test_brain.py` | Alert · Outcome · config_store · calibrate |
| `test_auction.py` | 경매 산정 |
| `test_personal_layer.py` | 개인화 보조 |

---

## 11. 외부 데이터 & 갭

| Tier | 소스 | 상태 |
|------|------|------|
| A | KB DataHub, 국토부, VWorld | ✅ |
| B | baroezip 급매, ODcloud 청약 | ✅ (주기적 갱신) |
| C | 시장강도(부동산지인/아실) | ✅ 프록시 — `signals/strength.py` (거래량비+급매) |
| C | 경매 자동수집 | ❌ — 수동/CSV |

---

## 12. 로드맵 & 구현 상태

| Phase | 항목 | 상태 |
|-------|------|------|
| **0** | 스냅샷 통합, events 확장, asof/confidence | ✅ |
| **1** | Timing v1, get_timing, listings meta, Alert v1 | ✅ |
| **2** | Outcome 라벨링, CalibrationProposal, config versioning | ✅ |
| **3** | Entity schema, ingest pipeline, 시장강도 프록시 | ✅ |
| **4** | Nick memory, ML 랭킹, (선택) React | 🔲 |

### Phase 3 요약

| 모듈 | 역할 |
|------|------|
| `entities.py` | `Provenance` · `RegionEntity` · `ListingEntity` 계약 |
| `ingest/pipeline.py` | `cache_health` · `build_market_strength` · `region_entity` |
| `signals/strength.py` | 시장강도 0–100 (거래량비+급매+시그널 소폭) |
| `GET /api/strength` | 지역/TOP 강도 |
| `GET /api/pipeline/health` | ok/partial/stale/missing |
| Nick `get_strength` | 유동성·거래 활발도 질문 |
| CLI `signal strength [--rebuild]` | TOP 요약 |

---

## 13. 디렉터리 맵 (Engine v1)

```
src/realty_signal/
├── api.py                 # FastAPI 오케스트레이터
├── store.py               # parquet I/O · fetch
├── db.py                  # SQLite
├── advisor.py             # Nick
├── personal_layer.py      # 동네·대출·체크리스트
├── digest.py              # 주간 이메일
├── brain/
│   ├── snapshots.py       # 시그널 스냅샷 통합
│   ├── alerts.py          # Alert Engine v1
│   ├── outcomes.py        # Outcome feature + labels
│   ├── config_store.py    # SignalConfig versioning
│   └── calibrate.py       # CalibrationProposal
├── entities.py            # Provenance · Region/Listing Entity (Phase 3)
├── routes/
│   └── brain.py           # /api/brain/* (Phase 2 분리)
├── signals/
│   ├── engine.py          # SignalConfig · evaluate · backtest
│   ├── timing.py          # Timing Engine v1
│   ├── strength.py        # 시장강도 프록시 (Phase 3)
│   ├── history.py         # snapshot.json I/O
│   ├── regime.py · cycle.py
├── ingest/
│   ├── pipeline.py        # cache health · strength 파생
│   └── …                  # 17 수집 모듈
└── web/index.html         # SPA
```

---

## 14. 참고 문서

- [CLAUDE.md](../CLAUDE.md) — 프로젝트 컨벤션·KB API
- [DESIGN.md](../DESIGN.md) — UI/UX·보이스
- [README.md](../README.md) — 설치·실행
- [DEPLOY.md](../DEPLOY.md) — Railway 배포

---

_Engine v1 = 규칙 기반 Decision + Outcome 라벨링 + CalibrationProposal(수동 apply).  
ML·Nick memory는 Phase 4. 임계값 자동 production 적용은 하지 않는다 — 백테스트 제안 후 수동 승인._
