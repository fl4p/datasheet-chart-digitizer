"""hv_ab.py SRC LABEL CLASS[,CLASS] HVKEY [JOBS]  (DSDIG_REVIEW_OUT=<dir>, default out/astra-review-50): run run_part (restricted classes) over tests/hv.json[HVKEY] into tests/hv-<LABEL>/<mfr>__<part>/.
Resumable (skips dirs with raw_results.json)."""
import json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
# Output tree (holds tests/hv.json and the per-side run dirs).
OUT = Path(os.environ.get('DSDIG_REVIEW_OUT', Path(__file__).resolve().parents[2] / 'out' / 'astra-review-50'))
SRC, LABEL, CLS, KEY = sys.argv[1:5]
JOBS = int(sys.argv[5]) if len(sys.argv) > 5 else 5
PY = sys.executable
rows = json.load(open(OUT / 'tests/hv.json'))[KEY]
def one(r):
    w = OUT / f'tests/hv-{LABEL}-{CLS.replace(",", "+")}' / f"{r['mfr']}__{r['part']}"
    if (w / 'raw_results.json').exists(): return r['part'], 'skip'
    w.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'PYTHONPATH': '', 'A50_CLASSES': CLS, 'OMP_NUM_THREADS': '2'}
    try:
        p = subprocess.run([PY, str(Path(__file__).resolve().parent / 'run_part.py'), SRC, r['pdf'], str(w)], capture_output=True, text=True, timeout=900, env=env)
        rc = p.returncode; (w / 'run.log').write_text((p.stdout + p.stderr)[-3000:])
    except subprocess.TimeoutExpired:
        rc = 'timeout'; (w / 'run.log').write_text('timeout')
    return r['part'], rc
bad = []
with ThreadPoolExecutor(JOBS) as ex:
    for x in ex.map(one, rows):
        print(*x, flush=True)
        if x[1] not in (0, 'skip'):
            bad.append(x)
# A failed or timed-out run is missing evidence, never a pass.
print('ALLDONE', 'failed_runs', len(bad), [b[0] for b in bad], flush=True)
sys.exit(0 if not bad else 2)
