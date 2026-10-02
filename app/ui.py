"""화면 공통 기능 — 상태 관리, 서식, 지도."""
from __future__ import annotations

import folium
import geopandas as gpd
import pandas as pd
import streamlit as st

from newtown import pipeline, store
from newtown.cost.compensation import CompensationResult, build_register
from newtown.models import Project
from newtown.site.parcels import boundary_shape, empty_parcels
from newtown.vworld.client import VWorldClient, VWorldError, load_credentials

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
MUTED = "#9a9993"


# ---------- 상태 ----------
def project() -> Project | None:
    return st.session_state.get("project")


def parcels() -> gpd.GeoDataFrame:
    if "parcels" not in st.session_state:
        st.session_state["parcels"] = empty_parcels()
    return st.session_state["parcels"]


def set_project(prj: Project, pcl: gpd.GeoDataFrame | None = None) -> None:
    st.session_state["project"] = prj
    set_parcels(pcl if pcl is not None else empty_parcels(), save=False)
    if pcl is not None:
        st.session_state["_parcels_saved"] = (prj.name, st.session_state["parcels_ver"])


def set_parcels(pcl: gpd.GeoDataFrame, save: bool = True) -> None:
    st.session_state["parcels"] = pcl
    st.session_state["parcels_ver"] = st.session_state.get("parcels_ver", 0) + 1
    if save:
        persist()


def persist() -> None:
    """프로젝트를 저장한다. 필지 파일은 필지가 바뀌었을 때만 다시 쓴다(수만 필지에서 매번 쓰면 느리다)."""
    prj = project()
    if prj is None:
        return
    sig = (prj.name, st.session_state.get("parcels_ver", 0))
    dirty = st.session_state.get("_parcels_saved") != sig
    store.save(prj, parcels() if dirty else None)
    st.session_state["_parcels_saved"] = sig


def need_project() -> Project:
    prj = project()
    if prj is None:
        st.info("먼저 **프로젝트** 화면에서 프로젝트를 만들거나 불러오세요.")
        st.page_link("views/0_project.py", label="프로젝트 화면으로 이동", icon=":material/folder_open:")
        st.stop()
    return prj


def need_boundary() -> Project:
    prj = need_project()
    if not prj.boundary:
        st.info("먼저 **① 대상지 선정**에서 사업구역 경계를 설정하세요.")
        st.page_link("views/1_site.py", label="① 대상지 선정으로 이동", icon=":material/map:")
        st.stop()
    return prj


def compensation() -> CompensationResult:
    """보상비 계산 결과. 필지나 설정이 바뀔 때만 다시 계산한다."""
    prj = project()
    sig = (st.session_state.get("parcels_ver", 0), prj.name, prj.comp.model_dump_json())
    cached = st.session_state.get("_comp")
    if cached and cached[0] == sig:
        return cached[1]
    res = build_register(parcels(), prj.comp) if len(parcels()) else CompensationResult(pd.DataFrame(), 0.0, 0.0, 0.25, {})
    st.session_state["_comp"] = (sig, res)
    return res


def evaluation() -> pipeline.Evaluation:
    return pipeline.evaluate(project(), parcels(), comp=compensation())


# ---------- 서식 ----------
def eok(won: float, digits: int = 1) -> str:
    """원 → 억원 표기."""
    if won is None or won != won:
        return "-"
    return f"{won / 1e8:,.{digits}f}억원"


def area_fmt(m2: float) -> str:
    return f"{m2:,.0f}㎡ ({m2 / 1e6:,.2f}㎢)" if m2 >= 1e5 else f"{m2:,.0f}㎡"


def step_header(title: str, caption: str) -> None:
    st.title(title)
    st.caption(caption)


# ---------- VWorld ----------
def vworld_ready() -> bool:
    return bool(load_credentials()[0])


@st.cache_resource(show_spinner=False)
def _client(key: str, domain: str) -> VWorldClient:
    return VWorldClient(key, domain)


def vworld_client() -> VWorldClient | None:
    key, domain = load_credentials()
    if not key:
        return None
    try:
        return _client(key, domain)
    except VWorldError:
        return None


