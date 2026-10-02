"""② 상위계획 및 입지현황 분석."""
import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

import charts
import ui
from newtown.context import analysis
from newtown.models import UpperPlanCheck
from newtown.site import parcels as P
from newtown.vworld.client import VWorldError

prj = ui.need_boundary()
ui.step_header("② 상위계획 및 입지현황 분석", "편입 필지의 지목·용도지역·소유·공시지가 현황을 집계하고 상위계획 부합 여부를 정리합니다.")

pcl = ui.parcels()
if pcl.empty:
    st.info("먼저 ① 대상지 선정에서 편입 필지를 수집하세요.")
    st.page_link("views/1_site.py", label="① 대상지 선정으로 이동", icon=":material/map:")
    st.stop()
if "가상" in prj.parcel_source:
    st.warning("현재 필지는 **가상 필지(시험용)** 입니다. 아래 현황은 실제 자료가 아닙니다.", icon=":material/science:")

boundary = P.boundary_shape(prj.boundary)
ov = analysis.overview(pcl, P.area_m2(boundary))
area = ov["parcel_area"]

# ---------- 개요 ----------
c = st.columns(5)
c[0].metric("사업면적(편입면적)", f"{area / 1e4:,.1f}만㎡")
c[1].metric("편입 필지", f"{ov['parcel_count']:,}건")
c[2].metric("평균 공시지가(㎡당)", f"{ov['avg_price'] / 1e4:,.1f}만원" if ov["avg_price"] == ov["avg_price"] else "-")
c[3].metric("농지(전·답·과수원)", f"{ov['farm_area'] / area:.1%}" if area else "-")
c[4].metric("개발제한구역", f"{ov['gb_area'] / area:.1%}" if area else "-")

# ---------- VWorld 속성 보강 ----------
_inc = pcl[pcl["included"]]
_needs_attrs = bool(len(_inc)) and bool(_inc[["zone", "use", "owner"]].isna().any(axis=1).mean() > 0.2)  # 속성이 많이 비어 있으면 펼쳐 둔다
with st.expander("VWorld에서 필지 속성 보강", expanded=_needs_attrs and ui.vworld_ready()):
    client = ui.vworld_client()
    st.caption("연속지적도에는 지번·공시지가만 있습니다. 보상배율 적용에 필요한 용도지역·이용상황·소유구분·공부면적을 추가로 조회합니다.")
    a, b = st.columns(2)
    with a:
        st.markdown("**용도지역 레이어 결합**")
        st.caption("용도지역·개발제한구역 도형과 겹쳐 필지의 용도지역을 채웁니다. 호출 수가 적어 빠릅니다.")
        if st.button("용도지역 결합", disabled=client is None):
            try:
                with st.spinner("용도지역 레이어 조회 중…"):
                    ui.set_parcels(P.enrich_zoning(client, pcl, boundary))
                st.rerun()
            except VWorldError as e:
                st.error(str(e))
    with b:
        st.markdown("**필지 속성 상세 조회**")
        n_inc = int(pcl["included"].sum())
        n_dong = pcl.loc[pcl["included"], "pnu"].str[:10].nunique()
        st.caption(f"편입 필지 {n_inc:,}건의 토지특성(지목·면적·용도지역·이용상황·공시지가)과 소유구분을 조회합니다. "
                   f"법정동·리({n_dong}곳) 단위로 한꺼번에 받아 보통 수십 초 안에 끝납니다.")
        if st.button("상세 조회 시작", disabled=client is None):
            bar = st.progress(0.0, "필지 속성 조회 중…")
            years = pcl["price_year"].dropna().astype(str)
            attrs, failed = client.fetch_parcel_attributes(
                pcl.loc[pcl["included"], "pnu"], price_year=years.mode().iloc[0] if len(years) else None,
                progress=lambda i, n: bar.progress(i / n, f"필지 속성 조회 중… ({i:,}/{n:,} 단계)"))
            ui.set_parcels(P.apply_attributes(pcl, attrs))
            st.session_state["attr_failed"] = failed
            st.rerun()
    if client is None:
        st.caption("VWorld 인증키를 설정해야 쓸 수 있습니다.")
    if st.session_state.get("attr_failed"):
        st.warning(f"{len(st.session_state['attr_failed']):,}건은 조회에 실패했습니다. 다시 실행하면 실패한 필지만 재조회합니다(성공한 응답은 캐시됨).")

