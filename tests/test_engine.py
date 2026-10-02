"""계산 엔진 검증 — 손계산 예제와 비교한다."""
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box, mapping

from newtown import pipeline, standards
from newtown.cost import compensation
from newtown.cost.total import compute_costs
from newtown.finance.analysis import build_cashflow, compute_metrics, irr, revenue_table
from newtown.landuse import plan as landuse_plan
from newtown.models import CompSettings, CostLine, FinanceParams, LandUseRow, Schedule, new_project
from newtown.report.excel import build_report
from newtown.site import parcels as P

STD = standards.compensation_standard("2018")


# ---------- 보상배율 ----------
def test_multiplier_guideline_example():
    # 경기도, 자연녹지지역, 답 → (1.92 + 1.77) / 2 = 1.845
    z = compensation.classify_zone("자연녹지지역", STD)
    u, basis = compensation.classify_use("답", "답", STD)
    assert (z, u, basis) == ("녹지·개발제한", "전·답", "이용상황")
    assert compensation.lookup_multiplier("경기", z, u, STD)["mult"] == pytest.approx(1.845)


def test_multiplier_missing_cell_falls_back_to_sido_total():
    # 서울은 관리지역 배율이 없다 → 전체 1.66 사용, 임야 2.77 → 2.215
    m = compensation.lookup_multiplier("서울", "관리", "임야", STD)
    assert m["zone_mult"] == 1.66 and m["mult"] == pytest.approx(2.215)
    assert "용도지역" in m["remark"]


@pytest.mark.parametrize("zone,expected", [
    ("자연환경보전지역", "농림·자연환경보전"), ("계획관리지역", "관리"), ("제2종일반주거지역", "주거·상업·공업"),
    ("자연녹지지역(개발제한구역)", "녹지·개발제한"), ("농림지역", "농림·자연환경보전"), (None, None),
])
def test_zone_classes(zone, expected):
    assert compensation.classify_zone(zone, STD) == expected


@pytest.mark.parametrize("use,jimok,expected", [
    ("주상용", None, "상업용·주상용"), ("단독", "대", "주거용·공업용"), ("전기타", "전", "전·답"),
    ("자연림", "임야", "임야"), ("도로등", "도로", "공공·기타"), ("발전소", "잡종지", "공공·기타"),
    (None, "과수원", "전·답"), (None, "구거", "공공·기타"),
])
def test_use_classes(use, jimok, expected):
    assert compensation.classify_use(use, jimok, STD)[0] == expected


# ---------- 편입면적·토지세목조서 ----------
def _parcel_gdf():
    import geopandas as gpd
    # EPSG:5186에서 100m×100m 필지 2개. 경계는 첫 필지 전부와 둘째 필지의 절반을 덮는다.
    g = gpd.GeoDataFrame(
        {"pnu": ["4111110100100010001", "4111110100100010002"], "jimok": ["답", "임야"],
         "zone": ["자연녹지지역", "자연녹지지역"], "use": ["답", "자연림"], "price": [100_000.0, 20_000.0],
         "area_reg": [10_000.0, 10_400.0]},
        geometry=[box(200000, 500000, 200100, 500100), box(200100, 500000, 200200, 500100)], crs=P.AREA_CRS).to_crs(P.WGS84)
    boundary = gpd.GeoSeries([box(200000, 500000, 200150, 500100)], crs=P.AREA_CRS).to_crs(P.WGS84).iloc[0]
    return P._fill_defaults(g), boundary


def test_inclusion_and_register():
    g, boundary = _parcel_gdf()
    inc = P.apply_inclusion(g, boundary, min_ratio=0.05)
    assert inc["incl_ratio"].tolist() == pytest.approx([1.0, 0.5], abs=1e-3)
    # 편입면적 = 공부면적 × 편입비율
    assert inc["area_incl"].tolist() == pytest.approx([10_000, 5_200], rel=1e-3)

    res = compensation.build_register(inc, CompSettings())
    # 답: 10,000 × 100,000 × (1.92+1.77)/2 = 1,845,000,000
    # 임야: 5,200 × 20,000 × (1.92+2.70)/2 = 240,240,000
    assert res.register["land_comp"].tolist() == pytest.approx([1_845_000_000, 240_240_000], rel=1e-3)
    assert res.obstacle == pytest.approx(res.land_comp * 0.25)


