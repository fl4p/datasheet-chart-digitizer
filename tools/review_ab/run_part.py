"""Run every dsdig chart class on one PDF (production entry points, annotate-equivalent + RR + leakage).

usage: run_part.py SRC PDF WORKDIR
Writes WORKDIR/raw_results.json (every class result / refusal, JSON-safe) and per-class artifacts.
"""
import json, sys, time, traceback, dataclasses
from pathlib import Path
SRC = str(Path(sys.argv[1]).resolve())
sys.path.insert(0, SRC)
import datasheet_chart_digitizer as pkg
assert pkg.__file__.startswith(SRC), pkg.__file__
from datasheet_chart_digitizer import (mosfet_capacitance, transfer_characteristics, breakdown_voltage,
    diode_forward_voltage, rdson_current, rdson_temperature, diode_reverse_leakage, reverse_recovery)
from datasheet_chart_digitizer.find_charts import process_pdf, write_outputs, asdict
from datasheet_chart_digitizer.gate_charge import digitize_gate_charge_fail_closed

pdf = Path(sys.argv[2]).resolve()
work = Path(sys.argv[3]).resolve(); work.mkdir(parents=True, exist_ok=True)
DPI = 220
import os
ONLY = set(filter(None, os.environ.get('A50_CLASSES', '').split(',')))
def want(c): return not ONLY or c in ONLY
def safe(o):
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {k: safe(v) for k, v in dataclasses.asdict(o).items()}
    if isinstance(o, dict): return {str(k): safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [safe(v) for v in o]
    if isinstance(o, float) and o != o: return None
    if isinstance(o, (str, int, float, bool)) or o is None: return o
    if hasattr(o, 'tolist'): return safe(o.tolist())
    if hasattr(o, '__dict__'): return {'__type__': type(o).__name__, **{k: safe(v) for k, v in vars(o).items() if not k.startswith('_')}}
    return repr(o)
out = {'pdf': str(pdf), 'src': SRC, 'dpi': DPI, 'panels': [], 'results': [], 'errors': [], 'timing': {}}
t0 = time.time()
panels = process_pdf(pdf, work, DPI) if (ONLY - {'gate_charge', 'reverse_recovery'} or not ONLY) else []
write_outputs(work, panels)
out['panels'] = [asdict(p) for p in panels]
out['timing']['find'] = time.time() - t0
for panel in panels:
    chart = asdict(panel); crop = work / panel.crop_png; stem = Path(panel.crop_png).with_suffix('')
    cls = {'capacitances': 'capacitance', 'transfer': 'transfer', 'breakdown_voltage': 'breakdown'}.get(panel.kind)
    if not cls or not want(cls): continue
    t = time.time()
    try:
        if cls == 'capacitance':
            r = mosfet_capacitance.process_chart(chart, crop, work, stem, pdf.parent)
        elif cls == 'transfer':
            r = transfer_characteristics.process_chart(chart, crop, work, stem, None)
        else:
            r = breakdown_voltage.process_chart(chart, crop, work, stem)
        out['results'].append({'class': cls, 'page': panel.page, 'diagram': panel.diagram, 'panel': chart, 'result': safe(r)})
    except Exception as e:
        out['errors'].append({'class': cls, 'page': panel.page, 'diagram': panel.diagram, 'error': str(e)[:800], 'tb': traceback.format_exc()[-1200:]})
    out['timing'][f'{cls}_p{panel.page}_d{panel.diagram}'] = time.time() - t
def fam(cls, fn):
    if not want(cls): return
    t = time.time()
    try:
        res, errs = fn()
        for r in res:
            p = r.get('panel', {})
            out['results'].append({'class': cls, 'page': p.get('page'), 'diagram': p.get('diagram'), 'panel': p, 'result': safe(r)})
        for e in errs:
            out['errors'].append({'class': cls, **safe(e)})
    except Exception as e:
        out['errors'].append({'class': cls, 'page': None, 'diagram': None, 'error': str(e)[:800], 'tb': traceback.format_exc()[-1200:]})
    out['timing'][cls] = time.time() - t
fam('body_diode', lambda: diode_forward_voltage.digitize_panels_fail_closed(panels, work))
fam('rds_on_current', lambda: rdson_current.digitize_pdf_fail_closed(pdf, work, DPI))
fam('rds_on_temperature', lambda: rdson_temperature.digitize_pdf_fail_closed(pdf, work, DPI))
fam('reverse_leakage', lambda: diode_reverse_leakage.digitize_panels_fail_closed(panels, work))
t = time.time()
try:
    if not want('gate_charge'): raise StopIteration
    gres, gerr = digitize_gate_charge_fail_closed(pdf, dpi=DPI, finder_dpi=DPI)
    for g in gres:
        out['results'].append({'class': 'gate_charge', 'page': g.panel.page, 'diagram': g.panel.diagram,
                               'panel': asdict(g.panel), 'result': safe(g)})
    for e in gerr: out['errors'].append({'class': 'gate_charge', **safe(e)})
except StopIteration:
    pass
except Exception as e:
    out['errors'].append({'class': 'gate_charge', 'error': str(e)[:800], 'tb': traceback.format_exc()[-1200:]})
out['timing']['gate_charge'] = time.time() - t
t = time.time()
try:
    if not want('reverse_recovery'): raise StopIteration
    for r in reverse_recovery.digitize_pdf(pdf, work / 'rr'):
        if 'skip' in r: out['rr_skip'] = r['skip']; continue
        if 'error' in r: out['errors'].append({'class': 'reverse_recovery', 'page': r.get('page'), 'diagram': r.get('number'), 'error': r['error']}); continue
        out['results'].append({'class': 'reverse_recovery', 'page': r['page'], 'diagram': r['number'], 'panel': None, 'result': safe(r)})
except StopIteration:
    pass
except Exception as e:
    out['errors'].append({'class': 'reverse_recovery', 'error': str(e)[:800], 'tb': traceback.format_exc()[-1200:]})
out['timing']['reverse_recovery'] = time.time() - t
out['timing']['total'] = time.time() - t0
(work / 'raw_results.json').write_text(json.dumps(out, indent=1, default=repr))
print('panels', len(panels), 'results', len(out['results']), 'errors', len(out['errors']), 'total %.0fs' % out['timing']['total'])