# ---------- 현황 집계 ----------
st.subheader("현황 집계")
THEMES = {"지목": "jimok", "용도지역": "zone", "소유구분": "owner_group", "이용상황": "use"}
tabs = st.tabs(list(THEMES) + ["자료 누락"])
for tab, (label, col) in zip(tabs, THEMES.items()):
    with tab:
        tbl = analysis.summary_by(pcl, col)
        l, r = st.columns([3, 2])
        l.dataframe(tbl, hide_index=True, use_container_width=True, column_config={
            "면적(㎡)": st.column_config.NumberColumn(format="localized"),
            "평균 공시지가(원/㎡)": st.column_config.NumberColumn(format="localized")})
        r.altair_chart(charts.hbar(tbl.head(10), tbl.columns[0], "비율(%)", "면적 비율(%)", ".1f"), use_container_width=True)
with tabs[-1]:
    st.dataframe(analysis.data_quality(pcl), hide_index=True, use_container_width=True)
    st.caption("용도지역·이용상황이 없으면 보상배율을 정확히 적용할 수 없습니다. 이용상황이 없으면 지목으로 대신 분류하고, "
               "용도지역이 없으면 해당 시도의 전체 배율을 씁니다.")

# ---------- 주제도 ----------
st.subheader("현황도")
theme = st.radio("표시 기준", ["지목", "용도지역", "소유구분", "공시지가"], horizontal=True, label_visibility="collapsed")
m = ui.base_map(ui.boundary_center(prj), 13)
ui.add_boundary(m, prj)
inc = pcl[pcl["included"]].copy()
tooltip = {"jibun": "지번", "jimok": "지목", "zone": "용도지역", "owner": "소유구분", "area_incl": "편입면적(㎡)", "price": "공시지가(원/㎡)"}
if theme == "공시지가":
    ramp = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
    qs = inc["price"].quantile([0.2, 0.4, 0.6, 0.8]).tolist() if inc["price"].notna().any() else [0, 0, 0, 0]
    drawn = ui.add_parcels(m, inc, lambda r: ui.MUTED if r["price"] != r["price"] else ramp[sum(r["price"] > q for q in qs)], tooltip)
    items = [(f"~{qs[0]:,.0f}", ramp[0])] + [(f"~{q:,.0f}", ramp[i + 1]) for i, q in enumerate(qs[1:])] + [(f"{qs[-1]:,.0f}~ (원/㎡)", ramp[4])]
else:
    col = {"지목": "jimok", "용도지역": "zone", "소유구분": "owner"}[theme]
    key = inc[col].map(analysis.owner_group) if theme == "소유구분" else inc[col].fillna("미확인")
    # 면적이 큰 순서로 7개 분류까지만 색을 주고 나머지는 '기타'로 묶는다
    top = inc.assign(_k=key).groupby("_k")["area_incl"].sum().sort_values(ascending=False).index.tolist()[:7]
    cmap = {k: ui.SERIES[i] for i, k in enumerate(top)}
    inc["_k"] = key.values
    drawn = ui.add_parcels(m, inc, lambda r: cmap.get(r["_k"], ui.MUTED), tooltip)
    items = list(cmap.items()) + ([("기타", ui.MUTED)] if key.nunique() > len(top) else [])
folium.LayerControl(collapsed=True).add_to(m)
st_folium(m, height=520, use_container_width=True, returned_objects=[], key=f"context_map_{theme}")
if drawn:
    ui.legend(items)
else:
    st.caption(f"필지가 {len(inc):,}건으로 많아 지도에는 경계만 표시합니다.")

# ---------- 상위계획 ----------
st.subheader("상위계획 검토")
st.caption("상위계획은 API로 제공되지 않아 직접 입력합니다. 계획별 관련 내용과 부합 여부를 정리하면 보고서에 포함됩니다.")
up_df = pd.DataFrame([{"계획": u.plan, "주요 내용": u.content, "부합 여부": u.conformity, "비고": u.note} for u in prj.upper_plans])
edited = st.data_editor(
    up_df, hide_index=True, use_container_width=True, num_rows="dynamic", key="upper_plans",
    column_config={"부합 여부": st.column_config.SelectboxColumn(options=["미검토", "부합", "조건부 부합", "불부합"], required=True),
                   "주요 내용": st.column_config.TextColumn(width="large")})
new = [UpperPlanCheck(plan=str(r["계획"] or ""), content=str(r["주요 내용"] or ""), conformity=r["부합 여부"] or "미검토",
                      note=str(r["비고"] or "")) for r in edited.to_dict("records") if r["계획"]]
if new != prj.upper_plans:
    prj.upper_plans = new
    ui.persist()
