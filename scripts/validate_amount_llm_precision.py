"""Precision audit of the LLM-recovered (amount+llm) fraud labels (read-only).

H4 measured the EXACT resolver's labels at ~84-88% precision. The LLM entity-resolution
pass added 79 labels marked ``match_method='amount+llm'`` (blocked by an exact dollar
match, then an LLM adjudicated the NAME). Those are not yet hand-validated. This script
joins every amount+llm label to its loan fields AND the matched DOJ release text so each
can be adjudicated: does the release genuinely charge the entity behind this loan, or did
the dollar amount collide with an unrelated loan and the LLM over-match the name?

There are only 79, so we audit ALL of them (no sampling). For each we show the loan's
borrower_name/state/amount, the LLM's matched defendant_name + confidence, the release
title, and snippets of the release body around (a) the matched dollar amount and (b) the
borrower-name tokens — the evidence needed to judge true / false / ambiguous.

Run: `uv run python scripts/validate_amount_llm_precision.py`. Read-only; no writes.
"""

from __future__ import annotations

import re

from relief_probe.warehouse import connect


def _window(body: str, needle: str, *, width: int = 180) -> str | None:
    """A +/- width snippet of body around the first case-insensitive `needle`."""
    if not body or not needle:
        return None
    i = body.lower().find(needle.lower())
    if i < 0:
        return None
    lo = max(0, i - width)
    hi = min(len(body), i + len(needle) + width)
    snip = body[lo:hi].replace("\n", " ")
    return ("…" if lo else "") + snip + ("…" if hi < len(body) else "")


def _name_tokens(name: str) -> list[str]:
    """Distinctive (>=4-char, non-corporate) tokens of a borrower name."""
    stop = {"LLC", "INC", "CORP", "CORPORATION", "COMPANY", "LTD", "GROUP",
            "SERVICES", "HOLDINGS", "ENTERPRISES", "THE", "AND"}
    toks = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper()).split()
    return [t for t in toks if len(t) >= 4 and t not in stop]


def main() -> None:
    with connect(read_only=True) as con:
        rows = con.execute(
            """
            SELECT f.loan_number, l.borrower_name, l.borrower_state,
                   l.current_approval_amount, f.defendant_name, f.match_confidence,
                   p.title, p.body
            FROM fraud_cases f
            JOIN loans l USING (loan_number)
            LEFT JOIN press_releases p ON f.source_url = p.url
            WHERE f.match_method = 'amount+llm'
            ORDER BY f.match_confidence DESC, l.borrower_name
            """
        ).fetchall()

    print(f"# amount+llm label precision audit — all {len(rows)} labels\n")
    for i, (ln, name, state, amt, defendant, conf, title, body) in enumerate(rows, 1):
        amt_str = f"{int(round(amt)):,}" if amt is not None else "?"
        print(f"[{i:02d}] loan {ln} · conf {conf}")
        print(f"     LOAN:     {name}  ({state}, ${amt_str})")
        print(f"     LLM match: {defendant}")
        print(f"     RELEASE:  {title}")
        body = body or ""
        amt_snip = _window(body, f"{int(round(amt)):,}") if amt is not None else None
        if amt_snip:
            print(f"     @amount:  {amt_snip}")
        shown = False
        for tok in _name_tokens(name)[:2]:
            snip = _window(body, tok, width=140)
            if snip:
                print(f"     @'{tok}': {snip}")
                shown = True
        if not shown:
            # No borrower-name token appears in the release body — a FP red flag.
            print("     @name:    (no borrower-name token found in release body)")
        print()


if __name__ == "__main__":
    main()
