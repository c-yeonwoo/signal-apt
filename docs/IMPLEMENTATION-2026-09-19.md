# 구매 의사결정 개선 — 구현 및 운영 인계

## 제품 계약

목적은 초보 실거주 구매자가 자금·생활 제약을 이해하고 후보를 좁힌 뒤 다음 확인을 결정하는 것이다. 상승 예측·최대 차입·거래 성사를 성공의 대리 지표로 삼지 않는다. 보류·제외도 유효한 결과다.

| 영역 | 구현 |
|---|---|
| 사용자 격리 | 요청별 uid가 고정된 상담 도구, 스트림 인증 선행, 관리자와 모델 사용 권한 분리 |
| 자금 | 실제 필요 대출/허용 한도 분리, 현금 매수, 미확인 소득, 비상자금, 기존 대출 포함 월 한도, 가격 구간별 최대값 탐색 |
| 후보 | 모든 시장 국면 허용, 후보 지역별 금융 판정, 가격 출처 보존, 자금 조건 내→미확인→조건 초과 순서, 동일 조건 내 다양성 |
| 판단 계약 | `buyer_decision`의 evidence/decision ID, 가정·정책 버전, 조건부/미확인/불가, 사유·미확인 항목·다음 확인 |
| 원천 | 국토부 endpoint×시군구×월 캐시, 전체 페이지, 계약 해제 제외, immutable 원본 수정 이력, 최근 월 갱신, 동시성·quota 차단 |
| 집계 | 동명이단지/다중주소 혼합 보류, 동일 반올림 전용면적·3개월 이내 매매/전세만 비교; 층·상태 차이는 여전히 미통제 |
| 운영 | 원자적 JSON 게시, 부분 수집 시 이전 정상 지역 보존, 소스별 독립 due/retry와 SQLite lease, live/readiness 분리 |
| LLM | 모든 Anthropic 호출 공통 게이트, 원자적 예상비 예약/사용량 정산, 동시성·입출력·횟수 제한, prompt cache, 요청별 uid/기능 원가 기록 |
| UI | 홈/내 조건/내 후보/더 찾아보기, 기본 후보 카드, 가격 성격·현금·월 부담, 실제 lazy loading, 뒤로가기, 키보드·Escape·포커스, 오래된 비동기 결과 무효화 |
| 검증 | 달력 12주 미성숙 제외, onset 신호 소급 변경 금지, 수정본 해시, 스냅샷 이력, purged holdout/walk-forward, 시간·지역 군집 bootstrap, 검증 일치 수동 승격 게이트 |
| CI | Python 의존성 lock, 격리 DB/합성 KB/네트워크 차단 pytest, 실제 JS 실행 및 합성 API Chromium 검사 |

