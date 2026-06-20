"""relief-probe dashboard — loan-fraud leads + data analysis + similar cases + vision.

Five tabs:
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
  * Prosecution pattern — a borrower/attorney/researcher looks up a loan and sees where
    it sits relative to the statistical pattern of the publicly-charged cases. NEVER a
    risk score or accusation — a statistical comparison on public data. The most
    legally-sensitive tab; see RESPONSIBLE_USE.md + docs/PROSECUTION_PATTERN.md.

Run (the Similar-cases tab also wants `--extra embeddings-lite`):
    uv run --extra viz --extra vision streamlit run app/dashboard.py

Read-only and demo-oriented. A high score is a lead for review, not evidence of
fraud — see RESPONSIBLE_USE.md.
"""

from __future__ import annotations

import json
import math
import os

import streamlit as st

from relief_probe.config import data_dir
from relief_probe.warehouse import connect

# Hosted-demo mode: build a small, fully-synthetic warehouse on first launch
# (the real warehouse is gitignored and never deployed — SIGN-007).
DEMO_MODE = os.environ.get("RELIEF_PROBE_DEMO") == "1"

DISCLAIMER = (
    "Statistical leads for review, not evidence of fraud. Loan data is public (SBA "
    "FOIA); labels are a small, prosecution-biased PU sample. See RESPONSIBLE_USE.md."
)
MODEL_PATH = data_dir() / "models" / "doc_authenticity.joblib"


@st.cache_resource
def get_connection():
    if DEMO_MODE:
        from relief_probe.demo import ensure_demo_warehouse

        ensure_demo_warehouse()
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


# --- Prosecution pattern comparison -------------------------------------------------
# The most legally-sensitive tab: a borrower / attorney / researcher looks up a loan and
# sees where it sits relative to the PUBLIC prosecution record. Never a risk score, never
# an accusation — a statistical comparison on public data. Every label is a *charge*
# (~84-88% resolved), not a conviction or a "fraud" finding. See RESPONSIBLE_USE.md +
# docs/PROSECUTION_PATTERN.md. Style rules live in that spec (never "suspicious"/"risk").

PATTERN_DISCLAIMER = (
    "⚠️ This tab compares a loan against publicly charged PPP cases. It is NOT legal "
    "advice, NOT a risk determination, and NOT an accusation. Statistical similarity to "
    "prosecuted loans does not mean legal jeopardy. See RESPONSIBLE_USE.md and "
    "docs/PROSECUTION_PATTERN.md before drawing any conclusions. Consult a licensed "
    "attorney for legal questions."
)
_DETECTOR_LABELS = {
    "naics_cohort_outlier": "Industry peer outlier ($/job vs NAICS+state cohort)",
    "payroll_cap_exceedance": "Program rule: implied $/job exceeds PPP payroll cap",
    "multiple_funded_loans": "Entity resolution: multiple funded loans for same borrower",
}
_DETECTOR_EXPLAIN = {
    "naics_cohort_outlier": (
        "The loan's dollars-per-reported-job is high relative to other loans in the same "
        "industry and state. A statistical anomaly, not an accusation."
    ),
    "payroll_cap_exceedance": (
        "The implied per-employee amount is above the PPP program's legal ceiling — a "
        "program-rule check, independent of peer comparison."
    ),
    "multiple_funded_loans": (
        "The same resolved borrower (name + building address) holds more funded loans "
        "than the one-per-draw rule allows. Entity resolution can merge distinct parties."
    ),
}

# fraud_cases joined by ON (not USING) so COUNT(fc.loan_number) counts only matches.
_PROS = (
    "LEFT JOIN (SELECT DISTINCT loan_number FROM fraud_cases WHERE loan_number IS NOT "
    "NULL) fc ON fc.loan_number = l.loan_number"
)


@st.cache_data(show_spinner=False)
def _pat_population(_con):
    n_total = _con.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
    n_slice = _con.execute(f"SELECT COUNT(*) FROM loans WHERE {SLICE}").fetchone()[0]
    n_labels = _con.execute(
        f"SELECT COUNT(DISTINCT fc.loan_number) FROM fraud_cases fc "
        f"JOIN loans l ON l.loan_number = fc.loan_number WHERE l.{SLICE}"
    ).fetchone()[0]
    return n_total, n_slice, n_labels