def test_missing_price_and_free_transfer():
    g, boundary = _parcel_gdf()
    inc = P.apply_inclusion(g, boundary)
    inc.loc[1, "price"] = np.nan
    inc.loc[0, "free"] = True
    res = compensation.build_register(inc, CompSettings())
    assert res.register.loc[0, "land_comp"] == 0
    assert res.issues["공시지가 없음(평균지가 대체)"] == 1
    assert res.register.loc[1, "land_comp"] > 0  # 평균지가로 대체해 계산


# ---------- 사업비 ----------
def _lines():
    return [
        CostLine(key="land_comp", category="용지비", name="토지보상비", method="auto", schedule="comp"),
        CostLine(key="obstacle", category="용지비", name="지장물", method="auto", schedule="comp"),
        CostLine(key="c", category="조성비", name="공사", method="qty_price", basis="total_area", unit_price=100_000,
                 vat=True, bid=True, schedule="const"),
        CostLine(key="d", category="조성비", name="설계", method="rate", base="construction", rate=0.05, vat=True, schedule="const"),
        CostLine(key="cap", category="자본비용", name="자본비용", method="rate", base="direct", rate=0.10, schedule="spread"),
    ]


def test_cost_two_views():
    fin = FinanceParams(bid_rate=0.9)
    r = compute_costs(_lines(), "rough", land_comp=1000e6, obstacle=250e6,
                      quantities={"total_area": 10_000, "paid_area": 5_000}, fin=fin)
    const = 10_000 * 100_000 * 0.9          # 900,000,000 (낙찰률 적용)
    design = const * 0.05                    # 45,000,000
    direct = 1250e6 + const + design         # 공급가액 기준 직접비 2,195,000,000
    capital = direct * 0.10
    vat = (const + design) * 0.10
    assert r.total_cost_basis == pytest.approx(direct + vat + capital)
    # 예타 관점: 자본비용 제외, 예비비 = 부가세 포함 사업비의 10%
    assert r.contingency == pytest.approx((direct + vat) * 0.10)
    assert r.total_feasibility == pytest.approx((direct + vat) * 1.10)
    assert r.unit_cost == pytest.approx(r.total_cost_basis / 5_000)


# ---------- 재무 지표 ----------
def test_metrics_hand_calc():
    t = np.array([1, 2, 3])
    outflow = np.array([100.0, 100.0, 0.0])
    inflow = np.array([0.0, 50.0, 200.0])
    m = compute_metrics(inflow, outflow, t, 0.045)
    pv_out = 100 / 1.045 + 100 / 1.045**2
    pv_in = 50 / 1.045**2 + 200 / 1.045**3
    assert m.pi == pytest.approx(pv_in / pv_out)
    assert m.fnpv == pytest.approx(pv_in - pv_out)
    # FIRR에서 NPV = 0
    flows = inflow - outflow
    assert float((flows / (1 + m.firr) ** t).sum()) == pytest.approx(0, abs=1e-5)


def test_irr_none_when_no_sign_change():
    assert irr(np.array([10.0, 10.0]), np.array([1, 2])) is None


def test_cashflow_totals_and_collection():
    rows = [LandUseRow(key="a", major="housing", name="주택", ratio=50, paid=True, price_basis="manual", unit_price=1_000_000),
            LandUseRow(key="b", major="road", name="도로", ratio=50, paid=False)]
    fin = FinanceParams(bid_rate=1.0)
    costs = compute_costs(_lines(), "rough", land_comp=1000e6, obstacle=250e6,
                          quantities={"total_area": 10_000, "paid_area": 5_000}, fin=fin)
    rev = revenue_table(rows, 10_000, costs.unit_cost)
    assert rev["분양수입(원)"].sum() == 5_000 * 1_000_000
    sch = Schedule(base_year=2026, years=[2027, 2028, 2029], comp=[100, 0, 0], const=[0, 100, 0],
                   sales={"housing": [0, 0, 100]}, collect=[50, 50])
    res = build_cashflow(costs, rev, sch, fin)
    cf = res.cashflow
    assert cf["연도"].tolist() == [2027, 2028, 2029, 2030]          # 회수 조건만큼 분석기간 연장
    assert cf["현금유입"].tolist() == [0, 0, 2.5e9, 2.5e9]
    assert cf["현금유출"].sum() == pytest.approx(costs.total_feasibility, rel=1e-6)
    assert "자본비용" not in cf.columns                               # 금융비용은 현금유출에서 제외
    assert cf.loc[0, "용지비"] == 1250e6 and cf.loc[1, "용지비"] == 0


