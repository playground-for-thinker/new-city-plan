"""신도시 개발사업 타당성 분석 시스템 — 실행: uv run streamlit run app/main.py"""
import sys
from pathlib import Path

import streamlit as st

# 패키지를 설치하지 않은 환경(Streamlit Community Cloud 등)에서도 src/newtown 을 찾도록 경로를 더한다
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ui  # noqa: E402

st.set_page_config(page_title="신도시 타당성 분석", page_icon=":material/location_city:", layout="wide")

nav = st.navigation([
    st.Page("views/0_project.py", title="프로젝트", icon=":material/folder_open:", default=True),
    st.Page("views/1_site.py", title="① 대상지 선정", icon=":material/map:"),
    st.Page("views/2_context.py", title="② 상위계획·입지현황", icon=":material/travel_explore:"),
    st.Page("views/3_cost.py", title="③ 사업비 산정", icon=":material/payments:"),
    st.Page("views/4_landuse.py", title="④ 토지이용계획", icon=":material/grid_view:"),
    st.Page("views/5_concept.py", title="⑤ 기본구상", icon=":material/architecture:"),
    st.Page("views/6_finance.py", title="⑥ 재무적 타당성", icon=":material/monitoring:"),
])

with st.sidebar:
    prj = ui.project()
    if prj is None:
        st.caption("열린 프로젝트 없음")
    else:
        st.markdown(f"**{prj.name}**")
        n = int(ui.parcels()["included"].sum()) if len(ui.parcels()) else 0
        st.caption(f"경계 {'설정됨' if prj.boundary else '미설정'} · 편입 필지 {n:,}건 · {prj.active_alt}")
    st.caption("VWorld 인증키: " + ("설정됨" if ui.vworld_ready() else "미설정"))

nav.run()
