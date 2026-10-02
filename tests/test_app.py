"""화면 점검 — 각 화면이 예외 없이 그려지는지 확인한다 (브라우저 없이 Streamlit AppTest 사용)."""
import geopandas as gpd
import pytest
from shapely.geometry import box, mapping
from pathlib import Path

from streamlit.testing.v1 import AppTest

from newtown import standards
from newtown.models import new_project
from newtown.site import parcels as P

MAIN = str(Path(__file__).resolve().parents[1] / "app" / "main.py")
PAGES = ["views/0_project.py", "views/1_site.py", "views/2_context.py", "views/3_cost.py",
         "views/4_landuse.py", "views/5_concept.py", "views/6_finance.py", "views/7_help.py"]


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setattr(standards, "PROJECTS_DIR", tmp_path)
    boundary = gpd.GeoSeries([box(200000, 500000, 201200, 500900)], crs=P.AREA_CRS).to_crs(P.WGS84).iloc[0]
    prj = new_project("화면시험")
    prj.boundary = mapping(boundary)
    prj.parcel_source = "가상 필지(시험용)"
    for l in prj.cost_lines:
        if l.key == "c_rough":
            l.unit_price = 150_000
        if l.method == "rate":
            l.rate = 0.03
    return prj, P.sample_parcels(boundary, seed=1)


def run(page, prj=None, parcels=None):
    at = AppTest.from_file(MAIN, default_timeout=60)
    if prj is not None:
        at.session_state["project"] = prj
        at.session_state["parcels"] = parcels
        at.session_state["parcels_ver"] = 1
    at.run()
    at.switch_page(page).run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_pages_without_project(page, tmp_path, monkeypatch):
    monkeypatch.setattr(standards, "PROJECTS_DIR", tmp_path)
    at = run(page)
    assert not at.exception, at.exception


@pytest.mark.parametrize("page", PAGES)
def test_pages_with_sample_project(page, sample):
    at = run(page, *sample)
    assert not at.exception, at.exception
    assert not at.error or page == "views/6_finance.py", [e.value for e in at.error]  # ⑥은 PI<1 판정을 error 상자로 표시


def test_create_project_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(standards, "PROJECTS_DIR", tmp_path)
    at = AppTest.from_file(MAIN, default_timeout=60).run()
    at.text_input[0].set_value("새 사업")
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state["project"].name == "새 사업"
    assert (tmp_path / "새 사업" / "project.json").exists()


def test_finance_page_shows_pi(sample):
    at = run("views/6_finance.py", *sample)
    labels = {m.label: m.value for m in at.metric}
    assert "PI (수익성지수)" in labels and float(labels["PI (수익성지수)"]) > 0


def test_parcel_file_rewritten_only_when_parcels_change(sample, tmp_path):
    """화면을 옮겨 다니는 것만으로는 필지 파일을 다시 쓰지 않는다."""
    import os
    from newtown import store
    prj, parcels = sample
    store.save(prj, parcels)
    pfile = tmp_path / "화면시험" / "parcels.geojson"
    os.utime(pfile, (1_000_000_000, 1_000_000_000))
    at = AppTest.from_file(MAIN, default_timeout=60).run()
    at.selectbox[0].set_value("화면시험")
    at.button[1].click().run()                      # 프로젝트 불러오기
    assert at.session_state["project"].name == "화면시험"
    for page in PAGES[1:]:
        at.switch_page(page).run()
        assert not at.exception, (page, at.exception)
    assert pfile.stat().st_mtime == 1_000_000_000
    assert (tmp_path / "화면시험" / "project.json").stat().st_mtime > 1_000_000_000


def test_help_page_shows_both_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(standards, "PROJECTS_DIR", tmp_path)
    at = run("views/7_help.py")
    assert not at.exception
    assert [t.label for t in at.tabs] == ["사용 설명서", "산정 방법과 근거"]
    body = " ".join(m.value for m in at.markdown)
    assert "① 대상지 선정" in body and "보상배율표" in body and "{{" not in body


def test_example_project_is_seeded_and_loads(tmp_path, monkeypatch):
    """빈 프로젝트 폴더로 시작해도(클라우드) 예제 프로젝트가 목록에 있고, 불러오면 전 단계가 계산된다."""
    monkeypatch.setattr(standards, "PROJECTS_DIR", tmp_path)
    at = AppTest.from_file(MAIN, default_timeout=90).run()
    example = "예제_남양주 오남-진접 일대"
    assert example in at.selectbox[0].options
    at.selectbox[0].set_value(example)
    at.button[1].click().run()
    assert not at.exception
    assert len(at.session_state["parcels"]) == 769 and at.session_state["project"].boundary
    at.switch_page("views/6_finance.py").run()
    labels = {m.label: m.value for m in at.metric}
    assert not at.exception and float(labels["PI (수익성지수)"]) > 0
