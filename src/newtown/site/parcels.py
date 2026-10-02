"""사업구역 경계와 편입 필지 처리."""
from __future__ import annotations

import re
from typing import Callable

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry

from ..vworld.client import CONSTRAINT_LAYERS, LAYER_PARCEL, ZONING_LAYERS, VWorldClient

WGS84 = "EPSG:4326"
AREA_CRS = "EPSG:5186"  # 면적 계산용 (중부원점 TM)

JIMOK_ABBR = {
    "전": "전", "답": "답", "과": "과수원", "목": "목장용지", "임": "임야", "광": "광천지", "염": "염전",
    "대": "대", "장": "공장용지", "학": "학교용지", "차": "주차장", "주": "주유소용지", "창": "창고용지",
    "도": "도로", "철": "철도용지", "제": "제방", "천": "하천", "구": "구거", "유": "유지", "양": "양어장",
    "수": "수도용지", "공": "공원", "체": "체육용지", "원": "유원지", "종": "종교용지", "사": "사적지",
    "묘": "묘지", "잡": "잡종지",
}

PARCEL_COLUMNS = ["pnu", "addr", "jibun", "jimok", "area_reg", "area_geom", "incl_ratio", "area_incl",
                  "zone", "use", "owner", "price", "price_year", "included", "free", "note"]

COLUMN_LABELS = {
    "pnu": "PNU", "addr": "소재지", "jibun": "지번", "jimok": "지목", "area_reg": "공부면적(㎡)",
    "area_geom": "도형면적(㎡)", "incl_ratio": "편입비율", "area_incl": "편입면적(㎡)", "zone": "용도지역",
    "use": "이용상황", "owner": "소유구분", "price": "개별공시지가(원/㎡)", "price_year": "공시연도",
    "included": "편입", "free": "무상귀속", "note": "비고",
}


def boundary_shape(geojson_geom: dict) -> BaseGeometry:
    geom = shape(geojson_geom)
    return geom if geom.is_valid else geom.buffer(0)


def area_m2(geom: BaseGeometry) -> float:
    return float(gpd.GeoSeries([geom], crs=WGS84).to_crs(AREA_CRS).area.iloc[0])


def parse_jibun(jibun: str | None) -> tuple[str, str | None]:
    """'123-4 대' → ('123-4', '대'), '산 12임' → ('산 12', '임야')."""
    if not jibun:
        return "", None
    s = str(jibun).strip()
    m = re.match(r"^(.*?\d)\s*([가-힣])$", s)
    if m and m.group(2) in JIMOK_ABBR:
        return m.group(1).strip(), JIMOK_ABBR[m.group(2)]
    return s, None


def parcels_from_features(features: list[dict]) -> gpd.GeoDataFrame:
    """연속지적도(LP_PA_CBND_BUBUN) Feature 목록 → 필지 표."""
    if not features:
        return empty_parcels()
    gdf = gpd.GeoDataFrame.from_features(features, crs=WGS84)
    out = gpd.GeoDataFrame(geometry=gdf.geometry, crs=WGS84)
    out["pnu"] = gdf.get("pnu", pd.Series(dtype=str)).astype(str)
    out["addr"] = gdf.get("addr", "")
    parsed = gdf.get("jibun", pd.Series([""] * len(gdf))).map(parse_jibun)
    out["jibun"] = parsed.map(lambda t: t[0])
    out["jimok"] = parsed.map(lambda t: t[1])
    out["price"] = pd.to_numeric(gdf.get("jiga"), errors="coerce")
    out["price_year"] = gdf.get("gosi_year")
    return _fill_defaults(out.drop_duplicates("pnu").reset_index(drop=True))


def empty_parcels() -> gpd.GeoDataFrame:
    return _fill_defaults(gpd.GeoDataFrame({"pnu": pd.Series(dtype=str)}, geometry=gpd.GeoSeries([], crs=WGS84), crs=WGS84))


