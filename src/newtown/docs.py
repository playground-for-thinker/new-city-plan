"""도움말 문서 — 사용 설명서와 산정 근거.

docs/*.md 의 `{{이름}}` 자리에 현재 기준정보(보상배율표, LH 단가, 요율표, 사례 자료)의 값을 채워 넣는다.
화면과 PDF가 같은 본문을 쓰므로, 기준정보를 바꾸면 문서의 표도 함께 바뀐다.
"""
from __future__ import annotations

import hashlib
import re

import pandas as pd

from . import standards
from .cost.total import BASE_LABELS, BASIS_LABELS, MODE_LABELS, SCHEDULE_LABELS
from .landuse import plan as landuse_plan
from .models import ConceptParams, default_cost_lines, default_schedule

DOCS_DIR = standards.ROOT / "docs"
DOCUMENTS = {"user_manual": "사용 설명서", "supplement": "산정 방법과 근거"}


def md_table(headers: list[str], rows: list[list]) -> str:
    def cell(v) -> str:
        if v is None or (isinstance(v, float) and v != v):
            return "—"
        if isinstance(v, bool):
            return "○" if v else ""
        if isinstance(v, float):
            return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.0f}"
        if isinstance(v, int):
            return f"{v:,}"
        return str(v).replace("|", "/")

    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


# ---------- 자리 채우기 ----------
def _versions() -> str:
    comp = standards.compensation_standard()
    lh = standards.lh_unit_costs()
    fin = standards.finance_standard()
    refs, disc = standards.landuse_references(), standards.lh_disclosures()
    return md_table(["기준정보", "현재 판·값", "파일"], [
        ["공시지가 보상배율표", f"{comp['version']} 기준", "data/standards/compensation_multipliers_2018.yaml"],
        ["사업비 항목 틀", f"제{standards.cost_template()['version']}판", "data/standards/cost_template.yaml"],
        ["LH 공개 조성원가 산정표", f"{len(disc)}건 (공개일 {disc['date'].min()} ~ {disc['date'].max()})", "data/references/lh_cost_disclosures.csv"],
        ["LH 추정자료 단가", f"{lh['version']}년판, {lh['price_base']} 기준 보정", "data/standards/lh_unit_costs_2024.yaml"],
        ["부대비 요율표", "2025년도 예산편성 운영기준", "data/standards/fee_rates.yaml"],
        ["신도시 토지이용계획 사례", f"{len(refs)}곳", "data/references/newtown_landuse.csv"],
        ["재무 기준값", f"할인율 {fin['discount_rate']:.1%}, 예비비 {fin['contingency_rate']:.0%}, 부가가치세 {fin['vat_rate']:.0%}",
         "data/standards/finance_params.yaml"],
    ])


def _multipliers() -> str:
    std = standards.compensation_standard()
    headers = ["시도", "전체", *std["zone_classes"], *std["use_classes"]]
    two = lambda x: None if x is None else f"{x:.2f}"
    return md_table(headers, [[s, two(v["전체"]), *map(two, v["zone"]), *map(two, v["use"])] for s, v in std["multipliers"].items()])


def _mapping(key: str, src: str) -> str:
    std = standards.compensation_standard()
    return md_table([src + "에 들어 있는 말", "배율표 분류"],
                    [[("앞부분이 " if "starts" in r else "") + f"“{r.get('contains') or r.get('starts')}”", r["class"]] for r in std[key]])


def _jimok_use() -> str:
    std = standards.compensation_standard()
    by_class: dict[str, list[str]] = {}
    for jimok, cls in std["jimok_to_use"].items():
        by_class.setdefault(cls, []).append(jimok)
    rows = [[", ".join(v), k] for k, v in by_class.items()] + [["그 밖의 지목 (도로, 하천, 구거, 유지, 잡종지, 묘지 등)", "공공·기타"]]
    return md_table(["지목", "배율표 이용상황 분류"], rows)


def _obstacle() -> str:
    std = standards.compensation_standard()
    return md_table(["사업 유형", "토지보상비 대비 비율"], [[k, f"{v:.0%}"] for k, v in std["obstacle_ratio"].items()])


def _disclosure_sample() -> str:
    s = standards.recent_disclosures()
    two = lambda x: None if x != x else f"{x:.2f}"
    rows = [[r["name"], r["date"], f"{r['area_total_m2'] / 1e4:.1f}", int(r["construction_per_m2"]), int(r["infra_per_m2"]),
             two(r["labor_rate_pct"]), two(r["sales_rate_pct"]), two(r["admin_rate_pct"]), two(r["capital_rate_pct"]), two(r["etc_rate_pct"])]
            for r in s.to_dict("records")]
    return md_table(["지구", "공개일", "면적(만㎡)", "조성비(원/㎡)", "기반시설설치비(원/㎡)", "직접인건비율(%)", "판매비율(%)",
                     "일반관리비율(%)", "자본비용률(%)", "그 밖의 비용율(%)"], rows)


