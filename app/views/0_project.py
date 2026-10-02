"""프로젝트 생성·불러오기와 진행 현황."""
import streamlit as st

import ui
from newtown import store
from newtown.models import new_project

ui.step_header("신도시 개발사업 타당성 분석", "대상지 선정부터 재무적 타당성 분석까지 6단계로 진행합니다. 입력은 자동 저장됩니다.")
ui.vworld_notice()

left, right = st.columns(2)
with left, st.container(border=True):
    st.subheader("새 프로젝트")
    with st.form("new_project", border=False):
        name = st.text_input("사업명", placeholder="예: ○○ 공공주택지구")
        if st.form_submit_button("만들기", type="primary"):
            if not name.strip():
                st.error("사업명을 입력하세요.")
            elif name.strip() in store.list_projects():
                st.error("같은 이름의 프로젝트가 있습니다. 오른쪽에서 불러오세요.")
            else:
                ui.set_project(new_project(name.strip()))
                ui.persist()
                st.rerun()
with right, st.container(border=True):
    st.subheader("프로젝트 불러오기")
    names = store.list_projects()
    if names:
        sel = st.selectbox("저장된 프로젝트", names, label_visibility="collapsed")
        if st.button("불러오기"):
            ui.set_project(*store.load(sel))
            st.rerun()
    else:
        st.caption("저장된 프로젝트가 없습니다.")

prj = ui.project()
if prj is None:
    st.stop()

st.divider()
st.subheader(f"진행 현황 — {prj.name}")
ev = ui.evaluation()
m = ev.finance.metrics
has_parcels = len(ev.comp.register) > 0
c = st.columns(5)
c[0].metric("사업면적", f"{ev.total_area / 1e4:,.1f}만㎡" if ev.total_area else "-")
c[1].metric("편입 필지", f"{len(ev.comp.register):,}건" if has_parcels else "-")
c[2].metric("직접보상비", ui.eok(ev.comp.direct, 0) if has_parcels else "-")
c[3].metric("총사업비(예타 관점)", ui.eok(ev.costs.total_feasibility, 0) if ev.costs.total_feasibility else "-")
c[4].metric("PI", f"{m.pi:.2f}" if m.pi == m.pi and m.total_in > 0 else "-")

steps = [
    ("① 대상지 선정", "views/1_site.py", bool(prj.boundary) and has_parcels, "경계 설정, 편입 필지 수집"),
    ("② 상위계획·입지현황", "views/2_context.py", has_parcels and any(u.conformity != "미검토" for u in prj.upper_plans), "현황 집계, 상위계획 검토"),
    ("③ 사업비 산정", "views/3_cost.py", has_parcels and not ev.costs.missing, "토지세목조서·보상비, 조성비 등"),
    ("④ 토지이용계획", "views/4_landuse.py", abs(sum(r.ratio for r in prj.landuse) - 100) < 0.05, "용도별 비율·면적"),
    ("⑤ 기본구상", "views/5_concept.py", ev.program.population > 0, "인구·세대, 사업 일정"),
    ("⑥ 재무적 타당성", "views/6_finance.py", m.total_in > 0 and not ev.costs.missing, "PI·FNPV·FIRR, 민감도"),
]
for title, page, done, desc in steps:
    a, b, d = st.columns([3, 5, 2])
    a.page_link(page, label=title)
    b.caption(desc)
    d.markdown(":material/check_circle: 입력됨" if done else ":material/radio_button_unchecked: 입력 필요")
if ev.costs.missing:
    st.caption("단가·요율이 입력되지 않은 사업비 항목: " + ", ".join(ev.costs.missing))
