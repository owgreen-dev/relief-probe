# Deploying the synthetic demo

The hosted dashboard runs on **fully synthetic data** — the real ~11.4M-loan
warehouse is gitignored and is never deployed (SIGN-007). In demo mode the app
builds a small fictitious warehouse on first launch (`relief_probe.demo`),
runs the **real** production detectors over it, and serves the five tabs.

## What's in the repo for this

- `src/relief_probe/demo/` — the synthetic-warehouse builder (`ensure_demo_warehouse`).
- `app/dashboard.py` — when `RELIEF_PROBE_DEMO=1`, it builds the demo warehouse
  and shows a "Demo mode — fully synthetic data" banner.
- `requirements.txt` — what Streamlit Community Cloud installs (`-e .` + pinned
  deps for the `viz` + `vision` + `embeddings-lite` extras). Regenerate after
  changing deps:
  `uv pip compile pyproject.toml --extra viz --extra vision --extra embeddings-lite -o requirements.txt`
- `.streamlit/config.toml` — theme + server settings (no secrets).

## Try it locally first

```bash
RELIEF_PROBE_DEMO=1 uv run --extra viz --extra vision --extra embeddings-lite \
  streamlit run app/dashboard.py
```

The first load builds the synthetic warehouse into your `data/` dir (instant).
It will **not** overwrite an existing non-empty warehouse, so this is safe to run
even if you have the real data ingested — but use a clean checkout or
`RELIEF_PROBE_DATA_DIR=/tmp/rp_demo` to be certain.

## Deploy to Streamlit Community Cloud

1. Push the repo to GitHub (public or private — free tier supports both).
2. Go to <https://share.streamlit.io> → **New app** → pick the repo + branch.
3. Set **Main file path** to `app/dashboard.py`.
4. Open **Advanced settings → Secrets** and add:
   ```toml
   RELIEF_PROBE_DEMO = "1"
   # Optional — enables the "Explain this cluster (LLM)" button on Similar cases:
   # ANTHROPIC_API_KEY = "sk-ant-..."
   ```
   Streamlit Cloud exposes secrets as environment variables, so this is what flips
   the app into demo mode.
5. **Deploy.** First boot installs deps and builds the synthetic warehouse; you
   get a public `*.streamlit.app` URL.
6. Paste that URL into the README's "Try the synthetic demo" line (replace the
   `<!-- TODO -->` placeholder).

## Notes

- **No keys required.** Every tab loads offline; the LLM explain button degrades
  gracefully to a deterministic summary when `ANTHROPIC_API_KEY` is absent.
- **Document authenticity tab.** It needs a locally-trained ELA model
  (`data/models/doc_authenticity.joblib`), which isn't built in demo mode, so that
  tab shows a "no trained model" note. To enable it, commit a model trained via
  `relief-probe vision-demo`, or leave the note — the four data tabs are the demo.
- **Base rate.** The demo's prosecution base rate is intentionally high (small
  population) so the tabs have something to show; it is not the real 0.034%.
