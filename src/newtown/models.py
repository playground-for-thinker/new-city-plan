"""프로젝트 데이터 모델."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

from . import standards


class CostLine(BaseModel):
    key: str
    category: str
    name: str
    method: Literal["auto", "qty_price", "rate", "manual", "capital"] = "manual"
    basis: str = "manual"          # qty_price 물량 연결 기준
    qty: float = 0.0               # basis 가 manual 일 때의 물량
    unit: str = ""
    unit_price: float = 0.0        # 원
    base: str = ""                 # rate 기준금액
    rate: float = 0.0              # 요율 (0.05 = 5%)
    amount: float = 0.0            # manual 금액 (원)
    vat: bool = False
    bid: bool = False
    schedule: Literal["comp", "const", "sales", "spread"] = "spread"
    group: str = ""                # 조성비 산정 방식: rough(실적 단가) / detail(추정자료 적산)
    auto: bool = False             # 단가·요율을 기준표(table)에서 자동으로 가져온다
    table: str = ""
    benchmark: str = ""            # 초기값을 LH 공개 산정표 통계에서 가져오는 항목


class LandUseRow(BaseModel):
    key: str
    major: str
    name: str
    ratio: float = 0.0             # 총면적 대비 %
    paid: bool = False
    price_basis: Literal["cost", "manual"] = "cost"
    cost_mult: float = 1.0         # 조성원가 대비 배율
    unit_price: float = 0.0        # 원/㎡ (manual)
    housing_type: Optional[Literal["detached", "apartment"]] = None
    far: Optional[float] = None    # 용적률 %
    unit_gfa: Optional[float] = None   # 세대당 연면적 ㎡
    lot_size: Optional[float] = None   # 단독주택 필지 면적 ㎡
    units_per_lot: Optional[float] = None


class CompSettings(BaseModel):
    multiplier_version: str = "2018"
    project_type: str = "택지개발"
    obstacle_mode: Literal["ratio", "manual"] = "ratio"
    obstacle_ratio: Optional[float] = None   # None 이면 기준정보 값
    obstacle_manual: float = 0.0
    price_adjust: float = 1.0      # 공시지가 기준연도 → 분석 기준연도 보정계수
    min_incl_ratio: float = 0.05   # 이 비율 미만으로 걸친 필지는 제외


class ConceptParams(BaseModel):
    persons_per_household: float = 2.5
    park_per_capita_min: float = 0.0     # 1인당 공원녹지 최소면적(㎡). 0 이면 검사 안 함
    park_ratio_min: float = 0.0          # 공원녹지 최소비율(%). 0 이면 검사 안 함
    households_per_elementary: float = 5000.0
    water_lpcd: float = 300.0              # 1인 1일 급수량 (L)
    sewage_ratio: float = 0.9            # 오수 전환율


class Schedule(BaseModel):
    base_year: int
    years: list[int]
    comp: list[float]                    # 연차별 보상 투입 %
    const: list[float]                   # 연차별 공사 투입 %
    sales: dict[str, list[float]]        # 대분류 key → 연차별 공급(계약) %
    collect: list[float] = Field(default_factory=lambda: [100.0])  # 계약연도, +1년, +2년 … 회수 %


class FinanceParams(BaseModel):
    discount_rate: float = 0.045
    contingency_rate: float = 0.10
    vat_rate: float = 0.10
    bid_rate: float = 1.0                # 낙찰률 (1.0 = 100%)
    include_tax: bool = False
    tax_rate: float = 0.0


class UpperPlanCheck(BaseModel):
    plan: str
    content: str = ""
    conformity: Literal["미검토", "부합", "조건부 부합", "불부합"] = "미검토"
    note: str = ""


DEFAULT_UPPER_PLANS = ["국토종합계획", "수도권정비계획(권역 구분)", "광역도시계획", "도시·군기본계획",
                       "광역교통계획", "기타 관련 계획"]


class Project(BaseModel):
    name: str
    created: str = Field(default_factory=lambda: date.today().isoformat())
    boundary: Optional[dict] = None      # GeoJSON geometry (EPSG:4326)
    parcel_source: str = ""
    comp: CompSettings = Field(default_factory=CompSettings)
    cost_mode: Literal["rough", "detail"] = "rough"
    cost_template_version: str = "1"
    cost_lines: list[CostLine] = Field(default_factory=list)
    terrain: Literal["평지", "구릉지"] = "평지"
    preserved_area: float = 0.0          # 보존면적(㎡) — 기본시설공사 단가의 사업면적에서 뺀다
    price_escalation: float = 1.0        # 단가 기준시점 → 분석 기준연도 보정계수 (물량×단가 항목에 곱한다)
    alternatives: dict[str, list[LandUseRow]] = Field(default_factory=dict)
    active_alt: str = "대안 1"
    concept: ConceptParams = Field(default_factory=ConceptParams)
    schedule: Optional[Schedule] = None
    finance: FinanceParams = Field(default_factory=FinanceParams)
    upper_plans: list[UpperPlanCheck] = Field(default_factory=list)
    blocks: list[dict] = Field(default_factory=list)   # 구상도 블록 [{"geometry": GeoJSON, "use": 용도 key}]

    @property
    def landuse(self) -> list[LandUseRow]:
        return self.alternatives.get(self.active_alt, [])


def default_cost_lines() -> list[CostLine]:
    """사업비 항목 틀. benchmark 가 지정된 항목은 LH 공개 조성원가 산정표 통계로 초기값을 채운다."""
    bench = standards.disclosure_benchmark()
    lines = []
    for spec in standards.cost_template()["lines"]:
        line = CostLine(**spec)
        value = bench.get(line.benchmark)
        if value is not None:
            if line.method == "qty_price":
                line.unit_price = value
            else:
                line.rate = value / 100
        lines.append(line)
    return lines


def upgrade_project(project: "Project") -> "Project":
    """이전 판의 틀로 저장된 프로젝트를 현재 틀에 맞춘다. 사용자가 입력한 값(0이 아닌 값)은 유지한다."""
    current = str(standards.cost_template()["version"])
    if project.cost_template_version != current:
        old = {l.key: l for l in project.cost_lines}
        lines = default_cost_lines()
        for line in lines:
            prev = old.get(line.key)
            if prev is None:
                continue
            for field in ("unit_price", "rate", "amount", "qty"):
                if getattr(prev, field):
                    setattr(line, field, getattr(prev, field))
                    if field in ("unit_price", "rate"):
                        line.auto = False
        project.cost_lines = lines
        project.cost_template_version = current
    # 공원·녹지가 한 행이던 계획을 공원/녹지 두 행으로 나눈다 (틀의 기본 구성비)
    tpl = {r["key"]: r for r in standards.landuse_template()["rows"]}
    for name, rows in project.alternatives.items():
        if any(r.key == "p_green" for r in rows) or not any(r.key == "p_park" for r in rows):
            continue
        out = []
        for r in rows:
            if r.key != "p_park":
                out.append(r)
                continue
            share = tpl["p_park"]["share_in_major"] / 100
            park = round(r.ratio * share, 2)
            out.append(r.model_copy(update={"name": tpl["p_park"]["name"], "ratio": park}))
            out.append(LandUseRow(key="p_green", major="park_green", name=tpl["p_green"]["name"], ratio=round(r.ratio - park, 2)))
        project.alternatives[name] = out
    return project


def default_schedule(start_year: int | None = None) -> Schedule:
    """보상 2년 → 공사 4년 → 공급 5년의 일반적인 형태를 초기값으로 둔다(화면에서 수정)."""
    start = start_year or date.today().year + 1
    years = list(range(start, start + 9))
    majors = [m["key"] for m in standards.landuse_template()["majors"]]
    sales = [0, 0, 10, 20, 25, 20, 15, 10, 0]
    return Schedule(
        base_year=start - 1,
        years=years,
        comp=[50, 50, 0, 0, 0, 0, 0, 0, 0],
        const=[0, 10, 25, 30, 25, 10, 0, 0, 0],
        sales={m: list(sales) for m in majors},
        collect=[30.0, 40.0, 30.0],
    )


def new_project(name: str) -> Project:
    from .landuse.plan import default_rows

    fin = standards.finance_standard()
    return Project(
        name=name,
        cost_template_version=str(standards.cost_template()["version"]),
        cost_lines=default_cost_lines(),
        alternatives={"대안 1": default_rows()},
        schedule=default_schedule(),
        finance=FinanceParams(discount_rate=fin["discount_rate"], contingency_rate=fin["contingency_rate"],
                              vat_rate=fin["vat_rate"]),
        upper_plans=[UpperPlanCheck(plan=p) for p in DEFAULT_UPPER_PLANS],
    )