def _benchmark() -> str:
    b, s = standards.disclosure_benchmark(), standards.recent_disclosures()
    return md_table(["항목", "초기값", "표본 범위"], [
        ["조성비 실적 단가", f"{b['construction_per_m2']:,.0f}원/㎡", f"{s['construction_per_m2'].min():,.0f} ~ {s['construction_per_m2'].max():,.0f}"],
        ["기반시설설치비 실적 단가", f"{b['infra_per_m2']:,.0f}원/㎡", f"{s['infra_per_m2'].min():,.0f} ~ {s['infra_per_m2'].max():,.0f}"],
        ["직접인건비율", f"{b['labor_rate_pct']:.2f}%", f"{s['labor_rate_pct'].min():.2f} ~ {s['labor_rate_pct'].max():.2f}"],
        ["판매비율", f"{b['sales_rate_pct']:.2f}%", f"{s['sales_rate_pct'].min():.2f} ~ {s['sales_rate_pct'].max():.2f}"],
        ["일반관리비율", f"{b['admin_rate_pct']:.2f}%", f"{s['admin_rate_pct'].min():.2f} ~ {s['admin_rate_pct'].max():.2f}"],
        ["자본비용률", f"{b['capital_rate_pct']:.2f}%", f"{s['capital_rate_pct'].min():.2f} ~ {s['capital_rate_pct'].max():.2f}"],
        ["그 밖의 비용율", f"{b['etc_rate_pct']:.2f}%", f"{s['etc_rate_pct'].min():.2f} ~ {s['etc_rate_pct'].max():.2f}"],
    ])


def _lh_basic() -> str:
    lh = standards.lh_unit_costs()
    rows = [[t, "초과 구간" if r["max_area"] is None else f"{r['max_area'] / 1e4:,.0f}만㎡ 이하", r["price"]]
            for t, items in lh["basic_housing"].items() for r in items]
    return md_table(["지형", "사업면적", "단가(원/㎡)"], rows)


def _lh_reference() -> str:
    return md_table(["구분", "항목", "단위", "단가(원)"],
                    [[r["group"], r["item"], r["unit"], r["price"]] for r in standards.lh_unit_costs()["reference"]])


def _fee_tables() -> str:
    out = []
    for t in standards.fee_rates()["tables"].values():
        rows = t["rows"]
        out.append(f"**{t['label']}**\n\n" + md_table(["공사비(억원)까지", *[f"{r[0]:g}" for r in rows]], [["요율(%)", *[f"{r[1]:.2f}" for r in rows]]]))
    return "\n\n".join(out)


def _cost_lines() -> str:
    rows = []
    for l in default_cost_lines():
        if l.method == "auto":
            how, init = "보상비 계산 결과", "—"
        elif l.method == "qty_price":
            how = f"{BASIS_LABELS[l.basis]} × 단가"
            init = "LH 단가표(지형·규모별)" if l.auto else (f"{l.unit_price:,.0f}원/{l.unit}" if l.unit_price else "0 (입력 필요)")
        elif l.method == "rate":
            how = f"{BASE_LABELS[l.base]} × 요율"
            init = standards.fee_rates()["tables"][l.table]["label"] if l.auto else (f"{l.rate:.2%}" if l.rate else "0 (입력 필요)")
        elif l.method == "capital":
            how, init = "순투입액 누적액 × 자본비용률", f"{l.rate:.2%}"
        else:
            how, init = "금액 직접 입력", "0 (입력 필요)"
        rows.append([MODE_LABELS.get(l.group, "공통"), l.category, l.name, how, init, l.vat, l.bid, SCHEDULE_LABELS[l.schedule]])
    return md_table(["방식", "구분", "항목", "계산", "초기값", "부가세 가산", "낙찰률 적용", "투입 일정"], rows)


def _landuse_refs() -> str:
    refs = standards.landuse_references()
    one = lambda x: None if x != x else f"{x:.1f}"
    rows = [[r["name"], r["generation"], round(r["total_area_m2"] / 1e4), *[one(r[c]) for c in standards.REF_PCT_COLS]] for r in refs.to_dict("records")]
    return md_table(["지구", "기수", "면적(만㎡)", "주택", "상업·업무", "자족", "공원·녹지", "도로", "학교", "기타 공공"], rows)


