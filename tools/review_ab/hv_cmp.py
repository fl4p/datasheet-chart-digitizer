"""hv_cmp.py DIR_A DIR_B OUT.json [--served-only]: compare two run trees chart by chart.

Per chart (class,page,diagram): status A->B, Vpl shift (% of VGS span), served-curve shift =
max over curves of the symmetric nearest-point distance in axis-normalised coords (log axes in
decades), in % of span; plus tick-set / plot-box changes. Reports every chart whose served output
changed > 0.5 % of span, and every status change."""
import json, math, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize import normalize

A, B, OUTF = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
SERVED = {'ok', 'pass', 'verified'}

def f(ax, v):
    if v is None: return None
    if ax['model'] == 'log10':
        return math.log10(v) if v > 0 else None
    return v

def span(ax):
    t = [f(ax, v) for v in ax.get('ticks') or []]
    t = [v for v in t if v is not None]
    return (max(t) - min(t)) if len(t) >= 2 else None

def curve_arr(c, xa, ya):
    pts = [(f(xa, a), f(ya, b)) for a, b in c.get('data', []) if a is not None and b is not None]
    pts = [(a, b) for a, b in pts if a is not None and b is not None]
    return np.array(pts, float) if pts else np.zeros((0, 2))

def shift(ca, cb):
    xa, ya = (next((a for a in ca['axes'] if a['orient'] == o), None) for o in ('x', 'y'))
    xb, yb = (next((a for a in cb['axes'] if a['orient'] == o), None) for o in ('x', 'y'))
    if not (xa and ya and xb and yb): return None, 'axes missing'
    sx, sy = span(xa), span(ya)
    if not sx or not sy: return None, 'no span'
    labs = {c['label'] for c in ca['curves']} & {c['label'] for c in cb['curves']}
    worst, detail = 0.0, {}
    for lab in sorted(labs):
        pa = curve_arr(next(c for c in ca['curves'] if c['label'] == lab), xa, ya)
        pb = curve_arr(next(c for c in cb['curves'] if c['label'] == lab), xb, yb)
        if len(pa) < 2 or len(pb) < 2: continue
        na, nb = pa / [sx, sy], pb / [sx, sy]
        step = max(1, len(na) // 400)
        d1 = np.sqrt(((na[::step, None, :] - nb[None, ::max(1, len(nb) // 400), :]) ** 2).sum(-1)).min(1)
        d2 = np.sqrt(((nb[::step, None, :] - na[None, ::max(1, len(na) // 400), :]) ** 2).sum(-1)).min(1)
        e = float(max(d1.max(), d2.max())) * 100
        detail[lab] = round(e, 3)
        worst = max(worst, e)
    only = sorted(({c['label'] for c in ca['curves']} ^ {c['label'] for c in cb['curves']}))
    return worst, {'per_curve': detail, 'curve_set_diff': only}

def charts(d):
    if not (d / 'raw_results.json').exists(): return None
    raw, cs, probs = normalize(d)
    out = {(c['class'], c['page'], c['diagram']): c for c in cs}
    for e in raw['errors']:
        out.setdefault((e.get('class'), e.get('page'), e.get('diagram')), {'status': 'error', 'error': str(e.get('error'))[:200], 'axes': [], 'curves': [], 'scalars': {}})
    return out

rows = []
names = sorted({p.name for p in A.iterdir() if p.is_dir()} | {p.name for p in B.iterdir() if p.is_dir()})
for name in names:
    da, db = A / name, B / name
    ca = charts(da) if da.exists() else None
    cb = charts(db) if db.exists() else None
    if ca is None or cb is None:
        side = 'A+B' if ca is None and cb is None else ('A' if ca is None else 'B')
        rows.append({'part': name, 'missing_side': side}); continue
    for k in sorted(set(ca) | set(cb), key=str):
        a, b = ca.get(k), cb.get(k)
        r = {'part': name, 'chart': list(k), 'status_a': (a or {}).get('status'), 'status_b': (b or {}).get('status')}
        if a is None or b is None:
            r['missing_chart_side'] = 'A' if a is None else 'B'
        if a and b and a.get('curves') and b.get('curves'):
            s_, det = shift(a, b); r['served_shift_pct_span'] = None if s_ is None else round(s_, 3); r['detail'] = det
        if a and b and k[0] == 'gate_charge':
            va, vb = a['scalars'].get('vpl_raw'), b['scalars'].get('vpl_raw')
            ya = next((x for x in a['axes'] if x['orient'] == 'y'), None)
            if va is not None and vb is not None and ya and span(ya):
                r['vpl_a'], r['vpl_b'] = va, vb
                r['vpl_shift_pct_span'] = round(abs(vb - va) / span(ya) * 100, 3)
        for side, c in (('a', a), ('b', b)):
            if c: r[f'diag_{side}'] = c.get('status_reasons') or c.get('diagnostics') or c.get('error')
        rows.append(r)
changed = [r for r in rows if r.get('missing_side') or r.get('missing_chart_side') or r.get('status_a') != r.get('status_b')
           or (r.get('served_shift_pct_span') or 0) > 0.5 or (r.get('vpl_shift_pct_span') or 0) > 0.5]
failures = [r for r in rows if r.get('missing_side') or r.get('missing_chart_side')]
summary = {'parts': len(names), 'charts': sum(1 for r in rows if 'chart' in r),
           'status_changes': sum(1 for r in rows if 'chart' in r and not r.get('missing_chart_side') and r.get('status_a') != r.get('status_b')),
           'served_shift_gt_0p5': sum(1 for r in rows if (r.get('served_shift_pct_span') or 0) > 0.5),
           'vpl_shift_gt_0p5': sum(1 for r in rows if (r.get('vpl_shift_pct_span') or 0) > 0.5),
           'missing_parts': sum(1 for r in rows if r.get('missing_side')),
           'missing_charts': sum(1 for r in rows if r.get('missing_chart_side')),
           'comparison_complete': not failures}
json.dump({'summary': summary, 'failures': failures, 'changed': changed, 'rows': rows}, open(OUTF, 'w'), indent=1, default=str)
print(summary)
for r in failures[:20]:
    print('INCOMPLETE', r['part'], r.get('chart'), 'missing side', r.get('missing_side') or r.get('missing_chart_side'))
for r in changed:
    if r in failures: continue
    print(r['part'], r.get('chart'), r.get('status_a'), '->', r.get('status_b'), 'shift', r.get('served_shift_pct_span'), 'vpl', r.get('vpl_a'), r.get('vpl_b'))
# Unevaluable is not OK: an incomplete comparison is a failed comparison.
sys.exit(0 if not failures else 2)
