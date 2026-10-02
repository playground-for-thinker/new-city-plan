"""③ 사업비 산정 — 보상비(토지세목조서)와 조성비 등."""
import io

import pandas as pd
import streamlit as st

import ui
from newtown import standards
from newtown.cost import compensation
from newtown.cost.total import BASE_LABELS, BASIS_LABELS, MODE_LABELS, SCHEDULE_LABELS, active_lines
from newtown.site import parcels as P

prj = ui.need_boundary()
ui.step_header("③ 사업비 산정", "보상비는 토지세목조서와 예비타당성조사 지침의 공시지가 보상배율로, 조성비 등은 LH 조성원가 항목 체계로 산정합니다.")
pcl = ui.parcels()
if "가상" in prj.parcel_source:
    st.warning("현재 필지는 **가상 필지(시험용)** 입니다. 보상비는 실제 값이 아닙니다.", icon=":material/science:")

tab_comp, tab_cost = st.tabs(["보상비 (토지세목조서)", "조성비 등 총사업비"])

# ======================= 보상비 =======================
with tab_comp:
    if pcl.empty:
        st.info("① 대상지 선정에서 편입 필지를 수집하면 토지세목조서가 만들어집니다.")
    else:
        std = standards.compensation_standard(prj.comp.multiplier_version)
        with st.expander("산정 기준", expanded=False):
            st.markdown(
                "- 필지 토지보상비 = 편입면적 × 개별공시지가 × 적용 배율\n"
                "- 적용 배율 = (시도별 **용도지역** 배율 + 시도별 **이용상황** 배율) ÷ 2\n"
                "- 지장물 및 기타 보상비 = 토지보상비 × 사업유형별 비율 (택지개발 25%)\n"
                "- 배율표에 값이 없는 칸은 해당 시도의 전체 배율로 대체합니다.")
            st.caption(f"배율표: {std['version']} — {std['source']}")
            mult = pd.DataFrame([{"시도": s, "전체": v["전체"], **dict(zip(std["zone_classes"], v["zone"])), **dict(zip(std["use_classes"], v["use"]))}
                                 for s, v in std["multipliers"].items()])
            st.dataframe(mult, hide_index=True, use_container_width=True)
            st.caption("앞 4개 열은 용도지역 배율, 뒤 5개 열은 이용상황 배율입니다.")

        s = st.columns(4)
        versions = standards.multiplier_versions()
        prj.comp.multiplier_version = s[0].selectbox("보상배율표 판", versions, index=versions.index(prj.comp.multiplier_version))
        prj.comp.price_adjust = s[1].number_input("공시지가 시점 보정계수", 0.5, 3.0, prj.comp.price_adjust, 0.01,
                                                  help="공시지가 기준연도와 분석 기준연도가 다를 때 지가변동률을 반영하는 계수입니다. 1.00이면 보정하지 않습니다.")
        mode = s[2].selectbox("지장물 및 기타 보상비", ["토지보상비 대비 비율", "금액 직접 입력"],
                              index=0 if prj.comp.obstacle_mode == "ratio" else 1)
        prj.comp.obstacle_mode = "ratio" if mode == "토지보상비 대비 비율" else "manual"
        if prj.comp.obstacle_mode == "ratio":
            default_ratio = std["obstacle_ratio"].get(prj.comp.project_type, 0.25)
            ratio_pct = s[3].number_input("비율(%)", 0.0, 100.0, (prj.comp.obstacle_ratio if prj.comp.obstacle_ratio is not None else default_ratio) * 100, 1.0,
                                          help="지침 기준: 택지개발 25%. 지장물 조사자료가 있으면 '금액 직접 입력'을 쓰세요.")
            prj.comp.obstacle_ratio = ratio_pct / 100
        else:
            prj.comp.obstacle_manual = s[3].number_input("금액(억원)", 0.0, None, prj.comp.obstacle_manual / 1e8, 10.0) * 1e8

        res = ui.compensation()
        k = st.columns(4)
        k[0].metric("토지보상비 (A)", ui.eok(res.land_comp, 0))
        k[1].metric("지장물 및 기타 (B)", ui.eok(res.obstacle, 0))
        k[2].metric("직접보상비 (A+B)", ui.eok(res.direct, 0))
        area = res.register["area_incl"].sum()
        k[3].metric("㎡당 토지보상비", f"{res.land_comp / area:,.0f}원" if area else "-")
        if res.issues:
            st.warning("자료 보완이 필요한 필지 — " + " · ".join(f"{k_} {v:,}건" for k_, v in res.issues.items()), icon=":material/report:")

        st.subheader("토지세목조서")
        st.caption("지목·용도지역·이용상황·소유구분·공시지가·무상귀속은 표에서 직접 고칠 수 있습니다. 고치면 배율과 보상비가 다시 계산됩니다. "
                   "기존 도로·구거·하천 등 무상귀속 대상 국공유지는 '무상귀속'에 표시하면 보상비에서 빠집니다.")
        f = st.columns([2, 2, 3])
        only_issue = f[0].toggle("비고가 있는 필지만 보기")
        jimok_filter = f[1].multiselect("지목", sorted(res.register["jimok"].dropna().unique()), placeholder="지목 필터", label_visibility="collapsed")
        reg = res.register
        if only_issue:
            reg = reg[reg["remark"] != ""]
        if jimok_filter:
            reg = reg[reg["jimok"].isin(jimok_filter)]
        show = ["no", "addr", "jibun", "jimok", "area_reg", "incl_ratio", "area_incl", "zone", "use", "owner", "price",
                "free", "zone_mult", "use_mult", "mult", "land_comp", "remark"]
        labels = {**compensation.REGISTER_COLUMNS, "free": "무상귀속"}
        editable = ["jimok", "zone", "use", "owner", "price", "free"]
        num = st.column_config.NumberColumn
        edited = st.data_editor(
            reg[show].rename(columns=labels), hide_index=True, use_container_width=True, height=420,
            disabled=[labels[c] for c in show if c not in editable],
            key=f"register_{st.session_state.get('parcels_ver', 0)}_{only_issue}_{'|'.join(jimok_filter)}",
            column_config={labels["area_reg"]: num(format="localized"), labels["area_incl"]: num(format="localized"),
                           labels["price"]: num(format="localized", min_value=0), labels["land_comp"]: num(format="localized"),
                           labels["incl_ratio"]: num(format="%.2f"), labels["mult"]: num(format="%.3f")})
        before = reg[editable].reset_index(drop=True)
        after = edited[[labels[c] for c in editable]].set_axis(editable, axis=1).reset_index(drop=True)
        diff = ~((before == after) | (before.isna() & after.isna())).all(axis=1)
        if diff.any():
            new = pcl.copy()
            by_pnu = new.set_index("pnu")
            for i in diff[diff].index:
                pnu = reg.iloc[i]["pnu"]
                for col in editable:
                    by_pnu.at[pnu, col] = after.at[i, col]
            new[editable] = by_pnu[editable].values
            new["free"] = new["free"].fillna(False).astype(bool)
            ui.set_parcels(new)
            st.rerun()

        buf = io.BytesIO()
        compensation.register_table(res).to_excel(buf, index=False, sheet_name="토지세목조서")
        st.download_button("토지세목조서 Excel 내려받기", buf.getvalue(), f"{prj.name}_토지세목조서.xlsx", icon=":material/download:")

        st.subheader("보상비 집계")
        g = st.tabs(["지목별", "용도지역 분류별", "이용상황 분류별", "소유구분별"])
        for tab, by in zip(g, ["jimok", "zone_class", "use_class", "owner"]):
            tab.dataframe(compensation.summarize(res, by), hide_index=True, use_container_width=True, column_config={
                "편입면적(㎡)": num(format="localized"), "토지보상비(원)": num(format="localized"), "㎡당 보상비(원)": num(format="localized")})

