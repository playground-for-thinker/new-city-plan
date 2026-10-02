"""토지이용계획 — 신도시 사례 기반 비율과 검증."""
from __future__ import annotations

import pandas as pd

from .. import standards
from ..models import ConceptParams, LandUseRow


def majors() -> list[dict]:
    return standards.landuse_template()["majors"]


def major_name(key: str) -> str:
    return next((m["name"] for m in majors() if m["key"] == key), key)


def reference_major_pct(ref: pd.Series) -> dict[str, float]:
    """사례 1건의 대분류 비율(%). 빈칸은 0으로 본다(출처에서 다른 항목에 합산된 경우)."""
    return {m["key"]: float(ref[m["ref_col"]]) if pd.notna(ref.get(m["ref_col"])) else 0.0 for m in majors()}


def reference_average(refs: pd.DataFrame) -> dict[str, float]:
    """여러 사례의 대분류 평균 비율(%)을 합계 100으로 맞춰 돌려준다."""
    if refs.empty:
        return dict(standards.landuse_template()["fallback_major_pct"])
    # 항목이 빈칸인 사례(출처에 해당 구분이 없는 경우)는 그 항목의 평균에서 뺀다
    avg = {m["key"]: float(refs[m["ref_col"]].mean()) if refs[m["ref_col"]].notna().any() else 0.0 for m in majors()}
    total = sum(avg.values())
    return {k: v * 100 / total for k, v in avg.items()} if total else avg


def rows_from_major_pct(major_pct: dict[str, float], keep: list[LandUseRow] | None = None) -> list[LandUseRow]:
    """대분류 비율을 세분류로 나눈다.

    keep 이 주어지면 그 계획의 세분류 구성비·가격·밀도 설정을 유지하고 대분류 합계만 맞춘다.
    """
    tpl = standards.landuse_template()
    rows: list[LandUseRow] = []
    if keep:
        for m in majors():
            group = [r for r in keep if r.major == m["key"]]
            cur = sum(r.ratio for r in group)
            for r in group:
                share = r.ratio / cur if cur > 0 else 1 / len(group)
                rows.append(r.model_copy(update={"ratio": round(major_pct.get(m["key"], 0.0) * share, 2)}))
        return rows
    for t in tpl["rows"]:
        t = dict(t)
        share = t.pop("share_in_major", 100)
        rows.append(LandUseRow(ratio=round(major_pct.get(t["major"], 0.0) * share / 100, 2), **t))
    return rows


def default_rows() -> list[LandUseRow]:
    rows = rows_from_major_pct(reference_average(standards.landuse_references()))
    return balance(rows)


def balance(rows: list[LandUseRow], absorb_key: str | None = None) -> list[LandUseRow]:
    """합계가 100%가 되도록 차이를 한 행(기본: 비율이 가장 큰 행)에 반영한다."""
    diff = round(100 - sum(r.ratio for r in rows), 2)
    if abs(diff) < 0.005 or not rows:
        return rows
    target = next((r for r in rows if r.key == absorb_key), None) or max(rows, key=lambda r: r.ratio)
    target.ratio = round(max(target.ratio + diff, 0.0), 2)
    return rows


def scale_to_100(rows: list[LandUseRow]) -> list[LandUseRow]:
    """모든 행을 같은 비율로 늘리거나 줄여 합계를 100%로 맞춘다."""
    total = sum(r.ratio for r in rows)
    if total <= 0:
        return rows
    for r in rows:
        r.ratio = round(r.ratio * 100 / total, 2)
    return balance(rows)


def plan_table(rows: list[LandUseRow], total_area: float) -> pd.DataFrame:
    return pd.DataFrame([{
        "key": r.key, "대분류": major_name(r.major), "용도": r.name, "비율(%)": r.ratio,
        "면적(㎡)": round(total_area * r.ratio / 100, 0), "유상공급": r.paid,
    } for r in rows])


def major_pct(rows: list[LandUseRow]) -> dict[str, float]:
    out = {m["key"]: 0.0 for m in majors()}
    for r in rows:
        out[r.major] = out.get(r.major, 0.0) + r.ratio
    return out


def areas(rows: list[LandUseRow], total_area: float) -> dict[str, float]:
    """사업비·재무 계산에 쓰는 면적 집계."""
    mp = major_pct(rows)
    green = sum(r.ratio for r in rows if r.key == "p_green")
    return {
        "total_area": total_area,
        "paid_area": total_area * sum(r.ratio for r in rows if r.paid) / 100,
        "road_area": total_area * mp.get("road", 0) / 100,
        "park_area": total_area * (mp.get("park_green", 0) - green) / 100,   # 공원 (녹지 제외)
        "green_area": total_area * green / 100,
    }


def comparison_table(rows: list[LandUseRow], refs: pd.DataFrame) -> pd.DataFrame:
    """계획안과 사례의 대분류 비율 비교표 (행: 대분류, 열: 계획안·사례 평균·최소·최대)."""
    mp = major_pct(rows)
    data = []
    for m in majors():
        col = refs[m["ref_col"]].dropna() if not refs.empty else pd.Series(dtype=float)
        data.append({"대분류": m["name"], "계획안(%)": round(mp[m["key"]], 1),
                     "사례 평균(%)": round(col.mean(), 1) if len(col) else None,
                     "사례 최소(%)": round(col.min(), 1) if len(col) else None,
                     "사례 최대(%)": round(col.max(), 1) if len(col) else None})
    return pd.DataFrame(data)


def validate(rows: list[LandUseRow], total_area: float, population: float, concept: ConceptParams,
             refs: pd.DataFrame) -> list[tuple[str, str]]:
    """검증 결과 [(수준, 문구)]. 수준: error / warning / ok."""
    msgs: list[tuple[str, str]] = []
    total = sum(r.ratio for r in rows)
    if abs(total - 100) > 0.05:
        msgs.append(("error", f"비율 합계가 {total:.2f}%입니다. 100%가 되도록 조정하세요 (차이 {100 - total:+.2f}%p)."))
    if any(r.ratio < 0 for r in rows):
        msgs.append(("error", "음수 비율이 있습니다."))
    mp = major_pct(rows)
    park_area = total_area * mp.get("park_green", 0) / 100
    if concept.park_ratio_min > 0 or concept.park_per_capita_min > 0:
        need = max(total_area * concept.park_ratio_min / 100, population * concept.park_per_capita_min)
        if park_area + 1e-6 < need:
            msgs.append(("error", f"공원·녹지 {park_area:,.0f}㎡가 확보 기준 {need:,.0f}㎡"
                                  f"(부지면적의 {concept.park_ratio_min:g}% 또는 1인당 {concept.park_per_capita_min:g}㎡ 중 큰 값)에 못 미칩니다."))
        else:
            msgs.append(("ok", f"공원·녹지 확보 기준 충족 ({park_area:,.0f}㎡ ≥ {need:,.0f}㎡)."))
    if not refs.empty:
        for m in majors():
            col = refs[m["ref_col"]].dropna()
            # 도로·학교는 출처에 따라 기타 공공시설에 합산돼 있어 범위 비교에서 뺀다
            if len(col) < 3 or m["key"] in ("road", "school", "other_public"):
                continue
            v = mp[m["key"]]
            if v < col.min() - 0.05 or v > col.max() + 0.05:
                msgs.append(("warning", f"{m['name']} {v:.1f}%는 사례 범위({col.min():.1f}~{col.max():.1f}%)를 벗어납니다."))
    if not any(lvl == "error" for lvl, _ in msgs):
        msgs.insert(0, ("ok", "비율 합계 100%."))
    return msgs
