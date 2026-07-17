"""SQLite 통합 저장소 (data/cache/app.db).

저장 전략 분리:
  - parquet  : 분석 시계열 (KB 주간 long, 입주물량 supply, 저평가 locality)
  - JSON     : 소형 파생·휘발성 (codes, macro, volume, quicksale)
  - SQLite   : 키-값/대용량/외부조회 캐시 ← 이 파일
      geocode        주소·단지명 → 좌표 (Nominatim 캐시)
      region_geo     시군구 → 중심좌표
      redev_progress 서울 정비사업 추진경과 (≈3만 행, sgg5 인덱스)
      building       건축물대장 표제부 (지번 키, 외부조회 캐시)

기존 파일(geocode.db, region_geo.json, redev_progress.json)은 최초 접속 시 자동 이관.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB = Path("data/cache/app.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS geocode(q TEXT PRIMARY KEY, lat REAL, lng REAL, ts INTEGER);
CREATE TABLE IF NOT EXISTS region_geo(region TEXT PRIMARY KEY, lat REAL, lng REAL, ts INTEGER);
CREATE TABLE IF NOT EXISTS redev_progress(biz TEXT, sgg5 TEXT, stage TEXT, cd INTEGER, day TEXT);
CREATE INDEX IF NOT EXISTS ix_redev_sgg ON redev_progress(sgg5);
CREATE TABLE IF NOT EXISTS building(k TEXT PRIMARY KEY, vlrat REAL, bcrat REAL,
    useapr TEXT, hhld INTEGER, floors INTEGER, ts INTEGER);
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE, pwhash TEXT, created INTEGER);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, uid INTEGER, ts INTEGER);
CREATE TABLE IF NOT EXISTS profile(uid INTEGER PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS favorites(uid INTEGER, kind TEXT, key TEXT, label TEXT, ts INTEGER,
    PRIMARY KEY(uid, kind, key));
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS news(link TEXT PRIMARY KEY, title TEXT, descr TEXT,
    source TEXT, topic TEXT, pubdate TEXT, ts INTEGER);
CREATE INDEX IF NOT EXISTS ix_news_ts ON news(ts);
CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, category TEXT, region TEXT, tags TEXT, source TEXT, eff_date TEXT, body TEXT, ts INTEGER);
CREATE INDEX IF NOT EXISTS ix_policy_ts ON policy(ts);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER, name TEXT, props TEXT, ts INTEGER);
CREATE INDEX IF NOT EXISTS ix_events_name_ts ON events(name, ts);
CREATE INDEX IF NOT EXISTS ix_events_uid_ts ON events(uid, ts);
CREATE TABLE IF NOT EXISTS nbhd_snap(uid INTEGER, region TEXT, week TEXT, data TEXT, ts INTEGER,
    PRIMARY KEY(uid, region, week));
CREATE INDEX IF NOT EXISTS ix_nbhd_snap_uid_region ON nbhd_snap(uid, region, ts);
"""

_migrated = [False]


def conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript(_SCHEMA)
    _migrate(c)
    return c


def _empty(c: sqlite3.Connection, table: str) -> bool:
    return c.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is None


def _migrate(c: sqlite3.Connection) -> None:
    if _migrated[0]:
        return
    _migrated[0] = True
    d = DB.parent
    # geocode.db → geocode
    old = d / "geocode.db"
    if old.exists() and _empty(c, "geocode"):
        try:
            o = sqlite3.connect(old)
            c.executemany("INSERT OR IGNORE INTO geocode VALUES(?,?,?,?)",
                          list(o.execute("SELECT q,lat,lng,ts FROM geo")))
            o.close()
        except Exception:
            pass
    # region_geo.json → region_geo
    rg = d / "region_geo.json"
    if rg.exists() and _empty(c, "region_geo"):
        try:
            for region, v in json.loads(rg.read_text(encoding="utf-8")).items():
                if v:
                    c.execute("INSERT OR IGNORE INTO region_geo VALUES(?,?,?,?)", (region, v[0], v[1], 0))
        except Exception:
            pass
    # redev_progress.json → redev_progress
    rp = d / "redev_progress.json"
    if rp.exists() and _empty(c, "redev_progress"):
        try:
            rows = json.loads(rp.read_text(encoding="utf-8"))
            c.executemany("INSERT INTO redev_progress(biz,sgg5,stage,cd,day) VALUES(?,?,?,?,?)",
                          [(r["biz"], r["sgg5"], r["단계"], r["cd"], r["day"]) for r in rows])
        except Exception:
            pass
    c.commit()


