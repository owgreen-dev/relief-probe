"""relief-probe dashboard — loan-fraud leads + data analysis + similar cases + vision.

Four tabs:
  * Loan leads — the ranked composite leads + the forward PU benchmark headline.
  * Data analysis ($150k+) — descriptive analytics over the public $150k+ disclosure
    slice (the labelable evaluation universe): size/jobs/$-per-job distributions, top
    states / industries / lenders, and detector-flag coverage.
  * Similar cases — enter a loan; find its look-alikes by business name (semantic +
    keyword) and dollar/area proximity, with each neighbor's fraud flag + an optional
    grounded LLM explanation. A retrieval/investigation tool, not a prediction.
  * Document authenticity — upload a supporting document; the ELA detector returns a
    forgery probability + the ELA heatmap. PPP fraud is largely fabricated supporting
    docs, so this pairs with the tabular core.

Run (the Similar-cases tab also wants `--extra embeddings-lite`):
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
    st.dataframe(rk[cols], width="stretch", hide_index=True)
    st.caption(
        "Top leads are typically large loans claiming very few jobs — the textbook "
        "PPP pattern. Composite = max(detector z) + 0.5·(corroborating detectors)."
    )


# The labelable $150k+ disclosure slice — the evaluation universe for the benchmark.
SLICE = "current_approval_amount >= 150000"


@st.cache_data(show_spinner=False)
def _slice_summary(_con):
    return _con.execute(
        f"""
        SELECT COUNT(*) AS n,
               SUM(current_approval_amount) AS total,
               MEDIAN(current_approval_amount) AS med,
               COUNT(*) FILTER (WHERE forgiveness_amount > 0) AS forgiven,
               COUNT(*) FILTER (WHERE processing_method = 'PPS') AS second_draw
        FROM loans WHERE {SLICE}
        """
    ).fetchone()


@st.cache_data(show_spinner=False)
def _amount_dist(_con):
    return _con.execute(
        f"""
        SELECT CASE
                 WHEN current_approval_amount < 350000 THEN '1 · $150k–350k'
                 WHEN current_approval_amount < 1000000 THEN '2 · $350k–1M'
                 WHEN current_approval_amount < 2000000 THEN '3 · $1M–2M'
                 ELSE '4 · $2M+' END AS bucket,
               COUNT(*) AS loans
        FROM loans WHERE {SLICE} GROUP BY 1 ORDER BY 1
        """
    ).df().set_index("bucket")


@st.cache_data(show_spinner=False)
def _jobs_dist(_con):
    return _con.execute(
        f"""
        SELECT CASE
                 WHEN jobs_reported IS NULL OR jobs_reported < 1 THEN '0 · <1 / null'
                 WHEN jobs_reported = 1 THEN '1 · exactly 1'
                 WHEN jobs_reported <= 5 THEN '2 · 2–5'
                 WHEN jobs_reported <= 25 THEN '3 · 6–25'
                 WHEN jobs_reported <= 100 THEN '4 · 26–100'
                 ELSE '5 · 100+' END AS bucket,
               COUNT(*) AS loans
        FROM loans WHERE {SLICE} GROUP BY 1 ORDER BY 1
        """
    ).df().set_index("bucket")


@st.cache_data(show_spinner=False)
def _top_dim(_con, dim: str, by_dollars: bool):
    col = {"State": "borrower_state", "NAICS": "naics_code",
           "Lender": "originating_lender"}[dim]
    agg = "SUM(current_approval_amount)" if by_dollars else "COUNT(*)"
    return _con.execute(
        f"""
        SELECT {col} AS k, {agg} AS v
        FROM loans WHERE {SLICE} AND {col} IS NOT NULL AND {col} <> ''
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15
        """
    ).df().set_index("k")


@st.cache_data(show_spinner=False)
def _detector_coverage(_con):
    return _con.execute(
        f"""
        SELECT s.detector_id AS detector,
               COUNT(DISTINCT s.loan_number) AS flagged_loans
        FROM signals s JOIN loans l USING (loan_number)
        WHERE l.{SLICE} GROUP BY 1 ORDER BY 2 DESC
        """
    ).df()


def data_tab() -> None:
    st.header("Data analysis — the $150k+ slice")
    con = get_connection()
    n, total, med, forgiven, second = _slice_summary(con)
    if not n:
        st.info("No loans loaded. Run `relief-probe ingest`.")
        return
    st.caption(
        "The public **$150k+ disclosure slice** — the labelable universe where the "
        "prosecuted-fraud labels live and the benchmark is measured."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Loans", f"{n:,}")
    c2.metric("Total approved", f"${total / 1e9:,.1f}B")
    c3.metric("Median loan", f"${med:,.0f}")
    c4.metric("Forgiven", f"{forgiven / n:.0%}")
    st.caption(f"{second / n:.0%} second-draw (PPS); the rest first-draw (PPP).")

    st.subheader("Distributions")
    d1, d2 = st.columns(2)
    with d1:
        st.caption("Loan amount")
        st.bar_chart(_amount_dist(con), y="loans")
    with d2:
        st.caption("Jobs reported (low headcount is the classic $/job risk)")
        st.bar_chart(_jobs_dist(con), y="loans")

    st.subheader("Top breakdowns")
    b1, b2 = st.columns([1, 1])
    dim = b1.selectbox("Group by", ["State", "NAICS", "Lender"])
    by_dollars = b2.toggle("By total $ (else loan count)", value=False)
    st.bar_chart(_top_dim(con, dim, by_dollars), y="v")

    st.subheader("Detector-flag coverage on this slice")
    cov = _detector_coverage(con)
    if cov.empty:
        st.warning("No signals yet — run `relief-probe score`.")
    else:
        st.dataframe(cov, width="stretch", hide_index=True)
        st.caption(
            "How many $150k+ loans each detector flags. Only the production detectors "
            "feed the composite; exploratory detectors appear only if scored in."
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
        st.image(img, caption="Uploaded", width="stretch")
    with col2:
        st.image(
            ela_image(img),
            caption="ELA (bright = high error)",
            width="stretch",
        )
    st.metric("P(forged)", f"{p:.1%}", delta_color="off")
    st.progress(min(max(p, 0.0), 1.0))
    st.caption("Bright, spatially-uneven ELA regions suggest a spliced/edited area.")
    st.warning(SYNTHETIC_NOTE)


@st.cache_resource
def get_embedders():
    """Load the (semantic, lexical) embedders once. Falls back to lexical-only when
    the embeddings-lite extra is absent (returns (None, HashingEmbedder()))."""
    from relief_probe.embeddings import HashingEmbedder, Model2VecEmbedder

    lexical = HashingEmbedder()
    sem = Model2VecEmbedder()
    try:
        sem.embed(["warmup"])  # trigger the lazy model load now
    except RuntimeError:
        return None, lexical
    return sem, lexical


def similar_tab() -> None:
    import pandas as pd

    from relief_probe.similarity.core import find_similar
    from relief_probe.similarity.explain import deterministic_summary

    st.header("Similar cases")
    st.caption(
        "Find loans that resemble a given one by business name (semantic + keyword) "
        "and dollar/area proximity — to surface rings/templates. A resemblance is a "
        "lead for review, not proof."
    )
    con = get_connection()
    sem, lex = get_embedders()
    if sem is None:
        st.info(
            "Semantic model not installed (`uv sync --extra embeddings-lite`); "
            "using the offline lexical embedder only."
        )

    c1, c2, c3 = st.columns(3)
    loan_number = c1.text_input("Loan number", "")
    k = c2.slider("Neighbors (k)", 5, 50, 20, step=5)
    same_state = c3.toggle("Same state only", value=True)
    c4, c5 = st.columns(2)
    min_amount = c4.number_input("Min amount ($)", value=150_000, step=50_000)
    amount_tol = c5.slider("Dollar band (±)", 0.05, 0.50, 0.25, step=0.05)

    if not loan_number.strip():
        st.info("Enter a loan number (e.g. a top lead from the Loan leads tab).")
        return

    res = find_similar(
        con, loan_number.strip(), k=k, min_amount=float(min_amount),
        amount_tol=amount_tol, same_state=same_state,
        embedder=sem or lex, lexical=lex,
    )
    if not res["available"]:
        st.warning(f"No similar cases ({res['reason']}).")
        return

    s = res["summary"]
    m1, m2, m3 = st.columns(3)
    m1.metric("Pool size", f"{s['pool_size']:,}")
    m2.metric("Prosecuted look-alikes", s["n_fraud_neighbors"])
    m3.metric("Same industry", s["n_same_naics"])
    st.write(deterministic_summary(res))

    df = pd.DataFrame(
        [
            {
                "rank": n["rank"], "loan_number": n["loan_number"],
                "borrower_name": n["borrower_name"], "naics": n["naics_code"],
                "state": n["borrower_state"], "amount": n["current_approval_amount"],
                "d$%": n["amount_delta_pct"], "semantic": n["semantic_sim"],
                "lexical": n["lexical_sim"], "fraud": n["is_fraud"],
            }
            for n in res["neighbors"]
        ]
    )
    st.dataframe(df, width="stretch", hide_index=True)

    if st.button("Explain this cluster (LLM)"):
        from relief_probe.config import llm_model
        from relief_probe.similarity.explain import explain_cluster

        try:
            st.write(explain_cluster(res, model=llm_model()))
        except RuntimeError as exc:
            st.warning(str(exc))
    st.caption(res["disclaimer"])


def main() -> None:
    st.set_page_config(page_title="relief-probe", layout="wide")
    st.title("relief-probe — PPP/SBA fraud leads")
    st.warning(DISCLAIMER)
    leads, data, similar, vision = st.tabs(
        ["Loan leads", "Data analysis ($150k+)", "Similar cases",
         "Document authenticity"]
    )
    with leads:
        leads_tab()
    with data:
        data_tab()
    with similar:
        similar_tab()
    with vision:
        vision_tab()


if __name__ == "__main__":
    main()
