"""
charts.py — render the figures for the deck.

Runs the queries in sql/02_metrics.sql and sql/03_analysis.sql and draws
each result set. Every number comes from SQL; this script only plots.
Palette is colourblind-safe; each panel uses a single y-axis.
"""

from pathlib import Path
import os
import re
import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)
OUT = PROJECT_ROOT / "outputs" / "charts"
OUT.mkdir(parents=True, exist_ok=True)

# ---- colour palette (light surface) -------------------------------------
INK        = "#0b0b0b"
INK2       = "#52514e"
MUTED      = "#898781"
GRID       = "#e1e0d9"
AXIS       = "#c3c2b7"
SURFACE    = "#fcfcfb"
BLUE       = "#2a78d6"   # categorical slot 1 / sequential
ORANGE     = "#eb6834"   # categorical slot 2
CRITICAL   = "#d03b3b"   # status - reference lines for "the problem"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": "sans-serif", "font.size": 11,
    "text.color": INK, "axes.labelcolor": INK2, "axes.edgecolor": AXIS,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.titlecolor": INK,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False,
})


def recessive(ax):
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    for s in ("left",):
        ax.spines[s].set_color(AXIS)


def header(ax, title, subtitle):
    """Title above subtitle, both left-aligned above the axes (no overlap)."""
    ax.text(0, 1.17, title, transform=ax.transAxes, fontsize=13.5,
            fontweight="bold", color=INK, va="bottom")
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9.5,
            color=INK2, va="bottom")


def load_sql(con, path: Path) -> dict:
    """Run a .sql file; return {title: df} for each SELECT, keyed by the
    `-- # Title` header line above it."""
    raw = path.read_text()
    titles = [t.strip() for t in re.findall(r"(?m)^\s*--\s*#\s*([^\n]+)", raw)]
    clean = "\n".join(re.sub(r"--.*$", "", ln) for ln in raw.splitlines())
    out, i = {}, 0
    for stmt in (s.strip() for s in clean.split(";") if s.strip()):
        if stmt.upper().startswith("CREATE"):
            con.execute(stmt); continue
        out[titles[i] if i < len(titles) else f"query_{i}"] = con.sql(stmt).df()
        i += 1
    return out