# ---------- geocode ----------
def geo_get_many(queries: list[str]) -> dict[str, list]:
    if not queries:
        return {}
    c = conn()
    out, qs = {}, list({q for q in queries if q})
    for i in range(0, len(qs), 400):
        chunk = qs[i:i + 400]
        ph = ",".join("?" * len(chunk))
        for q, lat, lng in c.execute(
                f"SELECT q,lat,lng FROM geocode WHERE q IN ({ph}) AND lat IS NOT NULL", chunk):
            out[q] = [lat, lng]
    c.close()
    return out


def geo_get(q: str):
    c = conn()
    row = c.execute("SELECT lat,lng FROM geocode WHERE q=?", (q,)).fetchone()
    c.close()
    return row  # None=미조회, (None,None)=조회했으나 실패, (lat,lng)=성공


def geo_set(q: str, lat, lng) -> None:
    c = conn()
    c.execute("INSERT OR REPLACE INTO geocode(q,lat,lng,ts) VALUES(?,?,?,?)",
              (q, lat, lng, int(time.time())))
    c.commit()
    c.close()


# ---------- region_geo ----------
def region_get(region: str):
    c = conn()
    row = c.execute("SELECT lat,lng FROM region_geo WHERE region=?", (region,)).fetchone()
    c.close()
    return list(row) if row and row[0] is not None else None


def region_set(region: str, coord) -> None:
    c = conn()
    c.execute("INSERT OR REPLACE INTO region_geo(region,lat,lng,ts) VALUES(?,?,?,?)",
              (region, coord[0] if coord else None, coord[1] if coord else None, int(time.time())))
    c.commit()
    c.close()


# ---------- redev_progress ----------
def redev_rows(sgg5: str | None = None) -> list[dict]:
    c = conn()
    if sgg5:
        cur = c.execute("SELECT biz,sgg5,stage,cd,day FROM redev_progress WHERE sgg5=?", (sgg5,))
    else:
        cur = c.execute("SELECT biz,sgg5,stage,cd,day FROM redev_progress")
    rows = [{"biz": b, "sgg5": s, "단계": st, "cd": cd, "day": d} for b, s, st, cd, d in cur]
    c.close()
    return rows


def redev_count() -> int:
    c = conn()
    n = c.execute("SELECT COUNT(*) FROM redev_progress").fetchone()[0]
    c.close()
    return n


def redev_replace(rows: list[dict]) -> None:
    c = conn()
    c.execute("DELETE FROM redev_progress")
    c.executemany("INSERT INTO redev_progress(biz,sgg5,stage,cd,day) VALUES(?,?,?,?,?)",
                  [(r["biz"], r["sgg5"], r["단계"], r["cd"], r["day"]) for r in rows])
    c.commit()
    c.close()


# ---------- building (건축물대장 캐시) ----------
def building_get(key: str):
    c = conn()
    row = c.execute("SELECT vlrat,bcrat,useapr,hhld,floors FROM building WHERE k=?", (key,)).fetchone()
    c.close()
    if row is None:
        return None
    return {"용적률": row[0], "건폐율": row[1], "사용승인일": row[2], "세대수": row[3], "최고층": row[4]}


def building_set(key: str, b: dict | None) -> None:
    c = conn()
    b = b or {}
    c.execute("INSERT OR REPLACE INTO building(k,vlrat,bcrat,useapr,hhld,floors,ts) VALUES(?,?,?,?,?,?,?)",
              (key, b.get("용적률"), b.get("건폐율"), b.get("사용승인일"), b.get("세대수"),
               b.get("최고층"), int(time.time())))
    c.commit()
    c.close()


# ---------- users / sessions ----------
def user_create(email: str, pwhash: str) -> int | None:
    c = conn()
    try:
        cur = c.execute("INSERT INTO users(email,pwhash,created) VALUES(?,?,?)",
                        (email.lower().strip(), pwhash, int(time.time())))
        c.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None  # 이미 가입된 이메일
    finally:
        c.close()


