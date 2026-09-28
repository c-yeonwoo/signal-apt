"""학구도안내서비스의 공개 SHP/CSV를 가져와 좌표별 통학구역 후보를 찾는다.

판정은 원본 투영 좌표의 다각형으로 한다. 매물 위치가 출입구/건물로
확인되지 않았으므로 결과는 배정 학교가 아닌 *표시 좌표 기준 후보*다.
"""

from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import tempfile
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

import shapefile
from pyproj import CRS, Transformer

from realty_signal import jsonx, store

BASE = "https://schoolzone.emac.kr"
INDEX = BASE + "/publicData/publicDataList.do"
FILE = BASE + "/publicData/publicDataFileDownload.do"
DB_PATH = store.CACHE_DIR / "school_zones.sqlite"
SOURCE_PAGE = "https://schoolzone.emac.kr/publicData/publicDataList.do"
_TITLES = {"zone": "초등학교 통학구역 및 공동통학구역",
           "school": "초중고 학교 위치", "link": "학교-학구도 연계정보"}


def _download(url: str, limit: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "realty-signal/1.0"})
    with urllib.request.urlopen(req, timeout=45) as response:  # noqa: S310 - fixed official host
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("schoolzone response too large")
    return raw


def discover(page: str) -> dict:
    """공식 목록의 가장 최신 파일 3개를 찾는다. 파일 ID만 URL에 사용한다."""
    result = {}
    for row in re.findall(r"<tr\b[^>]*>[\s\S]*?</tr>", page, flags=re.I):
        for kind, title in _TITLES.items():
            if kind in result or title not in row:
                continue
            match = re.search(r'data-nttId="(\d+)"\s+data-atchFileId="(FILE_[A-Za-z0-9_]+)"\s+data-fileSn="(\d+)"', row)
            if match:
                result[kind] = {"nttId": match[1], "atchFileId": match[2], "fileSn": match[3]}
    if result.keys() != _TITLES.keys():
        raise ValueError("schoolzone source files not found")
    return result


def _archive(meta: dict, limit: int) -> zipfile.ZipFile:
    url = FILE + "?" + urllib.parse.urlencode(meta)
    return zipfile.ZipFile(io.BytesIO(_download(url, limit)))


def _file(z: zipfile.ZipFile, suffix: str) -> bytes:
    matches = [n for n in z.namelist() if n.lower().endswith(suffix.lower())]
    if len(matches) != 1:
        raise ValueError(f"schoolzone archive missing {suffix}")
    return z.read(matches[0])


def _csv_rows(z: zipfile.ZipFile, encoding: str):
    raw = _file(z, ".csv")
    return csv.DictReader(io.StringIO(raw.decode(encoding)))


def _existing_version(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        with sqlite3.connect(path) as c:
            return c.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0]
    except (sqlite3.Error, IndexError, TypeError):
        return None


def refresh(path: Path = DB_PATH) -> dict:
    """버전이 달라졌을 때만 다운로드·구축하고 원자적으로 교체한다."""
    source = discover(_download(INDEX, 2_000_000).decode("utf-8", "replace"))
    version = ":".join(source[k]["nttId"] for k in ("zone", "school", "link"))
    if _existing_version(path) == version:
        return {"ok": True, "status": "unchanged", "version": version}
    zones = _archive(source["zone"], 60_000_000)
    schools = _archive(source["school"], 8_000_000)
    links = _archive(source["link"], 8_000_000)
    shp = shapefile.Reader(shp=io.BytesIO(_file(zones, ".shp")),
                           shx=io.BytesIO(_file(zones, ".shx")),
                           dbf=io.BytesIO(_file(zones, ".dbf")), encoding="euc-kr")
    crs_wkt = _file(zones, ".prj").decode("utf-8")
    CRS.from_wkt(crs_wkt)  # 배포된 좌표계를 확인하지 못하면 가져오기를 실패시킨다.
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix="school-zones-", suffix=".sqlite",
                                         dir=path.parent, delete=False)
    temp = Path(handle.name)
    handle.close()
    try:
        with sqlite3.connect(temp) as c:
            c.executescript("""
                CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE zones(id INTEGER PRIMARY KEY, zone_id TEXT, name TEXT,
                    kind TEXT, asof TEXT, geom BLOB NOT NULL);
                CREATE INDEX zones_zone_id ON zones(zone_id);
                CREATE VIRTUAL TABLE boxes USING rtree(id,minx,maxx,miny,maxy);
                CREATE TABLE schools(id TEXT PRIMARY KEY, name TEXT, lat REAL, lng REAL);
                CREATE TABLE zone_schools(zone_id TEXT, school_id TEXT,
                    PRIMARY KEY(zone_id,school_id));
                CREATE INDEX zone_schools_zone ON zone_schools(zone_id);
            """)
            c.executemany("INSERT INTO meta VALUES(?,?)", [("version", version), ("crs_wkt", crs_wkt),
                            ("source", SOURCE_PAGE)])
            for row in _csv_rows(schools, "utf-8-sig"):
                try:
                    lat, lng = float(row["위도"]), float(row["경도"])
                except (ValueError, KeyError):
                    continue
                if 33 <= lat <= 39 and 124 <= lng <= 132:
                    c.execute("INSERT OR REPLACE INTO schools VALUES(?,?,?,?)",
                              (row["학교ID"], row["학교명"], lat, lng))
            for row in _csv_rows(links, "euc-kr"):
                if row.get("학구ID") and row.get("학교ID"):
                    c.execute("INSERT OR IGNORE INTO zone_schools VALUES(?,?)",
                              (row["학구ID"], row["학교ID"]))
            count = 0
            for shape_row in shp.iterShapeRecords():
                record = shape_row.record.as_dict()
                shape = shape_row.shape
                if not shape.points or not shape.bbox or not record.get("HAKGUDO_ID"):
                    continue
                geometry = zlib.compress(jsonx.dumps({"points": shape.points,
                    "parts": list(shape.parts)}, separators=(",", ":")).encode(), level=6)
                cursor = c.execute("INSERT INTO zones(zone_id,name,kind,asof,geom) VALUES(?,?,?,?,?)",
                                   (record["HAKGUDO_ID"], record.get("HAKGUDO_NM"),
                                    record.get("HAKGUDO_GB"), record.get("BASE_DT"), geometry))
                minx, miny, maxx, maxy = shape.bbox
                c.execute("INSERT INTO boxes VALUES(?,?,?,?,?)", (cursor.lastrowid, minx, maxx, miny, maxy))
                count += 1
            if not count or c.execute("SELECT COUNT(*) FROM schools").fetchone()[0] == 0:
                raise ValueError("schoolzone archive has no usable records")
            c.commit()
        os.replace(temp, path)
        return {"ok": True, "status": "updated", "version": version, "zones": count}
    finally:
        if temp.exists():
            temp.unlink()


