"""relief-probe dashboard — loan-fraud leads + a document-authenticity (vision) tab.

Two tabs:
  * Loan leads — the ranked composite leads + the forward PU benchmark headline.
  * Document authenticity — upload a supporting document; the ELA detector returns a
    forgery probability + the ELA heatmap. PPP fraud is largely fabricated supporting
    docs, so this pairs with the tabular core.

Run:
    uv run --extra viz --extra vision streamlit run app/dashboard.py

Read-only and demo-oriented. A high score is a lead for review, not evidence of
fraud — see RESPONSIBLE_USE.md.
"""

from __future__ import annotations

import streamlit as st

from relief_probe.config import data_dir
from relief_probe.warehouse import connect

DISCLAIMER = (
    "Statistical leads for review, not evidence of fraud. Loan data is public (SBA "
    "FOIA); labels are a small, prosecution-biased PU sample. See RESPONSIBLE_USE.md."
)
MODEL_PATH = data_dir() / "models" / "doc_authenticity.joblib"


@st.cache_resource
def get_connection():
    return connect(read_only=True)


@st.cache_data(show_spinner=False)
def ranking(_con, top: int):
    from relief_probe.scoring import composite_ranking

    return composite_ranking(_con, limit=top)


def leads_tab() -> None:
    st.header("Loan-fraud leads")
    con = get_connection()
    n_loans = con.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
    n_sig = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    n_lab = con.execute(
        "SELECT COUNT(DISTINCT loan_number) FROM fraud_cases"
    ).fetchone()[0]
    if n_loans == 0:
        st.info("No loans loaded. Run `relief-probe ingest` then `relief-probe score`.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Loans", f"{n_loans:,}")
    c2.metric("Detector signals", f"{n_sig:,}")
    c3.metric("Prosecuted labels", f"{n_lab:,}")

    if n_sig == 0:
        st.warning("No signals yet — run `relief-probe score`.")
        return

    top = st.slider("Show top N leads", 10, 200, 25, step=5)
    rk = ranking(con, top)
    cols = ["loan_number", "borrower_name", "naics_code", "state",
            "amount", "jobs_reported", "composite_score", "n_signals", "detectors"]
    st.dataframe(rk[cols], use_container_width=True, hide_index=True)
    st.caption(
        "Top leads are typically large loans claiming very few jobs — the textbook "
        "PPP pattern. Composite = max(detector z) + 0.5·(corroborating detectors)."
    )


def vision_tab() -> None:
    st.header("Document authenticity (ELA)")
    st.write(
        "PPP fraud is overwhelmingly fabricated supporting documents. This screens an "
        "uploaded image for tampering via **Error Level Analysis** — recompression / "
        "splice artifacts, not a fraud determination."
    )
    try:
        from PIL import Image

        from relief_probe.vision import SYNTHETIC_NOTE
        from relief_probe.vision.ela import ela_image
        from relief_probe.vision.model import forgery_probability, load_model
    except ImportError:
        st.error("Install the vision extra: `uv sync --extra vision`.")
        return

    if not MODEL_PATH.exists():
        st.warning(
            "No trained model. Run `relief-probe vision-demo` (synthetic) or train on "
            "a folder of `authentic/` + `forged/` images."
        )
        return
    model = load_model(MODEL_PATH)

    up = st.file_uploader("Upload a document image", type=["jpg", "jpeg", "png"])
    if up is None:
        st.info("Upload an image to score it.")
        return
    img = Image.open(up)
    p = forgery_probability(model, img)

    col1, col2 = st.columns(2)
    with col1:
        st.image(img, caption="Uploaded", use_container_width=True)
    with col2:
        st.image(
            ela_image(img),
            caption="ELA (bright = high error)",
            use_container_width=True,
        )
    st.metric("P(forged)", f"{p:.1%}", delta_color="off")
    st.progress(min(max(p, 0.0), 1.0))
    st.caption("Bright, spatially-uneven ELA regions suggest a spliced/edited area.")
    st.warning(SYNTHETIC_NOTE)


def main() -> None:
    st.set_page_config(page_title="relief-probe", layout="wide")
    st.title("relief-probe — PPP/SBA fraud leads")
    st.warning(DISCLAIMER)
    leads, vision = st.tabs(["Loan leads", "Document authenticity"])
    with leads:
        leads_tab()
    with vision:
        vision_tab()


if __name__ == "__main__":
    main()
