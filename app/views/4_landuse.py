"""④ 토지이용계획 수립 — 신도시 사례 기반 비율, 직접 수정."""
import pandas as pd
import streamlit as st

import charts
import ui
from newtown import pipeline, standards
from newtown.landuse import plan as L

prj = ui.need_project()
ui.step_header("④ 토지이용계획 수립", "국내 신도시 사례의 토지이용 비율을 불러온 뒤, 용도별 비율이나 면적을 직접 수정합니다.")

total_area = pipeline.project_area(prj, ui.parcels())
refs = standards.landuse_references()
if total_area <= 0:
    st.info("사업구역이 아직 없어 면적은 0으로 표시됩니다. 비율은 지금 정해 둘 수 있습니다.")


def bump() -> None:
    """표 편집기를 새 값으로 다시 그리도록 키를 바꾼다."""
    st.session_state["lu_ver"] = st.session_state.get("lu_ver", 0) + 1
    ui.persist()


# ---------- 대안 관리 ----------
a = st.columns([3, 2, 1, 1])
names = list(prj.alternatives)
picked = a[0].selectbox("토지이용계획 대안", names, index=names.index(prj.active_alt))
if picked != prj.active_alt:
    prj.active_alt = picked
    bump()
    st.rerun()
new_name = a[1].text_input("새 대안 이름", placeholder=f"대안 {len(names) + 1}", label_visibility="visible")
a[2].write("")
a[2].write("")
if a[2].button("현재 안 복제", use_container_width=True):
    name = new_name.strip() or f"대안 {len(names) + 1}"
    if name in prj.alternatives:
        st.error("같은 이름의 대안이 있습니다.")
    else:
        prj.alternatives[name] = [r.model_copy() for r in prj.landuse]
        prj.active_alt = name
        bump()
        st.rerun()
a[3].write("")
a[3].write("")
if a[3].button("대안 삭제", use_container_width=True, disabled=len(names) <= 1):
    del prj.alternatives[prj.active_alt]
    prj.active_alt = next(iter(prj.alternatives))
    bump()
    st.rerun()

rows = prj.landuse

# ---------- 사례 불러오기 ----------
with st.container(border=True):
    st.markdown("**사례 비율 불러오기**")
    if refs.empty:
        st.caption("사례 파일(data/references/newtown_landuse.csv)이 없어 임시 기본 비율을 쓰고 있습니다.")
    else:
        gens = sorted(refs["generation"].dropna().unique())
        options = ["전체 사례 평균"] + [f"{g} 신도시 평균" for g in gens] + refs["name"].tolist()
        r1, r2 = st.columns([4, 1])
        choice = r1.selectbox("기준 사례", options, label_visibility="collapsed")
        if choice == "전체 사례 평균":
            pct = L.reference_average(refs)
        elif choice.endswith("신도시 평균"):
            pct = L.reference_average(refs[refs["generation"] == choice.split(" ")[0]])
        else:
            pct = L.reference_major_pct(refs[refs["name"] == choice].iloc[0])
        st.caption("대분류 비율 — " + " · ".join(f"{L.major_name(k)} {v:.1f}%" for k, v in pct.items())
                   + ". 불러오면 대분류 비율이 이 값으로 바뀌고, 대분류 안의 세분류 구성비와 가격·밀도 설정은 유지됩니다.")
        if r2.button("불러오기", type="primary", use_container_width=True):
            prj.alternatives[prj.active_alt] = L.balance(L.rows_from_major_pct(pct, keep=rows))
            bump()
            st.rerun()

# ---------- 계획표 편집 ----------
st.subheader("토지이용계획표")
mode = st.radio("입력 기준", ["비율(%)", "면적(㎡)"], horizontal=True, disabled=total_area <= 0,
                help="비율로 입력하면 면적이, 면적으로 입력하면 비율이 자동 계산됩니다.")
by_area = mode == "면적(㎡)" and total_area > 0
table = L.plan_table(rows, total_area)
num = st.column_config.NumberColumn
edited = st.data_editor(
    table, hide_index=True, use_container_width=True, height=35 * (len(table) + 1) + 3,
    column_order=["대분류", "용도", "비율(%)", "면적(㎡)", "유상공급"],
    disabled=["대분류", "면적(㎡)" if not by_area else "비율(%)"],
    key=f"lu_editor_{prj.active_alt}_{by_area}_{st.session_state.get('lu_ver', 0)}",
    column_config={"비율(%)": num(format="%.2f", min_value=0.0, max_value=100.0, step=0.1),
                   "면적(㎡)": num(format="localized", min_value=0), "용도": st.column_config.TextColumn(required=True)})