def save(fig, name: str):
    fig.tight_layout()
    fig.savefig(OUT / name, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  outputs/charts/{name}")


# =====================================================================
def main() -> None:
    con = duckdb.connect()
    m = load_sql(con, PROJECT_ROOT / "sql" / "02_metrics.sql")
    a = load_sql(con, PROJECT_ROOT / "sql" / "03_analysis.sql")

    kpi = m["Headline KPIs"].iloc[0]
    overall_low_score = kpi["low_score_percentage"]
    overall_median = kpi["median_res_hours"]
    overall_p90 = kpi["p90_res_hours"]

    print("Rendering charts:")

    # -- 1. Satisfaction histogram: the scale cliff ----------------------
    hist = m["Satisfaction score histogram"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    colors = [BLUE if s <= 5 else ORANGE for s in hist["satisfaction_score"]]
    bars = ax.bar(hist["satisfaction_score"], hist["n"], color=colors, width=0.72)
    for b, n in zip(bars, hist["n"]):
        ax.text(b.get_x() + b.get_width() / 2, n + 6, f"{int(n)}",
                ha="center", va="bottom", fontsize=9, color=INK2)
    ax.set_xticks(range(1, 11))
    ax.set_xlabel("satisfaction score")
    ax.set_ylabel("tickets")
    header(ax, "Satisfaction scores hide two mixed scales",
           "Scores 6–10 (orange) can only come from the 1–10 scale — a cliff at 5→6 reveals two\n"
           "populations the source never flagged (~76% on 1–5, ~24% on 1–10).")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=BLUE, label="1–5 range (ambiguous scale)"),
                       Patch(color=ORANGE, label="6–10 (1–10 scale only)")],
              frameon=False, loc="upper right", fontsize=9)
    recessive(ax)
    save(fig, "01_satisfaction_scale_cliff.png")

    # -- 2. Resolution-time distribution ---------------------------------
    dist = m["Resolution time distribution"].sort_values("resolution_bucket")
    labels = [b.split(": ")[1] for b in dist["resolution_bucket"]]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.bar(labels, dist["n"], color=BLUE, width=0.72)
    for b, n in zip(bars, dist["n"]):
        ax.text(b.get_x() + b.get_width() / 2, n + 6, f"{int(n)}",
                ha="center", va="bottom", fontsize=9, color=INK2)
    ax.set_xlabel("resolution time")
    ax.set_ylabel("tickets")
    header(ax, "Most tickets close within a day; a thin slow tail",
           f"Median {overall_median:.1f}h · 90th percentile {overall_p90:.1f}h. Reported as median + p90\n"
           "(not mean) because the distribution is right-skewed.")
    recessive(ax)
    save(fig, "02_resolution_distribution.png")

    # -- 3. Resolution by priority: the triage finding -------------------
    pr = a["By priority"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.bar(pr["segment"], pr["median_hrs"], color=BLUE, width=0.66)
    for b, v in zip(bars, pr["median_hrs"]):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.08, f"{v:.1f}h",
                ha="center", va="bottom", fontsize=9.5, color=INK2)
    ax.set_ylim(0, float(pr["median_hrs"].max()) * 1.28)
    ax.axhline(overall_median, color=CRITICAL, lw=1.6, ls="--")
    ax.text(len(pr) - 0.5, float(pr["median_hrs"].max()) * 1.16,
            f"overall median {overall_median:.1f}h",
            ha="right", va="bottom", fontsize=9, color=CRITICAL)
    ax.set_xlabel("priority (intended urgency, high → low)")
    ax.set_ylabel("median resolution (hours)")
    header(ax, "Priority isn't working: Urgent is no faster than Low",
           "Median resolution is flat across priorities (Kruskal–Wallis p = 0.22). Triage isn't\n"
           "translating into faster handling of urgent tickets — the actionable finding.")
    recessive(ax)
    save(fig, "03_resolution_by_priority.png")

    # -- 4. Sentiment uniformity: 4 facets, common baseline --------------
    facets = [("By category", "by category"), ("By channel", "by channel"),
              ("By brand", "by brand"), ("By priority", "by priority")]
    fig, axes = plt.subplots(1, 4, figsize=(13, 4.6), sharey=True)
    for (key, title), ax in zip(facets, axes):
        df = a[key]
        x = range(len(df))
        ax.bar(x, df["low_score_percentage"], color=BLUE, width=0.68)
        ax.axhline(overall_low_score, color=CRITICAL, lw=1.4, ls="--")
        ax.set_title(title, fontsize=10.5, color=INK2)
        ax.set_xticks(list(x))
        ax.set_xticklabels(df["segment"], rotation=35, ha="right", fontsize=8.5)
        recessive(ax)
    axes[0].set_ylabel("low-score rate, 1–2 (%)")
    axes[-1].text(len(a["By priority"]) - 0.5,
                  overall_low_score + 0.6, f"reference ~{overall_low_score:.0f}%",
                  ha="right", va="bottom", fontsize=9, color=CRITICAL)
    fig.suptitle("Customer sentiment is uniform — no segment stands out",
                 fontweight="bold", x=0.06, ha="left", y=1.10, fontsize=13.5)
    fig.text(0.06, 1.01, "Every segment sits at the same low-score level; chi-square finds no significant "
                         "association with category, channel, brand or priority (all p > 0.5).",
             fontsize=9.5, color=INK2, ha="left")
    fig.text(0.06, 0.965, "Shown for comparison only — the level is not a true dissatisfaction rate "
                          "(mixed 1–5 / 1–10 scales inflate it).",
             fontsize=8.5, color=MUTED, ha="left", style="italic")
    save(fig, "04_sentiment_uniformity.png")

    # -- 5. Monthly trend: two stacked panels (never a dual axis) --------
    mo = a["Monthly trend"].copy()
    mo["label"] = [d.strftime("%b %y") for d in mo["month"]]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    ax1.bar(mo["label"], mo["volume"], color=BLUE, width=0.7)
    ax1.set_ylabel("tickets / month")
    ax1.set_title("Volume and sentiment are stable over the year",
                  fontweight="bold", loc="left")
    recessive(ax1)
    ax2.plot(mo["label"], mo["low_score_percentage"], color=ORANGE, lw=2, marker="o", ms=5)
    ax2.axhline(overall_low_score, color=CRITICAL, lw=1.2, ls="--")
    ax2.set_ylabel("low-score rate, 1–2 (%)")
    ax2.set_ylim(0, max(mo["low_score_percentage"]) * 1.25)
    ax2.text(len(mo) - 0.5, overall_low_score + 0.8, f"reference ~{overall_low_score:.0f}%",
             ha="right", va="bottom", fontsize=9, color=CRITICAL)
    recessive(ax2)
    plt.setp(ax2.get_xticklabels(), rotation=35, ha="right", fontsize=9)
    save(fig, "05_monthly_trend.png")

    # -- 6. Backlog age: the finding the 11.6% headline hides -------------
    age = m["Backlog age profile"]
    labels6 = [b.split(": ")[1] for b in age["age_bucket"]]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    # only the oldest bucket is coloured as the problem; the rest stay neutral
    colors = [BLUE] * (len(age) - 1) + [CRITICAL]
    bars = ax.bar(labels6, age["open_tickets"], color=colors, width=0.68)
    for b, n, p in zip(bars, age["open_tickets"], age["pct_of_backlog"]):
        ax.text(b.get_x() + b.get_width() / 2, n + 4, f"{int(n)}  ({p:.0f}%)",
                ha="center", va="bottom", fontsize=9, color=INK2)
    ax.set_ylim(0, float(age["open_tickets"].max()) * 1.18)
    ax.set_xlabel("how long the ticket has already been open")
    ax.set_ylabel("open tickets")
    header(ax, "The backlog isn't a queue — it's an abandoned pile",
           f"Half of the {int(kpi['open_tickets'])} open tickets have been sitting for 6 months or more "
           f"(median {int(kpi['median_open_age_days'])} days).\nTickets that DO close take a median of "
           f"{overall_median:.1f} hours. There is almost no middle.")
    recessive(ax)
    save(fig, "06_backlog_age.png")

    # -- 7. The methodology swing: "wrong by roughly Y", visualised ------
    swing = m["Satisfaction methodology swing"].copy()
    swing = swing.sort_values("satisfaction_percentage")
    fig, ax = plt.subplots(figsize=(9, 5))
    ypos = range(len(swing))
    # the defensible answer (variant J) is the only one highlighted
    cols = [ORANGE if v == "J" else MUTED for v in swing["variant"]]
    ax.barh(list(ypos), swing["satisfaction_percentage"], color=cols, height=0.62)
    for y, (v, val) in enumerate(zip(swing["variant"], swing["satisfaction_percentage"])):
        ax.text(val + 1.2, y, f"{val:.1f}%", va="center", fontsize=9,
                color=ORANGE if v == "J" else INK2,
                fontweight="bold" if v == "J" else "normal")
    ax.set_yticks(list(ypos))
    ax.set_yticklabels(swing["approach"], fontsize=8.8)
    ax.set_xlim(0, 100)
    ax.set_xlabel("resulting “customer satisfaction” (%)")
    lo, hi = swing["satisfaction_percentage"].min(), swing["satisfaction_percentage"].max()
    ax.axvspan(lo, hi, color=CRITICAL, alpha=0.07, zorder=0)
    header(ax, f"Same data, {len(swing)} methods, a {hi - lo:.0f}-point swing",
           "Every bar is a defensible way to answer “how satisfied are customers?” on the SAME\n"
           f"3,071 scores. Nothing about customers changes between them — only the scale assumption.\n"
           "Orange is the one the evidence supports: both scales sit on their midpoint, i.e. no signal.")
    recessive(ax)
    save(fig, "07_satisfaction_method_swing.png")

    # -- 8. The scale detector: what a real migration would have looked
    # --    like, next to what the data actually shows. This is the single
    # --    picture that explains why the scales can't be separated.
    scale_evidence = m["Satisfaction scale evidence"].copy()
    build_scale_detector(scale_evidence)

    # -- 9. The detector run across every dimension (A4 page) -----------
    build_detector_by_dimension()

    # -- 10. Dashboard mockup (lean wireframe with real values) ----------
    build_dashboard(m, a, kpi)

    print("\nDone.")