# ---------- 토지이용계획 ----------
def test_landuse_rows_sum_to_100():
    rows = landuse_plan.default_rows()
    assert sum(r.ratio for r in rows) == pytest.approx(100, abs=0.01)
    rows[0].ratio += 5
    assert landuse_plan.validate(rows, 1e6, 1e4, new_project("t").concept, pd.DataFrame())[0][0] == "error"
    assert sum(r.ratio for r in landuse_plan.scale_to_100(rows)) == pytest.approx(100, abs=0.01)


# ---------- 전체 흐름 ----------
def test_end_to_end_sample(tmp_path, monkeypatch):
    import geopandas as gpd
    boundary = gpd.GeoSeries([box(200000, 500000, 201000, 500800)], crs=P.AREA_CRS).to_crs(P.WGS84).iloc[0]
    prj = new_project("시험")
    prj.boundary = mapping(boundary)
    parcels = P.sample_parcels(boundary, seed=1)
    assert len(parcels) > 100 and (parcels["incl_ratio"] < 1).any()
    for l in prj.cost_lines:
        if l.key == "c_rough":
            l.unit_price = 150_000
        if l.method == "rate":
            l.rate = 0.03
    ev = pipeline.evaluate(prj, parcels)
    # 사업면적 = 편입면적 합계 ≈ 경계 면적(80만㎡), 가상 필지의 공부면적 오차 범위 내
    assert ev.total_area == pytest.approx(800_000, rel=0.03)
    assert ev.comp.land_comp > 0 and ev.costs.unit_cost > 0
    m = ev.finance.metrics
    assert 0 < m.pi < 5 and (m.pi >= 1) == (m.fnpv >= 0)
    # 보상비가 오르면 PI는 내려가지 않을 수 있다(조성원가 연동 공급) → 공급가격 민감도는 단조 증가
    s = pipeline.sensitivity(prj, ev)
    price_row = s[s["변수"] == "공급가격"].iloc[0, 1:].astype(float).tolist()
    assert price_row == sorted(price_row)
    data = build_report(prj, parcels, ev)
    assert data[:2] == b"PK"

    monkeypatch.setattr(standards, "PROJECTS_DIR", tmp_path)
    from newtown import store
    store.save(prj, parcels)
    prj2, parcels2 = store.load("시험")
    assert prj2.model_dump_json() == prj.model_dump_json() and len(parcels2) == len(parcels)
    assert pipeline.evaluate(prj2, parcels2).finance.metrics.pi == pytest.approx(m.pi)


