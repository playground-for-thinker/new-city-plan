"""보상비 산정 — 토지세목조서와 공시지가 보상배율.

근거: 기획재정부 「예비타당성조사 수행 총괄지침」, KDI 공공기관 예타 일반지침(제2판) 제Ⅴ장 제3절.
  필지 토지보상비 = 편입면적 × 개별공시지가 × 적용 배율
  적용 배율 = (시도별 용도지역 배율 + 시도별 이용상황 배율) ÷ 2
  지장물 및 기타 보상비 = 토지보상비 × 사업유형별 비율 (택지개발 25%)
"""
from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd

from .. import standards
from ..models import CompSettings

OTHER_USE = "공공·기타"


def sido_of(pnu: str, std: dict) -> str | None:
    return std["sido_codes"].get(str(pnu)[:2])


def classify_zone(zone: str | None, std: dict) -> str | None:
    if not zone or (isinstance(zone, float) and np.isnan(zone)):
        return None
    for rule in std["zone_mapping"]:
        if rule["contains"] in str(zone):
            return rule["class"]
    return None


def classify_use(use: str | None, jimok: str | None, std: dict) -> tuple[str | None, str]:
    """이용상황 분류와 판단 근거('이용상황'/'지목')를 돌려준다."""
    if use and not (isinstance(use, float) and np.isnan(use)):
        for rule in std["use_mapping"]:
            if ("contains" in rule and rule["contains"] in str(use)) or \
                    ("starts" in rule and str(use).startswith(rule["starts"])):
                return rule["class"], "이용상황"
        return OTHER_USE, "이용상황"
    if jimok and not (isinstance(jimok, float) and np.isnan(jimok)):
        return std["jimok_to_use"].get(str(jimok), OTHER_USE), "지목"
    return None, ""


def lookup_multiplier(sido: str | None, zone_class: str | None, use_class: str | None, std: dict) -> dict:
    """적용 배율과 근거. 값이 없는 칸은 해당 시도의 '전체' 배율로 대체한다."""
    if sido not in std["multipliers"]:
        return {"zone_mult": np.nan, "use_mult": np.nan, "mult": np.nan, "remark": "시도 미확인"}
    row = std["multipliers"][sido]
    remarks = []

    def pick(classes: list[str], values: list, cls: str | None, label: str) -> float:
        if cls in classes and values[classes.index(cls)] is not None:
            return float(values[classes.index(cls)])
        remarks.append(f"{label} 배율 없음→시도 전체 배율")
        return float(row["전체"])

    zm = pick(std["zone_classes"], row["zone"], zone_class, "용도지역")
    um = pick(std["use_classes"], row["use"], use_class, "이용상황")
    return {"zone_mult": zm, "use_mult": um, "mult": round((zm + um) / 2, 4), "remark": "; ".join(remarks)}


@dataclass
class CompensationResult:
    register: pd.DataFrame       # 토지세목조서 (편입 필지)
    land_comp: float             # 토지보상비(A)
    obstacle: float              # 지장물 및 기타 보상비(B)
    obstacle_ratio: float
    issues: dict[str, int]       # 자료 누락 건수

    @property
    def direct(self) -> float:
        return self.land_comp + self.obstacle


