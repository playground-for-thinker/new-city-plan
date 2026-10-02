"""단계 간 연결 — 프로젝트 입력에서 사업비·재무 지표까지 한 번에 계산한다.

토지이용계획이나 단가가 바뀌면 이 함수를 다시 불러 뒤 단계 결과를 모두 갱신한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import pandas as pd

from .concept.program import Program, build_program
from .cost.compensation import CompensationResult, build_register
from .cost.total import CostResult, active_lines, compute_costs
from .finance.analysis import FinanceResult, build_cashflow, revenue_table
from .landuse import plan as landuse_plan
from .models import Project
from .site.parcels import area_m2, boundary_shape


@dataclass
class Evaluation:
    total_area: float
    comp: CompensationResult
    areas: dict[str, float]
    program: Program
    costs: CostResult
    finance: FinanceResult


def project_area(project: Project, parcels: gpd.GeoDataFrame | None) -> float:
    """사업면적: 편입 필지의 편입면적 합계. 필지가 없으면 경계 도형 면적."""
    if parcels is not None and len(parcels):
        inc = parcels[parcels["included"].astype(bool)]
        if len(inc) and inc["area_incl"].sum() > 0:
            return float(inc["area_incl"].sum())
    if project.boundary:
        return area_m2(boundary_shape(project.boundary))
    return 0.0


def evaluate_financials(project: Project, total_area: float, land_comp: float, obstacle: float, *,
                        rows=None, factors: dict[str, float] | None = None, sales_delay: int = 0,
                        discount_rate: float | None = None) -> tuple[dict, Program, CostResult, FinanceResult]:
    factors = factors or {}
    rows = project.landuse if rows is None else rows
    areas = landuse_plan.areas(rows, total_area)
    areas["dev_area"] = max(total_area - project.preserved_area, 0.0)
    program = build_program(rows, total_area, project.concept)
    capital_rate = next((l.rate for l in active_lines(project.cost_lines, project.cost_mode) if l.method == "capital"), 0.0)

    def run(capital: float):
        costs = compute_costs(project.cost_lines, project.cost_mode, land_comp=land_comp, obstacle=obstacle,
                              quantities={**areas, "population": program.population}, fin=project.finance, factors=factors,
                              terrain=project.terrain, escalation=project.price_escalation, capital_amount=capital)
        revenue = revenue_table(rows, total_area, costs.unit_cost, factors.get("price", 1.0))
        finance = build_cashflow(costs, revenue, project.schedule, project.finance, sales_delay=sales_delay,
                                 discount_rate=discount_rate)
        return costs, finance

    # 자본비용 = 순투입액 누적액 × 자본비용률. 자본비용이 조성원가(→ 조성원가 기준 공급가격 → 회수액)에 다시 영향을 주므로
    # 값이 변하지 않을 때까지 반복한다.
    capital = 0.0
    costs, finance = run(capital)
    if capital_rate > 0:
        for _ in range(25):
            new = finance.net_investment_years * capital_rate
            done = abs(new - capital) <= 1e-5 * max(new, 1.0)
            capital = new
            costs, finance = run(capital)
            if done:
                break
    return areas, program, costs, finance


def evaluate(project: Project, parcels: gpd.GeoDataFrame | None, comp: CompensationResult | None = None) -> Evaluation:
    if comp is None:
        comp = build_register(parcels, project.comp) if parcels is not None and len(parcels) else \
            CompensationResult(pd.DataFrame(), 0.0, 0.0, 0.25, {})
    total_area = project_area(project, parcels)
    areas, program, costs, finance = evaluate_financials(project, total_area, comp.land_comp, comp.obstacle)
    return Evaluation(total_area, comp, areas, program, costs, finance)


SENS_VARS = {"land": "보상비(용지비)", "construction": "조성비·기반시설설치비", "price": "공급가격"}


def sensitivity(project: Project, ev: Evaluation, steps=(-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3)) -> pd.DataFrame:
    """변수별 증감에 따른 PI."""
    recs = []
    for var, label in SENS_VARS.items():
        row = {"변수": label}
        for s in steps:
            *_, fin = evaluate_financials(project, ev.total_area, ev.comp.land_comp, ev.comp.obstacle, factors={var: 1 + s})
            row[f"{s:+.0%}"] = round(fin.metrics.pi, 3)
        recs.append(row)
    return pd.DataFrame(recs)


def scenario_table(project: Project, ev: Evaluation) -> pd.DataFrame:
    """분양 지연·할인율 변화에 따른 지표."""
    recs = []

    def add(label, **kw):
        *_, fin = evaluate_financials(project, ev.total_area, ev.comp.land_comp, ev.comp.obstacle, **kw)
        m = fin.metrics
        recs.append({"시나리오": label, "PI": round(m.pi, 2), "FNPV(억원)": round(m.fnpv / 1e8, 1),
                     "FIRR(%)": round(m.firr * 100, 2) if m.firr is not None else None})

    add("기준")
    for d in (1, 2, 3):
        add(f"분양 {d}년 지연", sales_delay=d)
    base = project.finance.discount_rate
    for r in (base - 0.01, base + 0.01, base + 0.02):
        add(f"할인율 {r:.1%}", discount_rate=r)
    return pd.DataFrame(recs)


def breakeven(project: Project, ev: Evaluation) -> pd.DataFrame:
    """PI = 1이 되는 변수별 증감률 (이분법, -90% ~ +400% 범위)."""
    recs = []
    for var, label in SENS_VARS.items():
        def pi_at(f, var=var):
            *_, fin = evaluate_financials(project, ev.total_area, ev.comp.land_comp, ev.comp.obstacle, factors={var: f})
            return fin.metrics.pi - 1

        lo, hi = 0.1, 5.0
        f_lo, f_hi = pi_at(lo), pi_at(hi)
        if not (f_lo == f_lo and f_hi == f_hi) or f_lo * f_hi > 0:
            recs.append({"변수": label, "PI=1이 되는 증감률": "범위 내 없음"})
            continue
        for _ in range(60):
            mid = (lo + hi) / 2
            f_mid = pi_at(mid)
            if f_lo * f_mid <= 0:
                hi = mid
            else:
                lo, f_lo = mid, f_mid
        recs.append({"변수": label, "PI=1이 되는 증감률": f"{(lo + hi) / 2 - 1:+.1%}"})
    return pd.DataFrame(recs)