def user_by_email(email: str):
    c = conn()
    row = c.execute("SELECT id,email,pwhash FROM users WHERE email=?", (email.lower().strip(),)).fetchone()
    c.close()
    return {"id": row[0], "email": row[1], "pwhash": row[2]} if row else None


def user_set_pwhash(uid: int, pwhash: str) -> bool:
    """비밀번호 해시 갱신. 대상 없으면 False."""
    c = conn()
    cur = c.execute("UPDATE users SET pwhash=? WHERE id=?", (pwhash, uid))
    c.commit()
    n = cur.rowcount
    c.close()
    return n > 0


def session_create(token: str, uid: int) -> None:
    c = conn()
    c.execute("INSERT OR REPLACE INTO sessions(token,uid,ts) VALUES(?,?,?)", (token, uid, int(time.time())))
    c.commit()
    c.close()


def session_user(token: str):
    if not token:
        return None
    c = conn()
    row = c.execute("SELECT u.id,u.email FROM sessions s JOIN users u ON u.id=s.uid WHERE s.token=?",
                    (token,)).fetchone()
    c.close()
    return {"id": row[0], "email": row[1]} if row else None


def session_delete(token: str) -> None:
    c = conn()
    c.execute("DELETE FROM sessions WHERE token=?", (token,))
    c.commit()
    c.close()


# ---------- profile (uid → JSON) ----------
def profile_get(uid: int) -> dict:
    c = conn()
    row = c.execute("SELECT data FROM profile WHERE uid=?", (uid,)).fetchone()
    c.close()
    return json.loads(row[0]) if row and row[0] else {}


def profile_set(uid: int, data: dict) -> None:
    c = conn()
    c.execute("INSERT OR REPLACE INTO profile(uid,data) VALUES(?,?)",
              (uid, json.dumps(data, ensure_ascii=False)))
    c.commit()
    c.close()


# ---------- favorites ----------
def fav_list(uid: int) -> list[dict]:
    c = conn()
    rows = c.execute("SELECT kind,key,label FROM favorites WHERE uid=? ORDER BY ts DESC", (uid,)).fetchall()
    c.close()
    return [{"kind": k, "key": key, "label": lb} for k, key, lb in rows]


def fav_add(uid: int, kind: str, key: str, label: str) -> None:
    c = conn()
    c.execute("INSERT OR REPLACE INTO favorites(uid,kind,key,label,ts) VALUES(?,?,?,?,?)",
              (uid, kind, key, label, int(time.time())))
    c.commit()
    c.close()


def fav_remove(uid: int, kind: str, key: str) -> None:
    c = conn()
    c.execute("DELETE FROM favorites WHERE uid=? AND kind=? AND key=?", (uid, kind, key))
    c.commit()
    c.close()


def all_fav_complexes() -> list[tuple[str, str]]:
    """전체 사용자 관심단지(kind='complex', key='region|name') → (region, name) 중복 제거. 주간 프리페치용."""
    c = conn()
    rows = c.execute("SELECT DISTINCT key FROM favorites WHERE kind='complex'").fetchall()
    c.close()
    out = []
    for (key,) in rows:
        if key and "|" in key:
            region, name = key.split("|", 1)
            out.append((region, name))
    return out


def all_fav_regions() -> list[str]:
    """전체 사용자 관심지역(kind='region') → 지역명 중복 제거. 급매 스캔 커버리지 확대용."""
    c = conn()
    rows = c.execute("SELECT DISTINCT key FROM favorites WHERE kind='region'").fetchall()
    c.close()
    return [k for (k,) in rows if k]


def users_with_region_favs() -> list[dict]:
    """관심지역(kind=region)이 1개 이상인 유저 목록. 주간 다이제스트용."""
    c = conn()
    rows = c.execute(
        "SELECT u.id, u.email, GROUP_CONCAT(f.key, '|') "
        "FROM users u JOIN favorites f ON f.uid=u.id AND f.kind='region' "
        "GROUP BY u.id, u.email"
    ).fetchall()
    c.close()
    out = []
    for uid, email, keys in rows:
        regions = [k for k in (keys or "").split("|") if k]
        if regions:
            out.append({"id": uid, "email": email, "regions": regions})
    return out


