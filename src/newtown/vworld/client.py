"""VWorld 오픈API 클라이언트.

- 2D 데이터 API (https://api.vworld.kr/req/data): 연속지적도·용도지역 등 도형 조회
- 국가중점데이터 API (https://api.vworld.kr/ned/data/...): PNU 단위 필지 속성 조회
- 검색 API (https://api.vworld.kr/req/search)

응답은 SQLite에 캐시해 같은 요청을 반복하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable

import httpx
from dotenv import load_dotenv

from .. import standards

DATA_URL = "https://api.vworld.kr/req/data"
SEARCH_URL = "https://api.vworld.kr/req/search"
NED_URL = "https://api.vworld.kr/ned/data/{op}"
PAGE_SIZE = 1000  # 2D 데이터 API 1회 최대 건수
NED_PAGE_SIZE = 1000

# 2D 데이터 API 레이어
LAYER_PARCEL = "LP_PA_CBND_BUBUN"
ZONING_LAYERS = {
    "LT_C_UQ111": "도시지역",
    "LT_C_UQ112": "관리지역",
    "LT_C_UQ113": "농림지역",
    "LT_C_UQ114": "자연환경보전지역",
}
CONSTRAINT_LAYERS = {
    "LT_C_UD801": "개발제한구역",
}


class VWorldError(RuntimeError):
    pass


def load_credentials() -> tuple[str, str]:
    """VWorld 인증키와 등록 도메인. 환경변수(.env 포함)를 먼저 보고, 없으면 Streamlit secrets를 본다."""
    load_dotenv(standards.ROOT / ".env")
    key, domain = os.environ.get("VWORLD_API_KEY", "").strip(), os.environ.get("VWORLD_DOMAIN", "").strip()
    if not key:
        try:
            import streamlit as st

            key = str(st.secrets.get("VWORLD_API_KEY", "")).strip()
            domain = domain or str(st.secrets.get("VWORLD_DOMAIN", "")).strip()
        except Exception:  # secrets 파일이 없거나 Streamlit 밖에서 실행 중
            pass
    return key, domain


class _Cache:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS resp (k TEXT PRIMARY KEY, v TEXT, t REAL)")

    def get(self, key: str):
        with self._lock:
            row = self._db.execute("SELECT v FROM resp WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO resp VALUES (?,?,?)", (key, json.dumps(value), time.time()))
            self._db.commit()


class VWorldClient:
    def __init__(self, key: str | None = None, domain: str | None = None, *, use_cache: bool = True,
                 min_interval: float = 0.05, transport: httpx.BaseTransport | None = None):
        env_key, env_domain = load_credentials()
        self.key = key or env_key
        self.domain = domain or env_domain
        if not self.key:
            raise VWorldError("VWorld 인증키가 없습니다. .env 파일에 VWORLD_API_KEY를 설정하세요.")
        self._http = httpx.Client(timeout=30, transport=transport)
        self._cache = _Cache(standards.CACHE_DIR / "vworld.sqlite") if use_cache else None
        self._min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    # ---- 공통 ----
    def _get(self, url: str, params: dict, *, retries: int = 3) -> dict:
        cache_key = hashlib.sha1((url + json.dumps(params, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
        if self._cache and (hit := self._cache.get(cache_key)) is not None:
            return hit
        q = dict(params, key=self.key)
        if self.domain:
            q["domain"] = self.domain
        last_err: Exception | None = None
        for attempt in range(retries):
            with self._lock:  # 호출 간격 제한
                wait = self._min_interval - (time.time() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.time()
            try:
                r = self._http.get(url, params=q)
                r.raise_for_status()
                body = r.json()
                if self._cache and _cacheable(body):
                    self._cache.put(cache_key, body)
                return body
            except (httpx.HTTPError, ValueError) as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        raise VWorldError(f"VWorld 요청 실패: {url} ({last_err})")

    # ---- 2D 데이터 API ----
    def get_features(self, layer: str, *, geom_filter: str | None = None, attr_filter: str | None = None,
                     max_pages: int = 200) -> list[dict]:
        """조건에 맞는 도형(GeoJSON Feature) 전체를 페이지를 넘겨 가며 수집한다."""
        feats: list[dict] = []
        page = 1
        while page <= max_pages:
            params = {"service": "data", "version": "2.0", "request": "GetFeature", "format": "json",
                      "data": layer, "size": PAGE_SIZE, "page": page, "crs": "EPSG:4326",
                      "geometry": "true", "attribute": "true"}
            if geom_filter:
                params["geomFilter"] = geom_filter
            if attr_filter:
                params["attrFilter"] = attr_filter
            resp = self._get(DATA_URL, params).get("response", {})
            status = resp.get("status")
            if status == "NOT_FOUND":
                break
            if status != "OK":
                err = resp.get("error", {})
                raise VWorldError(f"{layer} 조회 오류: {err.get('code', status)} {err.get('text', '')}")
            feats.extend(resp["result"]["featureCollection"]["features"])
            total_pages = int(resp.get("page", {}).get("total", 1))
            if page >= total_pages:
                break
            page += 1
        return feats

    def get_features_in_bounds(self, layer: str, bounds: tuple[float, float, float, float], *,
                               tile_deg: float = 0.01, id_field: str | None = None,
                               progress: Callable[[int, int], None] | None = None) -> list[dict]:
        """경계 사각형을 격자로 나눠 조회하고 중복을 제거한다 (대규모 구역 대응)."""
        minx, miny, maxx, maxy = bounds
        tiles = []
        y = miny
        while y < maxy:
            x = minx
            while x < maxx:
                tiles.append((x, y, min(x + tile_deg, maxx), min(y + tile_deg, maxy)))
                x += tile_deg
            y += tile_deg
        seen: dict[str, dict] = {}
        for i, (x0, y0, x1, y1) in enumerate(tiles, 1):
            for f in self.get_features(layer, geom_filter=f"BOX({x0:.7f},{y0:.7f},{x1:.7f},{y1:.7f})"):
                fid = str(f.get("properties", {}).get(id_field)) if id_field else f.get("id") or json.dumps(f["geometry"])[:200]
                seen[fid] = f
            if progress:
                progress(i, len(tiles))
        return list(seen.values())

    # ---- 국가중점데이터 API ----
    # pnu 자리에 PNU 19자리를 주면 그 필지, 법정동코드 10자리를 주면 그 동·리의 전체 필지를 돌려준다.
    def ned(self, op: str, pnu: str, **extra) -> list[dict]:
        """한 쪽(최대 1,000건)을 조회한다."""
        body = self._get(NED_URL.format(op=op), {"pnu": pnu, "format": "json", "numOfRows": NED_PAGE_SIZE, "pageNo": 1, **extra})
        _raise_ned_error(op, body)
        return _find_records(body)

    def ned_all(self, op: str, pnu: str, max_pages: int = 300, **extra) -> list[dict]:
        """모든 쪽을 넘겨 가며 조회한다."""
        recs: list[dict] = []
        for page in range(1, max_pages + 1):
            body = self._get(NED_URL.format(op=op), {"pnu": pnu, "format": "json", "numOfRows": NED_PAGE_SIZE, "pageNo": page, **extra})
            _raise_ned_error(op, body)
            got = _find_records(body)
            recs.extend(got)
            total = _total_count(body)
            if not got or len(got) < NED_PAGE_SIZE or (total is not None and page * NED_PAGE_SIZE >= total):
                break
        return recs

    def fetch_parcel_attributes(self, pnus: Iterable[str], *, price_year: str | None = None, bulk_min: int = 20, workers: int = 4,
                                progress: Callable[[int, int], None] | None = None) -> tuple[dict[str, dict], list[str]]:
        """필지의 토지특성(지목·면적·용도지역·이용상황·공시지가)과 소유구분을 조회한다.

        같은 법정동의 필지가 bulk_min건 이상이면 동·리 단위로 한꺼번에 받고(호출 수가 크게 준다),
        그보다 적거나 일괄 조회에서 빠진 필지는 필지별로 조회한다.
        price_year: 토지특성 기준연도. 일괄 조회의 응답량을 줄이는 데 쓴다(없으면 전 연도를 받아 최신 값을 고른다).
        (속성 dict, 실패 PNU 목록)을 돌려준다.
        """
        pnus = list(dict.fromkeys(str(p) for p in pnus))
        wanted = set(pnus)
        by_dong: dict[str, list[str]] = {}
        for p in pnus:
            by_dong.setdefault(p[:10], []).append(p)
        chars: dict[str, dict] = {}
        owners: dict[str, dict] = {}
        failed: set[str] = set()
        bulk = [d for d, ps in by_dong.items() if len(ps) >= bulk_min]
        single = [p for d, ps in by_dong.items() if len(ps) < bulk_min for p in ps]
        steps = len(bulk) * 2 + len(single)
        done = 0

        def tick() -> None:
            nonlocal done
            done += 1
            if progress:
                progress(min(done, steps), max(steps, 1))

        def keep_latest(rec: dict) -> None:
            pnu = rec.get("pnu")
            if pnu in wanted and _recency(rec) >= _recency(chars.get(pnu, {})):
                chars[pnu] = rec

        for dong in bulk:
            try:
                extra = {"stdrYear": price_year} if price_year else {}
                for rec in self.ned_all("getLandCharacteristics", dong, **extra):
                    keep_latest(rec)
            except VWorldError:
                pass  # 아래에서 필지별로 다시 조회한다
            tick()
            try:
                for rec in self.ned_all("ladfrlList", dong):
                    if rec.get("pnu") in wanted:
                        owners[rec["pnu"]] = rec
            except VWorldError:
                pass
            tick()

        def one(pnu: str) -> None:
            if pnu not in chars:
                for rec in self.ned("getLandCharacteristics", pnu):
                    keep_latest(rec)
            if pnu not in owners:
                recs = self.ned("ladfrlList", pnu)
                if recs:
                    owners[pnu] = recs[0]

        # 일괄 조회에서 빠진 필지(해당 연도 자료 없음 등)도 필지별로 보충한다
        leftovers = [p for d in bulk for p in by_dong[d] if p not in chars or p not in owners]
        steps += len(leftovers)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(one, p): p for p in single + leftovers}
            for fut, p in futures.items():
                try:
                    fut.result()
                except Exception:
                    failed.add(p)
                tick()

        out: dict[str, dict] = {}
        for p in pnus:
            ch, ow = chars.get(p, {}), owners.get(p, {})
            if not ch and not ow:
                continue
            out[p] = {
                "jimok": ch.get("lndcgrCodeNm") or ow.get("lndcgrCodeNm"),
                "area_reg": _num(ow.get("lndpclAr")) or _num(ch.get("lndpclAr")),
                "zone": ch.get("prposArea1Nm"),
                "use": ch.get("ladUseSittnNm"),
                "price": _num(ch.get("pblntfPclnd")),
                "price_year": ch.get("stdrYear"),
                "owner": ow.get("posesnSeCodeNm"),
            }
        return out, sorted(failed)

    # ---- 검색 ----
    def search(self, query: str, size: int = 10) -> list[dict]:
        """지명·주소 검색. [{title, address, lon, lat}]"""
        results = []
        for typ, extra in (("district", {"category": "L4"}), ("address", {"category": "parcel"}), ("place", {})):
            body = self._get(SEARCH_URL, {"service": "search", "request": "search", "version": "2.0", "format": "json",
                                          "crs": "EPSG:4326", "size": size, "page": 1, "query": query, "type": typ, **extra})
            resp = body.get("response", {})
            if resp.get("status") != "OK":
                continue
            for it in resp.get("result", {}).get("items", []):
                pt = it.get("point") or {}
                if not pt:
                    continue
                addr = it.get("address")
                if isinstance(addr, dict):
                    addr = addr.get("parcel") or addr.get("road") or ""
                results.append({"title": it.get("title") or addr or query, "address": addr or "",
                                "lon": float(pt["x"]), "lat": float(pt["y"])})
            if results:
                break
        return results


def _cacheable(body: dict) -> bool:
    if "response" in body:
        return body["response"].get("status") in ("OK", "NOT_FOUND")
    w = _wrapper(body)
    return not (w.get("resultCode") or w.get("error"))


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _recency(rec: dict) -> tuple:
    """토지특성 레코드의 최신 순서 (기준연도, 기준월, 갱신일)."""
    return (str(rec.get("stdrYear", "")), str(rec.get("stdrMt", "")), str(rec.get("lastUpdtDt", "")))


def _wrapper(body) -> dict:
    """국가중점데이터 API 응답의 바깥 객체 ({"landCharacteristicss": {...}} 의 안쪽)."""
    if isinstance(body, dict) and len(body) == 1:
        inner = next(iter(body.values()))
        if isinstance(inner, dict):
            return inner
    return body if isinstance(body, dict) else {}


def _total_count(body) -> int | None:
    try:
        return int(_wrapper(body).get("totalCount"))
    except (TypeError, ValueError):
        return None


def _raise_ned_error(op: str, body) -> None:
    w = _wrapper(body)
    code = w.get("resultCode") or w.get("error")
    if code and str(code) not in ("0", "00", "NORMAL_CODE", "INFO-000"):
        raise VWorldError(f"{op} 조회 오류: {code} {w.get('resultMsg') or w.get('message') or ''}".strip())


def _find_records(body) -> list[dict]:
    """국가중점데이터 API 응답에서 레코드 목록을 찾는다 (오퍼레이션마다 감싸는 키 이름이 다르다)."""
    if isinstance(body, list):
        if body and all(isinstance(x, dict) for x in body):
            return body
        return []
    if isinstance(body, dict):
        for v in body.values():
            recs = _find_records(v)
            if recs:
                return recs
    return []
