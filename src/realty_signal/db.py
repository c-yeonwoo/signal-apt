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


# ---------- kv (범용 JSON 캐시 — 비개인화 계산결과 영구 저장) ----------
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