def vworld_notice() -> None:
    if not vworld_ready():
        st.warning("VWorld 인증키가 설정되지 않았습니다. 프로젝트 폴더의 `.env` 파일에 `VWORLD_API_KEY`(와 `VWORLD_DOMAIN`)를 "
                   "넣으면 지적도 배경지도, 주소 검색, 필지·속성 자동 수집을 쓸 수 있습니다. "
                   "키가 없어도 필지 파일을 올리거나 가상 필지로 전체 흐름을 시험할 수 있습니다.", icon=":material/key:")


# ---------- 지도 ----------
def base_map(center: tuple[float, float] = (37.45, 127.1), zoom: int = 11) -> folium.Map:
    key, _ = load_credentials()
    if key:
        m = folium.Map(location=center, zoom_start=zoom, tiles=None, control_scale=True)
        folium.TileLayer(f"https://api.vworld.kr/req/wmts/1.0.0/{key}/Base/{{z}}/{{y}}/{{x}}.png", attr="VWorld",
                         name="일반지도", max_zoom=19).add_to(m)
        folium.TileLayer(f"https://api.vworld.kr/req/wmts/1.0.0/{key}/Satellite/{{z}}/{{y}}/{{x}}.jpeg", attr="VWorld",
                         name="위성영상", max_zoom=19, show=False).add_to(m)
        folium.WmsTileLayer("https://api.vworld.kr/req/wms", layers="lp_pa_cbnd_bubun", styles="lp_pa_cbnd_bubun_line",
                            fmt="image/png", transparent=True, version="1.3.0", name="연속지적도", overlay=True,
                            show=False, attr="VWorld", key=key).add_to(m)  # 브라우저 요청은 Referer로 인증되므로 domain을 붙이지 않는다
    else:
        m = folium.Map(location=center, zoom_start=zoom, control_scale=True)
    return m


def boundary_center(prj: Project) -> tuple[float, float] | None:
    if not prj.boundary:
        return None
    c = boundary_shape(prj.boundary).centroid
    return (c.y, c.x)


def add_boundary(m: folium.Map, prj: Project, fit: bool = True) -> None:
    if not prj.boundary:
        return
    geom = boundary_shape(prj.boundary)
    folium.GeoJson(prj.boundary, name="사업구역", style_function=lambda _: {
        "color": "#0b0b0b", "weight": 3, "fillOpacity": 0, "dashArray": "6 4"}).add_to(m)
    if fit:
        minx, miny, maxx, maxy = geom.bounds
        m.fit_bounds([[miny, minx], [maxy, maxx]])


MAX_MAP_PARCELS = 6000


def add_parcels(m: folium.Map, pcl: gpd.GeoDataFrame, color_of, tooltip_cols: dict[str, str]) -> bool:
    """필지를 지도에 그린다. color_of(row dict) → 채움색. 필지가 너무 많으면 그리지 않고 False."""
    if pcl.empty or len(pcl) > MAX_MAP_PARCELS:
        return pcl.empty
    cols = list(tooltip_cols)
    g = pcl[cols + ["geometry"]].copy()
    g["_c"] = [color_of(r) for r in pcl.to_dict("records")]
    for c in cols:
        g[c] = g[c].map(lambda v: "" if v is None or v != v else (f"{v:,.0f}" if isinstance(v, float) else str(v)))
    folium.GeoJson(
        g.to_json(), name="필지",
        style_function=lambda f: {"fillColor": f["properties"]["_c"], "color": "#ffffff", "weight": 0.6, "fillOpacity": 0.65},
        tooltip=folium.GeoJsonTooltip(fields=cols, aliases=[tooltip_cols[c] for c in cols]),
    ).add_to(m)
    return True


def legend(items: list[tuple[str, str]]) -> None:
    """색상 범례 (지도·차트 아래)."""
    chips = "".join(
        f'<span style="display:inline-flex;align-items:center;margin-right:14px;font-size:0.85rem;color:#52514e">'
        f'<span style="width:12px;height:12px;border-radius:3px;background:{c};display:inline-block;margin-right:5px"></span>{label}</span>'
        for label, c in items)
    st.markdown(f"<div>{chips}</div>", unsafe_allow_html=True)
