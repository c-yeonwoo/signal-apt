"""Generate the lightweight Seoul/Incheon/Gyeonggi boundary bundle from a dated SGG GeoJSON.

Input: mapcn-kr data/sgg.json derived from SGIS admdongkor ver20260701.
The source revision and government reform reference are recorded in the output metadata.
This is an explicit data update, never a request-time network dependency.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SOURCE_COMMIT = "06c7a3456dd82965bd29042130441515717519ab"
SOURCE_URL = f"https://github.com/DevMinGeonPark/mapcn-kr/blob/{SOURCE_COMMIT}/data/sgg.json"
REFORM_URL = "https://www.incheon.go.kr/IC01070101"
SIDO = {"11", "28", "41"}
REQUIRED_INC = {"제물포구": "28125", "영종구": "28155", "미추홀구": "28177",
                "서해구": "28275", "검단구": "28290"}


def _display_name(name: str) -> str:
    # SGIS 시·구 결합명은 KB의 공백 있는 시·구 표시명에 맞춘다.
    return re.sub(r"^(.*?시)\s*(.+구)$", r"\1 \2", name)


def transform(source: dict) -> dict:
    if source.get("type") != "FeatureCollection" or not isinstance(source.get("features"), list):
        raise ValueError("not_a_feature_collection")
    features, seen = [], set()
    for feature in source["features"]:
        props = feature.get("properties") or {}
        sido, code, name = str(props.get("sido") or ""), str(props.get("sgg") or ""), props.get("sggnm")
        if sido not in SIDO:
            continue
        if (not isinstance(name, str) or not name or len(code) != 5 or not code.startswith(sido)
                or code in seen or feature.get("geometry", {}).get("type") not in {"Polygon", "MultiPolygon"}):
            raise ValueError(f"invalid_feature:{code}")
        seen.add(code)
        features.append({"type": "Feature", "properties": {"name": _display_name(name), "sido": sido, "code": code},
                         "geometry": feature["geometry"]})
    by_name = {f["properties"]["name"]: f["properties"]["code"] for f in features
               if f["properties"]["sido"] == "28"}
    if any(by_name.get(name) != code for name, code in REQUIRED_INC.items()):
        raise ValueError("missing_2026_incheon_districts")
    if any(name in by_name for name in ("중구", "동구", "남구", "서구")):
        raise ValueError("obsolete_incheon_districts")
    if not 75 <= len(features) <= 90:
        raise ValueError("unexpected_feature_count")
    return {"type": "FeatureCollection", "metadata": {"asof": "2026-07-01",
            "source": SOURCE_URL, "reform_reference": REFORM_URL,
            "note": "SGIS-derived simplified boundaries; not a cadastral or legal boundary survey."},
            "features": features}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "src/realty_signal/web/sudo_gu.geojson")
    args = parser.parse_args()
    data = transform(json.loads(args.source.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"updated {args.output}: {len(data['features'])} districts")


if __name__ == "__main__":
    main()
