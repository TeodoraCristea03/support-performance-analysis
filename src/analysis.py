"""
analysis.py — run the analytical cuts and the significance tests.

Descriptive analysis is defined in the .sql files; this script executes
those queries, saves each cut to outputs/tables/, and runs the inferential
tests (chi-square, Kruskal-Wallis, a power simulation) in scipy.
"""

from pathlib import Path
import os
import re
import duckdb
import numpy as np
from scipy import stats

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)

CLEAN = "data/clean/tickets_clean.parquet"
ANALYSIS_SQL = PROJECT_ROOT / "sql" / "03_analysis.sql"
METRICS_SQL = PROJECT_ROOT / "sql" / "02_metrics.sql"
MATERIALITY_SQL = PROJECT_ROOT / "sql" / "02c_materiality.sql"
OUT_DIR = PROJECT_ROOT / "outputs" / "tables"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def rule(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def run_labelled_sql(con, path: Path) -> dict:
    """Execute a .sql file statement by statement, returning {title: df} for
    each SELECT. Each query's title is read from the `-- # Title` header line
    above it. Comments are stripped before splitting on ';' so a ';' inside a
    comment can't break a statement boundary."""
    raw = path.read_text()
    titles = [t.strip() for t in re.findall(r"(?m)^\s*--\s*#\s*([^\n]+)", raw)]
    clean = "\n".join(re.sub(r"--.*$", "", ln) for ln in raw.splitlines())
    statements = [s.strip() for s in clean.split(";") if s.strip()]

    results, i = {}, 0
    for sql in statements:
        if sql.upper().startswith("CREATE"):
            con.execute(sql)
            continue
        results[titles[i] if i < len(titles) else f"query_{i}"] = con.sql(sql).df()
        i += 1
    return results


def save_cut(df, label) -> None:
    df.to_csv(OUT_DIR / f"{slug(label)}.csv", index=False)


def main() -> None:
    con = duckdb.connect()
    con.execute(f"CREATE OR REPLACE VIEW tickets AS SELECT * FROM read_parquet('{CLEAN}')")

    # descriptive cuts
    rule("ANALYSIS CUTS")
    cuts = run_labelled_sql(con, ANALYSIS_SQL)
    for label, df in cuts.items():
        print(f"\n>>> {label}")
        print(df.to_string(index=False))
        save_cut(df, label)

    # spread of the low-score rate within each segment cut
    rule("UNIFORMITY SUMMARY (low-score rate % range within each cut)")
    for dim in ("category", "channel", "brand", "priority"):
        key = next((k for k in cuts if k.lower() == f"by {dim}"), None)
        if key and "low_score_percentage" in cuts[key]:
            col = cuts[key]["low_score_percentage"]
            print(f"by {dim:<10} low-score rate %: {col.min():.1f} - {col.max():.1f} "
                  f"(spread {col.max() - col.min():.1f} pts)")

    # =====================================================================
    # SIGNIFICANCE TESTS
    # =====================================================================
    rule("SIGNIFICANCE TESTS  (does any segment REALLY differ, or is it noise?)")

    # Chi-square of independence: is the low-score rate associated with each
    # dimension? SQL builds the contingency counts; scipy runs the test.
    print("\n[Chi-square of independence — low-score rate vs segment]")
    print("H0: the low-score rate is the same across the segment (any spread is noise).\n")
    for dim in ("category", "channel", "brand", "priority"):
        cont = con.sql(f"""
            SELECT {dim}                                             AS seg,
                   COUNT(*) FILTER (WHERE is_bottom_box)             AS botbox,
                   COUNT(*) FILTER (WHERE has_satisfaction AND NOT is_bottom_box) AS other
            FROM tickets
            WHERE has_satisfaction
            GROUP BY {dim}
        """).df()
        table = cont[["botbox", "other"]].to_numpy()
        chi2, p, dof, _ = stats.chi2_contingency(table)
        verdict = "NOT significant (within noise)" if p > 0.05 else "SIGNIFICANT"
        print(f"  {dim:<10} chi2={chi2:6.2f}  dof={dof}  p={p:.3f}  -> {verdict}")

    # Kruskal-Wallis: does resolution time differ by priority? Non-parametric
    # because resolution time is right-skewed.
    print("\n[Kruskal-Wallis — resolution time across priorities]")
    print("H0: resolution-time distribution is the same for every priority.\n")
    groups = []
    for pr in ("Urgent", "High", "Medium", "Low"):
        vals = con.sql(f"""
            SELECT resolution_hours FROM tickets
            WHERE is_resolved AND NOT is_negative_duration AND priority = '{pr}'
        """).df()["resolution_hours"].to_numpy()
        groups.append(vals)
    h, p = stats.kruskal(*groups)
    verdict = "NOT significant (priority does not change speed)" if p > 0.05 else "SIGNIFICANT"
    print(f"  H={h:.2f}  p={p:.3f}  -> {verdict}")

    # Worst-looking cell vs overall, with a multiple-comparisons correction.
    # Scanning 5x4 = 20 category x channel cells, ~1 looks significant at
    # p<0.05 by chance, so a bare p-value would mislead; Bonferroni sets the bar.
    print("\n[Two-proportion z-test — worst-looking cell vs overall, "
          "multiple-comparisons aware]")
    row = con.sql("""
        SELECT
            COUNT(*) FILTER (WHERE has_satisfaction AND category='Technical Issue' AND channel='chat') AS cell_n,
            COUNT(*) FILTER (WHERE is_bottom_box AND category='Technical Issue' AND channel='chat')    AS cell_bad,
            COUNT(*) FILTER (WHERE has_satisfaction) AS all_n,
            COUNT(*) FILTER (WHERE is_bottom_box)    AS all_bad
        FROM tickets
    """).df().iloc[0]
    p1 = row.cell_bad / row.cell_n
    p0 = row.all_bad / row.all_n
    import math
    pooled = (row.cell_bad + row.all_bad) / (row.cell_n + row.all_n)
    se = math.sqrt(pooled * (1 - pooled) * (1 / row.cell_n + 1 / row.all_n))
    z = (p1 - p0) / se
    pval = 2 * (1 - stats.norm.cdf(abs(z)))
    n_cells = 5 * 4                       # category x channel cells scanned
    bonf = 0.05 / n_cells
    survives = pval < bonf
    print(f"  Technical Issue x chat: {p1*100:.1f}%  vs overall {p0*100:.1f}%  "
          f"(n={int(row.cell_n)})  z={z:.2f}  p={pval:.3f}")
    print(f"  raw p<0.05? {'yes' if pval < 0.05 else 'no'}   "
          f"but Bonferroni threshold for {n_cells} cells = {bonf:.4f}   "
          f"-> {'REAL after correction' if survives else 'NOT real (expected false positive from slicing 20 ways)'}")

    # =====================================================================
    # THE BACKLOG — size was never the story; age is.
    # =====================================================================
    rule("BACKLOG AGE  (the finding the 11.6% headline hides)")
    age = con.sql("""
        SELECT COUNT(*) AS open_tickets,
               ROUND(MEDIAN(open_age_days))                    AS median_age_days,
               MAX(open_age_days)                              AS max_age_days,
               COUNT(*) FILTER (WHERE open_age_days >= 180)    AS over_180_days,
               COUNT(*) FILTER (WHERE open_age_days < 7)       AS under_7_days
        FROM tickets WHERE NOT is_resolved
    """).df().iloc[0]
    med_close = con.sql("""
        SELECT ROUND(MEDIAN(resolution_hours), 1) AS h FROM tickets
        WHERE is_resolved AND NOT is_negative_duration
    """).df().iloc[0]["h"]
    print(f"open tickets ........................... {int(age.open_tickets)}")
    print(f"median age of an open ticket ........... {int(age.median_age_days)} days")
    print(f"oldest still open ...................... {int(age.max_age_days)} days")
    print(f"open 180+ days ......................... {int(age.over_180_days)} "
          f"({100*age.over_180_days/age.open_tickets:.1f}% of the backlog)")
    print(f"open less than 7 days .................. {int(age.under_7_days)} "
          f"({100*age.under_7_days/age.open_tickets:.1f}%)")
    print(f"\nmedian time to close, when a ticket DOES close: {med_close} hours.")
    print("So a ticket is resolved within hours, or it is effectively never\n"
          "resolved at all. There is almost no middle. That gap — not the\n"
          "11.6% headline — is the actionable problem.")

    # =====================================================================
    # THE SATISFACTION QUESTION — the scale mixture model.
    # =====================================================================
    scale_mixture_test(con)

    # =====================================================================
    # MATERIALITY — "if you don't handle X, this metric is wrong by ~Y"
    # =====================================================================
    rule('MATERIALITY  ("if you don\'t handle X, the metric is wrong by ~Y")')
    print("Each query below deliberately re-introduces one defect and reports\n"
          "what the headline metric becomes. Source: sql/02c_materiality.sql\n")
    for label, df in run_labelled_sql(con, MATERIALITY_SQL).items():
        print(f">>> {label}")
        print(df.to_string(index=False), "\n")
        save_cut(df, label)

    rule("OUTPUTS WRITTEN")
    for f in sorted(OUT_DIR.glob("*.csv")):
        print(f.relative_to(PROJECT_ROOT))


def scale_mixture_test(con) -> None:
    """Separate the mixed 1-5 / 1-10 satisfaction scales at population level
    and test whether either group carries any signal.

    SQL produces the counts (see 02_metrics.sql); Python does only the
    inference — a chi-square goodness-of-fit plus a power simulation. The
    power step matters: without it, "we found no difference" is
    indistinguishable from "our test was too weak to find one".
    """
    rule("SATISFACTION SCALE — deconvolution + goodness-of-fit")

    obs = con.sql("""
        SELECT satisfaction_score, COUNT(*) AS n
        FROM tickets WHERE has_satisfaction
        GROUP BY 1 ORDER BY 1
    """).df()["n"].to_numpy(dtype=float)

    total = obs.sum()
    # Scores 6-10 can only come from the 1-10 scale, so their average height
    # IS that group's per-score height. Ten such scores => its total size.
    h = obs[5:].mean()
    n10, n5 = h * 10, total - h * 10

    print(f"scored tickets ......................... {int(total)}")
    print(f"estimated on the 1-10 scale ............ {n10:.0f}  ({100*n10/total:.1f}%)")
    print(f"estimated on the 1-5 scale ............. {n5:.0f}  ({100*n5/total:.1f}%)")

    # Is the 1-10 group evenly spread? Test its own five bins.
    chi_hi = stats.chisquare(obs[5:])
    print(f"\n[1] 1-10 group, scores 6-10 evenly spread?"
          f"   chi2={chi_hi.statistic:.2f}  p={chi_hi.pvalue:.3f}"
          f"  -> {'yes, flat' if chi_hi.pvalue > 0.05 else 'no'}")

    # Peel the 1-10 group off scores 1-5; is what remains evenly spread?
    implied_1_5 = obs[:5] - h
    chi_lo = stats.chisquare(implied_1_5)
    print(f"[2] 1-5 group after subtracting it ......"
          f"   chi2={chi_lo.statistic:.2f}  p={chi_lo.pvalue:.3f}"
          f"  -> {'yes, flat' if chi_lo.pvalue > 0.05 else 'no'}")
    print(f"    implied 1-5 counts: {np.round(implied_1_5, 0).astype(int).tolist()}")

    # The whole 10-bin histogram against the full model, in one test.
    # Two parameters were estimated from the data (n5, n10) -> ddof=2.
    expected = np.concatenate([np.full(5, total / 5 - h), np.full(5, h)])
    chi_all = stats.chisquare(obs, expected, ddof=2)
    print(f"[3] full model, both groups even ........"
          f"   chi2={chi_all.statistic:.2f}  p={chi_all.pvalue:.3f}"
          f"  -> {'MODEL FITS' if chi_all.pvalue > 0.05 else 'model rejected'}")

    # Power: could a real effect have hidden behind that null result? Simulate
    # a 1-5 group tilted towards higher scores and count how often the same
    # test would catch it.
    print("\n[4] Power check — could we have missed a real effect at this n?")
    rng = np.random.default_rng(7)
    for tilt in (0.1, 0.2, 0.3):
        probs = np.linspace(1 - tilt, 1 + tilt, 5)
        probs /= probs.sum()
        detected = 0
        trials = 600
        for _ in range(trials):
            sim = (np.concatenate([rng.multinomial(int(n5), probs), np.zeros(5)])
                   + rng.multinomial(int(n10), np.full(10, 0.1)))
            sh = sim[5:].mean()
            exp = np.concatenate([np.full(5, sim.sum() / 5 - sh), np.full(5, sh)])
            if stats.chisquare(sim, exp, ddof=2).pvalue < 0.05:
                detected += 1
        mean_score = float(np.dot(probs, range(1, 6)))
        print(f"    if the true 1-5 mean were {mean_score:.2f} (not 3.00), "
              f"we would detect it {100*detected/trials:.0f}% of the time")

    print("\nCONCLUSION: both groups sit on their own scale's midpoint "
          "(3.0/5 and 5.5/10).\n"
          "A survey whose answers are evenly spread carries no information — "
          "these scores are\nindistinguishable from random numbers. The ~34% "
          "'low-score rate' is simply what random\nscoring produces "
          f"(the model predicts {100*(n5*0.4 + n10*0.2)/total:.1f}%, "
          f"we observe {100*obs[:2].sum()/total:.1f}%). It is not a "
          "measure of\ncustomer sentiment, so it is not reported as a KPI.")

    # The same conclusion in the stakeholder's units: how far apart do
    # reasonable methods land? That spread is the materiality of the unflagged
    # scale migration. Definitions live in SQL (02_metrics.sql); this runs them.
    rule("SATISFACTION — the methodology-swing table")
    swing_titles = {
        "Satisfaction scale deconvolution", "Satisfaction methodology swing",
        "Backlog age profile", "Speed by denominator",
    }
    for label, df in run_labelled_sql(con, METRICS_SQL).items():
        if label not in swing_titles:
            continue
        print(f">>> {label}")
        print(df.to_string(index=False), "\n")
        save_cut(df, label)

    swing = con.sql("""
        WITH s AS (SELECT satisfaction_score AS x FROM tickets WHERE has_satisfaction)
        SELECT ROUND(MIN(v), 1) AS lowest, ROUND(MAX(v), 1) AS highest,
               ROUND(MAX(v) - MIN(v), 1) AS swing_points
        FROM (
            SELECT 100.0 * AVG((x - 1) / 9.0) AS v FROM s
            UNION ALL SELECT 100.0 * AVG(x) / 5.0 FROM s
        )
    """).df().iloc[0]
    print(f"Widest gap between two defensible methods: "
          f"{swing.lowest:.1f}% vs {swing.highest:.1f}% "
          f"= {swing.swing_points:.1f} percentage points of pure method choice,\n"
          "on identical data. That is the cost of the unflagged scale migration.")


if __name__ == "__main__":
    main()