def _landuse_avg() -> str:
    refs = standards.landuse_references()
    groups = [("전체", refs)] + [(f"{g} 신도시", refs[refs["generation"] == g]) for g in sorted(refs["generation"].dropna().unique())]
    rows = []
    for label, df in groups:
        avg = landuse_plan.reference_average(df)
        rows.append([f"{label} ({len(df)}곳)", *[f"{avg[m['key']]:.1f}" for m in landuse_plan.majors()]])
    return md_table(["표본", *[m["name"] for m in landuse_plan.majors()]], rows)


def _landuse_split() -> str:
    tpl = standards.landuse_template()
    rows = []
    for r in tpl["rows"]:
        density = ""
        if r.get("housing_type") == "apartment":
            density = f"용적률 {r['far']}%, 세대당 연면적 {r['unit_gfa']}㎡"
        elif r.get("housing_type") == "detached":
            density = f"필지 {r['lot_size']}㎡, 필지당 {r['units_per_lot']}가구"
        rows.append([landuse_plan.major_name(r["major"]), r["name"], f"{r['share_in_major']}%", r.get("paid", False), density])
    return md_table(["대분류", "세분류", "대분류 안 구성비", "유상공급", "밀도 가정"], rows)


def _concept_defaults() -> str:
    c = ConceptParams()
    return md_table(["항목", "초기값"], [
        ["세대당 인구", f"{c.persons_per_household:g}인"], ["초등학교 1개교당 세대수", f"{c.households_per_elementary:,.0f}세대"],
        ["1인 1일 급수량", f"{c.water_lpcd:g}L"], ["오수 전환율", f"{c.sewage_ratio:g}"],
    ])


def _schedule_default() -> str:
    s = default_schedule(2027)
    sales = next(iter(s.sales.values()))
    rows = [[f"{i + 1}차 연도", s.comp[i], s.const[i], sales[i]] for i in range(len(s.years))]
    return md_table(["연차", "보상(%)", "공사(%)", "용지 공급(%)"], rows) + \
        f"\n\n대금 회수 조건 초기값: 계약 연도 {s.collect[0]:g}%, 1년 뒤 {s.collect[1]:g}%, 2년 뒤 {s.collect[2]:g}%."


PLACEHOLDERS = {
    "REF_COUNT": lambda: str(len(standards.landuse_references())), "DISC_COUNT": lambda: str(len(standards.lh_disclosures())),
    "VERSIONS": _versions, "MULTIPLIERS": _multipliers, "ZONE_MAPPING": lambda: _mapping("zone_mapping", "용도지역 이름"),
    "USE_MAPPING": lambda: _mapping("use_mapping", "이용상황"), "JIMOK_USE": _jimok_use, "OBSTACLE": _obstacle,
    "DISCLOSURE_SAMPLE": _disclosure_sample, "BENCHMARK": _benchmark, "LH_BASIC": _lh_basic, "LH_REFERENCE": _lh_reference,
    "FEE_TABLES": _fee_tables, "COST_LINES": _cost_lines, "LANDUSE_REFS": _landuse_refs, "LANDUSE_AVG": _landuse_avg,
    "LANDUSE_SPLIT": _landuse_split, "CONCEPT_DEFAULTS": _concept_defaults, "SCHEDULE_DEFAULT": _schedule_default,
}


def render(name: str) -> str:
    """문서 본문(Markdown). `{{이름}}` 자리를 현재 기준정보의 표로 채운다."""
    text = (DOCS_DIR / f"{name}.md").read_text(encoding="utf-8")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: PLACEHOLDERS[m.group(1)](), text)


def content_hash(name: str) -> str:
    """본문과 그림을 합친 지문. PDF가 현재 본문으로 만들어졌는지 확인하는 데 쓴다."""
    text = render(name)
    h = hashlib.sha256(text.encode("utf-8"))
    for img in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
        path = DOCS_DIR / img
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()


def pdf_path(name: str):
    return DOCS_DIR / f"{name}.pdf"


def split_images(text: str) -> list[tuple[str, str, str]]:
    """본문을 글 덩어리와 그림으로 나눈다. [('md', 본문, ''), ('img', 경로, 설명), …]"""
    parts: list[tuple[str, str, str]] = []
    pos = 0
    for m in re.finditer(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", text, flags=re.M):
        if text[pos:m.start()].strip():
            parts.append(("md", text[pos:m.start()], ""))
        parts.append(("img", m.group(2), m.group(1)))
        pos = m.end()
    if text[pos:].strip():
        parts.append(("md", text[pos:], ""))
    return parts
