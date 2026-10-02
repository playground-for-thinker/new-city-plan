"""재무적 타당성 분석 — 예비타당성조사 지침(공공기관 예타 일반지침 제Ⅷ장) 기준.

- 주 지표 PI(수익성지수), 참고 지표 FNPV·FIRR
- 분석 기준연도 불변가격, 실질 재무적 할인율(기본 4.5%)
- 금융비용(자본비용)·감가상각은 현금유출에 넣지 않는다
- 분석기간: 사업 착수 ~ 분양대금 회수 완료
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..cost.total import DIRECT_EX_LABOR, CostResult
from ..landuse.plan import major_name
from ..models import FinanceParams, LandUseRow, Schedule


def _norm(pcts: list[float], n: int) -> np.ndarray:
    a = np.zeros(n)
    a[:min(len(pcts), n)] = pcts[:n]
    s = a.sum()
    return a / s if s > 0 else a


def revenue_table(rows: list[LandUseRow], total_area: float, unit_cost: float, price_factor: float = 1.0) -> pd.DataFrame:
    """유상공급 용지별 공급단가와 분양수입."""
    recs = []
    for r in rows:
        if not r.paid:
            continue
        area = total_area * r.ratio / 100
        price = (unit_cost * r.cost_mult if r.price_basis == "cost" else r.unit_price) * price_factor
        recs.append({"key": r.key, "major": r.major, "대분류": major_name(r.major), "용도": r.name, "면적(㎡)": round(area),
                     "공급기준": f"조성원가 × {r.cost_mult:g}" if r.price_basis == "cost" else "단가 직접 입력",
                     "공급단가(원/㎡)": round(price), "분양수입(원)": round(area * price)})
    return pd.DataFrame(recs, columns=["key", "major", "대분류", "용도", "면적(㎡)", "공급기준", "공급단가(원/㎡)", "분양수입(원)"])


@dataclass
class Metrics:
    pi: float
    fnpv: float
    firr: float | None
    pv_in: float
    pv_out: float
    total_in: float
    total_out: float

    @property
    def feasible(self) -> bool:
        return self.pi >= 1.0


def npv(flows: np.ndarray, t: np.ndarray, rate: float) -> float:
    return float((flows / (1 + rate) ** t).sum())


def irr(flows: np.ndarray, t: np.ndarray) -> float | None:
    """순현금흐름의 내부수익률 (이분법). 부호 변화가 없으면 None."""
    lo, hi = -0.95, 5.0
    f_lo, f_hi = npv(flows, t, lo), npv(flows, t, hi)
    if not np.isfinite(f_lo) or not np.isfinite(f_hi) or f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(flows, t, mid)
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
        if hi - lo < 1e-9:
            break
    return (lo + hi) / 2


def compute_metrics(inflow: np.ndarray, outflow: np.ndarray, t: np.ndarray, rate: float) -> Metrics:
    pv_in, pv_out = npv(inflow, t, rate), npv(outflow, t, rate)
    return Metrics(pi=pv_in / pv_out if pv_out > 0 else float("nan"), fnpv=pv_in - pv_out,
                   firr=irr(inflow - outflow, t), pv_in=pv_in, pv_out=pv_out,
                   total_in=float(inflow.sum()), total_out=float(outflow.sum()))


@dataclass
class FinanceResult:
    revenue: pd.DataFrame
    cashflow: pd.DataFrame
    metrics: Metrics
    warnings: list[str]
    net_investment_years: float = 0.0   # 연차별 순투입액 누적액의 합계 (원·년) — 자본비용 산정의 기준


def build_cashflow(costs: CostResult, revenue: pd.DataFrame, schedule: Schedule, fin: FinanceParams,
                   sales_delay: int = 0, discount_rate: float | None = None) -> FinanceResult:
    """연차별 현금흐름과 재무 지표.

    sales_delay: 분양 일정을 늦추는 연수 (민감도 분석용)
    """
    rate = fin.discount_rate if discount_rate is None else discount_rate
    warnings: list[str] = []
    n0 = len(schedule.years)
    collect = np.array(schedule.collect or [100.0], dtype=float)
    collect = collect / collect.sum() if collect.sum() > 0 else np.array([1.0])
    n = n0 + sales_delay + len(collect) - 1
    years = [schedule.years[0] + i for i in range(n)]

    for label, pcts in [("보상", schedule.comp), ("공사", schedule.const)]:
        if abs(sum(pcts) - 100) > 0.5:
            warnings.append(f"{label} 투입 비율 합계가 {sum(pcts):g}%입니다. 합계를 100%로 환산해 계산했습니다.")
    comp_w, const_w = _norm(schedule.comp, n), _norm(schedule.const, n)

    # ---- 유입: 용도별 계약(공급) 일정 × 대금 회수 조건 ----
    inflow = np.zeros(n)
    contract = np.zeros(n)
    for _, r in revenue.iterrows():
        pcts = schedule.sales.get(r["major"], [])
        if abs(sum(pcts) - 100) > 0.5 and r["분양수입(원)"] > 0:
            warnings.append(f"{r['대분류']} 공급 비율 합계가 {sum(pcts):g}%입니다. 합계를 100%로 환산해 계산했습니다.")
        w = _norm([0.0] * sales_delay + list(pcts), n)
        if w.sum() == 0 and r["분양수입(원)"] > 0:
            warnings.append(f"{r['대분류']}의 공급 일정이 비어 있어 {r['용도']} 수입이 반영되지 않았습니다.")
        c = r["분양수입(원)"] * w
        contract += c
        for lag, share in enumerate(collect):
            inflow[lag:] += (c * share)[: n - lag]
    sales_w = contract / contract.sum() if contract.sum() > 0 else np.zeros(n)

    # ---- 유출: 예타 관점 (자본비용 제외) ----
    t_cost = costs.table[costs.table["구분"] != "자본비용"]
    sched_w = {"comp": comp_w, "const": const_w, "sales": sales_w}
    out_by_cat: dict[str, np.ndarray] = {}
    spread_lines = []
    for _, l in t_cost.iterrows():
        if l["schedule"] == "spread" or (l["schedule"] == "sales" and sales_w.sum() == 0):
            spread_lines.append(l)
            continue
        out_by_cat[l["구분"]] = out_by_cat.get(l["구분"], np.zeros(n)) + l["합계(원)"] * sched_w[l["schedule"]]
    direct_flow = sum((v for k, v in out_by_cat.items() if k in DIRECT_EX_LABOR), np.zeros(n))
    spread_w = direct_flow / direct_flow.sum() if direct_flow.sum() > 0 else (comp_w + const_w) / max((comp_w + const_w).sum(), 1e-12)
    for l in spread_lines:
        out_by_cat[l["구분"]] = out_by_cat.get(l["구분"], np.zeros(n)) + l["합계(원)"] * spread_w
    # 자본비용 기준: 순투입액(투입 − 회수)의 누적액. 예비비와 법인세는 조성원가 항목이 아니므로 넣지 않는다.
    cum_net = np.cumsum(sum(out_by_cat.values(), np.zeros(n)) - inflow)
    net_investment_years = float(np.clip(cum_net, 0, None).sum())
    out_by_cat["예비비"] = costs.contingency * spread_w
    outflow = sum(out_by_cat.values(), np.zeros(n))

    if fin.include_tax and fin.tax_rate > 0:
        # 간이 법인세: 사업 전체 이익 × 세율을 수입 회수 비율대로 배분
        tax_total = max(inflow.sum() - outflow.sum(), 0.0) * fin.tax_rate
        tax = tax_total * (inflow / inflow.sum()) if inflow.sum() > 0 else np.zeros(n)
        out_by_cat["법인세(간이)"] = tax
        outflow = outflow + tax

    t = np.array(years) - schedule.base_year
    disc = 1 / (1 + rate) ** t
    cf = pd.DataFrame({"연도": years})
    for cat, arr in out_by_cat.items():
        cf[cat] = np.round(arr)
    cf["현금유출"] = np.round(outflow)
    cf["현금유입"] = np.round(inflow)
    cf["순현금흐름"] = cf["현금유입"] - cf["현금유출"]
    cf["누적 순현금흐름"] = cf["순현금흐름"].cumsum()
    cf["할인계수"] = np.round(disc, 4)
    cf["유출 현재가치"] = np.round(outflow * disc)
    cf["유입 현재가치"] = np.round(inflow * disc)
    return FinanceResult(revenue=revenue, cashflow=cf, metrics=compute_metrics(inflow, outflow, t, rate), warnings=warnings,
                         net_investment_years=net_investment_years)
