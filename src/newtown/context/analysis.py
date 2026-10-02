"""입지현황 분석 — 편입 필지 집계."""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

GROUP_LABELS = {"jimok": "지목", "zone": "용도지역", "owner": "소유구분", "use": "이용상황"}

# 소유구분 → 국유/공유/사유
_OWNER_GROUPS = [("국유", "국유"), ("도유", "공유"), ("시유", "공유"), ("군유", "공유"), ("구유", "공유"), ("공유지", "공유")]


def owner_group(owner) -> str:
    if not isinstance(owner, str) or not owner:
        return "미확인"
    for key, grp in _OWNER_GROUPS:
        if key in owner:
            return grp
    return "사유"


def included(parcels: gpd.GeoDataFrame) -> pd.DataFrame:
    df = pd.DataFrame(parcels.drop(columns="geometry", errors="ignore"))
    return df[df["included"].astype(bool)] if len(df) else df


def summary_by(parcels: gpd.GeoDataFrame, col: str) -> pd.DataFrame:
    """기준 열별 필지 수·면적·비율·면적가중 평균지가."""
    df = included(parcels)
    label = GROUP_LABELS.get(col, col)
    if df.empty:
        return pd.DataFrame(columns=[label, "필지 수", "면적(㎡)", "비율(%)", "평균 공시지가(원/㎡)"])
    key = df["owner"].map(owner_group) if col == "owner_group" else df[col].fillna("미확인").replace("", "미확인")
    df = df.assign(_k=key, _pw=pd.to_numeric(df["price"], errors="coerce") * df["area_incl"],
                   _w=df["area_incl"].where(pd.to_numeric(df["price"], errors="coerce").notna(), 0))
    g = df.groupby("_k")
    out = pd.DataFrame({"필지 수": g.size(), "면적(㎡)": g["area_incl"].sum().round(0)})
    total = out["면적(㎡)"].sum()
    out["비율(%)"] = (out["면적(㎡)"] / total * 100).round(1) if total else 0.0
    sums = g[["_pw", "_w"]].sum()
    out["평균 공시지가(원/㎡)"] = (sums["_pw"] / sums["_w"].where(sums["_w"] > 0)).round(0)
    return out.sort_values("면적(㎡)", ascending=False).rename_axis("소유구분" if col == "owner_group" else label).reset_index()


def overview(parcels: gpd.GeoDataFrame, boundary_area: float) -> dict:
    df = included(parcels)
    all_n = len(parcels)
    price = pd.to_numeric(df["price"], errors="coerce") if len(df) else pd.Series(dtype=float)
    w = df["area_incl"] if len(df) else pd.Series(dtype=float)
    has = price.notna()
    return {
        "boundary_area": boundary_area,
        "parcel_count": int(len(df)),
        "excluded_count": int(all_n - len(df)),
        "parcel_area": float(w.sum()) if len(df) else 0.0,
        "partial_count": int((df["incl_ratio"] < 1).sum()) if len(df) else 0,
        "avg_price": float((price[has] * w[has]).sum() / w[has].sum()) if has.any() and w[has].sum() > 0 else np.nan,
        "gb_area": float(df.loc[df["zone"].fillna("").str.contains("개발제한"), "area_incl"].sum()) if len(df) else 0.0,
        "farm_area": float(df.loc[df["jimok"].isin(["전", "답", "과수원"]), "area_incl"].sum()) if len(df) else 0.0,
        "forest_area": float(df.loc[df["jimok"] == "임야", "area_incl"].sum()) if len(df) else 0.0,
        "built_area": float(df.loc[df["jimok"].isin(["대", "공장용지", "창고용지", "학교용지", "종교용지", "주유소용지"]), "area_incl"].sum()) if len(df) else 0.0,
    }


def data_quality(parcels: gpd.GeoDataFrame) -> pd.DataFrame:
    """속성 누락 현황."""
    df = included(parcels)
    n = len(df)
    rows = []
    for col, label in [("jimok", "지목"), ("zone", "용도지역"), ("use", "이용상황"), ("owner", "소유구분"), ("price", "개별공시지가")]:
        miss = int(df[col].isna().sum()) if n else 0
        rows.append({"항목": label, "누락 필지 수": miss, "누락 비율(%)": round(miss / n * 100, 1) if n else 0.0})
    return pd.DataFrame(rows)
