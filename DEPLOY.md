# 배포 가이드 — Railway (최소비용·가벼운 운영)

Signal APT는 **FastAPI 단일 컨테이너**(프론트 `index.html` 포함 서빙) + **SQLite**(회원·프로필·캐시) 구조라, 앱 인스턴스 1개 + 영구 볼륨 1개면 충분합니다.

> 요약: Docker 빌드 → 컨테이너 실행 → `/app/data` 볼륨에 SQLite 영속 → 환경변수로 키 주입. 관리형 DB 불필요.

---

## 로컬 의존성 (배포 시 유의)

| 대상 | 위치 | 배포 처리 |
|---|---|---|
| **회원·세션·프로필·즐겨찾기 + 캐시** | `data/cache/app.db` (SQLite) | 🔴 **볼륨 필수** (없으면 재배포 시 회원 데이터 소실) |
| KB 시계열·codes·macro·locality | `data/cache/*.parquet, *.json` | 🟢 첫 부팅 시 자동 수집 / 볼륨에 함께 영속 |
| 지오코딩·정비사업·단지·임장 캐시 | `app.db` 내 kv/테이블 | 🟢 지연 재생성 (볼륨에 누적) |

- 코드에 절대경로·홈경로 하드코딩 없음. 데이터 경로는 전부 상대 `data/cache/` → cwd(`/app`) 기준 `/app/data/cache`.
- 비밀키는 레포에 없음(`.env` gitignore). **플랫폼 환경변수로 주입**.

---

## Railway 배포 순서

1. **레포 연결**: Railway → *New Project* → *Deploy from GitHub repo* → 이 레포 선택. (`Dockerfile`·`railway.json` 자동 감지)
2. **영구 볼륨 추가** (가장 중요): 서비스 → *Variables* 옆 *Volume* → *New Volume*, **Mount path = `/app/data`**.
   - `data/cache/app.db`가 이 볼륨에 저장되어 재배포·재시작에도 회원 데이터가 보존됩니다.
3. **환경변수 설정** (Variables 탭): 아래 중 사용하는 것만.
   ```
   PUBLIC_DATA_KEY=...        # 국토부 실거래(경매·급매·단지·재건축) — 핵심
   SEOUL_OPENAPI_KEY=...      # 서울 정비사업(재건축 zones/단계)
   KAKAO_REST_API_KEY=...     # 근처 공인중개사
   YOUTUBE_API_KEY=...        # 임장 리포트(영상)
   NAVER_CLIENT_ID=...        # 임장 리포트(블로그)
   NAVER_CLIENT_SECRET=...
   ANTHROPIC_API_KEY=...      # AI 결론·임장 종합 리포트 (없으면 규칙기반/큐레이션 폴백)
   AI_OPUS_WHITELIST=a@b.com  # (선택) 이 계정만 Opus 4.8. 그 외 리포트=Sonnet·비교해설=Haiku
   ODSAY_KEY=...              # (선택)
   ```
   - `PORT`·`HOST`는 건드릴 필요 없음 — Railway가 `PORT` 주입, Dockerfile이 `0.0.0.0` 바인딩.
   - 키를 안 넣은 기능은 **graceful 폴백**(빈 목록/검색 링크/규칙기반)으로 동작.
4. **도메인 생성**: 서비스 → *Settings → Networking → Generate Domain*.
5. **첫 부팅**: `data/cache`가 비어 있으면 lifespan이 KB 데이터를 자동 수집(수십 초). 이후 앱 내장 **주간 자동 갱신 루프**가 최신화.

### (선택) 초기 데이터 시딩 — 웜스타트
자동 수집 대신 로컬 캐시(≈10MB)를 볼륨에 미리 넣으면 즉시 완전체로 뜹니다.
```bash
# 로컬에서 railway CLI 로 1회 업로드 (볼륨 마운트된 경로로)
railway run bash -c 'mkdir -p /app/data/cache'
# 또는 최초 배포 후 임시로 파일 복사 스크립트 실행 (app.db·*.parquet·*.json)
```
> 시딩을 건너뛰어도 기능은 정상, 첫 조회만 느립니다.

---

## 리소스·비용

- **메모리**: pandas+pyarrow 로 **512MB 최소·1GB 권장**. Railway 기본으로 충분.
- **볼륨**: 1~3GB면 넉넉 (현재 캐시 ≈10MB, 회원·리포트 누적 대비).
- **예상 비용**: Hobby 기준 월 ~$5 (사용량 기반). 트래픽 적으면 그 이하.
- **자동배포**: `main` push → Railway 자동 재빌드·재배포. 볼륨 데이터는 유지.

---

## 자동 백업 (SQLite → S3 호환 스토리지)

볼륨은 단일 실패점이라, 회원·리포트 보존을 위해 **매일 `app.db` 스냅샷을 오브젝트 스토리지로 백업**합니다. env 설정 시 앱 내장 루프가 자동 실행(하루 1회), 미설정 시 비활성.

**추천 스토리지: Cloudflare R2** (무료 10GB, S3 호환) 또는 AWS S3 / Supabase Storage.

```
BACKUP_S3_BUCKET=signalapt-backup
BACKUP_S3_KEY_ID=...
BACKUP_S3_SECRET=...
BACKUP_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com   # R2/Supabase 등. AWS S3면 생략
BACKUP_S3_REGION=auto
```
- 온라인 백업(`.backup`)이라 **운영 중에도 안전**. gzip 압축(현재 ≈0.5MB), 최근 14개 유지·자동 정리.
- 수동 실행: `signal backup` (cron에 걸어도 됨).
- **복구**: 스토리지에서 최신 `signalapt/app-*.db.gz` 내려받아 `gunzip` → 볼륨의 `data/cache/app.db` 로 교체 후 재시작.

## Fly.io 로 옮기려면 (참고)
동일 `Dockerfile` 재사용. `fly launch --no-deploy` → `fly volumes create data --size 3` → `fly.toml`에 `[mounts] source="data" destination="/app/data"` + `[env] HOST="0.0.0.0"` → `fly secrets set KEY=..` → `fly deploy`. (scale-to-zero로 유휴 비용 절감 가능)