# ---------- funnel events ----------
_ALLOWED_EVENTS = frozenset({
    "signup", "profile_complete", "fav_add", "report_open", "nick_ask", "nbhd_open",
    "listing_detail_open", "listing_click", "timing_card_expand", "alert_feedback",
})


def event_log(uid: int | None, name: str, props: dict | None = None) -> bool:
    """퍼널 이벤트 1건 기록. 허용 이름만 저장. uid 없으면 스킵."""
    if not uid or name not in _ALLOWED_EVENTS:
        return False
    c = conn()
    c.execute(
        "INSERT INTO events(uid,name,props,ts) VALUES(?,?,?,?)",
        (uid, name, json.dumps(props or {}, ensure_ascii=False), int(time.time())),
    )
    c.commit()
    c.close()
    return True


def event_counts(days: int = 30) -> list[dict]:
    """최근 N일 이벤트명별 건수 (관리·검증용)."""
    since = int(time.time()) - max(1, days) * 86400
    c = conn()
    rows = c.execute(
        "SELECT name, COUNT(*) FROM events WHERE ts>=? GROUP BY name ORDER BY COUNT(*) DESC",
        (since,),
    ).fetchall()
    c.close()
    return [{"name": n, "count": cnt} for n, cnt in rows]


def _iso_week() -> str:
    import datetime as _dt
    return _dt.date.today().strftime("%G-W%V")


def usage_get(uid: int, kind: str) -> int:
    """주간 사용량(kind=nick|report)."""
    v = kv_get(f"usage:{kind}:{uid}:{_iso_week()}")
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def usage_inc(uid: int, kind: str) -> int:
    """주간 사용량 +1 후 현재값."""
    key = f"usage:{kind}:{uid}:{_iso_week()}"
    n = usage_get(uid, kind) + 1
    kv_set(key, n)
    return n


# ---------- 동네 리포트 스냅샷 (주간 diff·비교) ----------
def nbhd_snap_save(uid: int, region: str, week: str, data: dict) -> None:
    c = conn()
    c.execute(
        "INSERT OR REPLACE INTO nbhd_snap(uid,region,week,data,ts) VALUES(?,?,?,?,?)",
        (uid, region, week, json.dumps(data, ensure_ascii=False), int(time.time())),
    )
    c.commit()
    c.close()


def nbhd_snap_get(uid: int, region: str, week: str) -> dict | None:
    c = conn()
    row = c.execute(
        "SELECT data FROM nbhd_snap WHERE uid=? AND region=? AND week=?",
        (uid, region, week),
    ).fetchone()
    c.close()
    return json.loads(row[0]) if row and row[0] else None


def nbhd_snap_prev(uid: int, region: str, before_week: str) -> dict | None:
    """before_week 이전 가장 최근 스냅샷 {week, data}."""
    c = conn()
    row = c.execute(
        "SELECT week, data FROM nbhd_snap WHERE uid=? AND region=? AND week<? "
        "ORDER BY week DESC LIMIT 1",
        (uid, region, before_week),
    ).fetchone()
    c.close()
    if not row:
        return None
    return {"week": row[0], "data": json.loads(row[1]) if row[1] else {}}


def nbhd_snap_weeks(uid: int, region: str, limit: int = 8) -> list[str]:
    c = conn()
    rows = c.execute(
        "SELECT week FROM nbhd_snap WHERE uid=? AND region=? ORDER BY week DESC LIMIT ?",
        (uid, region, limit),
    ).fetchall()
    c.close()
    return [w for (w,) in rows]


# ---------- alert prefs (kv) ----------
def alert_prefs_get(uid: int) -> dict:
    v = kv_get(f"alert_prefs:{uid}")
    return v if isinstance(v, dict) else {}


def alert_prefs_set(uid: int, prefs: dict) -> dict:
    from realty_signal.brain.alerts import merge_prefs
    clean = merge_prefs(prefs)
    kv_set(f"alert_prefs:{uid}", clean)
    return clean


def kv_get(k: str, max_age: int | None = None):
    """캐시 값(JSON 역직렬화). 없거나 max_age(초) 초과 시 None."""
    c = conn()
    row = c.execute("SELECT v, ts FROM kv WHERE k=?", (k,)).fetchone()
    c.close()
    if not row:
        return None
    if max_age is not None and (time.time() - row[1]) > max_age:
        return None
    return json.loads(row[0])