# ---------- VWorld 클라이언트 (모의 응답) ----------
def test_vworld_client_paging_and_attributes(tmp_path, monkeypatch):
    import httpx
    from newtown.vworld.client import VWorldClient

    def feature(pnu, x):
        return {"type": "Feature", "properties": {"pnu": pnu, "jibun": "12-3 답", "addr": "시험", "jiga": "150000", "gosi_year": "2026"},
                "geometry": {"type": "Polygon", "coordinates": [[[x, 37.0], [x + 0.0005, 37.0], [x + 0.0005, 37.0005], [x, 37.0005], [x, 37.0]]]}}

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        calls.append(q)
        assert q["key"] == "K" and q["domain"] == "http://localhost"
        if request.url.path == "/req/data":
            page = int(q["page"])
            feats = [feature(f"41000000000000000{page}{i}", 127.0 + 0.001 * (page * 2 + i)) for i in range(2)] if page <= 2 else []
            return httpx.Response(200, json={"response": {"status": "OK", "page": {"total": "2", "current": str(page)},
                                                         "result": {"featureCollection": {"features": feats}}}})
        if request.url.path.endswith("getLandCharacteristics"):
            return httpx.Response(200, json={"landCharacteristicss": {"field": [
                {"pnu": q["pnu"], "stdrYear": "2025", "lndcgrCodeNm": "답", "lndpclAr": "1,000", "prposArea1Nm": "자연녹지지역",
                 "ladUseSittnNm": "답", "pblntfPclnd": "140000"},
                {"pnu": q["pnu"], "stdrYear": "2026", "lndcgrCodeNm": "답", "lndpclAr": "1,000", "prposArea1Nm": "자연녹지지역",
                 "ladUseSittnNm": "답", "pblntfPclnd": "150000"}]}})
        if request.url.path.endswith("ladfrlList"):
            return httpx.Response(200, json={"ladfrlVOList": {"ladfrlVOList": [
                {"pnu": q["pnu"], "posesnSeCodeNm": "개인", "lndpclAr": "1000", "lndcgrCodeNm": "답"}], "totalCount": "1"}})
        return httpx.Response(404)

    c = VWorldClient("K", "http://localhost", use_cache=False, min_interval=0, transport=httpx.MockTransport(handler))
    feats = c.get_features("LP_PA_CBND_BUBUN", geom_filter="BOX(127,37,127.01,37.01)")
    assert len(feats) == 4 and [q["page"] for q in calls] == ["1", "2"]          # 두 쪽을 모두 읽는다
    gdf = P.parcels_from_features(feats)
    assert gdf["jimok"].tolist() == ["답"] * 4 and gdf["jibun"].iloc[0] == "12-3" and gdf["price"].iloc[0] == 150000
    attrs, failed = c.fetch_parcel_attributes(gdf["pnu"])
    assert not failed
    a = attrs[gdf["pnu"].iloc[0]]
    assert a == {"jimok": "답", "area_reg": 1000.0, "zone": "자연녹지지역", "use": "답", "price": 150000.0,
                 "price_year": "2026", "owner": "개인"}                           # 최신 기준연도 값을 쓴다


# ---------- LH 기준 ----------
def test_rate_table_interpolation_and_limits():
    # 설계비(토목): 100억 4.95%, 200억 4.67% → 150억은 직선보간 4.81%
    assert standards.rate_from_table("design_civil", 100e8) == pytest.approx(0.0495)
    assert standards.rate_from_table("design_civil", 150e8) == pytest.approx(0.0481)
    assert standards.rate_from_table("design_civil", 9000e8) == pytest.approx(0.0390)      # 표 상한 초과 → 마지막 요율
    assert standards.rate_from_table("supervision_civil_normal", 10e8) == pytest.approx(0.1310)  # 표 하한 미만 → 첫 요율


def test_lh_basic_price_by_terrain_and_scale():
    assert standards.lh_basic_price("평지", 150_000) == 53_785
    assert standards.lh_basic_price("평지", 1_200_000) == 49_330
    assert standards.lh_basic_price("평지", 5_000_000) == 43_855
    assert standards.lh_basic_price("구릉지", 1_200_000) == 56_878


def test_reproduces_lh_disclosure_namyangju_jinjeop2():
    """LH가 공개한 남양주진접2 조성원가 산정표(2026.7)의 간접비·조성원가를 그대로 재현한다 (단위: 원)."""
    k = 1000
    amounts = {"levy": 25_497_620 * k, "construction": 454_843_706 * k, "infra": 770_094_438 * k, "relocation": 9_994_691 * k}
    cats = {"levy": "용지부담금", "construction": "조성비", "infra": "기반시설설치비", "relocation": "이주대책비"}
    lines = [CostLine(key="land_comp", category="용지비", name="용지비", method="auto")]
    lines += [CostLine(key=key, category=cats[key], name=key, method="manual", amount=v) for key, v in amounts.items()]
    lines += [CostLine(key="labor", category="직접인건비", name="직접인건비", method="rate", base="direct_ex_labor", rate=0.02),
              CostLine(key="sales", category="판매비", name="판매비", method="rate", base="direct", rate=0.0037),
              CostLine(key="admin", category="일반관리비", name="일반관리비", method="rate", base="direct", rate=0.025),
              CostLine(key="etc", category="그 밖의 비용", name="그 밖의 비용", method="rate", base="direct", rate=0.0002),
              CostLine(key="capital", category="자본비용", name="자본비용", method="capital", rate=0.0342)]
    r = compute_costs(lines, "rough", land_comp=847_562_964 * k, obstacle=0, quantities={"paid_area": 639_801}, fin=FinanceParams(),
                      capital_amount=196_660_961 * k)
    got = r.table.set_index("key")["합계(원)"]
    assert got["labor"] == pytest.approx(42_159_868 * k, abs=k)
    assert got["sales"] == pytest.approx(7_955_567 * k, abs=k)
    assert got["admin"] == pytest.approx(53_753_832 * k, abs=k)
    assert got["etc"] == pytest.approx(430_031 * k, abs=k)
    assert r.total_cost_basis == pytest.approx(2_408_953_679 * k, abs=5 * k)
    assert r.unit_cost == pytest.approx(3_765_161, abs=1)


