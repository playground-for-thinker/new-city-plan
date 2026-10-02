"""⑥ 재무적 타당성 분석 — 예비타당성조사 지침 기준 PI·FNPV·FIRR."""
import pandas as pd
import streamlit as st

import charts
import ui
from newtown import pipeline
from newtown.report.excel import build_report

prj = ui.need_project()
ui.step_header("⑥ 재무적 타당성 분석", "예비타당성조사 지침에 따라 연차별 현금흐름을 기준연도 불변가격으로 추정하고 수익성지수(PI)를 산정합니다.")
num = st.column_config.NumberColumn

with st.expander("분석 기준 (공공기관 예비타당성조사 일반지침 제Ⅷ장)"):
    st.markdown(
        "- 주 지표는 **수익성지수(PI)** = 현금유입의 현재가치 ÷ 현금유출의 현재가치. PI ≥ 1이면 재무적 타당성이 있습니다. FNPV·FIRR은 참고 지표입니다.\n"
        "- 재무적 할인율은 **실질 4.5%**, 가격은 분석 기준연도의 불변가격입니다.\n"
        "- 분석기간은 사업 착수부터 분양대금 회수 완료까지입니다.\n"
        "- 금융비용(자본비용)과 감가상각비는 현금유출에 넣지 않습니다.\n"
        "- 예비비는 부가가치세를 포함한 사업비의 10%입니다.")

p = st.columns(4)
prj.finance.discount_rate = p[0].number_input("재무적 할인율(실질, %)", 0.0, 20.0, prj.finance.discount_rate * 100, 0.1) / 100
prj.finance.include_tax = p[1].toggle("법인세 반영(간이)", prj.finance.include_tax,
                                      help="사업 전체 이익 × 세율을 수입 회수 비율대로 배분하는 간이 방식입니다. 지침은 세금이 실제 현금유출인지 검토해 반영하도록 합니다.")
if prj.finance.include_tax:
    prj.finance.tax_rate = p[2].number_input("법인세율(%)", 0.0, 50.0, prj.finance.tax_rate * 100, 1.0) / 100

# ---------- 공급가격 ----------
st.subheader("용도별 공급가격")
st.caption("유상공급 용지의 공급가격 기준을 정합니다. '조성원가 기준'은 ㎡당 조성원가 × 배율, '단가 직접 입력'은 감정가격·낙찰 예상가 등 "
           "㎡당 단가를 넣습니다. 용도별 공급기준은 택지 공급가격 관련 법령·지침을 확인해 입력하세요(초기값은 조성원가 × 1.0).")
ev = ui.evaluation()
paid_rows = [r for r in prj.landuse if r.paid]
BASIS = {"cost": "조성원가 기준", "manual": "단가 직접 입력"}
rev = ev.finance.revenue.set_index("key")
pdf = pd.DataFrame([{
    "key": r.key, "용도": r.name, "면적(㎡)": rev.at[r.key, "면적(㎡)"], "공급기준": BASIS[r.price_basis],
    "조성원가 대비 배율": r.cost_mult, "직접 입력 단가(원/㎡)": r.unit_price,
    "적용 단가(원/㎡)": rev.at[r.key, "공급단가(원/㎡)"], "분양수입(억원)": round(rev.at[r.key, "분양수입(원)"] / 1e8, 1),
} for r in paid_rows])
ep = st.data_editor(
    pdf, hide_index=True, use_container_width=True, column_order=[c for c in pdf.columns if c != "key"],
    disabled=["용도", "면적(㎡)", "적용 단가(원/㎡)", "분양수입(억원)"], key=f"price_{prj.active_alt}",
    column_config={"공급기준": st.column_config.SelectboxColumn(options=list(BASIS.values()), required=True),
                   "면적(㎡)": num(format="localized"), "조성원가 대비 배율": num(format="%.2f", min_value=0.0, step=0.05),
                   "직접 입력 단가(원/㎡)": num(format="localized", min_value=0), "적용 단가(원/㎡)": num(format="localized"),
                   "분양수입(억원)": num(format="localized")})
changed = False
for r, e in zip(paid_rows, ep.to_dict("records")):
    basis = "cost" if e["공급기준"] == BASIS["cost"] else "manual"
    mult = float(e["조성원가 대비 배율"] if e["조성원가 대비 배율"] == e["조성원가 대비 배율"] and e["조성원가 대비 배율"] is not None else 0)
    price = float(e["직접 입력 단가(원/㎡)"] if e["직접 입력 단가(원/㎡)"] == e["직접 입력 단가(원/㎡)"] and e["직접 입력 단가(원/㎡)"] is not None else 0)
    if (basis, mult, price) != (r.price_basis, r.cost_mult, r.unit_price):
        r.price_basis, r.cost_mult, r.unit_price = basis, mult, price
        changed = True
if changed:
    ui.persist()
    st.rerun()
st.caption(f"㎡당 조성원가 {ev.costs.unit_cost:,.0f}원 (조성원가 관점 총사업비 {ui.eok(ev.costs.total_cost_basis, 0)} ÷ 유상공급 면적 {ev.areas['paid_area']:,.0f}㎡)")

# ---------- 결과 ----------
ev = ui.evaluation()
m = ev.finance.metrics
st.subheader("분석 결과")
if ev.total_area <= 0 or m.total_out <= 0:
    st.info("사업구역과 사업비가 입력되면 결과가 표시됩니다.")
    ui.persist()
    st.stop()
if ev.costs.missing:
    st.warning("단가·요율이 입력되지 않은 사업비 항목이 있어 아래 결과는 사업비가 과소 반영된 값입니다: " + ", ".join(ev.costs.missing),
               icon=":material/report:")
