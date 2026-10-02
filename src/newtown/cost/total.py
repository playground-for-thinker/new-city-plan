"""총사업비 산정 — LH 조성원가 항목 체계.

같은 사업비 내역에서 두 관점의 금액을 만든다.
  - 조성원가 관점: 자본비용 포함, 예비비 없음. 단위 조성원가 = 총사업비 ÷ 유상공급 면적
  - 예타 재무 분석 관점: 자본비용(금융비용) 제외, 예비비(부가세 포함 사업비의 10%) 가산

간접비 산식 (LH 조성원가 산정 안내, 공개 산정표로 검증):
  직접인건비 = (용지비 + 용지부담금 + 조성비 + 기반시설설치비 + 이주대책비) × 직접인건비율
  판매비·일반관리비·그 밖의 비용 = (위 금액 + 직접인건비) × 각 비율
  자본비용 = 순투입액의 누적액 × 자본비용률
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .. import standards
from ..models import CostLine, FinanceParams

CATEGORY_ORDER = ["용지비", "용지부담금", "조성비", "기반시설설치비", "이주대책비", "직접인건비",
                  "판매비", "일반관리비", "자본비용", "그 밖의 비용"]
DIRECT_EX_LABOR = ["용지비", "용지부담금", "조성비", "기반시설설치비", "이주대책비"]

BASIS_LABELS = {"total_area": "총면적", "dev_area": "사업면적(보존면적 제외)", "road_area": "도로 면적", "park_area": "공원 면적",
                "green_area": "녹지 면적", "paid_area": "유상공급 면적", "population": "계획인구", "manual": "직접 입력"}
BASE_LABELS = {"construction": "조성공사비", "land_direct": "직접보상비(토지+지장물)", "land": "용지비",
               "direct_ex_labor": "직접비(인건비 제외)", "direct": "직접비"}
SCHEDULE_LABELS = {"comp": "보상 일정", "const": "공사 일정", "sales": "분양 일정", "spread": "투입액 비례"}
MODE_LABELS = {"rough": "LH 공개 조성원가 실적 단가", "detail": "LH 추정자료 단가 적산"}


@dataclass
class CostResult:
    table: pd.DataFrame          # 항목별 금액 (공급가액, 부가세, 합계)
    by_category: pd.DataFrame
    total_cost_basis: float      # 조성원가 관점 총사업비
    total_feasibility: float     # 예타 관점 총사업비 (예비비 포함)
    contingency: float
    unit_cost: float             # ㎡당 조성원가 (유상공급 면적 기준)
    missing: list[str]           # 단가·요율이 입력되지 않은 항목
    applied: dict[str, float]    # 항목별 적용 단가(원) 또는 요율(소수) — 기준표 자동 적용 결과 포함


def active_lines(lines: list[CostLine], cost_mode: str) -> list[CostLine]:
    """조성비·기반시설설치비는 선택한 산정 방식(rough/detail)의 항목만 쓴다."""
    return [l for l in lines if not l.group or l.group == cost_mode]


def unit_price_of(line: CostLine, quantities: dict[str, float], terrain: str) -> float:
    if line.auto and line.table == "lh_basic_housing":
        return standards.lh_basic_price(terrain, quantities.get("dev_area", quantities.get("total_area", 0.0)))
    return line.unit_price


def compute_costs(lines: list[CostLine], cost_mode: str, *, land_comp: float, obstacle: float,
                  quantities: dict[str, float], fin: FinanceParams, factors: dict[str, float] | None = None,
                  terrain: str = "평지", escalation: float = 1.0, capital_amount: float = 0.0) -> CostResult:
    """사업비를 계산한다.

    quantities: total_area, dev_area, road_area, park_area, green_area, paid_area, population
    factors: 민감도 분석용 배율 {'land': 용지비, 'construction': 조성비·기반시설설치비}
    escalation: 단가 기준시점 보정계수 (물량 × 단가 항목에 곱한다)
    capital_amount: 자본비용 (현금흐름이 있어야 계산되므로 밖에서 구해 넘긴다)
    """
    factors = factors or {}
    f_land, f_const = factors.get("land", 1.0), factors.get("construction", 1.0)
    lines = active_lines(lines, cost_mode)
    net: dict[str, float] = {}
    applied: dict[str, float] = {}

    def cat_sum(cats: list[str]) -> float:
        return sum(net.get(l.key, 0.0) for l in lines if l.category in cats)

    def base_amount(base: str) -> float:
        if base == "construction":
            return sum(net.get(l.key, 0.0) for l in lines if l.category == "조성비" and l.method != "rate")
        if base == "land_direct":
            return net.get("land_comp", 0.0) + net.get("obstacle", 0.0)
        if base == "land":
            return cat_sum(["용지비"])
        if base == "direct_ex_labor":
            return cat_sum(DIRECT_EX_LABOR)
        if base == "direct":
            return cat_sum(DIRECT_EX_LABOR + ["직접인건비"])
        return 0.0

    def factor_of(l: CostLine) -> float:
        if l.category in ("용지비", "용지부담금", "이주대책비"):
            return f_land
        if l.category in ("조성비", "기반시설설치비"):
            return f_const
        return 1.0

    # 1) 자동·물량·직접입력 항목
    for l in lines:
        if l.method == "auto":
            net[l.key] = {"land_comp": land_comp, "obstacle": obstacle}.get(l.key, 0.0) * f_land
        elif l.method == "qty_price":
            qty = l.qty if l.basis == "manual" else quantities.get(l.basis, 0.0)
            applied[l.key] = unit_price_of(l, quantities, terrain)
            net[l.key] = qty * applied[l.key] * escalation * (fin.bid_rate if l.bid else 1.0) * f_const
        elif l.method == "manual":
            net[l.key] = l.amount * factor_of(l)
        elif l.method == "capital":
            net[l.key] = capital_amount
            applied[l.key] = l.rate
    # 2) 요율 항목 — 기준금액이 먼저 확정되는 순서로 계산
    for base in ("construction", "land_direct", "land", "direct_ex_labor", "direct"):
        for l in lines:
            if l.method == "rate" and l.base == base:
                amount = base_amount(base)
                applied[l.key] = standards.rate_from_table(l.table, amount) if l.auto and l.table else l.rate
                net[l.key] = amount * applied[l.key]

    rows = []
    for l in lines:
        amount = net.get(l.key, 0.0)
        vat = amount * fin.vat_rate if l.vat else 0.0
        rows.append({"key": l.key, "구분": l.category, "항목": l.name, "공급가액(원)": round(amount),
                     "부가세(원)": round(vat), "합계(원)": round(amount + vat), "schedule": l.schedule})
    table = pd.DataFrame(rows)

    cost_basis_total = float(table["합계(원)"].sum())
    feas = table[table["구분"] != "자본비용"]
    contingency_base = float(feas.loc[feas["구분"].isin(DIRECT_EX_LABOR), "합계(원)"].sum())
    contingency = contingency_base * fin.contingency_rate
    feas_total = float(feas["합계(원)"].sum()) + contingency

    by_cat = table.groupby("구분", sort=False)["합계(원)"].sum().reindex(
        [c for c in CATEGORY_ORDER if c in table["구분"].values]).reset_index()
    by_cat["조성원가 관점(원)"] = by_cat["합계(원)"]
    by_cat["예타 재무 분석 관점(원)"] = by_cat["합계(원)"].where(by_cat["구분"] != "자본비용", 0)
    by_cat = by_cat.drop(columns="합계(원)")
    by_cat.loc[len(by_cat)] = ["예비비", 0, round(contingency)]
    by_cat.loc[len(by_cat)] = ["합계", round(cost_basis_total), round(feas_total)]

    missing = [l.name for l in lines
               if (l.method == "qty_price" and applied.get(l.key, 0) == 0)
               or (l.method in ("rate", "capital") and applied.get(l.key, 0) == 0)]
    paid = quantities.get("paid_area", 0.0)
    return CostResult(table=table, by_category=by_cat, total_cost_basis=cost_basis_total,
                      total_feasibility=feas_total, contingency=contingency,
                      unit_cost=cost_basis_total / paid if paid > 0 else 0.0, missing=missing, applied=applied)