def _sample_project():
    import geopandas as gpd
    boundary = gpd.GeoSeries([box(200000, 500000, 201000, 500800)], crs=P.AREA_CRS).to_crs(P.WGS84).iloc[0]
    prj = new_project("시험")
    prj.boundary = mapping(boundary)
    return prj, P.sample_parcels(boundary, seed=1)


def test_defaults_come_from_lh_disclosures_and_capital_cost_converges():
    prj, parcels = _sample_project()
    bench = standards.disclosure_benchmark()
    by_key = {l.key: l for l in prj.cost_lines}
    assert by_key["c_rough"].unit_price == bench["construction_per_m2"] > 100_000
    assert by_key["labor"].rate == pytest.approx(bench["labor_rate_pct"] / 100)
    ev = pipeline.evaluate(prj, parcels)
    capital = ev.costs.table.set_index("key").loc["capital", "합계(원)"]
    # 자본비용 = 순투입액 누적액 × 자본비용률 (반복 계산이 수렴한 값)
    assert capital == pytest.approx(ev.finance.net_investment_years * by_key["capital"].rate, rel=1e-4)
    assert capital > 0 and "자본비용" not in ev.finance.cashflow.columns


def test_detail_mode_uses_lh_tables():
    prj, parcels = _sample_project()
    prj.cost_mode = "detail"
    ev = pipeline.evaluate(prj, parcels)
    assert ev.costs.applied["c_basic"] == standards.lh_basic_price("평지", ev.total_area)
    amounts = ev.costs.table.set_index("key")["공급가액(원)"]
    construction = amounts[["c_basic", "c_park", "c_green"]].sum()
    assert amounts["design"] == pytest.approx(construction * standards.rate_from_table("design_civil", construction), rel=1e-6)
    assert amounts["survey"] == pytest.approx(construction * 0.01, rel=1e-6)
    prj.terrain = "구릉지"
    assert pipeline.evaluate(prj, parcels).costs.applied["c_basic"] == standards.lh_basic_price("구릉지", ev.total_area)


def test_upgrade_keeps_user_values_and_splits_park_row():
    from newtown.models import upgrade_project
    prj, _ = _sample_project()
    prj.cost_template_version = "1"
    prj.cost_lines = [CostLine(key="c_rough", category="조성비", name="조성공사비(개략)", method="qty_price", basis="total_area", unit_price=180_000),
                      CostLine(key="labor", category="직접인건비", name="직접인건비", method="rate", base="direct_ex_labor", rate=0.0)]
    rows = [r for r in prj.landuse if r.key != "p_green"]
    park = next(r for r in rows if r.key == "p_park")
    park.ratio = 25.0
    prj.alternatives[prj.active_alt] = rows
    upgrade_project(prj)
    by_key = {l.key: l for l in prj.cost_lines}
    assert by_key["c_rough"].unit_price == 180_000                       # 사용자가 넣은 값 유지
    assert by_key["labor"].rate == pytest.approx(standards.disclosure_benchmark()["labor_rate_pct"] / 100)  # 비어 있던 값은 새 초기값
    assert "c_basic" in by_key and prj.cost_template_version == "2"
    keys = {r.key: r.ratio for r in prj.landuse}
    assert keys["p_park"] + keys["p_green"] == pytest.approx(25.0) and keys["p_green"] > 0
