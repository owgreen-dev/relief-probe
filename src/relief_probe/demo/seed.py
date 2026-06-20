"""Synthetic demo warehouse — a small, fully fictitious PPP dataset.

Why this exists
---------------
The real warehouse (~11.4M loans, several GB) is gitignored and must never be
deployed (SIGN-007). The hosted dashboard demo therefore needs its own
*synthetic* data: fictitious borrowers, a fabricated look-alike ring, planted
duplicate-funding entities, and made-up "prosecution" labels. **Nothing here is
real** — every name, loan number, and label is invented. See RESPONSIBLE_USE.md.

Design intent: the data is shaped so the three *production* detectors actually
fire, and we run the **real** detectors over it (:func:`run_all`) rather than
hand-writing signals. So the demo exercises the genuine pipeline:

* ``naics_cohort_outlier`` — each NAICS x state cohort has >=30 loans with a
  spread of dollars-per-job plus a few extreme high-$/job leads (robust-z tail).
* ``payroll_cap_exceedance`` — those extreme leads sit far above the per-employee
  payroll ceiling, while normal loans stay below it.
* ``multiple_funded_loans`` — a few entities hold the same name+address across
  several loans (the one-per-draw rule violated).

A separate name-similar "ring" (shared root, distinct entities) gives the
Similar-cases tab a coordinated cluster to surface, some of it prosecuted.

Determinism: everything is driven by a single seeded RNG so the demo warehouse
is byte-stable across rebuilds (no wall-clock, no os-randomness).
"""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pandas as pd

DEMO_SEED = 42

# (state) -> (city, 5-digit zip) anchor for that state's loans.
LOCATIONS: dict[str, tuple[str, str]] = {
    "FL": ("MIAMI", "33101"),
    "CA": ("LOS ANGELES", "90012"),
    "TX": ("DALLAS", "75201"),
    "NY": ("BROOKLYN", "11201"),
    "GA": ("ATLANTA", "30303"),
}

# Each cohort (state, 6-digit NAICS, label) gets >=30 loans so the cohort
# detector's ``min_cohort_size`` gate is satisfied. FL/722511 is NAICS 72
# (food) — a higher payroll cap, on purpose, to exercise that branch.
COHORTS: list[tuple[str, str, str]] = [
    ("FL", "722511", "Full-Service Restaurants"),
    ("CA", "541611", "Management Consulting"),
    ("TX", "236220", "Commercial Construction"),
    ("NY", "621610", "Home Health Care Services"),
    ("GA", "484110", "General Freight Trucking"),
]

LENDERS = [
    "Summit Capital Bank", "Riverstone Financial", "Cedar Trust Bank",
    "Pinnacle Lending Group", "Harbor National Bank", "Frontier Fintech Partners",
]

# Fictitious name building blocks. NORTHWIND is reserved for the look-alike ring
# below, so it is deliberately absent from the random root pool.
ROOTS = [
    "EVERGREEN", "BLUE HARBOR", "IRONGATE", "CEDAR POINT", "BRIGHTSTONE",
    "RED OAK", "SILVERLINE", "PIONEER", "ATLAS", "MERIDIAN", "CRESCENT",
    "STONEBRIDGE", "VANGUARD", "MAPLEWOOD", "HARBORVIEW", "GOLDLEAF",
    "FROSTPEAK", "LIBERTY BAY", "CLEARWATER", "GRANITE", "WILLOW CREEK",
    "EMBERLINE", "TRUE NORTH", "FAIRMONT", "ASHWOOD", "BRIGHTON",
    "COPPERFIELD", "DELMAR", "ELKHORN", "FOXGLOVE",
]
SECTOR_WORDS: dict[str, list[str]] = {
    "722511": ["HOSPITALITY", "RESTAURANT GROUP", "KITCHEN", "CAFE", "BISTRO"],
    "541611": ["CONSULTING", "ADVISORY", "STRATEGY", "PARTNERS", "MANAGEMENT"],
    "236220": ["BUILDERS", "CONSTRUCTION", "CONTRACTING", "DEVELOPMENT", "WORKS"],
    "621610": ["HOME CARE", "HEALTH SERVICES", "WELLNESS", "CARE GROUP", "NURSING"],
    "484110": ["LOGISTICS", "TRUCKING", "FREIGHT", "TRANSPORT", "HAULING"],
}
SUFFIXES = ["LLC", "INC", "CORP", "CO", "GROUP", "ENTERPRISES", "HOLDINGS"]
STREETS = ["MAIN ST", "OAK AVE", "COMMERCE ST", "MARKET ST", "BROADWAY",
           "PARK AVE", "CEDAR LN", "INDUSTRIAL BLVD", "HARBOR DR", "ELM ST"]