PR 순서: [194](https://github.com/c-yeonwoo/signal-apt/pull/194) → [195](https://github.com/c-yeonwoo/signal-apt/pull/195) → [196](https://github.com/c-yeonwoo/signal-apt/pull/196) → [197](https://github.com/c-yeonwoo/signal-apt/pull/197) → [198](https://github.com/c-yeonwoo/signal-apt/pull/198) → 판단 계약 최종 묶음.

## 구조와 의미

`transaction_source/jobs → 자료/집계 → buying_power → buyer_decision → 홈·후보·AI 설명`

- `가능=true`는 입력 가정의 산술 결과일 뿐 은행 승인 또는 해당 매물 구매 가능 확정이 아니다. 공개 decision은 최대 `conditional`이다.
- 호가 없는 단지/청약/경매 최저가는 실제 구매 가격과 구분한다. 데이터 미확인 후보를 정상 가능 후보로 승격하지 않는다.
- 홈 숏리스트는 제한된 5개 지역의 단지 실거래 추정치다. 전체 매물 탐색이나 현재 호가 보증이 아니다. 명시한 가격 상한만 hard cap이며 자동 산출한 공통 지역 예산은 다른 지역 금융 판정을 덮지 않는다.
- decision은 반환 read model이며 별도의 영구 개인 의사결정 이력 저장소는 아니다. 원천과 시장 스냅샷은 수정 이력을 보존한다. `entity_id`는 로컬 참조 해시이지 정부 공식 단지 ID가 아니다.
- `observed_at/published_at/fetched_at`가 없는 자료는 null/미검증으로 유지한다. 처리 시각을 원천 공표 시각으로 꾸미지 않는다.
- AI 캐시는 사용자·프로필·근거·모델·프롬프트 버전을 포함하고 응답 생성 시각만 바뀌는 것은 제외한다. 숫자·자격은 LLM이 계산하지 않는다.
- 거래 차액은 취득 비용·보증금 반환·입주 제약을 반영한 필요현금이 아니다. ‘실투자금’으로 표현하지 않는다.

## 운영 설정

| 설정 | 기본/역할 |
|---|---|
| `ADMIN_EMAILS` | 명시한 관리자만 운영 API 사용. `AI_OPUS_WHITELIST`는 관리자 권한을 부여하지 않음 |
| `MOLIT_DAILY_REQUEST_LIMIT` | 기본 9,500. 실제 키의 승인 한도를 확인하고 조정. 원천 quota 오류 22는 일일 차단 |
| `LLM_DAILY_BUDGET_USD` | 기본 $50/일. 앱의 보수적 **예상비** 예약 한도이며 공급자 청구 상한 보증 아님 |
| `LLM_MAX_CONCURRENT` | 기본 4. 모든 기능 공유 |
| `BACKUP_S3_*` | 설정 시 일일 DB 백업. 업로드 실패는 job 실패/재시도로 기록 |

LLM 기본 제한: 호출당 입력 100,000 bytes, 출력 2,000 tokens, client당 6회. SDK 40초 timeout, 자동 재시도 0. 120초는 다음 호출 시작을 막는 게이트이며 모든 tool/stream 전체의 절대 종료 시간 보증은 아니다. 취소·불명확 실패는 당일 예약분을 남긴다. 원가 테이블은 [Anthropic 공식 가격](https://platform.claude.com/docs/en/about-claude/pricing) 2026-09-19 확인값이며 청구서와 대조해야 한다. 서비스 모델 등급은 임의로 낮추지 않았다.

단일 앱 프로세스·영속 SQLite 볼륨을 기준으로 한다. thread single-flight와 SQLite lease는 분산 저장소가 아니다. 독립 볼륨 replica 증설은 지원한다고 주장하지 않는다.

점검 경로:

- `/live`: 프로세스 응답. Railway healthcheck도 이 경로 사용.
- `/ready`: DB 접근과 KB 캐시 존재/14일 이내 기준. 모든 매물 공급원의 건강을 보증하지 않음.
- 관리자 `/api/operations`: job 성공/오류/차기 실행, 소스 건강, LLM 사용량·예상비.
- `/api/freshness`: 기준일과 수집 기록을 구분해서 확인. 갱신 시각만으로 모든 지역이 신선하다고 해석하지 않음.

## 실행 검증

```sh
pip install -c requirements.lock -e '.[dev]'
pytest -q
node --test tests/ui_runtime.test.cjs
npm install --no-save --package-lock=false playwright@1.62.1
npx playwright install --with-deps chromium --only-shell
node tests/ui_browser.cjs
```

최종 묶음 로컬 검사: pytest **455개**, JS 런타임 **3개** 통과. Chromium에서 360px 가로 넘침, 후보 기본 노출, hidden source 미호출/최초 펼침, 뒤로가기, 서버 계산 결과 렌더, Enter/Space toggle, A→B 지연 응답, Escape 포커스 복귀를 실행했다. 지도 vendor는 adapter로 대체한 합성 API 테스트다. 프로덕션 로그인 전체 E2E 또는 실제 구매자 과업 테스트가 아니다.

검증 프로토콜 실행:

```sh
python scripts/evaluate_buyer.py evidence.json
```

입력은 `records`, `protocol`, `params`이며 records 필드는 `brain/evaluation.py` 참고. `--persist`는 설정된 DB에 결과를 저장하므로 환경을 확인해야 한다. 전체 정규화된 SignalConfig와 동일한 해시의 통과 결과만 수동 apply 가능하다. 합성 테스트의 통과를 투자 성과로 게시하면 안 된다.

## 아직 운영 확인이 필요한 항목

1. **매물 원천 사용권**: 바로이집 코드의 개인용/로컬 제한 주석과 회원용 사용의 허용 범위를 운영자가 확인해야 한다. 권한을 확인했다고 가정하지 않았고 중단된 koczip 연동은 복구하지 않았다.
2. **금융·세무 규정**: 기존 숫자의 최신 법적 적합성을 인증하지 않았다. 항목별 출처·효력·검증일을 담는 manifest는 미검증 상태다. 공식 조항을 확인해 기록하기 전 승인 확정 표현 금지.
3. **실측 운영**: 공급자 청구서, 실제 키 quota, 인증된 운영 수집 건강, 외부 백업 복원 훈련. 이번 구현에서 유료 모델 실호출·사용자 데이터 열람·대량 backfill은 하지 않았다.
4. **실제 효과 검증**: 당시 공표 원본 확보, 성숙 기간 경과, 독립 보류셋과 구매자 과업 테스트가 필요하다. 실제 보류셋 성능·상승 확률 또는 신규 사용자 5명 검증 완료를 주장하지 않는다.
5. **기존 별도 작업**: 원래 루트의 미커밋 strategy_compare/관련 파일은 그대로 보존했다. 무주택=생애최초 및 갭 기반 필요현금 가정을 포함한 별도 실거주/임대 비교는 이 묶음에 끼워 머지하지 않았다.

남은 항목은 코드 테스트로 대신할 수 없는 운영·자료·시간 조건이다. 모니터링이나 새 기능 확장을 자동으로 생성하지 않았다.
