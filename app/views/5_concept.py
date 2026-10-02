"""⑤ 기본구상안 수립 — 인구·주택 계획, 구상도, 사업 일정."""
import folium
import pandas as pd
import streamlit as st
from folium.plugins import Draw
from shapely.geometry import shape
from streamlit_folium import st_folium

import ui
from newtown import pipeline, standards
from newtown.landuse import plan as L
from newtown.site import parcels as P

prj = ui.need_project()
ui.step_header("⑤ 기본구상안 수립", "토지이용계획에서 세대수·인구·밀도와 기반시설 소요를 산정하고, 구상도와 사업 일정을 정합니다.")
rows = prj.landuse
total_area = pipeline.project_area(prj, ui.parcels())
num = st.column_config.NumberColumn

# ---------- 주택·인구 계획 ----------
st.subheader("주택·인구 계획")
st.caption("용적률·세대당 연면적·필지 규모·세대당 인구의 초기값은 프로그램의 가정값입니다. 사업 여건에 맞게 수정하세요.")
apt = [r for r in rows if r.housing_type == "apartment"]
det = [r for r in rows if r.housing_type == "detached"]
c1, c2, c3 = st.columns([4, 4, 2])
changed = False


def edit(col, title: str, group: list, fields: dict[str, str], key: str) -> bool:
    """주택 유형별 밀도 가정 편집. fields: 모델 속성 → 열 이름. 값이 바뀌었으면 True."""
    dirty = False
    col.markdown(f"**{title}**")
    df = pd.DataFrame([{"주택 유형": r.name, "면적(㎡)": round(total_area * r.ratio / 100),
                        **{label: getattr(r, attr) for attr, label in fields.items()}} for r in group])
    out = col.data_editor(df, hide_index=True, use_container_width=True, disabled=["주택 유형", "면적(㎡)"], key=key,
                          column_config={"면적(㎡)": num(format="localized"), **{label: num(min_value=0.1, required=True) for label in fields.values()}})
    for r, e in zip(group, out.to_dict("records")):
        for attr, label in fields.items():
            v = e[label]
            v = None if v is None or v != v else float(v)
            if v != getattr(r, attr):
                setattr(r, attr, v)
                dirty = True
    return dirty


if apt:
    changed = edit(c1, "공동주택", apt, {"far": "용적률(%)", "unit_gfa": "세대당 연면적(㎡)"}, f"apt_{prj.active_alt}")
if det:
    changed = edit(c2, "단독주택", det, {"lot_size": "필지 면적(㎡)", "units_per_lot": "필지당 가구수"}, f"det_{prj.active_alt}") or changed
prj.concept.persons_per_household = c3.number_input("세대당 인구(인)", 1.0, 5.0, prj.concept.persons_per_household, 0.1)
if changed:
    ui.persist()
    st.rerun()

ev = ui.evaluation()
pg = ev.program
k = st.columns(4)
k[0].metric("계획 세대수", f"{pg.households:,.0f}세대")
k[1].metric("계획 인구", f"{pg.population:,.0f}인")
k[2].metric("총밀도", f"{pg.gross_density:,.0f}인/ha", help="계획인구 ÷ 사업면적")
k[3].metric("순밀도", f"{pg.net_density:,.0f}인/ha", help="계획인구 ÷ 주택건설용지 면적")
l, r = st.columns(2)
l.dataframe(pg.housing.drop(columns="key"), hide_index=True, use_container_width=True,
            column_config={c: num(format="localized") for c in ["면적(㎡)", "세대수", "인구(인)"]})
refs = standards.landuse_references()
dens = refs.dropna(subset=["population", "total_area_m2"]) if not refs.empty else refs
if len(dens):
    d = pd.DataFrame({"지구": dens["name"], "기수": dens["generation"],
                      "총밀도(인/ha)": (dens["population"] / (dens["total_area_m2"] / 1e4)).round(0)})
    d = pd.concat([pd.DataFrame([{"지구": f"계획안({prj.active_alt})", "기수": "-", "총밀도(인/ha)": round(pg.gross_density)}]), d])
    r.dataframe(d, hide_index=True, use_container_width=True, height=180)
    r.caption("사례 신도시의 총밀도(계획인구 ÷ 총면적)와 비교")