@st.cache_data(show_spinner=False)
def _pat_loan(_con, loan_number: str):
    return _con.execute(
        "SELECT borrower_name, naics_code, borrower_state, current_approval_amount, "
        "jobs_reported, originating_lender, loan_status, date_approved "
        "FROM loans WHERE loan_number = ?",
        [loan_number],
    ).fetchone()


@st.cache_data(show_spinner=False)
def _pat_in_labels(_con, loan_number: str) -> bool:
    return _con.execute(
        "SELECT COUNT(*) FROM fraud_cases WHERE loan_number = ?", [loan_number]
    ).fetchone()[0] > 0


@st.cache_data(show_spinner=False)
def _pat_signals(_con, loan_number: str):
    return _con.execute(
        "SELECT detector_id, score, evidence_json FROM signals "
        "WHERE loan_number = ? ORDER BY score DESC",
        [loan_number],
    ).fetchall()


@st.cache_data(show_spinner=False)
def _pat_pop_median_dpj(_con):
    return _con.execute(
        f"SELECT MEDIAN(current_approval_amount / NULLIF(jobs_reported, 0)) "
        f"FROM loans WHERE {SLICE} AND jobs_reported >= 1 "
        f"AND current_approval_amount > 0"
    ).fetchone()[0]


@st.cache_data(show_spinner=False)
def _pat_prosecuted_medians(_con):
    return _con.execute(
        f"SELECT MEDIAN(l.current_approval_amount), "
        f"MEDIAN(l.current_approval_amount / NULLIF(l.jobs_reported, 0)) "
        f"FROM loans l {_PROS.replace('LEFT JOIN', 'JOIN')} "
        f"WHERE l.{SLICE} AND l.jobs_reported >= 1"
    ).fetchone()


@st.cache_data(show_spinner=False)
def _pat_dpj_percentile(_con, dpj: float) -> float:
    return _con.execute(
        f"SELECT 100.0 * COUNT(*) FILTER (WHERE current_approval_amount / "
        f"NULLIF(jobs_reported, 0) < ?) / NULLIF(COUNT(*), 0) "
        f"FROM loans WHERE {SLICE} AND jobs_reported >= 1 "
        f"AND current_approval_amount > 0",
        [dpj],
    ).fetchone()[0]


@st.cache_data(show_spinner=False)
def _pat_dpj_hist(_con):
    return _con.execute(
        f"SELECT ROUND(LOG10(current_approval_amount / jobs_reported), 1) AS log_dpj, "
        f"COUNT(*) AS loans FROM loans "
        f"WHERE {SLICE} AND jobs_reported >= 1 AND current_approval_amount > 0 "
        f"GROUP BY 1 ORDER BY 1"
    ).df()


@st.cache_data(show_spinner=False)
def _pat_sectors(_con):
    return _con.execute(
        f"SELECT LEFT(l.naics_code, 2) AS sector, COUNT(*) AS total_loans, "
        f"COUNT(fc.loan_number) AS prosecuted_loans, "
        f"ROUND(100.0 * COUNT(fc.loan_number) / COUNT(*), 4) AS rate_pct "
        f"FROM loans l {_PROS} "
        f"WHERE l.{SLICE} AND l.naics_code IS NOT NULL AND l.naics_code <> '' "
        f"GROUP BY 1 ORDER BY total_loans DESC LIMIT 10"
    ).df()


