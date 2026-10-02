"""차트 (Altair). 색은 검증된 범주형 팔레트의 앞 3칸만 쓰고, 값은 표로도 함께 제공한다."""
from __future__ import annotations

import altair as alt
import pandas as pd

from ui import MUTED, SERIES

_AXIS = dict(labelColor="#52514e", titleColor="#52514e", gridColor="#ebeae6", domainColor="#c9c8c2", tickColor="#c9c8c2")


def _style(chart: alt.Chart) -> alt.Chart:
    return chart.configure_axis(**_AXIS).configure_view(stroke=None).configure_legend(
        labelColor="#52514e", titleColor="#52514e", orient="top", title=None)


def hbar(df: pd.DataFrame, cat: str, val: str, val_title: str, fmt: str = ",.0f") -> alt.Chart:
    """크기 비교용 가로 막대 (단일 색)."""
    base = alt.Chart(df).encode(y=alt.Y(f"{cat}:N", sort="-x", title=None), x=alt.X(f"{val}:Q", title=val_title))
    bars = base.mark_bar(color=SERIES[0], cornerRadiusEnd=4, height={"band": 0.6}).encode(
        tooltip=[alt.Tooltip(f"{cat}:N"), alt.Tooltip(f"{val}:Q", format=fmt, title=val_title)])
    labels = base.mark_text(align="left", dx=4, color="#52514e").encode(text=alt.Text(f"{val}:Q", format=fmt))
    return _style((bars + labels).properties(height=max(120, 30 * len(df))))


def landuse_compare(cmp: pd.DataFrame) -> alt.Chart:
    """계획안(강조)과 사례 평균(회색), 사례 최소~최대 범위."""
    long = cmp.melt(id_vars="대분류", value_vars=["계획안(%)", "사례 평균(%)"], var_name="구분", value_name="비율").dropna()
    order = cmp["대분류"].tolist()
    bars = alt.Chart(long).mark_bar(cornerRadiusEnd=4, height={"band": 0.75}).encode(
        y=alt.Y("대분류:N", sort=order, title=None),
        yOffset=alt.YOffset("구분:N", sort=["계획안(%)", "사례 평균(%)"]),
        x=alt.X("비율:Q", title="총면적 대비 비율(%)"),
        color=alt.Color("구분:N", scale=alt.Scale(domain=["계획안(%)", "사례 평균(%)"], range=[SERIES[0], MUTED])),
        tooltip=["대분류:N", "구분:N", alt.Tooltip("비율:Q", format=".1f")])
    layers = [bars]
    rng = cmp.dropna(subset=["사례 최소(%)", "사례 최대(%)"])
    if len(rng):
        layers.append(alt.Chart(rng.assign(구분="사례 평균(%)")).mark_rule(color="#0b0b0b", strokeWidth=1.5).encode(
            y=alt.Y("대분류:N", sort=order), yOffset=alt.YOffset("구분:N", sort=["계획안(%)", "사례 평균(%)"]),
            x="사례 최소(%):Q", x2="사례 최대(%):Q",
            tooltip=["대분류:N", alt.Tooltip("사례 최소(%):Q", format=".1f"), alt.Tooltip("사례 최대(%):Q", format=".1f")]))
    return _style(alt.layer(*layers).properties(height=52 * len(cmp)))


def cashflow_bars(cf: pd.DataFrame) -> alt.Chart:
    """연차별 현금유입·유출 (억원)."""
    d = pd.DataFrame({"연도": cf["연도"].astype(str), "현금유입": cf["현금유입"] / 1e8, "현금유출": cf["현금유출"] / 1e8})
    long = d.melt(id_vars="연도", var_name="구분", value_name="금액")
    chart = alt.Chart(long).mark_bar(cornerRadiusEnd=4).encode(
        x=alt.X("연도:N", title=None, axis=alt.Axis(labelAngle=0)),
        xOffset=alt.XOffset("구분:N", sort=["현금유출", "현금유입"]),
        y=alt.Y("금액:Q", title="억원"),
        color=alt.Color("구분:N", scale=alt.Scale(domain=["현금유출", "현금유입"], range=[SERIES[1], SERIES[0]])),
        tooltip=["연도:N", "구분:N", alt.Tooltip("금액:Q", format=",.0f", title="금액(억원)")])
    return _style(chart.properties(height=280))


def cumulative_line(cf: pd.DataFrame) -> alt.Chart:
    """누적 순현금흐름 (억원)."""
    d = pd.DataFrame({"연도": cf["연도"].astype(str), "누적": cf["누적 순현금흐름"] / 1e8})
    line = alt.Chart(d).mark_line(color=SERIES[0], strokeWidth=2, point=alt.OverlayMarkDef(size=60, color=SERIES[0])).encode(
        x=alt.X("연도:N", title=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("누적:Q", title="억원"),
        tooltip=["연도:N", alt.Tooltip("누적:Q", format=",.0f", title="누적 순현금흐름(억원)")])
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#0b0b0b", strokeWidth=1).encode(y="y:Q")
    return _style((zero + line).properties(height=220))


def sensitivity_lines(sens: pd.DataFrame) -> alt.Chart:
    """변수별 증감률에 따른 PI. 기준선 PI = 1."""
    long = sens.melt(id_vars="변수", var_name="증감", value_name="PI")
    long["증감률(%)"] = long["증감"].str.rstrip("%").astype(float)
    domain = sens["변수"].tolist()
    color = alt.Color("변수:N", scale=alt.Scale(domain=domain, range=SERIES[: len(domain)]))
    x = alt.X("증감률(%):Q", title="변수 증감률(%)", axis=alt.Axis(values=sorted(long["증감률(%)"].unique())))
    y = alt.Y("PI:Q", title="PI", scale=alt.Scale(zero=False))
    line = alt.Chart(long).mark_line(strokeWidth=2).encode(x=x, y=y, color=color)
    # 선이 겹칠 수 있어 색과 함께 점 모양으로도 변수를 구분한다
    points = alt.Chart(long).mark_point(size=80, filled=True, opacity=1).encode(
        x=x, y=y, color=color, shape=alt.Shape("변수:N", scale=alt.Scale(domain=domain, range=["circle", "square", "triangle-up"])),
        tooltip=["변수:N", alt.Tooltip("증감률(%):Q", format="+.0f"), alt.Tooltip("PI:Q", format=".3f")])
    one = alt.Chart(pd.DataFrame({"y": [1.0]})).mark_rule(color="#0b0b0b", strokeDash=[4, 3]).encode(y="y:Q")
    label = alt.Chart(pd.DataFrame({"x": [long["증감률(%)"].min()], "y": [1.0], "t": ["PI = 1 (타당성 기준)"]})).mark_text(
        align="left", dy=-8, color="#52514e").encode(x="x:Q", y="y:Q", text="t:N")
    return _style((one + label + line + points).properties(height=300))
