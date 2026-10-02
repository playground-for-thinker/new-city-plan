"""① 사업대상지 선정 — 경계 설정과 편입 필지 수집."""
import json
import tempfile
from pathlib import Path

import folium
import geopandas as gpd
import streamlit as st
from folium.plugins import Draw
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from streamlit_folium import st_folium

import ui
from newtown.site import parcels as P
from newtown.vworld.client import VWorldError

prj = ui.need_project()
ui.step_header("① 사업대상지 선정", "지도에서 사업구역 경계를 정하고, 경계에 걸리는 필지를 수집합니다.")
ui.vworld_notice()

SIDO = {"서울": "11", "부산": "26", "대구": "27", "인천": "28", "광주": "29", "대전": "30", "울산": "31", "세종": "36",
        "경기": "41", "강원": "51", "충북": "43", "충남": "44", "전북": "52", "전남": "46", "경북": "47", "경남": "48", "제주": "50"}


def set_boundary(geom) -> None:
    geom = geom if geom.is_valid else geom.buffer(0)
    prj.boundary = json.loads(json.dumps(mapping(geom)))
    prj.parcel_source = ""
    ui.set_parcels(P.empty_parcels())
    st.session_state.pop("map_center", None)


def read_upload(upload) -> gpd.GeoDataFrame:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / upload.name
        path.write_bytes(upload.getvalue())
        gdf = gpd.read_file(f"zip://{path}" if path.suffix.lower() == ".zip" else path)
    return gdf.set_crs(P.WGS84) if gdf.crs is None else gdf.to_crs(P.WGS84)


# ---------- 1. 경계 ----------
st.subheader("1. 사업구역 경계")
tab_draw, tab_file = st.tabs(["지도에서 그리기", "경계 파일 올리기"])

with tab_draw:
    client = ui.vworld_client()
    if client:
        with st.form("search", border=False):
            q1, q2 = st.columns([5, 1])
            query = q1.text_input("주소·지명 검색", placeholder="예: 남양주시 진접읍", label_visibility="collapsed")
            if q2.form_submit_button("검색") and query:
                try:
                    hits = client.search(query)
                    if hits:
                        st.session_state["map_center"] = (hits[0]["lat"], hits[0]["lon"])
                        st.caption(f"이동: {hits[0]['title']} {hits[0]['address']}")
                    else:
                        st.warning("검색 결과가 없습니다.")
                except VWorldError as e:
                    st.error(str(e))
    center = st.session_state.get("map_center") or ui.boundary_center(prj) or (37.45, 127.1)
    m = ui.base_map(center, 14 if "map_center" in st.session_state else 11)
    ui.add_boundary(m, prj, fit="map_center" not in st.session_state)
    pcl = ui.parcels()
    drawn_ok = ui.add_parcels(
        m, pcl, lambda r: ui.MUTED if not r["included"] else (ui.SERIES[1] if r["incl_ratio"] < 1 else ui.SERIES[0]),
        {"jibun": "지번", "jimok": "지목", "area_incl": "편입면적(㎡)", "incl_ratio": "편입비율"})
    Draw(export=False, draw_options={"polyline": False, "circle": False, "marker": False, "circlemarker": False,
                                     "polygon": True, "rectangle": True}).add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    out = st_folium(m, height=560, use_container_width=True, returned_objects=["all_drawings"], key="site_map")
    if len(pcl):
        ui.legend([("전부 편입", ui.SERIES[0]), ("부분 편입", ui.SERIES[1]), ("제외", ui.MUTED)])
        if not drawn_ok:
            st.caption(f"필지가 {len(pcl):,}건으로 많아 지도에는 경계만 표시합니다.")
    drawings = [d for d in (out or {}).get("all_drawings") or [] if d.get("geometry", {}).get("type") in ("Polygon", "MultiPolygon")]
    st.caption("지도 왼쪽의 다각형·사각형 도구로 구역을 그린 뒤 아래 버튼을 누르세요. 여러 개를 그리면 합쳐서 하나의 구역으로 만듭니다.")
    if st.button("그린 도형을 사업구역으로 설정", type="primary", disabled=not drawings):
        set_boundary(unary_union([shape(d["geometry"]) for d in drawings]))
        ui.persist()
        st.rerun()