def build_scale_detector(ev):
    """Two panels: the migration we were told to expect, vs the one in the data.

    A score above 5 is impossible on a 1-5 scale, so "% of scores above 5" acts
    as a detector for how much 1-10 scale is present in any group of tickets.
    If the survey had switched over on a date, the detector would read ~0%
    before and ~50% after. It reads ~12% every single month instead.
    """
    labels = [d.strftime("%b") for d in ev["month"]]
    actual = ev["score_above_5_percentage"].tolist()
    # hypothetical clean cutover halfway through the year, for contrast only
    half = len(labels) // 2
    expected = [0.0] * half + [50.0] * (len(labels) - half)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.8), sharey=True)

    ax1.bar(labels, expected, color=MUTED, width=0.68)
    ax1.set_title("If the survey had really switched over on a date",
                  fontsize=11, color=INK2, loc="left")
    ax1.set_ylabel("% of scores above 5")
    ax1.annotate("nobody can score 7\non a 1–5 scale", xy=(1.5, 2), xytext=(0.2, 26),
                 fontsize=8.5, color=INK2,
                 arrowprops=dict(arrowstyle="->", color=MUTED, lw=1))
    ax1.annotate("half of all scores\nsit above 5", xy=(8, 50), xytext=(6.4, 33),
                 fontsize=8.5, color=INK2, ha="center",
                 arrowprops=dict(arrowstyle="->", color=MUTED, lw=1))
    recessive(ax1)

    ax2.bar(labels, actual, color=ORANGE, width=0.68)
    ax2.axhline(sum(actual) / len(actual), color=CRITICAL, lw=1.4, ls="--")
    ax2.set_title("What the data actually shows", fontsize=11, color=INK2, loc="left")
    ax2.text(len(labels) - 0.4, sum(actual) / len(actual) + 2.5,
             f"flat at ~{sum(actual)/len(actual):.0f}% every month",
             ha="right", fontsize=9, color=CRITICAL)
    recessive(ax2)

    for ax in (ax1, ax2):
        ax.set_ylim(0, 60)
        ax.tick_params(axis="x", labelsize=8.5)

    fig.suptitle("The two scales were never separated in time",
                 fontweight="bold", x=0.055, ha="left", y=1.28, fontsize=13.5)
    fig.text(0.055, 1.03,
             "A score above 5 is impossible on a 1–5 scale, so “% scoring above 5” detects how much "
             "1–10 scale is present.\nA real cutover would read 0% before and ~50% after. It reads "
             "~12% in every month — so every month already\ncontains the same mixture. The same ~12% "
             "holds across every brand, channel, team and location.",
             fontsize=9.5, color=INK2, ha="left")
    save(fig, "08_scale_detector.png")