def build_register(parcels: gpd.GeoDataFrame, settings: CompSettings) -> CompensationResult:
    """편입 필지로 토지세목조서를 만들고 보상비를 계산한다."""
    std = standards.compensation_standard(settings.multiplier_version)
    df = pd.DataFrame(parcels.drop(columns="geometry", errors="ignore"))
    df = df[df["included"].astype(bool)].copy() if len(df) else df
    ratio = settings.obstacle_ratio if settings.obstacle_ratio is not None else std["obstacle_ratio"].get(settings.project_type, 0.25)
    if df.empty:
        return CompensationResult(pd.DataFrame(), 0.0, settings.obstacle_manual if settings.obstacle_mode == "manual" else 0.0,
                                  ratio, {})

    df["sido"] = df["pnu"].map(lambda p: sido_of(p, std))
    df["zone_class"] = df["zone"].map(lambda z: classify_zone(z, std))
    uses = [classify_use(u, j, std) for u, j in zip(df["use"], df["jimok"])]
    df["use_class"] = [u[0] for u in uses]
    df["use_basis"] = [u[1] for u in uses]
    looked = [lookup_multiplier(s, z, u, std) for s, z, u in zip(df["sido"], df["zone_class"], df["use_class"])]
    df["zone_mult"] = [x["zone_mult"] for x in looked]
    df["use_mult"] = [x["use_mult"] for x in looked]
    df["mult"] = [x["mult"] for x in looked]
    remarks = pd.Series([x["remark"] for x in looked], index=df.index)

    # 공시지가 없는 필지: 같은 지목 편입 필지의 면적가중 평균지가로 대체
    price = pd.to_numeric(df["price"], errors="coerce")
    missing_price = price.isna() | (price <= 0)
    known = df[~missing_price]
    if missing_price.any() and len(known):
        w = known["area_incl"].clip(lower=0.0)
        by_jimok = (known.assign(_pw=price[~missing_price] * w, _w=w).groupby("jimok")[["_pw", "_w"]].sum())
        jimok_avg = (by_jimok["_pw"] / by_jimok["_w"].where(by_jimok["_w"] > 0)).to_dict()
        overall = float((price[~missing_price] * w).sum() / w.sum()) if w.sum() > 0 else np.nan
        fill = df.loc[missing_price, "jimok"].map(lambda j: jimok_avg.get(j, overall)).fillna(overall)
        price = price.copy()
        price[missing_price] = fill.round(-2)
        remarks[missing_price] = remarks[missing_price].map(lambda r: "; ".join(filter(None, [r, "공시지가 없음→동일 지목 평균지가"])))
    df["price_applied"] = price * settings.price_adjust

    free = df["free"].astype(bool)
    df["land_comp"] = (df["area_incl"] * df["price_applied"] * df["mult"]).where(~free, 0.0).round(0)
    remarks[free] = remarks[free].map(lambda r: "; ".join(filter(None, [r, "무상귀속"])))
    remarks[df["use_basis"] == "지목"] = remarks[df["use_basis"] == "지목"].map(
        lambda r: "; ".join(filter(None, [r, "이용상황 없음→지목 기준 분류"])))
    df["remark"] = [("; ".join(filter(None, [str(n) if isinstance(n, str) else "", r]))) for n, r in zip(df["note"], remarks)]

    issues = {
        "용도지역 미확인": int(df["zone_class"].isna().sum()),
        "이용상황 없음(지목으로 분류)": int((df["use_basis"] == "지목").sum()),
        "공시지가 없음(평균지가 대체)": int(missing_price.sum()),
        "시도 미확인": int(df["sido"].isna().sum()),
        "보상비 계산 불가": int((df["land_comp"].isna() & ~free).sum()),
    }
    land = float(df["land_comp"].sum(skipna=True))
    obstacle = settings.obstacle_manual if settings.obstacle_mode == "manual" else land * ratio
    df = df.sort_values(["pnu"]).reset_index(drop=True)
    df.insert(0, "no", range(1, len(df) + 1))
    return CompensationResult(df, land, float(obstacle), ratio, {k: v for k, v in issues.items() if v})


REGISTER_COLUMNS = {
    "no": "일련번호", "pnu": "PNU", "addr": "소재지", "jibun": "지번", "jimok": "지목",
    "area_reg": "공부면적(㎡)", "incl_ratio": "편입비율", "area_incl": "편입면적(㎡)",
    "zone": "용도지역", "use": "이용상황", "owner": "소유구분",
    "price": "개별공시지가(원/㎡)", "price_year": "공시연도", "price_applied": "적용지가(원/㎡)",
    "zone_class": "용도지역 분류", "zone_mult": "용도지역 배율", "use_class": "이용상황 분류", "use_mult": "이용상황 배율",
    "mult": "적용 배율", "land_comp": "토지보상비(원)", "remark": "비고",
}


def register_table(result: CompensationResult) -> pd.DataFrame:
    """출력용 토지세목조서 (한글 열 이름)."""
    if result.register.empty:
        return pd.DataFrame(columns=list(REGISTER_COLUMNS.values()))
    return result.register[list(REGISTER_COLUMNS)].rename(columns=REGISTER_COLUMNS)


def summarize(result: CompensationResult, by: str) -> pd.DataFrame:
    """지목·용도지역·소유구분 등 기준별 보상비 집계."""
    reg = result.register
    if reg.empty:
        return pd.DataFrame(columns=[by, "필지 수", "편입면적(㎡)", "토지보상비(원)", "평균 배율", "㎡당 보상비(원)"])
    g = reg.assign(_k=reg[by].fillna("미확인")).groupby("_k")
    out = pd.DataFrame({
        "필지 수": g.size(),
        "편입면적(㎡)": g["area_incl"].sum(),
        "토지보상비(원)": g["land_comp"].sum(),
    })
    out["평균 배율"] = (g.apply(lambda d: np.average(d["mult"].fillna(0), weights=d["area_incl"].clip(lower=1e-9)), include_groups=False)).round(2)
    out["㎡당 보상비(원)"] = (out["토지보상비(원)"] / out["편입면적(㎡)"].where(out["편입면적(㎡)"] > 0)).round(0)
    return out.sort_values("편입면적(㎡)", ascending=False).rename_axis(REGISTER_COLUMNS.get(by, by)).reset_index()