with tab_file:
    up = st.file_uploader("경계 파일 (GeoJSON, KML, 또는 SHP를 묶은 ZIP)", type=["geojson", "json", "kml", "zip"], key="boundary_file")
    if up and st.button("이 파일을 사업구역으로 설정"):
        try:
            g = read_upload(up)
            polys = g[g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
            if polys.empty:
                st.error("파일에 면(Polygon) 도형이 없습니다.")
            else:
                set_boundary(unary_union(polys.geometry.tolist()))
                ui.persist()
                st.rerun()
        except Exception as e:  # 파일 형식 오류를 사용자에게 그대로 알린다
            st.error(f"파일을 읽지 못했습니다: {e}")

if not prj.boundary:
    st.stop()

boundary = P.boundary_shape(prj.boundary)
b_area = P.area_m2(boundary)
st.success(f"사업구역 경계 설정됨 — 도형 면적 {ui.area_fmt(b_area)}", icon=":material/check_circle:")

# ---------- 2. 필지 수집 ----------
st.subheader("2. 편입 필지 수집")
prj.comp.min_incl_ratio = st.slider(
    "최소 편입비율 — 경계에 이보다 적게 걸친 필지는 제외로 분류합니다", 0.0, 0.5, prj.comp.min_incl_ratio, 0.01, format="%.2f")
t_vw, t_up, t_sample = st.tabs(["VWorld에서 수집", "필지 파일 올리기", "가상 필지 생성(시험용)"])

with t_vw:
    st.caption("연속지적도에서 경계와 겹치는 필지를 수집합니다. 구역을 격자로 나눠 조회하며, 한 번 받은 응답은 캐시에 보관합니다.")
    if st.button("필지 수집 시작", disabled=client is None, type="primary"):
        bar = st.progress(0.0, "연속지적도 조회 중…")
        try:
            got = P.collect_parcels(client, boundary, prj.comp.min_incl_ratio,
                                    progress=lambda i, n: bar.progress(i / n, f"연속지적도 조회 중… ({i}/{n} 구역)"))
            prj.parcel_source = "VWorld 연속지적도"
            ui.set_parcels(got)
            st.rerun()
        except VWorldError as e:
            st.error(str(e))
    if client is None:
        st.caption("VWorld 인증키를 설정해야 쓸 수 있습니다.")

with t_up:
    st.caption("필지 도형 파일을 올립니다. 인식하는 열: pnu, 지번(jibun), 지목(jimok), 면적(area), 용도지역(zone), "
               "이용상황(use), 소유구분(owner), 공시지가(jiga/price), 공시연도. 보상배율의 시도는 PNU 앞 2자리로 판단합니다.")
    pf = st.file_uploader("필지 파일 (GeoJSON 또는 SHP를 묶은 ZIP)", type=["geojson", "json", "zip"], key="parcel_file")
    if pf and st.button("필지 불러오기"):
        try:
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / pf.name
                path.write_bytes(pf.getvalue())
                got = P.parcels_from_file(f"zip://{path}" if path.suffix.lower() == ".zip" else str(path), boundary, prj.comp.min_incl_ratio)
            prj.parcel_source = f"파일({pf.name})"
            ui.set_parcels(got)
            st.rerun()
        except Exception as e:
            st.error(f"파일을 읽지 못했습니다: {e}")

with t_sample:
    st.warning("가상 필지는 화면과 계산 흐름을 시험하기 위한 것입니다. 지목·공시지가·소유구분이 모두 임의로 만든 값이므로 "
               "실제 분석에는 쓸 수 없습니다.", icon=":material/science:")
    s1, s2 = st.columns([2, 1])
    sido = s1.selectbox("보상배율을 적용할 시도", list(SIDO), index=list(SIDO).index("경기"))
    cell = s2.number_input("필지 한 변 길이(m)", 30, 200, 60, 10)
    if st.button("가상 필지 생성"):
        prj.parcel_source = "가상 필지(시험용)"
        ui.set_parcels(P.sample_parcels(boundary, SIDO[sido], seed=1, cell_m=float(cell), min_ratio=prj.comp.min_incl_ratio))
        st.rerun()

pcl = ui.parcels()
if pcl.empty:
    ui.persist()
    st.stop()

# 최소 편입비율을 바꾸면 자동 판정을 다시 적용 (사용자가 직접 고친 편입 여부는 표에서 다시 지정)
if st.session_state.get("_min_ratio_applied", prj.comp.min_incl_ratio) != prj.comp.min_incl_ratio:
    pcl = pcl.copy()
    pcl["included"] = pcl["incl_ratio"] >= prj.comp.min_incl_ratio
    ui.set_parcels(pcl)
st.session_state["_min_ratio_applied"] = prj.comp.min_incl_ratio

# ---------- 3. 편입 필지 ----------
st.subheader("3. 편입 필지 목록")
if "가상" in prj.parcel_source:
    st.warning("현재 필지는 **가상 필지(시험용)** 입니다.", icon=":material/science:")
inc = pcl[pcl["included"]]
c = st.columns(4)
c[0].metric("편입 필지", f"{len(inc):,}건")
c[1].metric("편입면적 합계", f"{inc['area_incl'].sum():,.0f}㎡")
c[2].metric("부분 편입", f"{int((inc['incl_ratio'] < 1).sum()):,}건")
c[3].metric("제외", f"{len(pcl) - len(inc):,}건")
st.caption(f"자료 출처: {prj.parcel_source} · 편입면적 = 공부면적 × 편입비율 · 경계 도형 면적과의 차이 "
           f"{(inc['area_incl'].sum() - b_area) / b_area:+.1%}")

only_partial = st.toggle("부분 편입·제외 필지만 보기", value=False)
view = pcl.drop(columns="geometry")
if only_partial:
    view = view[(view["incl_ratio"] < 1) | (~view["included"])]
cols = ["included", "pnu", "addr", "jibun", "jimok", "area_reg", "incl_ratio", "area_incl", "price"]
edited = st.data_editor(
    view[cols].rename(columns=P.COLUMN_LABELS), hide_index=True, use_container_width=True, height=380,
    disabled=[P.COLUMN_LABELS[c] for c in cols if c != "included"], key=f"parcel_editor_{st.session_state.get('parcels_ver', 0)}",
    column_config={"편입비율": st.column_config.NumberColumn(format="%.2f"),
                   "공부면적(㎡)": st.column_config.NumberColumn(format="localized"),
                   "편입면적(㎡)": st.column_config.NumberColumn(format="localized"),
                   "개별공시지가(원/㎡)": st.column_config.NumberColumn(format="localized")})
changed = edited["편입"].values != view["included"].values
if changed.any():
    pcl = pcl.copy()
    pcl.loc[view.index[changed], "included"] = edited["편입"].values[changed]
    ui.set_parcels(pcl)
    st.rerun()
ui.persist()