# ---------- 기반시설 소요 ----------
st.subheader("기반시설 소요")
i = st.columns(3)
prj.concept.households_per_elementary = i[0].number_input("초등학교 1개교당 세대수", 1000.0, 20000.0, float(prj.concept.households_per_elementary), 500.0)
prj.concept.water_lpcd = i[1].number_input("1인 1일 급수량(L)", 100.0, 600.0, float(prj.concept.water_lpcd), 10.0)
prj.concept.sewage_ratio = i[2].number_input("오수 전환율", 0.5, 1.0, prj.concept.sewage_ratio, 0.05)
pg = ui.evaluation().program
st.dataframe(pg.infra, hide_index=True, use_container_width=True, column_config={"값": num(format="localized")})

# ---------- 구상도 ----------
st.subheader("구상도 (블록 배치)")
if not prj.boundary:
    st.info("① 대상지 선정에서 경계를 설정하면 블록을 그릴 수 있습니다.")
else:
    st.caption("지도에 블록을 그리고 '그린 블록 추가'를 누른 뒤, 아래 표에서 블록별 용도를 지정하세요. 블록 면적 합계를 토지이용계획표와 비교합니다.")
    use_names = {r.key: r.name for r in rows}
    color_of = {r.key: ui.SERIES[n % len(ui.SERIES)] for n, r in enumerate(rows)}
    m = ui.base_map(ui.boundary_center(prj), 13)
    ui.add_boundary(m, prj)
    for blk in prj.blocks:
        folium.GeoJson(blk["geometry"], tooltip=use_names.get(blk["use"], "미지정"), style_function=lambda _, c=color_of.get(blk["use"], ui.MUTED): {
            "fillColor": c, "color": "#ffffff", "weight": 1.5, "fillOpacity": 0.7}).add_to(m)
    Draw(export=False, draw_options={"polyline": False, "circle": False, "marker": False, "circlemarker": False}).add_to(m)
    out = st_folium(m, height=480, use_container_width=True, returned_objects=["all_drawings"], key=f"concept_map_{len(prj.blocks)}")
    drawings = [d for d in (out or {}).get("all_drawings") or [] if d.get("geometry", {}).get("type") == "Polygon"]
    b = st.columns([1, 1, 4])
    if b[0].button("그린 블록 추가", type="primary", disabled=not drawings):
        prj.blocks += [{"geometry": d["geometry"], "use": rows[0].key} for d in drawings]
        ui.persist()
        st.rerun()
    if b[1].button("블록 모두 지우기", disabled=not prj.blocks):
        prj.blocks = []
        ui.persist()
        st.rerun()
    if prj.blocks:
        bt = pd.DataFrame([{"블록": n + 1, "용도": use_names.get(blk["use"], rows[0].name),
                            "면적(㎡)": round(P.area_m2(shape(blk["geometry"])))} for n, blk in enumerate(prj.blocks)])
        l, r = st.columns(2)
        eb = l.data_editor(bt, hide_index=True, use_container_width=True, disabled=["블록", "면적(㎡)"], key=f"blocks_{len(prj.blocks)}",
                           column_config={"용도": st.column_config.SelectboxColumn(options=list(use_names.values()), required=True),
                                          "면적(㎡)": num(format="localized")})
        name_to_key = {v: k_ for k_, v in use_names.items()}
        new_uses = [name_to_key[u] for u in eb["용도"]]
        if new_uses != [blk["use"] for blk in prj.blocks]:
            for blk, u in zip(prj.blocks, new_uses):
                blk["use"] = u
            ui.persist()
            st.rerun()
        drawn = eb.groupby("용도")["면적(㎡)"].sum()
        cmp = L.plan_table(rows, total_area)[["용도", "면적(㎡)"]].rename(columns={"면적(㎡)": "계획 면적(㎡)"})
        cmp["블록 면적(㎡)"] = cmp["용도"].map(drawn).fillna(0)
        cmp["차이(㎡)"] = cmp["블록 면적(㎡)"] - cmp["계획 면적(㎡)"]
        r.dataframe(cmp, hide_index=True, use_container_width=True, column_config={c: num(format="localized") for c in cmp.columns[1:]})