BUSINESS_TYPES = [
    "Limited  Liability Company(LLC)", "Corporation", "Subchapter S Corporation",
    "Sole Proprietorship", "Partnership",
]
AGE_NORMAL = "Existing or more than 2 years old"
AGE_NEW = ["New Business or 2 years or less",
           "Startup, Loan Funds will Open Business", "Change of Ownership"]

FIRST_NAMES = ["James", "Maria", "David", "Linda", "Robert", "Patricia",
               "Michael", "Jennifer", "William", "Angela", "Carlos", "Nicole"]
LAST_NAMES = ["Carter", "Nguyen", "Patel", "Rivera", "Thompson", "Brooks",
              "Hughes", "Foster", "Reyes", "Coleman", "Sandoval", "Pierce"]

_BASE_DATE = date(2020, 4, 3)  # PPP round-1 opening; offsets are deterministic.


def _loan_number(i: int) -> str:
    return str(9_000_000_000 + i)


def _approved(rng) -> str:
    return (_BASE_DATE + timedelta(days=rng.randint(0, 400))).isoformat()


def _loan(
    i: int, name: str, naics: str, state: str, *, amount: float, jobs: int,
    rng, address: str | None = None, method: str | None = None,
    new_business: bool = False,
) -> dict:
    city, zip_code = LOCATIONS[state]
    forgiven = rng.random() < 0.7
    method = method or ("PPS" if rng.random() < 0.25 else "PPP")
    return {
        "loan_number": _loan_number(i),
        "date_approved": _approved(rng),
        "processing_method": method,
        "borrower_name": name,
        "borrower_address": address or f"{rng.randint(100, 9899)} {rng.choice(STREETS)}",
        "borrower_city": city,
        "borrower_state": state,
        "borrower_zip": zip_code,
        "loan_status": "Paid in Full" if forgiven else "Exemption 4",
        "term": rng.choice([24, 60]),
        "initial_approval_amount": amount,
        "current_approval_amount": amount,
        "jobs_reported": float(jobs),
        "naics_code": naics,
        "business_type": rng.choice(BUSINESS_TYPES),
        "originating_lender": rng.choice(LENDERS),
        "originating_lender_state": state,
        "payroll_proceed": round(amount * rng.uniform(0.85, 1.0), 2),
        "forgiveness_amount": amount if forgiven else 0.0,
        "business_age_description": rng.choice(AGE_NEW) if new_business else AGE_NORMAL,
    }


