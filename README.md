# Support Performance Analysis
Read full project details from my technical design doc: https://docs.google.com/document/d/18n-f061WSxgJf8trs7hmAv-0kxH0h0BqTfQYEokYTAs/edit?usp=sharing

Analysis of ~5,000 cross-brand support tickets: how many, how fast, how customers feel, and where to focus.


## How to run

```bash
pip install duckdb matplotlib scipy
python src/clean.py       # build the clean dataset (+ QA report)   -> data/clean/
python src/analysis.py    # metrics, analysis cuts, significance tests -> outputs/tables/
python src/charts.py      # render all figures + dashboard mockup   -> outputs/charts/
```

The primary engine is **DuckDB** (reads the CSVs directly, zero setup). All analysis logic lives in
`sql/`; Python only orchestrates and draws. 

## Layout

```
data/raw/     original CSVs (untouched)
data/clean/   cleaned, reproducible dataset (tickets_clean.parquet/csv)
sql/          canonical logic (DuckDB):
              01_stage_clean · 02_metrics · 02b_validation · 02c_materiality · 03_analysis
src/          plain-Python runners: clean.py · analysis.py · charts.py
outputs/      tables/ (CSV cuts) · charts/ (PNGs + dashboard mockup)
```


**Charts, and what each one is for:**
The charts are generated only after running `src/charts.py`
| Chart | Shows |
|---|---|
| `01_satisfaction_scale_cliff.png` | the 5→6 cliff that reveals two mixed scales |
| `02_resolution_distribution.png` | resolution times, why median + p90 |
| `03_resolution_by_priority.png` | priority isn't changing speed |
| `04_sentiment_uniformity.png` | no segment stands out |
| `05_monthly_trend.png` | volume and sentiment flat over 12 months |
| `06_backlog_age.png` | **the headline** — half the backlog is 6+ months old |
| `07_satisfaction_method_swing.png` | **the materiality** — 29.7%–73.4% from method choice alone |
| `08_scale_detector.png` | **teaching aid** — the cutover we'd expect vs the mixture we found |
| `09_detector_by_dimension.png` | the scale detector across all 15 brand/channel/team/location groups, with 95% CIs |
| `dashboard_mockup.png` | the one-screen CS-ops dashboard |

 


