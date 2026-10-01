"""Measure duplicate thresholds against an existing dsdig corpus (no extraction).

Usage: PYTHONPATH=src python tools/rds_vgs_duplicate_calibration.py \
    --batch /path/to/batch_all_v5 --out /path/to/calibration.json
The tests additionally modify and re-extract a real source PDF's temperature.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import statistics
from pathlib import Path

import cv2
import numpy as np

from datasheet_chart_digitizer import rdson_gate_voltage_duplicates as dup


def run(batch: Path) -> dict:
    manifest = batch / 'rdson_gate_voltage.json'
    rows = json.loads(manifest.read_text())['panels']
    positives, other, times = [], [], []
    for pdf in sorted({r['pdf'] for r in rows}):
        group = [r for r in rows if r['pdf'] == pdf]
        if len(group) < 2:
            continue
        _, audit = dup.deduplicate_pdf(copy.deepcopy(group), Path(pdf), batch)
        times.append(audit['seconds'])
        for a, b in itertools.combinations(group, 2):
            check = dup.compare_panels(a, b, batch)
            raw = 0.0
            # Diagnostic only: same-voltage residual, with no x allowance.
            for c, d in zip(a['curves'], b['curves']):
                p, q = np.array(c['points']), np.array(d['points'])
                for s, t in ((p,q),(q,p)):
                    overlap = s[(s[:,0] >= t[0,0]) & (s[:,0] <= t[-1,0])]
                    raw = max(raw, dup._directed_value_diff(overlap,t,0.0))
            positives.append(check | {'raw_same_vgs_max_relative_diff':raw})
    for a, b in itertools.combinations(rows, 2):
        if a['pdf'] == b['pdf']:
            continue
        score = dup.visual_evidence(a,b,batch)['visual_score']
        other.append({'parts':[a['part'],b['part']], 'visual_score':score})
    other.sort(key=lambda r:r['visual_score'], reverse=True)
    source = next(r for r in rows if r['part']=='CSD17310Q5A_TI' and r['page']==1)
    twin = next(r for r in rows if r['part']==source['part'] and r['page']!=1)
    negatives = []
    for field, value in [('id_a',21), ('temperature_c',150), ('temperature_kind','Tj')]:
        bad = copy.deepcopy(twin)
        bad['curves'][0][field] = value
        negatives.append({'mutation':field, **dup.compare_panels(source,bad,batch)})
    for factor in [1.02, 2, 10000]:
        bad = copy.deepcopy(twin)
        bad['curves'][0]['points'][-1][1] *= factor
        negatives.append({'mutation':f'last_sample_x{factor}', **dup.compare_panels(source,bad,batch)})
    # Reflect the actual source plot, with unchanged numeric extraction, to
    # isolate the visual guard. No new source value is inferred from pixels.
    ink, _ = dup._plot_ink(source,batch)
    a, b = ink.astype(float), np.fliplr(ink).astype(float)
    a,b = a-a.mean(),b-b.mean()
    reflected_score = float(np.sum(a*b)/np.sqrt(np.sum(a*a)*np.sum(b*b)))
    return {'input_manifest':str(manifest), 'input_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'thresholds':{'visual_min':dup.VISUAL_MIN,'resistance_relative':dup.VALUE_REL_TOL,
                          'curve_x_fraction_of_span':dup.X_SPAN_TOL},
            'same_pdf_pairs':positives,'different_pdf_visual_pairs':other,
            'constructed_data_conflicts':negatives,'reflected_real_plot_score':reflected_score,
            'duplicate_check_seconds':{'sum':sum(times),'median':statistics.median(times),'max':max(times)},
            'note':'Cross-PDF scores calibrate appearance only; production never merges across PDFs.'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--batch',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.batch)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(f"same-PDF pairs {len(result['same_pdf_pairs'])}; cross-PDF controls "
          f"{len(result['different_pdf_visual_pairs'])}; reflected score {result['reflected_real_plot_score']:.6f}; "
          f"runtime {result['duplicate_check_seconds']}")