@st.cache_data(show_spinner=False)
def _pat_top_prosecuted_sector(_con):
    row = _con.execute(
        f"SELECT LEFT(l.naics_code, 2) AS sector, COUNT(*) AS n FROM loans l "
        f"{_PROS.replace('LEFT JOIN', 'JOIN')} "
        f"WHERE l.{SLICE} AND l.naics_code IS NOT NULL AND l.naics_code <> '' "
        f"GROUP BY 1 ORDER BY n DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


@st.cache_data(show_spinner=False)
def _pat_cell(_con, naics: str, state: str):
    return _con.execute(
        f"SELECT COUNT(*) AS total, COUNT(fc.loan_number) AS prosecuted, "
        f"MEDIAN(l.current_approval_amount / NULLIF(l.jobs_reported, 0)) AS dpj "
        f"FROM loans l {_PROS} "
        f"WHERE l.naics_code = ? AND l.borrower_state = ? AND l.{SLICE}",
        [naics, state],
    ).fetchone()


def prosecution_pattern_tab() -> None:
    import pandas as pd

    st.header("Prosecution pattern comparison")
    con = get_connection()
    n_total, n_slice, n_labels = _pat_population(con)

    # Mode A — no warehouse: explain what's needed; do not render empty charts.
    if n_total == 0:
        st.error(PATTERN_DISCLAIMER)
        st.info(
            "**No warehouse loaded.** This tab compares a loan against the public "
            "DOJ-prosecution pattern. To use it, build the warehouse and labels:\n\n"
            "```\nrelief-probe ingest --slice 150k_plus\nrelief-probe score\n"
            "relief-probe fetch-labels && relief-probe resolve-labels\n```\n\n"
            "See docs/PROSECUTION_PATTERN.md for what the comparison does and — "
            "importantly — does not mean."
        )
        return

    # Mode B — warehouse loaded.
    st.error(PATTERN_DISCLAIMER)
    loan_number = st.text_input(
        "Enter a PPP loan number",
        placeholder="e.g. 1234567890",
        help="The SBA loan number from your PPP loan documentation.",
    ).strip()

    base_rate = (n_labels / n_slice) if n_slice else 0.0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Loans in warehouse", f"{n_total:,}")
    c2.metric("$150k+ slice", f"{n_slice:,}")
    c3.metric("Prosecution labels", f"{n_labels:,}")
    c4.metric("Base prosecution rate", f"{base_rate:.3%}")
    st.caption(
        "Prosecution labels are a tiny, prosecution-biased sample — see "
        "docs/PROSECUTION_PATTERN.md."
    )

    if not loan_number:
        st.info("Enter a loan number to compare it against the prosecution pattern.")
        return

    row = _pat_loan(con, loan_number)
    if row is None:
        st.warning(
            "Loan number not found in warehouse. Only loans in the $150k+ disclosure "
            "slice are loaded by default."
        )
        return
    name, naics, state, amount, jobs, lender, status, approved = row
    dpj = (amount / jobs) if (amount and jobs and jobs >= 1) else None
    in_labels = _pat_in_labels(con, loan_number)
    signals = _pat_signals(con, loan_number)
    pop_med = _pat_pop_median_dpj(con)
    pros_amt_med, pros_dpj_med = _pat_prosecuted_medians(con)

    # --- Loan profile card ---
    st.subheader("Loan profile")
    left, right = st.columns(2)
    with left:
        amount_s = f"${amount:,.0f}" if amount is not None else "—"
        jobs_s = f"{jobs:,.0f}" if jobs is not None else "—"
        dpj_s = f"${dpj:,.0f}" if dpj is not None else "—"
        # Escape $ so Streamlit markdown doesn't read paired $...$ as LaTeX math.
        profile = (
            f"**Borrower** {name or '—'}  \n"
            f"**NAICS** {naics or '—'} · **State** {state or '—'}  \n"
            f"**Loan amount** {amount_s} · **Jobs reported** {jobs_s}  \n"
            f"**Implied $/job** {dpj_s}  \n"
            f"**Lender** {lender or '—'}  \n"
            f"**Status** {status or '—'} · **Approved** {approved or '—'}"
        )
        st.markdown(profile.replace("$", "\\$"))
    with right:
        if in_labels:
            st.error(
                "⚠️ This loan number appears in the entity-resolved prosecution labels. "
                "This reflects a public DOJ charge — verify at justice.gov/opa. Entity "
                "resolution is ~84-88% precise; matches may be incorrect."
            )
        else:
            st.success(
                "✓ Not in the prosecution labels. This does not mean the loan was "
                "legitimate — only that it did not resolve to a publicly announced "
                "charge in this dataset."
            )
        if signals:
            st.info(f"**{len(signals)} detector signal(s) fired** — see Signal detail.")
        else:
            st.info("No production detector signals on this loan.")
        if dpj is not None:
            pct = _pat_dpj_percentile(con, dpj)
            st.caption(
                (
                    f"Your $/job: **${dpj:,.0f}** · population median: ${pop_med:,.0f} · "
                    f"prosecuted median: ${pros_dpj_med:,.0f} "
                    f"(≈ {pct:.0f}th percentile of the slice)"
                ).replace("$", "\\$")
            )

    # --- Signal detail ---
    with st.expander("Signal detail — what the detectors found", expanded=True):
        if not signals:
            st.write(
                "No production detector signals fired on this loan. See "
                "docs/PROSECUTION_PATTERN.md for what this means and does not mean — a "
                "loan with no anomaly is not thereby 'clean' or legally protected."
            )
        for det_id, score, ev_json in signals:
            st.markdown(f"**{_DETECTOR_LABELS.get(det_id, det_id)}** — score {score:.2f}")
            st.caption(_DETECTOR_EXPLAIN.get(det_id, ""))
            try:
                ev = json.loads(ev_json) if ev_json else {}
            except (TypeError, ValueError):
                ev = {}
            if ev:
                st.dataframe(
                    pd.DataFrame(
                        [(k, str(v)) for k, v in ev.items()], columns=["field", "value"]
                    ),
                    width="stretch",
                    hide_index=True,
                )

    # --- Where this loan sits (two charts) ---
    st.subheader("Where this loan sits")
    g1, g2 = st.columns(2)
    with g1:
        import altair as alt

        hist = _pat_dpj_hist(con)
        layers = [
            alt.Chart(hist).mark_bar(color="#9ecae1").encode(
                x=alt.X("log_dpj:Q", title="log10($ per reported job)"),
                y=alt.Y("loans:Q", title="loans"),
            )
        ]
        if dpj and dpj > 0:
            layers.append(
                alt.Chart(pd.DataFrame({"v": [math.log10(dpj)]}))
                .mark_rule(color="red", strokeDash=[6, 4])
                .encode(x="v:Q")
            )
        if pros_dpj_med and pros_dpj_med > 0:
            layers.append(
                alt.Chart(pd.DataFrame({"v": [math.log10(pros_dpj_med)]}))
                .mark_rule(color="orange", strokeDash=[6, 4])
                .encode(x="v:Q")
            )
        st.altair_chart(alt.layer(*layers), use_container_width=True)
        st.caption(
            "Dollars per reported job across the $150k+ slice. Red = this loan; orange = "
            "prosecuted median. The high tail holds prosecuted loans — and many "
            "legitimate high-wage businesses."
        )
    with g2:
        import altair as alt

        sectors = _pat_sectors(con).copy()
        this_sector = (naics or "")[:2]
        sectors["is_this"] = sectors["sector"] == this_sector
        st.altair_chart(
            alt.Chart(sectors).mark_bar().encode(
                y=alt.Y("sector:N", sort="-x", title="NAICS sector (2-digit)"),
                x=alt.X("rate_pct:Q", title="prosecution rate %"),
                color=alt.condition(
                    alt.datum.is_this, alt.value("orange"), alt.value("#9ecae1")
                ),
                tooltip=["sector", "total_loans", "prosecuted_loans", "rate_pct"],
            ),
            use_container_width=True,
        )
        st.caption(
            "Prosecution rate by sector (top 10 by loan count; this loan's sector "
            "highlighted). The rate reflects DOJ enforcement patterns and investigative "
            "capacity — not the true fraud rate in each sector."
        )

    # --- Industry + state context ---
    st.subheader("Industry + state context")
    if naics and state:
        total, prosecuted, cell_dpj = _pat_cell(con, naics, state)
        rate = (prosecuted / total) if total else 0.0
        st.dataframe(
            pd.DataFrame(
                [
                    ("Loans in same NAICS + state", f"{total:,}"),
                    ("Prosecuted in same NAICS + state", f"{prosecuted:,}"),
                    ("Prosecution rate in that cell", f"{rate:.3%}"),
                    ("This loan's $/job", dpj_s),
                    (
                        "Median $/job in that cell",
                        f"${cell_dpj:,.0f}" if cell_dpj else "—",
                    ),
                ],
                columns=["metric", "value"],
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption("Industry + geography context. Few-loan cells have unstable rates.")
    else:
        st.caption("No NAICS/state on this loan — industry context unavailable.")

    # --- Prosecution pattern summary (aggregate only — no individual names) ---
    st.subheader("Prosecution pattern summary (aggregate)")
    top_sector = _pat_top_prosecuted_sector(con)
    s1, s2, s3 = st.columns(3)
    amt_med_s = f"${pros_amt_med:,.0f}" if pros_amt_med else "—"
    dpj_med_s = f"${pros_dpj_med:,.0f}" if pros_dpj_med else "—"
    s1.metric("Prosecuted median amount", amt_med_s)
    s2.metric("Prosecuted median $/job", dpj_med_s)
    s3.metric("Top prosecuted sector (NAICS)", top_sector or "—")
    t1, t2, t3 = st.columns(3)
    if amount and pros_amt_med:
        t1.metric(
            "This loan's amount", f"${amount:,.0f}",
            delta=f"{(amount - pros_amt_med) / pros_amt_med:+.0%} vs median",
            delta_color="off",
        )
    if dpj and pros_dpj_med:
        t2.metric(
            "This loan's $/job", f"${dpj:,.0f}",
            delta=f"{(dpj - pros_dpj_med) / pros_dpj_med:+.0%} vs median",
            delta_color="off",
        )
    t3.metric(
        "NAICS matches top prosecuted sector?",
        "Yes" if (top_sector and (naics or "")[:2] == top_sector) else "No",
    )
    st.caption(
        "Aggregate characteristics of the entity-resolved prosecution labels. These are "
        "already-charged public cases — see docs/LABEL_PRECISION.md."
    )

    # --- Bottom disclaimer (repeated, expanded) ---
    st.info(
        "**What this comparison means — and doesn't mean**\n\n"
        "A loan that shares signals with prosecuted cases is statistically similar to "
        "cases that were charged — it is not thereby at elevated legal risk. Prosecution "
        "decisions depend on evidence, witness availability, district capacity, and the "
        "specific facts of each case — none of which are in this dataset.\n\n"
        "A loan with no shared signals is not thereby legally protected. Many fraud "
        "schemes leave no statistical footprint in aggregate loan-level public data.\n\n"
        "For questions about your specific situation, consult a licensed attorney. "
        "See RESPONSIBLE_USE.md and docs/PROSECUTION_PATTERN.md."
    )
    st.markdown(
        "📄 [Full methodology and limitations](docs/PROSECUTION_PATTERN.md) · "
        "[Responsible use policy](RESPONSIBLE_USE.md) · "
        "[DOJ enforcement releases](https://www.justice.gov/opa)"
    )


def main() -> None:
    st.set_page_config(page_title="relief-probe", layout="wide")
    st.title("relief-probe — PPP/SBA fraud leads")
    if DEMO_MODE:
        st.info(
            "🧪 **Demo mode — fully synthetic data.** Every borrower, loan number, "
            "ring, and prosecution label below is fabricated for illustration; the "
            "real production detectors are run over it live. No real PPP loans or DOJ "
            "cases appear here. See RESPONSIBLE_USE.md."
        )
    st.warning(DISCLAIMER)
    leads, data, similar, vision, pattern = st.tabs(
        ["Loan leads", "Data analysis ($150k+)", "Similar cases",
         "Document authenticity", "Prosecution pattern"]
    )
    with leads:
        leads_tab()
    with data:
        data_tab()
    with similar:
        similar_tab()
    with vision:
        vision_tab()
    with pattern:
        prosecution_pattern_tab()


if __name__ == "__main__":
    main()
