# review_ab: same-host A/B of served chart output

Used by the astra-review-50 pass (out/astra-review-50/). `run_part.py SRC PDF WORK` runs
every chart class on one PDF against the package at SRC (asserted) and writes
raw_results.json. `hv_ab.py` runs it over a key of `$DSDIG_REVIEW_OUT/tests/hv.json`
for one side; `hv_cmp.py A B OUT.json` compares two run trees chart by chart
(status, served curves in % of span, Vpl). A part or chart present on one side only,
or a failed run, makes the comparison INCOMPLETE and the exit code 2: missing
evidence is never reported as "0 changes".