def kv_set(k: str, v) -> None:
    c = conn()
    c.execute("INSERT OR REPLACE INTO kv(k,v,ts) VALUES(?,?,?)",
              (k, json.dumps(v, ensure_ascii=False), int(time.time())))
    c.commit()
    c.close()


def kv_keys(prefix: str) -> list[str]:
    c = conn()
    rows = c.execute("SELECT k FROM kv WHERE k LIKE ?", (prefix + "%",)).fetchall()
    c.close()
    return [r[0] for r in rows]


def kv_ts(k: str) -> int | None:
    """캐시 키의 마지막 기록 시각(unix). 없으면 None. (데이터 신선도 표시용)"""
    c = conn()
    row = c.execute("SELECT ts FROM kv WHERE k=?", (k,)).fetchone()
    c.close()
    return row[0] if row else None


def kv_max_ts(prefix: str) -> int | None:
    """prefix 로 시작하는 캐시 키들 중 가장 최근 기록 시각(unix). 없으면 None."""
    c = conn()
    row = c.execute("SELECT MAX(ts) FROM kv WHERE k LIKE ?", (prefix + "%",)).fetchone()
    c.close()
    return row[0] if row and row[0] is not None else None


# ---------- 정책 KB (뉴스로 안 잡히는 제도·개발계획을 큐레이션) ----------
def _policy_row(r) -> dict:
    return {"id": r[0], "title": r[1], "category": r[2], "region": r[3],
            "tags": r[4], "source": r[5], "eff_date": r[6], "body": r[7], "ts": r[8]}


def policy_add(title: str, body: str, category: str = "", region: str = "",
               tags: str = "", source: str = "", eff_date: str = "") -> int:
    c = conn()
    cur = c.execute(
        "INSERT INTO policy(title,category,region,tags,source,eff_date,body,ts) VALUES(?,?,?,?,?,?,?,?)",
        (title, category, region, tags, source, eff_date, body, int(time.time())))
    c.commit()
    rid = cur.lastrowid
    c.close()
    return rid