# ---------- 사업 일정 ----------
st.subheader("사업 일정")
st.caption("연차별 보상·공사 투입 비율과 용도별 용지 공급(계약) 비율을 입력합니다. 각 열의 합계는 100%여야 합니다. "
           "초기값은 보상 2년·공사 5년·공급 6년의 예시 일정입니다.")
sch = prj.schedule
t = st.columns(3)
start = int(t[0].number_input("사업 착수 연도", 2000, 2100, sch.years[0], 1))
n_years = int(t[1].number_input("사업 기간(년)", 2, 30, len(sch.years), 1))
base_year = int(t[2].number_input("분석 기준연도", 1990, 2100, sch.base_year, 1, help="현재가치의 기준 시점. 지침은 조사 의뢰 전년도 말을 기준으로 합니다."))
majors_paid = [m_ for m_ in L.majors() if any(r.paid and r.major == m_["key"] and r.ratio > 0 for r in rows)]


def fit(pcts: list[float]) -> list[float]:
    return (list(pcts) + [0.0] * n_years)[:n_years]


if start != sch.years[0] or n_years != len(sch.years) or base_year != sch.base_year:
    sch.years = [start + n for n in range(n_years)]
    sch.base_year = base_year
    sch.comp, sch.const = fit(sch.comp), fit(sch.const)
    sch.sales = {k_: fit(v) for k_, v in sch.sales.items()}
    ui.persist()
    st.rerun()

sdf = pd.DataFrame({"연도": sch.years, "보상(%)": fit(sch.comp), "공사(%)": fit(sch.const),
                    **{f"공급: {m_['name']}(%)": fit(sch.sales.get(m_["key"], [])) for m_ in majors_paid}})
es = st.data_editor(sdf, hide_index=True, use_container_width=True, disabled=["연도"], key=f"schedule_{start}_{n_years}",
                    column_config={c: num(min_value=0.0, max_value=100.0, format="%.0f") for c in sdf.columns[1:]})
sums = es.drop(columns="연도").sum()
bad = sums[(sums - 100).abs() > 0.5]
if len(bad):
    st.warning("합계가 100%가 아닌 열: " + ", ".join(f"{c} {v:g}%" for c, v in bad.items()) + " — 계산할 때는 합계를 100%로 환산합니다.")
new_comp, new_const = es["보상(%)"].fillna(0).tolist(), es["공사(%)"].fillna(0).tolist()
new_sales = dict(sch.sales)
for m_ in majors_paid:
    new_sales[m_["key"]] = es[f"공급: {m_['name']}(%)"].fillna(0).tolist()

st.markdown("**대금 회수 조건**")
cc = st.columns(4)
collect = (list(sch.collect) + [0.0, 0.0, 0.0])[:3]
new_collect = [cc[n].number_input(label, 0.0, 100.0, float(collect[n]), 5.0)
               for n, label in enumerate(["계약 연도 회수(%)", "1년 뒤 회수(%)", "2년 뒤 회수(%)"])]
cc[3].metric("회수 합계", f"{sum(new_collect):g}%")
while len(new_collect) > 1 and new_collect[-1] == 0:
    new_collect.pop()
if (new_comp, new_const, new_sales, new_collect) != (sch.comp, sch.const, sch.sales, sch.collect):
    sch.comp, sch.const, sch.sales, sch.collect = new_comp, new_const, new_sales, new_collect
    ui.persist()
ui.persist()