def _inside_ring(x: float, y: float, ring: list) -> tuple[bool, bool]:
    inside = near = False
    for i in range(len(ring)):
        ax, ay = ring[i-1]
        bx, by = ring[i]
        dx, dy = bx-ax, by-ay
        span = dx*dx + dy*dy
        if span:
            u = max(0, min(1, ((x-ax)*dx+(y-ay)*dy)/span))
            near |= (x-ax-u*dx)**2 + (y-ay-u*dy)**2 <= 100  # 경계 10m 이내
        if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside = not inside
    return inside, near


def _contains(x: float, y: float, geometry: dict) -> tuple[bool, bool]:
    points, parts = geometry["points"], geometry["parts"]
    inside = near = False
    for i, start in enumerate(parts):
        ring = points[start:parts[i+1] if i+1 < len(parts) else len(points)]
        hit, edge = _inside_ring(x, y, ring)
        inside ^= hit  # SHP의 외곽/구멍을 짝수·홀수 규칙으로 처리
        near |= edge
    return inside, near


def at_point(lat: float, lng: float, path: Path = DB_PATH) -> dict:
    """좌표가 속한 학구와 연결 학교를 반환한다. 배정 확정은 하지 않는다."""
    if not path.exists():
        return {"status": "unavailable", "reason": "학구도 자료를 준비 중입니다.", "zones": []}
    if not 33 <= lat <= 39 or not 124 <= lng <= 132:
        return {"status": "invalid_coordinate", "zones": []}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as c:
        wkt = c.execute("SELECT value FROM meta WHERE key='crs_wkt'").fetchone()[0]
        x, y = Transformer.from_crs("EPSG:4326", CRS.from_wkt(wkt), always_xy=True).transform(lng, lat)
        rows = c.execute("SELECT z.zone_id,z.name,z.kind,z.asof,z.geom FROM zones z JOIN boxes b ON z.id=b.id "
                         "WHERE b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=?", (x+10, x-10, y+10, y-10))
        found = []
        for zone_id, name, kind, asof, blob in rows:
            geometry = jsonx.loads(zlib.decompress(blob))
            inside, near = _contains(x, y, geometry)
            if not inside and not near:
                continue
            schools = [dict(zip(("id", "name", "lat", "lng"), school)) for school in c.execute(
                "SELECT s.id,s.name,s.lat,s.lng FROM schools s JOIN zone_schools zs ON zs.school_id=s.id "
                "WHERE zs.zone_id=? ORDER BY s.name", (zone_id,))]
            found.append({"id": zone_id, "name": name, "kind": kind, "asof": asof,
                          "boundary_near": near, "schools": schools})
    if not found:
        return {"status": "no_match", "reason": "해당 좌표의 통학구역을 찾지 못했습니다.", "zones": []}
    return {"status": "candidate", "reason": "매물 표시 좌표 기준 후보입니다. 실제 주소와 관할 교육청에서 확인하세요.",
            "boundary_near": any(z["boundary_near"] for z in found),
            "zones": found, "source": SOURCE_PAGE}
