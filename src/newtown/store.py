"""프로젝트 저장·불러오기 (projects/<이름>/project.json, parcels.geojson)."""
from __future__ import annotations

import re

import geopandas as gpd

from . import standards
from .models import Project, upgrade_project
from .site.parcels import empty_parcels, parcels_from_geojson, parcels_to_geojson


def _dir(name: str):
    safe = re.sub(r"[^\w가-힣 .-]", "_", name).strip() or "project"
    return standards.PROJECTS_DIR / safe


def list_projects() -> list[str]:
    if not standards.PROJECTS_DIR.exists():
        return []
    return sorted(p.name for p in standards.PROJECTS_DIR.iterdir() if (p / "project.json").exists())


def save(project: Project, parcels: gpd.GeoDataFrame | None) -> None:
    d = _dir(project.name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(project.model_dump_json(indent=2), encoding="utf-8")
    if parcels is not None:
        (d / "parcels.geojson").write_text(parcels_to_geojson(parcels), encoding="utf-8")


def load(name: str) -> tuple[Project, gpd.GeoDataFrame]:
    d = _dir(name)
    project = upgrade_project(Project.model_validate_json((d / "project.json").read_text(encoding="utf-8")))
    pfile = d / "parcels.geojson"
    parcels = parcels_from_geojson(pfile.read_text(encoding="utf-8")) if pfile.exists() else empty_parcels()
    return project, parcels