def _generate_loans(rng) -> list[dict]:
    """Build the synthetic loan population (all in the $150k+ slice)."""
    loans: list[dict] = []
    i = 0
    used: set[str] = set()

    def fresh_name(naics: str) -> str:
        while True:
            name = (f"{rng.choice(ROOTS)} {rng.choice(SECTOR_WORDS[naics])} "
                    f"{rng.choice(SUFFIXES)}")
            if name not in used:
                used.add(name)
                return name

    for state, naics, _label in COHORTS:
        # Normal loans: $/job kept below the $20,833 payroll ceiling so only the
        # genuine outliers trip payroll_cap; spread gives the cohort a real MAD.
        for _ in range(46):
            amount = round(rng.uniform(150_000, 850_000), 2)
            dpj = rng.uniform(9_000, 19_000)
            jobs = max(8, round(amount / dpj))
            loans.append(_loan(i, fresh_name(naics), naics, state,
                               amount=amount, jobs=jobs, rng=rng))
            i += 1
        # Extreme leads: tiny headcount + large amount → very high $/job, so both
        # the cohort and payroll-cap detectors fire and they top the composite.
        for _ in range(5):
            amount = round(rng.uniform(600_000, 2_400_000), 2)
            jobs = rng.randint(1, 4)
            loans.append(_loan(i, fresh_name(naics), naics, state,
                               amount=amount, jobs=jobs, rng=rng, new_business=True))
            i += 1

    # --- The NORTHWIND look-alike ring (Similar-cases hero) -------------------
    # Distinct entities (different names + addresses, so NOT multiple-funded) that
    # share a fabricated root, area, and dollar band — the retrieval target.
    ring_names = [
        "NORTHWIND HOSPITALITY LLC", "NORTHWIND HOSPITALITY GROUP",
        "NORTHWIND KITCHEN PARTNERS", "NORTHWIND BISTRO HOLDINGS",
        "NORTHWIND CATERING CO", "NORTHWIND RESTAURANT GROUP",
    ]
    ring_loan_numbers: list[str] = []
    for name in ring_names:
        amount = round(rng.uniform(820_000, 980_000), 2)
        jobs = rng.randint(2, 4)
        loans.append(_loan(i, name, "722511", "FL",
                           amount=amount, jobs=jobs, rng=rng, new_business=True))
        ring_loan_numbers.append(_loan_number(i))
        i += 1

    # --- Duplicate-funding entities (multiple_funded_loans) -------------------
    # Same normalized name + building across >2 funded loans → the one-per-draw
    # rule is violated; every loan in the entity fires.
    dup_specs = [
        ("IRONGATE BUILDERS LLC", "100 COMMERCE ST", "236220", "TX", 3),
        ("HARBORVIEW LOGISTICS INC", "250 INDUSTRIAL BLVD", "484110", "GA", 2),
        ("MERIDIAN HOME CARE LLC", "77 PARK AVE", "621610", "NY", 2),
    ]
    dup_loan_numbers: list[str] = []
    for name, address, naics, state, n in dup_specs:
        for _ in range(n):
            amount = round(rng.uniform(300_000, 900_000), 2)
            jobs = rng.randint(3, 10)
            loans.append(_loan(i, name, naics, state, amount=amount, jobs=jobs,
                               rng=rng, address=address, method="PPP",
                               new_business=True))
            dup_loan_numbers.append(_loan_number(i))
            i += 1

    return loans, ring_loan_numbers, dup_loan_numbers


def _generate_cases(rng, loans, ring_lns, dup_lns) -> list[dict]:
    """Invent prosecution labels resolved to a subset of the high-$/job loans."""
    by_ln = {lo["loan_number"]: lo for lo in loans}
    # High-$/job extreme leads (jobs <= 4) outside the ring/dup sets.
    extreme = [lo["loan_number"] for lo in loans
               if lo["jobs_reported"] <= 4
               and lo["loan_number"] not in set(ring_lns) | set(dup_lns)]
    rng.shuffle(extreme)
    chosen = ring_lns[:3] + dup_lns[:2] + extreme[:10]

    cases: list[dict] = []
    for n, ln in enumerate(chosen):
        lo = by_ln[ln]
        charge = (date(2023, 1, 1) + timedelta(days=rng.randint(0, 900))).isoformat()
        alleged = round(lo["current_approval_amount"] * rng.uniform(1.0, 2.5), 2)
        cases.append({
            "case_id": f"DEMO-{n:04d}",
            "loan_number": ln,
            "defendant_name": f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
            "business_name": lo["borrower_name"],
            "alleged_amount": alleged,
            "charge_date": charge,
            "source": "doj",
            # Clearly-synthetic source ref — NOT a real DOJ URL.
            "source_url": f"synthetic://demo-case/{n:04d}",
            "match_method": "demo_synthetic",
            "match_confidence": 1.0,
        })
    return cases


def _insert(con: duckdb.DuckDBPyConnection, table: str, rows: list[dict]) -> None:
    """INSERT BY NAME from a DataFrame so unlisted columns default to NULL."""
    frame = pd.DataFrame(rows)
    con.register("_demo_tmp", frame)
    con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _demo_tmp")
    con.unregister("_demo_tmp")


def build_demo_warehouse(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Populate ``con`` with the synthetic demo dataset and run the real detectors.

    Idempotent: clears the three tables first, so a rebuild is byte-stable.
    """
    import random

    rng = random.Random(DEMO_SEED)
    con.execute("DELETE FROM signals")
    con.execute("DELETE FROM fraud_cases")
    con.execute("DELETE FROM loans")

    loans, ring_lns, dup_lns = _generate_loans(rng)
    cases = _generate_cases(rng, loans, ring_lns, dup_lns)
    _insert(con, "loans", loans)
    _insert(con, "fraud_cases", cases)

    # Run the genuine production detectors over the synthetic loans.
    from relief_probe.detectors.runner import run_all

    counts = run_all(con)
    return {"loans": len(loans), "fraud_cases": len(cases), "signals": counts}