# ======================= 조성비 등 =======================
with tab_cost:
    ev = ui.evaluation()
    disc = standards.lh_disclosures()
    sample = standards.recent_disclosures(disc)
    num = st.column_config.NumberColumn
    sel = st.column_config.SelectboxColumn
    by_key = {l.key: l for l in prj.cost_lines}

    modes = list(MODE_LABELS)
    picked = st.radio(
        "조성비·기반시설설치비 산정 방식", modes, index=modes.index(prj.cost_mode), format_func=MODE_LABELS.get, horizontal=True,
        captions=["LH가 공개한 지구별 조성원가 산정표의 ㎡당 실적 (설계·감리·자재·부담금 포함)",
                  "기본시설공사 단가 + 추가공종 + 요율표로 쌓아 올리는 방식"])
    if picked != prj.cost_mode:
        prj.cost_mode = picked
        ui.persist()
        st.rerun()
    if prj.cost_mode == "rough":
        st.info(f"초기 단가는 LH 누리집 '조성원가 공개사업지구'의 산정표 가운데 2024년 이후 최초 산정된 30만㎡ 이상 공공주택지구 "
                f"{len(sample)}곳의 중앙값입니다(총사업면적 ㎡당). 지구마다 편차가 크므로 아래 참고표에서 유사한 지구를 골라 조정하세요. "
                "특히 기반시설설치비는 광역교통개선대책 규모에 따라 크게 달라집니다.", icon=":material/info:")
    else:
        st.info("LH 「2024년도 단지개발사업 조성비 및 기반시설설치비 추정자료」의 단가(인천연구원 가이드라인 재인용, 2024년 12월 기준 보정)로 적산합니다. "
                "기본시설공사 단가는 기본공종만 포함하므로 하천·저류지·전기·통신·특수구조물·부담금 등은 추가 항목에 따로 넣어야 합니다. "
                "설계비·감리비·시설부대비는 정부 예산편성 기준 요율표를 씁니다.", icon=":material/info:")

    p = st.columns(4)
    prj.finance.bid_rate = p[0].number_input("낙찰률(%)", 50.0, 100.0, prj.finance.bid_rate * 100, 0.5,
                                             help="낙찰률 적용 대상으로 표시한 공사비 항목에 곱합니다.") / 100
    prj.finance.vat_rate = p[1].number_input("부가가치세율(%)", 0.0, 20.0, prj.finance.vat_rate * 100, 1.0,
                                             help="부가세 가산 대상으로 표시한 항목에 더합니다. 실적 단가에는 이미 들어 있어 가산하지 않습니다.") / 100
    prj.finance.contingency_rate = p[2].number_input("예비비율(%)", 0.0, 30.0, prj.finance.contingency_rate * 100, 1.0,
                                                     help="예타 지침: 부가가치세를 포함한 사업비의 10%. 예타 재무 분석 관점에만 반영됩니다.") / 100
    prj.price_escalation = p[3].number_input("단가 시점 보정계수", 0.5, 3.0, float(prj.price_escalation), 0.01,
                                             help="단가 기준시점과 분석 기준연도의 물가 차이를 반영합니다(건설투자 GDP 디플레이터 등). 물량 × 단가 항목에 곱합니다.")
    if prj.cost_mode == "detail":
        d = st.columns(4)
        prj.terrain = d[0].selectbox("지형", ["평지", "구릉지"], index=["평지", "구릉지"].index(prj.terrain),
                                     help="LH 기본시설공사 단가의 지형 구분")
        prj.preserved_area = d[1].number_input("보존면적(㎡)", 0.0, None, float(prj.preserved_area), 10000.0,
                                               help="기본시설공사 단가는 사업면적(총면적 − 보존면적)에 적용합니다.")

    q = {**ev.areas, "population": ev.program.population}
    st.caption(f"자동 연결 물량 — 총면적 {q['total_area']:,.0f}㎡ · 사업면적 {q['dev_area']:,.0f}㎡ · 도로 {q['road_area']:,.0f}㎡ · "
               f"공원 {q['park_area']:,.0f}㎡ · 녹지 {q['green_area']:,.0f}㎡ · 유상공급 {q['paid_area']:,.0f}㎡ (④·⑤ 단계를 바꾸면 자동으로 갱신됩니다)")

    lines = active_lines(prj.cost_lines, prj.cost_mode)
    amounts = ev.costs.table.set_index("key")["합계(원)"]
    applied = ev.costs.applied
    inv = lambda d: {v: k for k, v in d.items()}
    CAPITAL_BASE = "순투입액 누적액"

    def f(v, default=0.0):
        return default if v is None or v != v else float(v)

    def editor(title: str, caption: str, df: pd.DataFrame, key: str, disabled: list[str], config: dict) -> list[dict]:
        st.markdown(f"**{title}**")
        st.caption(caption)
        out = st.data_editor(df, hide_index=True, use_container_width=True, height=35 * (len(df) + 1) + 3, key=key,
                             column_order=[c for c in df.columns if c != "key"], disabled=["구분", "항목", "금액(억원)", *disabled],
                             column_config={"투입 일정": sel(options=list(SCHEDULE_LABELS.values()), required=True),
                                            "금액(억원)": num(format="localized"), **config})
        return out.to_dict("records")

    def common(l) -> dict:
        return {"key": l.key, "구분": l.category, "항목": l.name}

    def tail(l) -> dict:
        return {"부가세": l.vat, "투입 일정": SCHEDULE_LABELS[l.schedule], "금액(억원)": round(amounts.get(l.key, 0) / 1e8, 1)}

    def shared(r: dict) -> dict:
        return {"vat": bool(r["부가세"]), "schedule": inv(SCHEDULE_LABELS)[r["투입 일정"]]}

    updates: dict[str, dict] = {}
    qp = [l for l in lines if l.method == "qty_price"]
    recs = editor(
        "물량 × 단가 항목",
        "'물량 기준'이 '직접 입력'이면 물량 칸의 값을 쓰고, 그 밖에는 토지이용계획에서 물량을 가져옵니다. "
        "'표 단가'에 표시된 항목은 LH 단가표(지형·규모별)를 자동으로 적용하며, 표시를 끄면 입력한 단가를 씁니다.",
        pd.DataFrame([{**common(l), "물량 기준": BASIS_LABELS[l.basis], "물량": l.qty if l.basis == "manual" else round(q.get(l.basis, 0.0)),
                       "단위": l.unit, "표 단가": l.auto and bool(l.table), "단가(원)": applied.get(l.key, l.unit_price),
                       "낙찰률 적용": l.bid, **tail(l)} for l in qp]),
        f"cost_qp_{prj.cost_mode}_{prj.terrain}_{st.session_state.get('cost_ver', 0)}", ["단위"],
        {"물량 기준": sel(options=list(BASIS_LABELS.values()), required=True), "물량": num(format="localized", min_value=0),
         "단가(원)": num(format="localized", min_value=0)})
    for r in recs:
        l = by_key[r["key"]]
        basis = inv(BASIS_LABELS)[r["물량 기준"]]
        auto = bool(r["표 단가"]) and bool(l.table)
        updates[r["key"]] = {**shared(r), "basis": basis, "auto": auto, "bid": bool(r["낙찰률 적용"]),
                             **({} if auto else {"unit_price": f(r["단가(원)"])}), **({"qty": f(r["물량"])} if basis == "manual" else {})}

    rt = [l for l in lines if l.method in ("rate", "capital")]
    recs = editor(
        "요율 항목", "기준금액 × 요율로 계산합니다. '표 요율'에 표시된 항목은 공사비 규모에 맞는 요율표 값을 자동으로 적용합니다. "
        "직접인건비·판매비·일반관리비·자본비용·그 밖의 비용의 초기 요율은 LH 공개 산정표의 최근 제비용률입니다. "
        "자본비용은 연차별 순투입액 누적액에 자본비용률을 곱합니다(⑤의 사업 일정과 ⑥의 공급가격에 따라 달라집니다).",
        pd.DataFrame([{**common(l), "요율 기준": CAPITAL_BASE if l.method == "capital" else BASE_LABELS[l.base],
                       "표 요율": l.auto and bool(l.table), "요율(%)": round(applied.get(l.key, l.rate) * 100, 4), **tail(l)} for l in rt]),
        f"cost_rate_{prj.cost_mode}_{st.session_state.get('cost_ver', 0)}", [],
        {"요율 기준": sel(options=[*BASE_LABELS.values(), CAPITAL_BASE], required=True),
         "요율(%)": num(format="%.2f", min_value=0.0, step=0.01)})
    for r in recs:
        l = by_key[r["key"]]
        auto = bool(r["표 요율"]) and bool(l.table)
        upd = {**shared(r), "auto": auto, **({} if auto else {"rate": round(f(r["요율(%)"]) / 100, 8)})}
        if l.method == "rate" and r["요율 기준"] in inv(BASE_LABELS):
            upd["base"] = inv(BASE_LABELS)[r["요율 기준"]]
        updates[r["key"]] = upd

    mn = [l for l in lines if l.method == "manual"]
    recs = editor(
        "직접 입력 항목", "금액을 억원 단위로 입력합니다.",
        pd.DataFrame([{**common(l), "입력 금액(억원)": l.amount / 1e8, **tail(l)} for l in mn]),
        f"cost_manual_{prj.cost_mode}_{st.session_state.get('cost_ver', 0)}", [], {"입력 금액(억원)": num(format="%.1f", min_value=0.0)})
    for r in recs:
        updates[r["key"]] = {**shared(r), "amount": round(f(r["입력 금액(억원)"]) * 1e8)}
    changed = False
    for key, upd in updates.items():
        l = by_key[key]
        for k_, v in upd.items():
            cur = getattr(l, k_)
            if (abs(cur - v) > 1e-9) if isinstance(v, float) and isinstance(cur, (int, float)) else cur != v:
                setattr(l, k_, v)
                changed = True
    if changed:
        st.session_state["cost_ver"] = st.session_state.get("cost_ver", 0) + 1
        ui.persist()
        st.rerun()

    # ---------- 근거 자료 ----------
    with st.expander(f"LH 공개 조성원가 산정표 ({len(disc)}곳) — 실적 단가와 제비용률"):
        st.caption("출처: LH 누리집 › 사업안내 › 신도시 › 조성원가 공개사업지구의 지구별 조성원가 산정표. ㎡당 금액은 총사업면적 기준이며, "
                   "조성비에는 공사비 외에 설계비·자재비·감리비·각종 부담금이 들어 있습니다.")
        if len(disc):
            show = disc[["name", "kind", "date", "area_total_m2", "construction_per_m2", "infra_per_m2", "levy_per_m2", "labor_rate_pct",
                         "sales_rate_pct", "admin_rate_pct", "capital_rate_pct", "etc_rate_pct", "unit_cost_won_per_m2", "source_url"]].rename(columns={
                "name": "지구", "kind": "구분", "date": "공개일", "area_total_m2": "총사업면적(㎡)", "construction_per_m2": "조성비(원/㎡)",
                "infra_per_m2": "기반시설설치비(원/㎡)", "levy_per_m2": "용지부담금(원/㎡)", "labor_rate_pct": "직접인건비율(%)",
                "sales_rate_pct": "판매비율(%)", "admin_rate_pct": "일반관리비율(%)", "capital_rate_pct": "자본비용률(%)",
                "etc_rate_pct": "그 밖의 비용율(%)", "unit_cost_won_per_m2": "조성원가(원/㎡)", "source_url": "산정표"})
            st.dataframe(show, hide_index=True, use_container_width=True, column_config={
                "산정표": st.column_config.LinkColumn(display_text="열기"),
                **{c: num(format="localized") for c in ["총사업면적(㎡)", "조성비(원/㎡)", "기반시설설치비(원/㎡)", "용지부담금(원/㎡)", "조성원가(원/㎡)"]}})
            chosen = st.multiselect("실적 단가의 근거로 쓸 지구", disc["name"].tolist(), default=sample["name"].tolist())
            sub = disc[disc["name"].isin(chosen)]
            if len(sub):
                med_c, med_i = round(sub["construction_per_m2"].median(), -3), round(sub["infra_per_m2"].median(), -3)
                m_ = st.columns(3)
                m_[0].metric("조성비 중앙값", f"{med_c:,.0f}원/㎡", help=f"범위 {sub['construction_per_m2'].min():,.0f} ~ {sub['construction_per_m2'].max():,.0f}")
                m_[1].metric("기반시설설치비 중앙값", f"{med_i:,.0f}원/㎡", help=f"범위 {sub['infra_per_m2'].min():,.0f} ~ {sub['infra_per_m2'].max():,.0f}")
                if m_[2].button("이 중앙값을 실적 단가로 적용", disabled=prj.cost_mode != "rough"):
                    by_key["c_rough"].unit_price, by_key["infra_rough"].unit_price = float(med_c), float(med_i)
                    st.session_state["cost_ver"] = st.session_state.get("cost_ver", 0) + 1
                    ui.persist()
                    st.rerun()
    with st.expander("LH 추정자료 단가표 (2024) · 부대비 요율표"):
        lh = standards.lh_unit_costs()
        st.caption(f"출처: {lh['source']}")
        for note in lh["notes"]:
            st.caption("· " + note)
        c1, c2 = st.columns([2, 3])
        basic = pd.DataFrame([{"지형": t, "사업면적": "초과 구간" if r["max_area"] is None else f"{r['max_area'] / 1e4:,.0f}만㎡ 이하", "단가(원/㎡)": r["price"]}
                              for t, rows_ in lh["basic_housing"].items() for r in rows_])
        c1.markdown("**주택단지 기본시설공사 단가**")
        c1.dataframe(basic, hide_index=True, use_container_width=True, column_config={"단가(원/㎡)": num(format="localized")})
        c2.markdown("**추가공종·기반시설 참고 단가**")
        c2.dataframe(pd.DataFrame(lh["reference"]).rename(columns={"group": "구분", "item": "항목", "unit": "단위", "price": "단가(원)"}),
                     hide_index=True, use_container_width=True, height=318, column_config={"단가(원)": num(format="localized")})
        fees = standards.fee_rates()
        st.caption(f"요율표 출처: {fees['source']}")
        tabs_ = st.tabs([t["label"] for t in fees["tables"].values()])
        for tab_, t in zip(tabs_, fees["tables"].values()):
            tab_.dataframe(pd.DataFrame(t["rows"], columns=["공사비(억원)까지", "요율(%)"]).T, use_container_width=True)

    with st.expander("농지보전부담금 계산 도우미"):
        st.caption("편입 농지(전·답·과수원)의 개별공시지가에 부과율을 곱하고 ㎡당 상한을 적용해 합산합니다. "
                   "부과율·상한·감면율은 「농지법」 시행령 등 현행 법령을 확인해 입력하세요.")
        h = st.columns(4)
        farm_rate = h[0].number_input("부과율(%)", 0.0, 100.0, 30.0, 1.0)
        farm_cap = h[1].number_input("㎡당 상한(원)", 0, None, 50_000, 1_000)
        farm_cut = h[2].number_input("감면율(%)", 0.0, 100.0, 0.0, 5.0)
        inc = pcl[pcl["included"]] if len(pcl) else pcl
        farm = inc[inc["jimok"].isin(["전", "답", "과수원"])] if len(inc) else inc
        levy = float(((farm["price"].fillna(0) * farm_rate / 100).clip(upper=farm_cap) * farm["area_incl"]).sum() * (1 - farm_cut / 100)) if len(farm) else 0.0
        h[3].metric("계산 결과", ui.eok(levy))
        st.caption(f"편입 농지 {farm['area_incl'].sum() if len(farm) else 0:,.0f}㎡ · 임야 {inc.loc[inc['jimok'] == '임야', 'area_incl'].sum() if len(inc) else 0:,.0f}㎡")
        if st.button("이 금액을 농지보전부담금에 반영"):
            by_key["levy_farm"].amount = round(levy)
            st.session_state["cost_ver"] = st.session_state.get("cost_ver", 0) + 1
            ui.persist()
            st.rerun()

    ev = ui.evaluation()
    st.subheader("총사업비")
    k = st.columns(4)
    k[0].metric("조성원가 관점", ui.eok(ev.costs.total_cost_basis, 0), help="자본비용 포함, 예비비 제외. 공급가격(조성원가) 산정에 씁니다.")
    k[1].metric("예타 재무 분석 관점", ui.eok(ev.costs.total_feasibility, 0), help="자본비용(금융비용) 제외, 예비비 포함. 재무적 타당성 분석의 현금유출입니다.")
    k[2].metric("㎡당 조성원가", f"{ev.costs.unit_cost:,.0f}원" if ev.costs.unit_cost else "-", help="조성원가 관점 총사업비 ÷ 유상공급 면적")
    k[3].metric("유상공급 면적", f"{ev.areas['paid_area'] / 1e4:,.1f}만㎡")
    if ev.costs.missing:
        st.warning("단가·요율이 입력되지 않은 항목(0으로 계산됨): " + ", ".join(ev.costs.missing), icon=":material/report:")
    bench = standards.disclosure_benchmark()
    if ev.total_area > 0 and bench:
        cat_amt = ev.costs.table.groupby("구분")["합계(원)"].sum()
        per_c, per_i = cat_amt.get("조성비", 0) / ev.total_area, cat_amt.get("기반시설설치비", 0) / ev.total_area
        st.caption(f"이 계획의 ㎡당 조성비 {per_c:,.0f}원 · 기반시설설치비 {per_i:,.0f}원 (총면적 기준) — "
                   f"LH 공개 산정표 최근 중앙값: 조성비 {bench['construction_per_m2']:,.0f}원 · 기반시설설치비 {bench['infra_per_m2']:,.0f}원")
        if prj.cost_mode == "detail" and per_c < 0.5 * bench["construction_per_m2"]:
            st.warning("적산한 조성비가 LH 공개 실적 중앙값의 절반에 못 미칩니다. 기본시설공사 단가는 기본공종만 포함하므로, "
                       "추가공종(하천·저류지·전기·통신·구조물 등)과 부담금을 입력하지 않으면 조성비가 과소 산정됩니다.", icon=":material/report:")
    cat = ev.costs.by_category.copy()
    for col in cat.columns[1:]:
        cat[col] = (cat[col] / 1e8).round(1)
    cat.columns = ["구분", "조성원가 관점(억원)", "예타 재무 분석 관점(억원)"]
    st.dataframe(cat, hide_index=True, use_container_width=True, height=35 * (len(cat) + 1) + 3,
                 column_config={c: num(format="localized") for c in cat.columns[1:]})
    st.caption("조성원가 관점에는 자본비용이 들어가고 예비비가 없습니다. 예타 재무 분석 관점은 지침에 따라 금융비용을 빼고 예비비를 더합니다.")
ui.persist()