if "가상" in prj.parcel_source:
    st.warning("현재 필지는 **가상 필지(시험용)** 입니다. 결과는 실제 분석값이 아닙니다.", icon=":material/science:")
for w in ev.finance.warnings:
    st.warning(w)

k = st.columns(5)
k[0].metric("PI (수익성지수)", f"{m.pi:.2f}")
k[1].metric("FNPV", ui.eok(m.fnpv, 0))
k[2].metric("FIRR", f"{m.firr:.2%}" if m.firr is not None else "산정 불가")
k[3].metric("현금유입 합계", ui.eok(m.total_in, 0))
k[4].metric("현금유출 합계", ui.eok(m.total_out, 0))
if round(m.pi, 2) >= 1:
    st.success(f"PI {m.pi:.2f} ≥ 1 — 재무적 타당성이 있는 것으로 분석됩니다.", icon=":material/check_circle:")
else:
    st.error(f"PI {m.pi:.2f} < 1 — 재무적 타당성이 부족한 것으로 분석됩니다.", icon=":material/cancel:")
st.caption(f"현재가치 기준 유입 {ui.eok(m.pv_in, 0)} · 유출 {ui.eok(m.pv_out, 0)} · 할인율 {prj.finance.discount_rate:.1%} · "
           f"기준연도 {prj.schedule.base_year}년 · 분석기간 {ev.finance.cashflow['연도'].iloc[0]}~{ev.finance.cashflow['연도'].iloc[-1]}년")

t_cf, t_sens, t_alt = st.tabs(["현금흐름", "민감도·시나리오", "대안 비교"])
with t_cf:
    cf = ev.finance.cashflow
    l, r = st.columns(2)
    l.markdown("**연차별 현금유입·유출**")
    l.altair_chart(charts.cashflow_bars(cf), use_container_width=True)
    r.markdown("**누적 순현금흐름**")
    r.altair_chart(charts.cumulative_line(cf), use_container_width=True)
    show = cf.copy()
    money = [c for c in show.columns if c not in ("연도", "할인계수")]
    show[money] = (show[money] / 1e8).round(1)
    total = {"연도": "합계", **{c: show[c].sum() for c in money if c != "누적 순현금흐름"}}
    show["연도"] = show["연도"].astype(str)
    show = pd.concat([show, pd.DataFrame([total])], ignore_index=True)
    st.markdown("**현금흐름표 (억원, 기준연도 불변가격)**")
    st.dataframe(show, hide_index=True, use_container_width=True,
                 column_config={**{c: num(format="localized") for c in money}, "할인계수": num(format="%.4f")})

with t_sens:
    sig = (prj.model_dump_json(), round(ev.total_area), round(ev.comp.land_comp), round(ev.comp.obstacle))
    if st.session_state.get("_sens", (None,))[0] != sig:
        st.session_state["_sens"] = (sig, pipeline.sensitivity(prj, ev), pipeline.scenario_table(prj, ev), pipeline.breakeven(prj, ev))
    _, sens, scen, brk = st.session_state["_sens"]
    st.markdown("**변수별 증감에 따른 PI**")
    st.altair_chart(charts.sensitivity_lines(sens), use_container_width=True)
    st.dataframe(sens, hide_index=True, use_container_width=True)
    st.caption("공급가격을 '조성원가 기준'으로 정한 용지는 사업비가 늘면 공급가격도 함께 오릅니다. "
               "그래서 보상비·조성비 증감이 PI에 미치는 영향은 직접 입력 단가(감정가 등) 용지의 비중이 클수록 커집니다.")
    l, r = st.columns([3, 2])
    l.markdown("**시나리오 분석**")
    l.dataframe(scen, hide_index=True, use_container_width=True,
                column_config={"FNPV(억원)": num(format="localized")})
    r.markdown("**분기점 분석**")
    r.dataframe(brk, hide_index=True, use_container_width=True)

with t_alt:
    recs = []
    for name, alt_rows in prj.alternatives.items():
        _, prog, costs, fin = pipeline.evaluate_financials(prj, ev.total_area, ev.comp.land_comp, ev.comp.obstacle, rows=alt_rows)
        mm = fin.metrics
        recs.append({"대안": name, "유상공급 비율(%)": round(sum(x.ratio for x in alt_rows if x.paid), 1),
                     "계획인구(인)": round(prog.population), "총사업비(억원)": round(costs.total_feasibility / 1e8),
                     "㎡당 조성원가(원)": round(costs.unit_cost), "분양수입(억원)": round(mm.total_in / 1e8),
                     "PI": round(mm.pi, 2), "FNPV(억원)": round(mm.fnpv / 1e8), "FIRR(%)": round(mm.firr * 100, 2) if mm.firr is not None else None})
    st.dataframe(pd.DataFrame(recs), hide_index=True, use_container_width=True,
                 column_config={c: num(format="localized") for c in ["계획인구(인)", "총사업비(억원)", "㎡당 조성원가(원)", "분양수입(억원)", "FNPV(억원)"]})
    st.caption("④ 토지이용계획에서 대안을 추가하면 같은 사업비 단가·일정으로 대안별 결과를 비교합니다.")

st.divider()
st.download_button("분석 보고서 Excel 내려받기", build_report(prj, ui.parcels(), ev),
                   f"{prj.name}_타당성분석.xlsx", type="primary", icon=":material/download:")
st.caption("요약, 입지현황, 토지세목조서, 보상비 집계, 사업비, 토지이용계획, 기본구상, 분양수입, 현금흐름, 민감도 시트가 들어 있습니다.")
ui.persist()
