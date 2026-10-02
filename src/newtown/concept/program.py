"""기본구상 — 토지이용계획에서 세대수·인구·밀도·기반시설 소요를 산정한다."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..models import ConceptParams, LandUseRow


@dataclass
class Program:
    housing: pd.DataFrame        # 주택 유형별 면적·세대수·인구
    households: float
    population: float
    gross_density: float         # 총밀도 (인/ha, 총면적 기준)
    net_density: float           # 순밀도 (인/ha, 주택건설용지 기준)
    infra: pd.DataFrame          # 기반시설 소요


def households_of(row: LandUseRow, area: float) -> float:
    """용지 1행의 세대수.

    공동주택: 대지면적 × 용적률 ÷ 세대당 연면적, 단독주택: 대지면적 ÷ 필지 면적 × 필지당 가구수.
    """
    if row.housing_type == "apartment" and row.far and row.unit_gfa:
        return area * row.far / 100 / row.unit_gfa
    if row.housing_type == "detached" and row.lot_size:
        return area / row.lot_size * (row.units_per_lot or 1)
    return 0.0


def build_program(rows: list[LandUseRow], total_area: float, concept: ConceptParams) -> Program:
    recs = []
    for r in rows:
        if not r.housing_type:
            continue
        area = total_area * r.ratio / 100
        hh = households_of(r, area)
        recs.append({"key": r.key, "주택 유형": r.name, "면적(㎡)": round(area), "세대수": round(hh),
                     "인구(인)": round(hh * concept.persons_per_household)})
    housing = pd.DataFrame(recs, columns=["key", "주택 유형", "면적(㎡)", "세대수", "인구(인)"])
    households = float(housing["세대수"].sum())
    population = float(housing["인구(인)"].sum())
    housing_area = total_area * sum(r.ratio for r in rows if r.major == "housing") / 100
    park_area = total_area * sum(r.ratio for r in rows if r.major == "park_green") / 100
    school_area = total_area * sum(r.ratio for r in rows if r.major == "school") / 100
    water = population * concept.water_lpcd / 1000  # ㎥/일
    infra = pd.DataFrame([
        {"항목": "1인당 공원·녹지 면적", "값": round(park_area / population, 1) if population else None, "단위": "㎡/인"},
        {"항목": "초등학교 소요", "값": round(households / concept.households_per_elementary, 1) if concept.households_per_elementary else None, "단위": "개교"},
        {"항목": "학교용지 면적", "값": round(school_area), "단위": "㎡"},
        {"항목": "계획 급수량(일평균)", "값": round(water), "단위": "㎥/일"},
        {"항목": "계획 오수량(일평균)", "값": round(water * concept.sewage_ratio), "단위": "㎥/일"},
    ])
    return Program(
        housing=housing, households=households, population=population,
        gross_density=population / (total_area / 10_000) if total_area else 0.0,
        net_density=population / (housing_area / 10_000) if housing_area else 0.0,
        infra=infra,
    )
