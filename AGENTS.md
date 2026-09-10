# AGENTS.md — ili_historical_analog_forecast

Independent research project. Scope is this repository only.

## Hard rules

1. **v1 algorithm is frozen.** Segment normalisation is division by the segment's own last
   week; the score is the mean absolute difference of the two normalised 8-week segments; a
   single nearest segment is selected, ties broken toward the earlier candidate end week;
   `prediction[h] = x[8] * y_future[h] / y[8]`. Do not add z-score normalisation, DTW, time
   warping, top-k or multi-segment averaging, recent-trend blending, manual reshaping,
   smoothing, injected noise or forced upward drift.
2. **Never use a future actual** to select or adjust a segment. Forecast generation and
   scoring stay in separate phases.
3. **DIM is the only week authority** (`DIM_DATA.public.dim_weekdate`). Never ISO week,
   `pandas.resample("W")`, a fixed weekday, `origin + h * 7`, a fixed 7-day slice, a fixed
   56-day horizon, or `yearweek + 1`.
4. **Never zero-fill** a missing, NULL or uncovered cell, and never build a national total
   from part of the counties or one source. Report `INELIGIBLE` instead of degrading.
5. **No hard-coded credentials.** Connections go through the installed `eic_utils`
   (`conn.deco.postgres(dbname=...)`) with the existing `?` placeholder contract.
6. **No LLM, no TimesFM, no GPU.** TimesFM is never invoked from this repository; an
   external forecast CSV may be compared, otherwise the comparison is `NOT AVAILABLE`.
7. **Not a router candidate.** This model must not be added to the
   `disease_forecast_llm_model_router` production candidate pool.
8. **Honest labelling.** 2026 backtests are `exploratory retrospective evaluation` only,
   never an untouched holdout or promotion evidence. Every run records
   `current database snapshot; not historical as-of replay`.
9. **Spring Festival policy is denominator-week exclusion only** and requires explicit user
   confirmation in `configs/spring_festival.json`. Never introduce a data-derived
   "anomalous week" threshold.
10. **Raw floats on disk**; round only inside figures. No confidence or prediction interval
    while v1 has no probabilistic model.

## Verification

The DB, model and pytest runs happen on the server, executed by the user. Do not connect
over SSH and do not run database queries from here. Commit code, docs and configs only —
never data extracts, generated outputs, figures or credentials.
