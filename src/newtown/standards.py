"""기준정보(YAML·CSV) 로딩."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(os.environ.get("NEWTOWN_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = ROOT / "data"
STANDARDS_DIR = DATA_DIR / "standards"
REFERENCES_DIR = DATA_DIR / "references"
PROJECTS_DIR = ROOT / "projects"
CACHE_DIR = ROOT / ".cache"


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def multiplier_versions() -> list[str]:
    """사용할 수 있는 보상배율표 판 목록 (파일명 기준)."""
    return sorted(p.stem.removeprefix("compensation_multipliers_")
                  for p in STANDARDS_DIR.glob("compensation_multipliers_*.yaml"))


@lru_cache
def compensation_standard(version: str = "2018") -> dict:
    return _load_yaml(STANDARDS_DIR / f"compensation_multipliers_{version}.yaml")


@lru_cache
def finance_standard() -> dict:
    return _load_yaml(STANDARDS_DIR / "finance_params.yaml")


@lru_cache
def cost_template() -> dict:
    return _load_yaml(STANDARDS_DIR / "cost_template.yaml")


@lru_cache
def landuse_template() -> dict:
    return _load_yaml(STANDARDS_DIR / "landuse_template.yaml")


@lru_cache
def lh_unit_costs(version: str = "2024") -> dict:
    return _load_yaml(STANDARDS_DIR / f"lh_unit_costs_{version}.yaml")


@lru_cache
def fee_rates() -> dict:
    return _load_yaml(STANDARDS_DIR / "fee_rates.yaml")


def lh_disclosures() -> pd.DataFrame:
    """LH가 공개한 지구별 조성원가 산정표 (항목별 금액, ㎡당 단가, 제비용률)."""
    path = REFERENCES_DIR / "lh_cost_disclosures.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def recent_disclosures(df: pd.DataFrame | None = None, since: str = "2024-01-01", min_area: float = 300_000) -> pd.DataFrame:
    """초기 단가의 근거로 쓰는 표본: 최근 최초 산정된 일정 규모 이상 공공주택지구."""
    df = lh_disclosures() if df is None else df
    if df.empty:
        return df
    return df[(df["date"] >= since) & df["kind"].str.contains("최초") & df["name"].str.contains("공공주택지구")
              & (df["area_total_m2"] >= min_area)]


RATE_BENCHMARKS = ["labor_rate_pct", "sales_rate_pct", "admin_rate_pct", "capital_rate_pct", "etc_rate_pct"]


def disclosure_benchmark() -> dict[str, float]:
    """사업비 초기값: ㎡당 단가는 표본의 중앙값, 제비용률은 가장 최근 연도 산정표에서 가장 많이 쓰인 값."""
    sample = recent_disclosures()
    if sample.empty:
        return {}
    out = {c: float(round(sample[c].median(), -3)) for c in ("construction_per_m2", "infra_per_m2")}
    latest = sample[sample["date"].str[:4] == sample["date"].str[:4].max()]
    for c in RATE_BENCHMARKS:
        vals = latest[c].dropna()
        if len(vals):
            out[c] = float(vals.mode().iloc[0])
    return out


def rate_from_table(table: str, amount: float) -> float:
    """요율표에서 공사비(원)에 해당하는 요율(소수)을 직선보간으로 구한다. 표 범위를 벗어나면 끝 구간 요율."""
    rows = fee_rates()["tables"][table]["rows"]
    x = amount / 1e8
    if x <= rows[0][0]:
        return rows[0][1] / 100
    for (x0, r0), (x1, r1) in zip(rows, rows[1:]):
        if x <= x1:
            return (r0 + (r1 - r0) * (x - x0) / (x1 - x0)) / 100
    return rows[-1][1] / 100


def lh_basic_price(terrain: str, area: float, version: str = "2024") -> float:
    """LH 주택단지 기본시설공사 단가(원/㎡) — 지형과 사업면적 규모별."""
    for row in lh_unit_costs(version)["basic_housing"][terrain]:
        if row["max_area"] is None or area <= row["max_area"]:
            return float(row["price"])
    return 0.0


REF_PCT_COLS = ["housing_pct", "commercial_pct", "self_sufficient_pct", "park_green_pct",
                "road_pct", "school_pct", "other_public_pct"]


def landuse_references() -> pd.DataFrame:
    """신도시 토지이용계획 사례. 파일이 없으면 빈 표."""
    path = REFERENCES_DIR / "newtown_landuse.csv"
    if not path.exists():
        return pd.DataFrame(columns=["name", "generation", "total_area_m2", "population", "households",
                                     *REF_PCT_COLS, "source", "source_url", "note"])
    df = pd.read_csv(path)
    for c in REF_PCT_COLS + ["total_area_m2", "population", "households"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df