def _fill_defaults(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    defaults = {"addr": "", "jibun": "", "jimok": None, "area_reg": np.nan, "area_geom": np.nan,
                "incl_ratio": np.nan, "area_incl": np.nan, "zone": None, "use": None, "owner": None,
                "price": np.nan, "price_year": None, "included": True, "free": False, "note": ""}
    for col, val in defaults.items():
        if col not in gdf.columns:
            gdf[col] = val
    return gdf[PARCEL_COLUMNS + ["geometry"]]


def apply_inclusion(parcels: gpd.GeoDataFrame, boundary: BaseGeometry, min_ratio: float = 0.05) -> gpd.GeoDataFrame:
    """경계와의 교차 비율로 편입 여부·편입면적을 계산한다.

    편입면적 = 공부면적 × 편입비율. 공부면적이 없으면 도형면적을 쓴다.
    """
    if parcels.empty:
        return parcels
    p = parcels.to_crs(AREA_CRS)
    b = gpd.GeoSeries([boundary], crs=WGS84).to_crs(AREA_CRS).iloc[0]
    geom_area = p.geometry.area
    inter = p.geometry.buffer(0).intersection(b).area
    ratio = (inter / geom_area.where(geom_area > 0)).fillna(0).clip(0, 1)
    ratio = ratio.where(ratio < 0.99, 1.0)  # 측량 오차 수준의 차이는 전부편입으로 본다
    out = parcels.copy()
    out["area_geom"] = geom_area.round(1).values
    out["incl_ratio"] = ratio.round(4).values
    out = out[out["incl_ratio"] > 0].copy()
    out["area_reg"] = out["area_reg"].fillna(out["area_geom"])
    out["area_incl"] = (out["area_reg"] * out["incl_ratio"]).round(1)
    out["included"] = out["incl_ratio"] >= min_ratio
    return out.reset_index(drop=True)


def collect_parcels(client: VWorldClient, boundary: BaseGeometry, min_ratio: float = 0.05,
                    progress: Callable[[int, int], None] | None = None) -> gpd.GeoDataFrame:
    """VWorld 연속지적도에서 경계와 겹치는 필지를 수집한다."""
    feats = client.get_features_in_bounds(LAYER_PARCEL, boundary.bounds, id_field="pnu", progress=progress)
    return apply_inclusion(parcels_from_features(feats), boundary, min_ratio)


def enrich_zoning(client: VWorldClient, parcels: gpd.GeoDataFrame, boundary: BaseGeometry) -> gpd.GeoDataFrame:
    """용도지역·개발제한구역 레이어와 공간 결합해 필지의 용도지역을 채운다 (필지 내부 대표점 기준)."""
    if parcels.empty:
        return parcels
    out = parcels.copy()
    pts = gpd.GeoDataFrame({"idx": out.index}, geometry=out.geometry.representative_point(), crs=WGS84)
    for layer, fallback_name in {**ZONING_LAYERS, **CONSTRAINT_LAYERS}.items():
        feats = client.get_features_in_bounds(layer, boundary.bounds, tile_deg=0.05)
        if not feats:
            continue
        zg = gpd.GeoDataFrame.from_features(feats, crs=WGS84)
        zg["zname"] = zg["uname"] if "uname" in zg.columns else fallback_name
        zg["zname"] = zg["zname"].fillna(fallback_name)
        hit = gpd.sjoin(pts, zg[["zname", "geometry"]], predicate="within", how="inner").drop_duplicates("idx")
        if layer in CONSTRAINT_LAYERS:
            # 개발제한구역은 용도지역 위에 겹치는 구역이므로 비고에 남기고 용도지역 명칭에 덧붙인다
            idx = hit["idx"].values
            out.loc[idx, "zone"] = out.loc[idx, "zone"].fillna("").map(lambda z: f"{z}(개발제한구역)" if z else "개발제한구역")
        else:
            empty = out.loc[hit["idx"].values, "zone"].isna()
            target = hit.set_index("idx").loc[empty[empty].index, "zname"]
            out.loc[target.index, "zone"] = target.values
    return out


def apply_attributes(parcels: gpd.GeoDataFrame, attrs: dict[str, dict]) -> gpd.GeoDataFrame:
    """국가중점데이터 API로 받은 필지 속성을 반영하고 편입면적을 다시 계산한다."""
    out = parcels.copy()
    in_gb = out["zone"].fillna("").str.contains("개발제한")  # 레이어 결합으로 확인한 개발제한구역 여부
    for col in ("jimok", "area_reg", "zone", "use", "price", "price_year", "owner"):
        new = out["pnu"].map(lambda p, c=col: (attrs.get(p) or {}).get(c))
        out[col] = new.where(new.notna(), out[col])
    add_gb = in_gb & ~out["zone"].fillna("").str.contains("개발제한")
    out.loc[add_gb, "zone"] = out.loc[add_gb, "zone"].map(lambda z: f"{z}(개발제한구역)" if isinstance(z, str) and z else "개발제한구역")
    out["area_reg"] = pd.to_numeric(out["area_reg"], errors="coerce").fillna(out["area_geom"])
    out["price"] = pd.to_numeric(out["price"], errors="coerce")
    out["area_incl"] = (out["area_reg"] * out["incl_ratio"]).round(1)
    return out


# 업로드 파일의 열 이름 → 내부 열 이름
_UPLOAD_ALIASES = {
    "pnu": ["pnu", "PNU", "필지고유번호"],
    "addr": ["addr", "주소", "소재지"],
    "jibun": ["jibun", "지번"],
    "jimok": ["jimok", "지목"],
    "area_reg": ["area_reg", "area", "면적", "공부면적", "lndpclAr"],
    "zone": ["zone", "용도지역", "prposArea1Nm"],
    "use": ["use", "이용상황", "ladUseSittnNm"],
    "owner": ["owner", "소유구분", "posesnSeCodeNm"],
    "price": ["price", "jiga", "공시지가", "개별공시지가", "pblntfPclnd"],
    "price_year": ["price_year", "gosi_year", "공시연도", "stdrYear"],
}


def parcels_from_file(path: str, boundary: BaseGeometry, min_ratio: float = 0.05) -> gpd.GeoDataFrame:
    """필지 도형 파일(GeoJSON, SHP zip 등)을 읽어 필지 표로 만든다."""
    src = gpd.read_file(path)
    if src.crs is None:
        src = src.set_crs(WGS84)
    src = src.to_crs(WGS84)
    out = gpd.GeoDataFrame(geometry=src.geometry, crs=WGS84)
    for col, names in _UPLOAD_ALIASES.items():
        found = next((n for n in names if n in src.columns), None)
        if found:
            out[col] = src[found].values
    if "pnu" not in out.columns:
        out["pnu"] = [f"UPLOAD{i:013d}" for i in range(len(out))]
    out["pnu"] = out["pnu"].astype(str)
    if "jimok" not in out.columns and "jibun" in out.columns:
        parsed = out["jibun"].map(parse_jibun)
        out["jibun"], out["jimok"] = parsed.map(lambda t: t[0]), parsed.map(lambda t: t[1])
    for c in ("area_reg", "price"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return apply_inclusion(_fill_defaults(out), boundary, min_ratio)


def sample_parcels(boundary: BaseGeometry, sido_code: str = "41", seed: int = 0, cell_m: float = 60.0,
                   min_ratio: float = 0.05) -> gpd.GeoDataFrame:
    """화면·계산 시험용 **가상 필지**를 만든다. 실제 지적·공시지가가 아니다."""
    rng = np.random.default_rng(seed)
    b = gpd.GeoSeries([boundary], crs=WGS84).to_crs(AREA_CRS).iloc[0]
    minx, miny, maxx, maxy = b.bounds
    jimoks = ["전", "답", "임야", "대", "도로", "구거", "잡종지", "과수원", "공장용지"]
    weights = [0.28, 0.30, 0.20, 0.06, 0.05, 0.03, 0.03, 0.03, 0.02]
    base_price = {"전": 250_000, "답": 220_000, "임야": 60_000, "대": 900_000, "도로": 150_000, "구거": 80_000,
                  "잡종지": 400_000, "과수원": 230_000, "공장용지": 800_000}
    use_of = {"전": "전", "답": "답", "임야": "자연림", "대": "단독", "도로": "도로등", "구거": "하천등",
              "잡종지": "기타", "과수원": "과수원", "공장용지": "공업용"}
    zones = ["자연녹지지역", "자연녹지지역(개발제한구역)", "계획관리지역", "생산관리지역", "농림지역"]
    rows, geoms = [], []
    n = 0
    y = miny - cell_m / 2
    while y < maxy:
        x = minx - cell_m / 2
        # 넓은 띠 단위로 용도지역을 달리해 현실의 덩어리진 분포를 흉내 낸다
        zone = zones[int((y - miny) // max((maxy - miny) / len(zones), 1)) % len(zones)]
        while x < maxx:
            w = cell_m * rng.uniform(0.7, 1.3)
            cell = box(x, y, x + w, y + cell_m)
            x += w
            if not cell.intersects(b):
                continue
            n += 1
            jm = str(rng.choice(jimoks, p=weights))
            is_public = jm in ("도로", "구거")
            geoms.append(cell)
            rows.append({
                "pnu": f"{sido_code}99910100{1 if jm != '임야' else 2}{n // 10 + 1:04d}{n % 10:04d}",
                "addr": f"(가상) 시험동 {n // 10 + 1}-{n % 10}",
                "jibun": f"{'산 ' if jm == '임야' else ''}{n // 10 + 1}-{n % 10}",
                "jimok": jm,
                "area_reg": round(cell.area * rng.uniform(0.97, 1.03), 0),
                "zone": zone,
                "use": use_of[jm],
                "owner": str(rng.choice(["국유지", "시, 도유지"])) if is_public else str(rng.choice(["개인", "법인", "국유지"], p=[0.85, 0.1, 0.05])),
                "price": round(base_price[jm] * rng.uniform(0.7, 1.4), -2),
                "price_year": "가상",
                "note": "가상 필지",
            })
        y += cell_m
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs=AREA_CRS).to_crs(WGS84)
    return apply_inclusion(_fill_defaults(gdf), boundary, min_ratio)


def parcels_to_geojson(parcels: gpd.GeoDataFrame) -> str:
    return parcels.to_json()


def parcels_from_geojson(text: str) -> gpd.GeoDataFrame:
    import json

    data = json.loads(text)
    if not data.get("features"):
        return empty_parcels()
    gdf = gpd.GeoDataFrame.from_features(data["features"], crs=WGS84)
    gdf = _fill_defaults(gdf)
    gdf["pnu"] = gdf["pnu"].astype(str)
    gdf["included"] = gdf["included"].fillna(True).astype(bool)
    gdf["free"] = gdf["free"].fillna(False).astype(bool)
    return gdf
