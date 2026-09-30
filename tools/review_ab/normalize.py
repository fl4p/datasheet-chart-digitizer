"""Normalize raw_results.json of every class into chart records in PDF-POINT coordinates.

Every served axis becomes  model_coord(value) = A * pt + B  (model_coord = value or log10 value),
reconstructed from the class's own served mapping (m/b in crop px, or per-point pt/value).
Curves carry the served (x, y) values and the PDF-point positions they were read from.

Pixel conventions (per class, from the calibration-audit notes and the per-class code):
  capacitance, gate_charge (curve/x ticks): CONTINUOUS crop px  -> pt = x0 + px / s
  everything else, gate y_grid served px:   INDEX crop px       -> pt = x0 + (px + 0.5) / s
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

UNITS = {
    'capacitance': ('VDS', 'V', 'C', 'pF'),
    'transfer': ('VGS', 'V', 'ID', 'A'),
    'rds_on_temperature': ('T (junction or case, per source title)', 'degC', 'RDS(on) (norm or abs, per source title)', ''),
    'rds_on_current': ('ID', 'A', 'RDS(on)', 'mOhm'),
    'body_diode': ('VSD', 'V', 'IS', 'A'),
    'reverse_leakage': ('VR', 'V', 'IR', 'A'),
    'breakdown': ('T (per source title)', 'degC', 'V(BR)DSS', 'V'),
    'gate_charge': ('Qg', 'nC', 'VGS', 'V'),
    'reverse_recovery': ('x', '', 'y', ''),
}


class Frame:
    """crop px <-> page pt for one crop."""

    def __init__(self, crop_box_pt, size_px, convention):
        self.x0, self.y0, x1, y1 = [float(v) for v in crop_box_pt]
        w, h = size_px
        self.sx = w / (x1 - self.x0)
        self.sy = h / (y1 - self.y0)
        self.c = 0.5 if convention == 'index' else 0.0
        self.convention = convention

    def pt(self, px, py):
        return self.x0 + (px + self.c) / self.sx, self.y0 + (py + self.c) / self.sy

    def ptx(self, px):
        return self.x0 + (px + self.c) / self.sx

    def pty(self, py):
        return self.y0 + (py + self.c) / self.sy


def _img_size(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def axis_from_px(model, m, b, frame, orient):
    """coord = m*px + b  ->  coord = A*pt + B."""
    if orient == 'x':
        s, o = frame.sx, frame.x0
    else:
        s, o = frame.sy, frame.y0
    # px = (pt - o) * s - c
    A = m * s
    B = b + m * (-o * s - frame.c)
    return {'model': model, 'A': A, 'B': B}


def _coord(model, v):
    return math.log10(v) if model == 'log10' else v


def served_pt(ax, v):
    try:
        return (_coord(ax['model'], v) - ax['B']) / ax['A']
    except (ValueError, ZeroDivisionError):
        return None


def value_at(ax, pt):
    c = ax['A'] * pt + ax['B']
    return 10 ** c if ax['model'] == 'log10' else c


def _numeric_axis(d, frame, orient, name, unit, gc=None):
    ax = axis_from_px(d['model'], d['m'], d['b'], frame, orient)
    ticks = [t['value'] for t in d.get('ticks', [])]
    if not ticks and gc:
        ticks = [t['value'] for t in gc.get('ticks', [])]
    ax.update(name=name, orient=orient, unit=unit, ticks=ticks,
              grid_check=(gc or {}).get('status'), grid_reason=(gc or {}).get('reason'))
    return ax


def _status(r):
    return str(r.get('status'))


def norm_capacitance(rec, work):
    R = rec['result']
    panel = rec['panel']
    crop = Path(R['crop'])
    frame = Frame(panel['crop_box_pt'], _img_size(crop), 'continuous')
    out = base(rec, frame, crop)
    ac = R.get('axis_calibration')
    out['status_reasons'] = R.get('status_reasons')
    out['trusted'] = R.get('axis_calibration_trusted')
    out['physical_output_available'] = R.get('physical_output_available')
    out['axis_error'] = R.get('axis_error')
    if R.get('shared_collapse_spans'):
        out['scalars']['shared_collapse_spans'] = '; '.join(f"{'='.join(sp['curves'])} over crop x {sp['x0_px']}-{sp['x1_px']} px (flagged shared)" for sp in R['shared_collapse_spans'])
    pts = []
    if R.get('points') and (work / R['points']).exists():
        pts = list(csv.DictReader(open(work / R['points'])))
    curves = {}
    for p in pts:
        c = curves.setdefault(p['trace'], {'label': p['trace'], 'pts_pt': [], 'data': []})
        x, y = frame.pt(float(p['x_px']), float(p['y_px']))
        c['pts_pt'].append((x, y))
        c['data'].append((float(p['vds_V']) if p.get('vds_V') else None, float(p['cap_pF']) if p.get('cap_pF') else None))
    out['curves'] = list(curves.values())
    if ac:
        xm = 'log10' if ac.get('x_log') else 'linear'
        ym = 'log10' if ac.get('y_log') else 'linear'
        xa = axis_from_px(xm, ac['x_scale'], ac['x_offset'], frame, 'x')
        ya = axis_from_px(ym, ac['y_scale'], ac['y_offset'], frame, 'y')
        xa.update(name='x', orient='x', unit='V', ticks=list(ac.get('x_ticks_v') or []),
                  grid_check=(ac.get('x_grid_check') or {}).get('status') if isinstance(ac.get('x_grid_check'), dict) else ac.get('x_grid_check'),
                  source=ac.get('x_source'))
        yt = [10 ** d for d in ac.get('y_decades') or []] if ac.get('y_log') else list(ac.get('y_ticks_pf') or [])
        ya.update(name='y', orient='y', unit='pF', ticks=yt,
                  grid_check=(ac.get('y_grid_check') or {}).get('status') if isinstance(ac.get('y_grid_check'), dict) else ac.get('y_grid_check'),
                  source=ac.get('y_source'))
        out['axes'] = [xa, ya]
    if R.get('plot_box_px'):
        pb = R['plot_box_px']
        out['plot_box_pt'] = [*frame.pt(pb[0], pb[1]), *frame.pt(pb[2], pb[3])]
    out['served'] = bool(ac) and bool(R.get('physical_output_available'))
    return out


def base(rec, frame, crop):
    panel = rec.get('panel') or {}
    return {'class': rec['class'], 'page': rec['page'], 'diagram': rec['diagram'], 'status': _status(rec['result']),
            'title': panel.get('title'), 'crop_png': str(crop), 'crop_box_pt': list(frame_box(frame, crop)),
            'px_per_pt': frame.sx, 'px_convention': frame.convention, 'axes': [], 'curves': [], 'scalars': {}}


def frame_box(frame, crop):
    w, h = _img_size(crop)
    return (frame.x0, frame.y0, frame.x0 + w / frame.sx, frame.y0 + h / frame.sy)


def norm_numeric_family(rec, work):
    """body_diode, rds_on_current, rds_on_temperature, reverse_leakage."""
    R = rec['result']
    panel = R['panel']
    crop = work / panel['crop_png']
    frame = Frame(panel['crop_box_pt'], _img_size(crop), 'index')
    out = base(rec, frame, crop)
    out['diagnostics'] = R.get('diagnostics')
    out['binding_error'] = R.get('binding_error')
    xu, xunit, yu, yunit = UNITS[rec['class']]
    gc = R.get('grid_check') or {}
    if R.get('x_axis') and R.get('y_axis'):
        out['axes'] = [_numeric_axis(R['x_axis'], frame, 'x', xu, xunit, gc.get('x')),
                       _numeric_axis(R['y_axis'], frame, 'y', yu, yunit, gc.get('y'))]
    pb = R.get('plot_box_px')
    if isinstance(pb, dict):
        out['plot_box_pt'] = [*frame.pt(pb['x0'], pb['y0']), *frame.pt(pb['x1'], pb['y1'])]
    for c in R.get('curves') or []:
        lab = []
        for k in ('temperature_c', 'gate_voltage_v', 'label', 'series', 'identity', 'role'):
            if c.get(k) is not None:
                lab.append(f"{k}={c[k]}")
        pp = c.get('points_px') or []
        data = c.get('points') or []
        out['curves'].append({'label': ' '.join(lab) or '?', 'pts_pt': [frame.pt(x, y) for x, y in pp],
                              'data': [tuple(d) if isinstance(d, (list, tuple)) else (d.get('x'), d.get('y')) for d in data],
                              'n_px': len(pp), 'n_data': len(data)})
    if R.get('unit'):
        out['scalars']['unit'] = R.get('unit')
    for k in ('reference_temperature_c', 'hint_source'):
        if R.get(k) is not None:
            out['scalars'][k] = R[k]
    out['served'] = out['status'] in ('ok', 'unverified', 'verified', 'pass') and bool(out['curves']) and bool(out['axes'])
    return out


def norm_transfer(rec, work):
    R = rec['result']
    panel = rec['panel']
    crop = work / panel['crop_png']
    frame = Frame(panel['crop_box_pt'], _img_size(crop), 'index')
    out = base(rec, frame, crop)
    out['diagnostics'] = R.get('warnings')
    cal = R.get('calibration') or {}
    gc = cal.get('grid_check') or {}
    if cal.get('x_axis') and cal.get('y_axis'):
        out['axes'] = [_numeric_axis(cal['x_axis'], frame, 'x', 'VGS', 'V', gc.get('x')),
                       _numeric_axis(cal['y_axis'], frame, 'y', 'ID', 'A', gc.get('y'))]
    if R.get('plot_box_px') or cal.get('plot_box_px'):
        pb = R.get('plot_box_px') or cal.get('plot_box_px')
        pb = [pb['x0'], pb['y0'], pb['x1'], pb['y1']] if isinstance(pb, dict) else pb
        out['plot_box_pt'] = [*frame.pt(pb[0], pb[1]), *frame.pt(pb[2], pb[3])]
    if R.get('csv') and Path(R['csv']).exists() and out['axes']:
        xa, ya = out['axes']
        curves = {}
        for row in csv.DictReader(open(R['csv'])):
            c = curves.setdefault(row['Tj_C'], {'label': f"Tj={row['Tj_C']}C", 'pts_pt': [], 'data': []})
            vx, vy = float(row['Vgs_V']), float(row['Id_A'])
            c['data'].append((vx, vy))
            px = served_pt(xa, vx)
            py = served_pt(ya, vy) if (ya['model'] != 'log10' or vy > 0) else None
            c['pts_pt'].append((px, py))
        out['curves'] = list(curves.values())
        out['curves_from'] = 'served values inverted through the served mapping (transfer CSV has no pixel columns)'
    out['served'] = bool(out['curves']) and out['status'] not in ('refused',)
    return out


def norm_breakdown(rec, work):
    R = rec['result']
    panel = rec['panel']
    crop = work / panel['crop_png']
    frame = Frame(panel['crop_box_pt'], _img_size(crop), 'index')
    out = base(rec, frame, crop)
    out['diagnostics'] = R.get('warnings')
    cal = R.get('calibration') or {}
    gc = cal.get('grid_check') or {}
    for key, orient, name, unit in (('x', 'x', 'Tj', 'degC'), ('y', 'y', 'V(BR)DSS', 'V')):
        d = cal.get(f'{key}_axis') or cal.get(f'{key}_served')
        if d is None and cal.get(f'{key}_m') is not None:
            d = {'model': 'linear', 'm': cal[f'{key}_m'], 'b': cal[f'{key}_b']}
        if d is not None:
            out['axes'].append(_numeric_axis(d, frame, orient, name, unit, gc.get(key)))
    pb = R.get('plot_box_px') or cal.get('plot_box_px')
    if pb:
        pb = [pb['x0'], pb['y0'], pb['x1'], pb['y1']] if isinstance(pb, dict) else pb
        out['plot_box_pt'] = [*frame.pt(pb[0], pb[1]), *frame.pt(pb[2], pb[3])]
    if R.get('csv') and Path(R['csv']).exists() and len(out['axes']) == 2:
        rows = list(csv.reader(open(R['csv'])))
        head, rows = rows[0], rows[1:]
        c = {'label': 'V(BR)DSS', 'pts_pt': [], 'data': []}
        for row in rows:
            try:
                vx, vy = float(row[0]), float(row[1])
            except (ValueError, IndexError):
                continue
            c['data'].append((vx, vy))
            c['pts_pt'].append((served_pt(out['axes'][0], vx), served_pt(out['axes'][1], vy)))
        out['curves'] = [c]
        out['csv_header'] = head
    for k in ('fit', 'summary', 'anchor', 'spec_anchor'):
        if R.get(k) is not None:
            out['scalars'][k] = R[k]
    out['served'] = bool(out['curves']) and out['status'] not in ('refused',)
    return out


def norm_gate(rec, work, pdf):
    R = rec['result']
    import pymupdf
    cb = R['crop_box_pt']
    dpi = R.get('dpi') or 220
    s = dpi / 72.0
    w, h = round((cb[2] - cb[0]) * s), round((cb[3] - cb[1]) * s)

    class F(Frame):
        pass
    frame = Frame(cb, (w, h), 'continuous')
    frame_idx = Frame(cb, (w, h), 'index')
    out = {'class': 'gate_charge', 'page': rec['page'], 'diagram': rec['diagram'], 'status': _status(R),
           'title': (R.get('panel') or {}).get('title'), 'crop_png': None, 'crop_box_pt': cb, 'px_per_pt': s,
           'px_convention': 'continuous (curve, x ticks); index (y_grid served)', 'axes': [], 'curves': [], 'scalars': {}}
    out['diagnostics'] = R.get('diagnostics')
    pb = R.get('plot_box_px')
    if pb:
        out['plot_box_pt'] = [*frame.pt(pb[0], pb[1]), *frame.pt(pb[2], pb[3])]
    yg = (R.get('y_grid') or {})
    served = [(t['value'], t['served_px']) for t in (yg.get('anchoring') or {}).get('ticks', []) if t.get('served_px') is not None]
    ysrc = 'y_grid.anchoring served_px (index)'
    fr_y = frame_idx
    if len(served) < 2 and R.get('y_ticks_px'):
        served = [tuple(t) for t in R['y_ticks_px']]
        ysrc = 'y_ticks_px (continuous; serialized snapped ticks - NOT the served line)'
        fr_y = frame
    if len(served) >= 2:
        v = np.array([a for a, _ in served]); p = np.array([fr_y.pty(b) for _, b in served])
        A, B = np.polyfit(p, v, 1)
        out['axes'].append({'name': 'VGS', 'orient': 'y', 'unit': 'V', 'model': 'linear', 'A': float(A), 'B': float(B),
                            'ticks': [float(x) for x in v], 'source': ysrc,
                            'grid_check': (yg.get('grid_check') or {}).get('status') if isinstance(yg.get('grid_check'), dict) else yg.get('grid_check')})
    xt = R.get('x_ticks_px') or []
    if len(xt) >= 2:
        v = np.array([a for a, _ in xt]); p = np.array([frame.ptx(b) for _, b in xt])
        A, B = np.polyfit(p, v, 1)
        out['axes'].insert(0, {'name': 'Qg', 'orient': 'x', 'unit': R.get('x_tick_unit') or 'nC', 'model': 'linear', 'A': float(A), 'B': float(B),
                               'ticks': [float(x) for x in v], 'source': 'x_ticks_px (continuous; Qg is not a served quantity)'})
    cp = R.get('curve_px') or []
    if cp:
        pts = [frame.pt(x, y) for x, y in cp]
        data = []
        xa = next((a for a in out['axes'] if a['orient'] == 'x'), None)
        ya = next((a for a in out['axes'] if a['orient'] == 'y'), None)
        for x, y in pts:
            data.append((value_at(xa, x) if xa else None, value_at(ya, y) if ya else None))
        out['curves'] = [{'label': 'VGS(Qg)', 'pts_pt': pts, 'data': data}]
    out['scalars']['vpl_v'] = R.get('vpl') if out['status'] == 'ok' else None
    out['scalars']['vpl_raw'] = R.get('vpl')
    if R.get('vpl_y_px') is not None:
        out['scalars']['vpl_y_pt'] = frame.pty(R['vpl_y_px'])
    out['scalars']['trace_source'] = R.get('trace_source')
    out['scalars']['score'] = R.get('score')
    out['served'] = out['status'] == 'ok'
    return out


def norm_rr(rec, work, pdf):
    R = rec['result']
    out = {'class': 'reverse_recovery', 'page': rec['page'], 'diagram': rec['diagram'], 'status': str(R.get('scale')),
           'title': R.get('title'), 'crop_png': None, 'axes': [], 'curves': [], 'scalars': {}, 'diagnostics': R.get('warnings')}
    out['scalars']['conditions'] = R.get('conditions')
    out['scalars']['x_quantity'] = R.get('x_quantity')
    for key, orient in (('x_axis', 'x'), ('y_left', 'y'), ('y_right', 'y')):
        d = R.get(key)
        if not d:
            continue
        # value = m_per_pt * pt + b
        grid = d.get('grid') or {}
        ticks = [t.get('value') for t in (grid.get('ticks') or []) if isinstance(t, dict)] if isinstance(grid, dict) else []
        out['axes'].append({'name': key, 'orient': orient, 'unit': '', 'model': 'linear', 'A': d['m_per_pt'], 'B': d['b'],
                            'ticks': ticks, 'grid_check': grid.get('status') if isinstance(grid, dict) else None})
    for c in R.get('curves') or []:
        if not c.get('csv') or not Path(c['csv']).exists():
            out['curves'].append({'label': f"{c.get('quantity')} {c.get('temp_c')}C (no values: {c.get('error')})", 'pts_pt': [], 'data': []})
            continue
        rows = list(csv.reader(open(c['csv'])))[1:]
        data = [(float(a), float(b)) for a, b in rows]
        xa = out['axes'][0]
        ya = next((a for a in out['axes'] if a['name'] == c.get('axis')), None) or (out['axes'][1] if len(out['axes']) > 1 else None)
        pts = [(served_pt(xa, x), served_pt(ya, y) if ya else None) for x, y in data]
        out['curves'].append({'label': f"{c.get('quantity')} {c.get('temp_c')}C [{c.get('axis')}]", 'pts_pt': pts, 'data': data})
    out['served'] = bool(out['curves'])
    return out


def normalize(work: Path):
    raw = json.loads((work / 'raw_results.json').read_text())
    pdf = raw['pdf']
    charts, problems = [], []
    for rec in raw['results']:
        cls = rec['class']
        try:
            if cls == 'capacitance':
                c = norm_capacitance(rec, work)
            elif cls in ('body_diode', 'rds_on_current', 'rds_on_temperature', 'reverse_leakage'):
                c = norm_numeric_family(rec, work)
            elif cls == 'transfer':
                c = norm_transfer(rec, work)
            elif cls == 'breakdown':
                c = norm_breakdown(rec, work)
            elif cls == 'gate_charge':
                c = norm_gate(rec, work, pdf)
            elif cls == 'reverse_recovery':
                c = norm_rr(rec, work, pdf)
            else:
                continue
        except Exception as exc:  # normalizer gap is reported, never swallowed
            import traceback
            problems.append({'class': cls, 'page': rec.get('page'), 'diagram': rec.get('diagram'), 'normalize_error': repr(exc),
                             'tb': traceback.format_exc()[-800:]})
            continue
        c['pdf'] = pdf
        if not c.get('plot_box_pt'):
            xa = next((a for a in c['axes'] if a['orient'] == 'x' and a.get('ticks')), None)
            ya = next((a for a in c['axes'] if a['orient'] == 'y' and a.get('ticks')), None)
            if xa and ya:
                xs = [served_pt(xa, t) for t in xa['ticks']]; ys = [served_pt(ya, t) for t in ya['ticks']]
                xs = [v for v in xs if v is not None]; ys = [v for v in ys if v is not None]
                if xs and ys:
                    c['plot_box_pt'] = [min(xs), min(ys), max(xs), max(ys)]
                    c['plot_box_source'] = 'tick_span (extractor reports no plot box)'
        else:
            c.setdefault('plot_box_source', 'extractor plot_box_px')
        charts.append(c)
    return raw, charts, problems