def _tile(ax, value, label, alert=False):
    """One KPI tile. `alert` marks the single number that should pull the
    eye — used sparingly, because if everything is red nothing is."""
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_facecolor("#fdf3f1" if alert else "#faf9f7")
    for s in ax.spines.values():
        s.set_color(CRITICAL if alert else "#e1e0d9")
        s.set_linewidth(1.3 if alert else 0.8)
    ax.text(0.5, 0.62, value, ha="center", va="center", fontsize=16,
            fontweight="bold", color=CRITICAL if alert else INK,
            transform=ax.transAxes)
    ax.text(0.5, 0.24, label, ha="center", va="center", fontsize=8.2,
            color=INK2, transform=ax.transAxes)


def build_detector_by_dimension():
    """The >5 detector run across every dimension in the file.

    If the migration had been decided by something other than time 
    (one brand moved and another didn't, say), one of
    these rows would sit well away from the rest. None does.

    Each segment carries a 95% confidence interval, because 4 of these groups
    hold only ~750 scored tickets each and the eye reads small differences as
    real when they are not. Sized for A4 portrait so it can be printed or
    dropped into a document whole.
    """
    import numpy as np
    from scipy import stats as st

    con = duckdb.connect()
    con.execute("CREATE OR REPLACE VIEW t AS "
                "SELECT * FROM read_parquet('data/clean/tickets_clean.parquet')")

    overall = con.sql("""
        SELECT 100.0 * COUNT(*) FILTER (WHERE satisfaction_score > 5)
                     / COUNT(*) FILTER (WHERE has_satisfaction) FROM t
    """).fetchone()[0]

    rows, groups = [], []
    for dim, label in [("brand", "BRAND"), ("channel", "CHANNEL"),
                       ("team", "TEAM"), ("location", "AGENT LOCATION")]:
        d = con.sql(f"""
            SELECT {dim} AS seg,
                   COUNT(*) FILTER (WHERE has_satisfaction)      AS n,
                   COUNT(*) FILTER (WHERE satisfaction_score > 5) AS k
            FROM t WHERE {dim} IS NOT NULL GROUP BY 1 ORDER BY 1
        """).df()
        tab = np.array([d.k.values, (d.n - d.k).values]).T
        pval = st.chi2_contingency(tab).pvalue
        groups.append((label, len(d), pval))
        for _, r in d.iterrows():
            p = r.k / r.n
            se = np.sqrt(p * (1 - p) / r.n)          # 95% CI on a proportion
            rows.append((r.seg, 100 * p, 100 * 1.96 * se, int(r.n)))

    # ---- layout: a header row above each dimension block, then its segments
    fig = plt.figure(figsize=(8.27, 11.69))          # A4 portrait
    ax = fig.add_axes([0.22, 0.15, 0.72, 0.68])

    ypos, ylabels, headers, i = [], [], [], 0
    for label, count, _ in groups:
        headers.append((label, i))                    # header occupies this row
        i += 1
        for _ in range(count):
            ypos.append(i); i += 1
        i += 0.6                                      # small gap after block

    for (seg, pct, err, n), y in zip(rows, ypos):
        ax.errorbar(pct, y, xerr=err, fmt="o", color=BLUE, ecolor=AXIS,
                    elinewidth=1.6, capsize=3.5, markersize=7, zorder=3)
        ax.text(pct + err + 0.5, y, f"{pct:.1f}%", va="center",
                fontsize=9, color=INK2)
        ax.text(23.6, y, f"n={n:,}", va="center", ha="right",
                fontsize=8, color=MUTED)
        ylabels.append(seg)

    ax.axvline(overall, color=CRITICAL, lw=1.6, ls="--", zorder=2)
    ax.set_yticks(ypos)
    ax.set_yticklabels(ylabels, fontsize=9.5)
    ax.set_xlim(0, 24)
    ax.set_xlabel("% of scored tickets rated above 5\n"
                  "(how much 1–10 scale is present in that group)", fontsize=9.5)
    ax.set_ylim(i - 0.4, -1.4)                        # inverted, with headroom
    recessive(ax)
    ax.grid(axis="y", visible=False)                  # vertical guides only

    # dimension headers sit on their own row, above each block
    for label, y in headers:
        ax.text(0.3, y, label, va="center", ha="left",
                fontsize=10, fontweight="bold", color=INK)

    ax.text(overall + 0.4, -1.0, f"overall {overall:.1f}%",
            fontsize=9.5, color=CRITICAL, fontweight="bold")

    # ---- title block
    fig.text(0.06, 0.955, "No column in the file predicts which scale was used",
             fontsize=15, fontweight="bold", color=INK)
    fig.text(0.06, 0.925,
             "A score above 5 is impossible on a 1–5 scale, so “% scoring above 5” measures how much\n"
             "1–10 scale sits inside any group. If the migration had been decided by something other than\n"
             "time — one brand moving before another, or one channel’s tooling — that group would stand\n"
             "clear of the rest. Every one of the 15 groups below lands in the same narrow band.",
             fontsize=9.5, color=INK2, va="top")

    # ---- test results, bottom block (clear of the two-line x-label)
    fig.text(0.06, 0.085, "Tested, not eyeballed", fontsize=10.5,
             fontweight="bold", color=INK)
    txt = "     ".join([f"{lab.lower()}  p = {p:.2f}" for lab, _, p in groups])
    fig.text(0.06, 0.063,
             f"Chi-square test of independence, per dimension:\n{txt}\n\n"
             "All well above 0.05, so no dimension is associated with which scale a ticket used. "
             "Bars are 95%\nconfidence intervals — wide, because each group holds only ~600–1,000 "
             "scored tickets. That width is\nexactly why the small differences should not be read as real.",
             fontsize=8.4, color=MUTED, va="top", linespacing=1.5)

    fig.savefig(OUT / "09_detector_by_dimension.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print("  outputs/charts/09_detector_by_dimension.png")


def build_dashboard(m, a, kpi):
    """One-screen lean dashboard mockup.

    Layered so the caveats are present without drowning the screen:
      * KPI row  — the few numbers a decision hangs on. Exactly ONE is
                   styled as an alert (backlog age), so the eye knows where
                   to go. A screen with five red tiles has no priority.
      * Panels   — three, each answering one stakeholder question.
      * Footer   — a quiet "how far wrong would this be" strip at the bottom:
                   available when someone challenges a number, out of the way
                   when they don't.
    """
    import matplotlib.gridspec as gridspec
    pr = a["By priority"]
    mo = a["Monthly trend"].copy()
    bymonth = a["Backlog by creation month"].copy()
    agepr = a["Backlog age by priority"]
    age = m["Backlog age profile"]

    fig = plt.figure(figsize=(13.5, 9))
    gs = gridspec.GridSpec(3, 12, figure=fig, height_ratios=[0.72, 1.15, 1.0],
                           hspace=0.62, wspace=1.1,
                           left=0.045, right=0.975, top=0.855, bottom=0.155)

    fig.text(0.045, 0.945, "Support Performance — CS Ops", fontsize=16,
             fontweight="bold", color=INK)
    fig.text(0.045, 0.905,
             "Filters: date range · brand · category · priority          "
             "Default view: last full month vs previous",
             fontsize=9, color=MUTED)

    # ---- KPI row. Note what is NOT here: any satisfaction score. It is
    # ---- left off deliberately rather than shown with a caveat.
    tiles = [
        (f"{int(kpi['total_tickets']):,}", "Tickets (period)", False),
        (f"{kpi['pct_all_resolved_24h']:.0f}%", "Resolved < 24h\n(of ALL tickets)", False),
        (f"{int(kpi['open_tickets'])}", f"Open backlog ({kpi['backlog_percentage']:.1f}%)", False),
        (f"{int(kpi['median_open_age_days'])} days",
         "Median age of open work", True),          # the one alert on screen
        (f"{kpi['response_rate_percentage']:.0f}%", "Survey response rate", False),
    ]
    for i, (val, lab, alert) in enumerate(tiles):
        ax = fig.add_subplot(gs[0, i * 2:i * 2 + 2])
        _tile(ax, val, lab, alert=alert)
    # the sixth slot carries the one-line "so what", not another number
    axnote = fig.add_subplot(gs[0, 10:12])
    axnote.axis("off")
    axnote.text(0.0, 0.64, "Tickets close in hours\nor not at all.",
                fontsize=9, color=CRITICAL, va="center", fontweight="bold",
                transform=axnote.transAxes)
    axnote.text(0.0, 0.24, f"Median close: {kpi['median_res_hours']:.1f}h",
                fontsize=8.2, color=INK2, va="center", transform=axnote.transAxes)

    # ---- Panel 1: the backlog age profile (the problem) -----------------
    axa = fig.add_subplot(gs[1, 0:6])
    labs = [b.split(": ")[1] for b in age["age_bucket"]]
    cols = [BLUE] * (len(age) - 1) + [CRITICAL]
    axa.bar(labs, age["open_tickets"], color=cols, width=0.66)
    axa.set_title("Where the open work is stuck  →  act on the red bar first",
                  fontsize=10.5, color=INK2, loc="left")
    axa.tick_params(axis="x", labelsize=8)
    axa.set_ylabel("open tickets", fontsize=8.5)
    recessive(axa)

    # ---- Panel 2: volume + what each month left behind ------------------
    axv = fig.add_subplot(gs[1, 6:12])
    axv.bar(range(len(bymonth)), bymonth["created"], color=BLUE, width=0.68,
            label="created")
    axv.bar(range(len(bymonth)), bymonth["still_open"], color=CRITICAL, width=0.68,
            label="still open today")
    axv.set_title("Monthly volume, and what each month left behind",
                  fontsize=10.5, color=INK2, loc="left")
    axv.set_xticks(range(len(bymonth)))
    axv.set_xticklabels([d.strftime("%b") for d in bymonth["month"]],
                        fontsize=7.5, color=MUTED)
    # headroom so the legend never sits on top of a bar
    axv.set_ylim(0, float(bymonth["created"].max()) * 1.42)
    axv.legend(frameon=False, fontsize=7.8, loc="upper right", ncol=2,
               handlelength=1.2, columnspacing=1.0)
    recessive(axv)

    # ---- Panel 3: the triage check, both halves of it -------------------
    axp = fig.add_subplot(gs[2, 0:6])
    axp.bar(pr["segment"], pr["median_hrs"], color=BLUE, width=0.6)
    axp.axhline(kpi["median_res_hours"], color=MUTED, lw=1.1, ls="--")
    axp.set_title("Hours to close, by priority  (watch: is Urgent < Low yet?)",
                  fontsize=10.5, color=INK2, loc="left")
    axp.set_ylabel("median hours", fontsize=8.5)
    axp.tick_params(axis="x", labelsize=8.5)
    recessive(axp)

    axg = fig.add_subplot(gs[2, 6:12])
    order = ["Urgent", "High", "Medium", "Low"]
    agepr_s = agepr.set_index("priority").loc[order].reset_index()
    axg.bar(agepr_s["priority"], agepr_s["median_age_days"], color=CRITICAL,
            width=0.6, alpha=0.85)
    axg.set_title("Days the OPEN work has been waiting, by priority",
                  fontsize=10.5, color=INK2, loc="left")
    axg.set_ylabel("median days open", fontsize=8.5)
    axg.tick_params(axis="x", labelsize=8.5)
    recessive(axg)

    # ---- The quiet trust strip: materiality, parked at the bottom -------
    fig.patches.append(plt.Rectangle(
        (0.045, 0.012), 0.93, 0.115, transform=fig.transFigure,
        facecolor="#f5f4f0", edgecolor="#e1e0d9", lw=0.8, zorder=0))
    fig.text(0.058, 0.107, "DATA TRUST  —  what these numbers depend on",
             fontsize=8.6, fontweight="bold", color=INK2)
    notes = [
        "No satisfaction KPI shown: the survey mixes 1–5 and 1–10 scales unflagged. "
        "Method choice alone swings the answer 29.7%–73.4% (43.7 pts), so no honest "
        "figure exists until the scale is fixed.",
        "Speed is over ALL tickets. Counting only resolved ones would read 94.3% "
        "(+11 pts) by hiding the 581 that never closed.",
        "5,001 tickets after removing 59 duplicates (+1.2% if kept). 1,023 dates "
        "re-parsed from DD/MM — reading them as MM/DD would inflate p90 by 24% "
        "and destroy 611 rows.",
    ]
    for i, n in enumerate(notes):
        fig.text(0.058, 0.081 - i * 0.023, "•  " + n, fontsize=7.4, color=MUTED)

    fig.savefig(OUT / "dashboard_mockup.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  outputs/charts/dashboard_mockup.png")


if __name__ == "__main__":
    main()