changed = False
for r, e in zip(rows, edited.to_dict("records")):
    ratio = round(float(e["면적(㎡)"] or 0) / total_area * 100, 4) if by_area else float(e["비율(%)"] or 0)
    name, paid = str(e["용도"]), bool(e["유상공급"])
    if abs(ratio - r.ratio) > 1e-6 or name != r.name or paid != r.paid:
        r.ratio, r.name, r.paid = ratio, name, paid
        changed = True
if changed:
    bump()
    st.rerun()

total = sum(r.ratio for r in rows)
paid_ratio = sum(r.ratio for r in rows if r.paid)
s = st.columns(4)
s[0].metric("비율 합계", f"{total:.2f}%", delta=None if abs(total - 100) < 0.005 else f"{total - 100:+.2f}%p", delta_color="off")
s[1].metric("유상공급 비율", f"{paid_ratio:.1f}%")
s[2].metric("유상공급 면적", f"{total_area * paid_ratio / 100:,.0f}㎡")
s[3].metric("무상공급(공공시설) 면적", f"{total_area * (total - paid_ratio) / 100:,.0f}㎡")

if abs(total - 100) >= 0.005:
    b = st.columns([2, 3, 2])
    if b[0].button("전체를 비례 조정해 100% 맞추기"):
        L.scale_to_100(rows)
        bump()
        st.rerun()
    absorb = b[1].selectbox("차이를 반영할 용도", [r.name for r in rows], index=max(range(len(rows)), key=lambda i: rows[i].ratio),
                            label_visibility="collapsed")
    if b[2].button(f"차이 {100 - total:+.2f}%p를 이 용도에 반영"):
        L.balance(rows, next(r.key for r in rows if r.name == absorb))
        bump()
        st.rerun()

# ---------- 검증 ----------
ev = ui.evaluation()
with st.expander("법정 확보 기준 설정 (공원·녹지)"):
    st.caption("「도시공원 및 녹지 등에 관한 법률 시행규칙」의 개발계획 규모별 확보 기준을 사업 유형·규모에 맞게 입력하세요. "
               "두 값 중 큰 면적을 기준으로 검사합니다. 0이면 검사하지 않습니다. 이 검사는 계획표의 '공원·녹지' 면적만 셉니다. "
               "「공공주택 업무처리지침」처럼 하천·공공공지 등을 공원·녹지에 포함하는 기준이라면 그만큼 감안해 판단하세요.")
    g = st.columns(2)
    prj.concept.park_ratio_min = g[0].number_input("부지면적 대비 최소 비율(%)", 0.0, 50.0, prj.concept.park_ratio_min, 1.0)
    prj.concept.park_per_capita_min = g[1].number_input("1인당 최소 면적(㎡)", 0.0, 50.0, prj.concept.park_per_capita_min, 1.0)
    notes = standards.REFERENCES_DIR / "park_green_rule_notes.md"
    if notes.exists():
        st.markdown(notes.read_text(encoding="utf-8"))
for level, msg in L.validate(rows, total_area, ev.program.population, prj.concept, refs):
    {"error": st.error, "warning": st.warning, "ok": st.success}[level](msg)

# ---------- 사례 비교 ----------
st.subheader("사례 비교")
cmp = L.comparison_table(rows, refs)
l, r = st.columns([3, 2])
with l:
    st.altair_chart(charts.landuse_compare(cmp), use_container_width=True)
    st.caption("검은 선은 사례의 최소~최대 범위입니다. 출처에 따라 도로·학교가 '기타 공공시설'에 합산된 사례가 있어, "
               "이 세 항목의 사례 값은 참고용입니다.")
with r:
    st.dataframe(cmp, hide_index=True, use_container_width=True)

if not refs.empty:
    with st.expander(f"사례 자료 ({len(refs)}곳)와 출처"):
        show = refs.rename(columns={
            "name": "지구", "generation": "기수", "total_area_m2": "면적(㎡)", "population": "계획인구", "households": "세대수",
            "housing_pct": "주택(%)", "commercial_pct": "상업·업무(%)", "self_sufficient_pct": "자족(%)", "park_green_pct": "공원·녹지(%)",
            "road_pct": "도로(%)", "school_pct": "학교(%)", "other_public_pct": "기타 공공(%)", "source": "출처", "source_url": "링크", "note": "비고"})
        st.dataframe(show, hide_index=True, use_container_width=True, column_config={
            "링크": st.column_config.LinkColumn(display_text="열기"), "면적(㎡)": num(format="localized"),
            "계획인구": num(format="localized"), "세대수": num(format="localized")})
ui.persist()