def policy_all(limit: int = 200) -> list[dict]:
    c = conn()
    rows = c.execute("SELECT id,title,category,region,tags,source,eff_date,body,ts "
                     "FROM policy ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [_policy_row(r) for r in rows]


def policy_delete(pid: int) -> None:
    c = conn()
    c.execute("DELETE FROM policy WHERE id=?", (pid,))
    c.commit()
    c.close()


def policy_count() -> int:
    c = conn()
    n = c.execute("SELECT COUNT(*) FROM policy").fetchone()[0]
    c.close()
    return n


# 동의어·별칭 그룹 — 사용자 표현과 KB 용어의 간극을 메워 리콜↑ (임베딩 없이 시맨틱 근접)
_POLICY_SYN = [
    {"dsr", "총부채", "원리금", "대출한도", "한도", "스트레스dsr", "상환능력"},
    {"ltv", "담보인정", "자기자본", "계약금", "대출비율"},
    {"gtx", "광역급행", "지하철", "전철", "교통", "역세권", "노선"},
    {"신도시", "택지", "공급", "분양", "사전청약", "본청약", "3기"},
    {"재건축", "재개발", "정비사업", "안전진단", "재초환", "초과이익", "리모델링"},
    {"규제", "규제지역", "조정대상", "투기과열", "완화", "해제"},
    {"금리", "기준금리", "이자", "대출금리"},
    {"청약", "가점", "특별공급", "특공", "생애최초"},
]


def _tok(s: str) -> set[str]:
    """한국어 토큰 집합 — 단어(영문·숫자·한글 런) + 한글 2-gram. 부분일치·표기차 흡수."""
    import re
    s = (s or "").lower()
    out: set[str] = set()
    for w in re.findall(r"[a-z0-9]+|[가-힣]+", s):
        if len(w) >= 2:
            out.add(w)
        if re.match(r"[가-힣]+", w) and len(w) >= 2:
            for i in range(len(w) - 1):        # 한글 2-gram
                out.add(w[i:i + 2])
    return out


def _expand(toks: set[str]) -> set[str]:
    """동의어 그룹에 걸리면 그룹 전체를 쿼리에 추가."""
    ex = set(toks)
    for g in _POLICY_SYN:
        if toks & g:
            ex |= g
    return ex


def policy_search(query: str, region: str = "", limit: int = 5) -> list[dict]:
    """한국어 토큰화 + 동의어 확장 + BM25형 필드가중 검색. 쿼리 비면 최근순."""
    rows = policy_all(200)
    q = (query or "").strip()
    if not q and not region:
        return rows[:limit]
    import math
    qtoks = _expand(_tok(q))
    if not qtoks and not region:
        return rows[:limit]
    # idf — 여러 문서에 흔한 토큰은 가중치↓
    N = max(1, len(rows))
    df: dict[str, int] = {}
    doc_tok = []
    for r in rows:
        title_t = _tok(r["title"])
        meta_t = _tok((r["tags"] or "") + " " + (r["region"] or "") + " " + (r["category"] or ""))
        body_t = _tok(r["body"])
        allt = title_t | meta_t | body_t
        doc_tok.append((title_t, meta_t, body_t))
        for t in allt:
            df[t] = df.get(t, 0) + 1
    scored = []
    for r, (title_t, meta_t, body_t) in zip(rows, doc_tok):
        s = 0.0
        for t in qtoks:
            if t not in title_t and t not in meta_t and t not in body_t:
                continue
            idf = math.log(1 + N / (1 + df.get(t, 0)))
            w = 3 if t in title_t else (2 if t in meta_t else 1)
            s += w * idf
        if region and (region in (r["region"] or "") or (r["region"] or "") in region or not r["region"]):
            s += 2.0
        if s > 0:
            scored.append((s, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = [r for _, r in scored[:limit]]
    return out or rows[:limit]


# ---------- news (부동산 뉴스 KB — 누적) ----------
def news_upsert(items: list[dict]) -> int:
    """뉴스 항목 누적(link PK 중복 무시). 새로 추가된 건수 반환."""
    if not items:
        return 0
    c = conn()
    before = c.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    c.executemany("INSERT OR IGNORE INTO news(link,title,descr,source,topic,pubdate,ts) "
                  "VALUES(?,?,?,?,?,?,?)",
                  [(i["link"], i.get("title"), i.get("descr"), i.get("source"),
                    i.get("topic"), i.get("pubdate"), int(time.time())) for i in items])
    c.commit()
    added = c.execute("SELECT COUNT(*) FROM news").fetchone()[0] - before
    c.close()
    return added


def news_list(topic: str | None = None, limit: int = 60) -> list[dict]:
    c = conn()
    if topic and topic != "전체":
        cur = c.execute("SELECT link,title,descr,source,topic,pubdate FROM news WHERE topic=? "
                        "ORDER BY pubdate DESC, ts DESC LIMIT ?", (topic, limit))
    else:
        cur = c.execute("SELECT link,title,descr,source,topic,pubdate FROM news "
                        "ORDER BY pubdate DESC, ts DESC LIMIT ?", (limit,))
    rows = [{"link": l, "title": t, "descr": d, "source": s, "topic": tp, "pubdate": pd}
            for l, t, d, s, tp, pd in cur]
    c.close()
    return rows


def news_since(topic: str | None, days: int = 30, limit: int = 40) -> list[dict]:
    """최근 days일 내 수집(ts 기준) 뉴스 — AI 요약용."""
    since = int(time.time()) - days * 86400
    c = conn()
    if topic and topic != "전체":
        cur = c.execute("SELECT title,descr,source,topic,pubdate FROM news WHERE topic=? AND ts>=? "
                        "ORDER BY ts DESC LIMIT ?", (topic, since, limit))
    else:
        cur = c.execute("SELECT title,descr,source,topic,pubdate FROM news WHERE ts>=? "
                        "ORDER BY ts DESC LIMIT ?", (since, limit))
    rows = [{"title": t, "descr": d, "source": s, "topic": tp, "pubdate": pd}
            for t, d, s, tp, pd in cur]
    c.close()
    return rows


def news_recent_for_ai(limit: int = 15) -> list[dict]:
    """AI 리포트용 최근 뉴스 요약(제목+토픽) — 정책·시장 맥락 주입용."""
    return [{"title": n["title"], "topic": n["topic"], "date": n["pubdate"]}
            for n in news_list(None, limit)]
