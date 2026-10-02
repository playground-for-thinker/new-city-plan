"""도움말 — 사용 설명서와 산정 근거. 화면에서 읽거나 PDF로 내려받는다."""
import streamlit as st

import ui
from newtown import docs

ui.step_header("도움말", "프로그램 사용 방법과, 각 단계의 숫자가 어떻게 계산되는지를 설명합니다. 화면에서 읽거나 PDF로 내려받을 수 있습니다.")

TABS = {"user_manual": ("사용 설명서", "화면을 어떤 순서로, 무엇을 눌러 쓰는지"),
        "supplement": ("산정 방법과 근거", "단계별 산식, 근거 자료, 현재 판의 기준값과 한계")}

for tab, (name, (title, about)) in zip(st.tabs([t for t, _ in TABS.values()]), TABS.items()):
    with tab:
        text = docs.render(name)
        head = st.columns([3, 1])
        head[0].caption(about)
        pdf = docs.pdf_path(name)
        if pdf.exists():
            head[1].download_button(f"{name}.pdf 내려받기", pdf.read_bytes(), f"{name}.pdf", "application/pdf",
                                    type="primary", icon=":material/download:", use_container_width=True, key=f"dl_{name}")
        else:
            head[1].caption("PDF 파일이 아직 만들어지지 않았습니다.")
        # 목차: 큰 제목(##)만 모아 보여 준다
        sections = [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]
        with st.expander("목차", expanded=False):
            st.markdown("\n".join(f"- {s}" for s in sections))
        for kind, body, caption in docs.split_images(text):
            if kind == "md":
                st.markdown(body)
            else:
                path = docs.DOCS_DIR / body
                if path.exists():
                    st.image(str(path), caption=caption, use_container_width=True)
