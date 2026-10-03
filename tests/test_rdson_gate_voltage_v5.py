"""Independent-review F01-F12: real producer output and constructed failures."""

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from datasheet_chart_digitizer import rdson_gate_voltage as rgv
from datasheet_chart_digitizer import rdson_gate_voltage_report as report
from datasheet_chart_digitizer import rdson_gate_voltage_evidence as evidence
from datasheet_chart_digitizer import rdson_gate_voltage_duplicates as duplicates
from datasheet_chart_digitizer.rdson_gate_voltage_conditions import typical_temperature_note, pulse_conditions
from datasheet_chart_digitizer.rdson_gate_voltage_traces import Trace
from datasheet_chart_digitizer.rdson_spec_table import parse_rdson_spec_rows


def panel(name, page, diagram):
    from test_rdson_gate_voltage_review import _panel
    return _panel(name, page, diagram)


def captured(name, page, diagram):
    from test_rdson_gate_voltage_review import _captured
    return _captured(name)[(page, diagram)]


class BatchAllV5Tests(unittest.TestCase):
    def test_f01_assumed_temperature_cannot_verify(self):
        row = panel("CSD17306Q5A_TI", 4, "7")
        self.assertEqual(row['validation']['verdict'], 'consistent_at_assumed_conditions')
        self.assertTrue(any('equivalence not established' in r for r in row['reasons']))
        cap = captured("CSD17306Q5A_TI", 4, "7")
        from test_rdson_gate_voltage_review import DS
        specs = parse_rdson_spec_rows(DS/'CSD17306Q5A_TI.pdf')
        for kind in ('Ta', 'Tc', None, 'unspecified'):
            curves = copy.deepcopy(row['curves'])
            for c in curves:
                c['temperature_kind'] = kind
            result = report.validate_against_table(curves, specs, cap['calibration'], cap['scale'])
            self.assertEqual(result['verdict'], 'verified' if kind == 'Ta' else 'consistent_at_assumed_conditions')
        self.assertEqual(report.validate_against_table([], specs, cap['calibration'], cap['scale'])['verdict'], 'not_evaluable')

    def test_f02_printed_currents(self):
        for name, figure, amps in [('HSP4048_LCSC_C701029','2',20), ('ME95N03T_LCSC_C709730','t344',50)]:
            row = panel(name, 3, figure)
            self.assertEqual([c['id_a'] for c in row['curves']], [amps])
            self.assertTrue(row['isolated_condition_labels'])
            self.assertIn('OCR agreement', row['isolated_condition_labels'][0]['text'])

    def test_f02_ocr_disagreement_and_blank_refuse(self):
        import tempfile
        from pathlib import Path
        from datasheet_chart_digitizer import rdson_gate_voltage_labels as labels
        cap = captured('HSP4048_LCSC_C701029',3,'2')
        with tempfile.TemporaryDirectory() as tmp:
            args = (cap['calibration'].plot,Path(tmp),SimpleNamespace(part='known_bad'),'test')
            calls = []
            def contradictory(*a, **kw):
                calls.append(kw['psm'])
                return [(0,0,80,30,'ID=20A' if kw['psm']==7 else 'ID=200A')]
            with patch.object(labels,'_tesseract_words',contradictory):
                self.assertEqual(labels.isolated_condition_labels(cap['gray'],*args), [])
                self.assertTrue(calls)
            self.assertEqual(labels.isolated_condition_labels(np.full_like(cap['gray'],255),*args), [])

    def test_f03_page_scope_and_f07_table_heading(self):
        row=panel('ME95N03T_LCSC_C709730',3,'t344')
        c=row['curves'][0]
        self.assertEqual((c['temperature_c'],c['temperature_kind']),(25,'Tj'))
        self.assertIn('Typical Characteristics',c['parameter_binding']['temperature_evidence'])
        for text in ('Electrical Characteristics (TJ =25℃ Noted)', 'Typical Characteristics', 'TJ=25℃'):
            self.assertEqual(typical_temperature_note(SimpleNamespace(get_text=lambda _t: text)),[])
        from test_rdson_gate_voltage_review import DS
        for name in ('DMN3023L','DMN4008LFG','DMT6009LCT'):
            rows=parse_rdson_spec_rows(DS/(name+'_Diodes.pdf'))
            self.assertTrue(rows)
            for r in rows:
                self.assertEqual((r.temperature_c,r.temperature_kind,r.temperature_source),(25,'Ta','table_heading'))
                self.assertIn('+25',r.temperature_evidence)
        self.assertTrue(all(c['temperature_c'] is None for c in panel('DMN3023L_Diodes',3,'4')['curves']))

    def test_f02_colored_ownership_rejects_missing_or_extra_curve(self):
        import cv2
        from test_rdson_gate_voltage_review import _captured_crop
        cap=captured('ME95N03T_LCSC_C709730',3,'t344')
        image=cv2.imread(str(_captured_crop('ME95N03T_LCSC_C709730',3,'t344')))
        trace,plot=cap['traces'][0],cap['calibration'].plot
        self.assertIsNotNone(evidence.single_colored_trace_evidence(image,trace,plot))
        self.assertIsNone(evidence.single_colored_trace_evidence(np.full_like(image,255),trace,plot))
        for dx in (20,100):
            bad=image.copy();bad[200:220,plot.x0+dx:plot.x0+dx+3]=(180,50,0)
            self.assertIsNone(evidence.single_colored_trace_evidence(bad,trace,plot))
        truncated=replace(trace,points_px=trace.points_px[len(trace.points_px)//2:])
        self.assertIsNone(evidence.single_colored_trace_evidence(image,truncated,plot))

    def test_f05_decimal_needs_printed_dot(self):
        from datasheet_chart_digitizer.rdson_gate_voltage_axes import _printed_decimal
        import cv2
        image=np.full((40,70),255,np.uint8)
        cv2.putText(image,'25',(2,30),cv2.FONT_HERSHEY_SIMPLEX,1,0,2)
        self.assertIsNone(_printed_decimal(image,'25'))
        self.assertIsNone(_printed_decimal(np.full_like(image,255),'25'))
        row=panel('RQ3E180AJ_Rohm',7,'12')
        tick_values={t['value'] for t in row['calibration']['printed_tick_evidence']['x']['ticks']}
        self.assertTrue({.5,1,1.5,2.5,3.5,4.5} <= tick_values)

    def test_f04_printed_kind_and_source_start(self):
        row=panel('WSR3090_LCSC_C719278',3,'2')
        self.assertEqual({c['temperature_kind'] for c in row['curves']},{'Tj'})
        for c in row['curves']:
            self.assertEqual([r['status'] for r in c['readouts'][:2]],['not_on_chart']*2)
            self.assertEqual(c['source_start_evidence'],'blank_plot_strip_before_first_ink')
        cap=captured('WSR3090_LCSC_C719278',3,'2')
        gray=cap['gray'].copy();cal=cap['calibration'];pts=cap['traces'][0].points_px
        self.assertTrue(evidence.blank_before_source_start(gray,cal,pts))
        for width in (1,20):
            bad=gray.copy();bad[101:105,cal.plot.x0+10:cal.plot.x0+10+width]=0
            self.assertFalse(evidence.blank_before_source_start(bad,cal,pts))
        self.assertFalse(evidence.blank_before_source_start(None,cal,pts))
        self.assertEqual(report.readouts([(3.4,9),(10,4)],False,open_left=True,targets=(2.5,3.3),axis_limits=(3,11))[0]['status'],'not_on_chart')
        self.assertEqual(report.readouts([(3.4,9),(10,4)],False,open_left=True,targets=(3.3,),axis_limits=(3,11))[0]['status'],'not_in_extracted_trace')

    def test_f05_f06_all_printed_residuals_and_no_map_change(self):
        me=panel('ME95N03T_LCSC_C709730',3,'t344')
        ticks=me['calibration']['printed_tick_evidence']['x']
        self.assertEqual({t['value'] for t in ticks['ticks']},{0,2,4,6,8,10})
        self.assertGreater(ticks['max_residual_px'],4)
        zero = next(t for t in me['calibration']['printed_tick_evidence']['y']['ticks'] if t['value'] == 0)
        self.assertEqual(zero['role'], 'diagnostic_only')
        self.assertGreater(abs(zero['residual_px']), 1)
        self.assertTrue(any('axis_printed_rule_residual' in r for r in me['reasons']))
        zv=panel('ZVNL120A_Diodes',3,'t394')
        tick=next(t for t in zv['calibration']['printed_tick_evidence']['x']['ticks'] if t['value']==20)
        self.assertAlmostEqual(abs(tick['residual_px']),4.069,delta=.02)
        self.assertTrue(any('beyond_labelled_20V' in r for r in zv['reasons']))
        top=next(t for t in zv['calibration']['printed_tick_evidence']['y']['ticks'] if t['value']==100)
        self.assertEqual(top['state'],'measured')
        for name,page,figure,axis,value in [('CSD17309Q3_TI',1,'t544','x',10),
                                           ('DMN4008LFG_Diodes',3,'4','y',.005)]:
            # Page-1 repeats are no longer served, but their extraction and
            # calibration evidence are still exercised by the capture path.
            ticks=captured(name,page,figure)['row']['calibration']['printed_tick_evidence'][axis]['ticks']
            tick=next(t for t in ticks if t['value']==value)
            self.assertEqual(tick['state'],'measured')
            self.assertLess(abs(tick['residual_px']),1)

    def test_f05_rule_evidence_known_bads(self):
        cap=captured('ME95N03T_LCSC_C709730',3,'t344');cal=cap['calibration']
        for bad in (replace(cal,grid_x=()),replace(cal,x_axis=replace(cal.x_axis,m=float('nan'))),
                    replace(cal,grid_x=tuple(x+1000 for x in cal.grid_x))):
            _,result,reasons=evidence.printed_tick_evidence(bad,[])
            self.assertTrue(reasons)
            self.assertEqual(result['x']['added_span_ticks'],[])
            self.assertTrue(any('unverified' in r for r in reasons))
        cal=captured('DMN4008LFG_Diodes',3,'4')['calibration']
        for extra in (708.8,710.,718.):
            bad=replace(cal,grid_y=cal.grid_y+(extra,),vector_grid_y=cal.vector_grid_y+(extra,))
            _,result,reasons=evidence.printed_tick_evidence(bad,[])
            tick=next(t for t in result['y']['ticks'] if t['value']==.005)
            self.assertEqual(tick['state'],'unverified')
            self.assertTrue(any('unverified' in r for r in reasons))
        cal=captured('ZVNL120A_Diodes',3,'t394')['calibration']
        seated=next(t.pixel for t in cal.y_axis.ticks if t.value==100)
        for delta in (.2,1.4):
            extra=seated+delta
            bad=replace(cal,grid_y=cal.grid_y+(extra,),vector_grid_y=cal.vector_grid_y+(extra,))
            _,result,_=evidence.printed_tick_evidence(bad,[])
            self.assertEqual(next(t for t in result['y']['ticks'] if t['value']==100)['state'],'unverified')

    def test_f09_small_excess_is_inventory_not_contradiction(self):
        for name, target in [('CSD17304Q3_TI',3),('CSD17307Q5A_TI',8),('CSD17309Q3_TI',4.5)]:
            row=panel(name,6 if '17309' in name else 4,'7')
            notes=[n for n in row['validation']['condition_mismatch_notes'] if n['vgs_v']==target]
            self.assertTrue(notes,(name,row['validation']))
            self.assertGreater(notes[0]['excess_mohm'],0)
            self.assertIn('one pixel',notes[0]['text'])
        cap=captured('CSD17309Q3_TI',6,'7');row=cap['row']
        from test_rdson_gate_voltage_review import DS
        specs=parse_rdson_spec_rows(DS/'CSD17309Q3_TI.pdf')
        base=next(r for r in specs if r.vgs_v==4.5)
        hot=max(row['curves'],key=lambda c:c['temperature_c'])
        value=report.readouts(hot['points'],False,targets=(4.5,))[0]['rds_mohm']
        for excess in (0.0001,0.1,1.0):
            notes=report.condition_mismatch_notes([hot],[replace(base,max_mohm=value-excess)],cap['calibration'],lambda _v:1)
            self.assertEqual(len(notes),1)
        self.assertEqual(report.condition_mismatch_notes([hot],[replace(base,max_mohm=value)],cap['calibration'],lambda _v:1),[])

    def test_f10_pulse_inventory_and_local_scope(self):
        from datasheet_chart_digitizer.rdson_gate_voltage_conditions import pulse_conditions
        row=panel('FDP8870_onsemi',5,'9')
        self.assertTrue(any(c.get('duration_us')==80 for c in row['conditions']))
        self.assertTrue(any(c.get('duty_cycle_percent')==.5 and c['duty_cycle_is_maximum'] for c in row['conditions']))
        for part in ('RQ3E110AJ_Rohm','RQ3E180AJ_Rohm','RQ6E080AJ_Rohm'):
            conditions=panel(part,7,'12')['conditions']
            self.assertTrue(any(c['regime']=='pulsed' for c in conditions))
            self.assertFalse(any('duration_us' in c for c in conditions))
        cap=captured('FDP8870_onsemi',5,'9')
        label=SimpleNamespace(text='Pulsed',cx=-100,cy=-100)
        self.assertEqual(pulse_conditions(SimpleNamespace(words=[]),None,cap['calibration'].plot,[label]),[])

    def test_f11_separate_ink_is_added_contact_retained(self):
        row=panel('WSR3090_LCSC_C719278',3,'2')
        for index,v in ((0,8.06345),(1,8.50895)):
            c=row['curves'][index]
            p=min(c['points'],key=lambda p:abs(p[0]-v))
            self.assertLess(abs(p[0]-v),.006)
            self.assertTrue(any(n['mode']=='separate_annotation_contact_ink' for n in c['gap_tracing']))
            self.assertTrue(c['gap_kinds']['annotation_contact'])

    def test_f11_missing_wide_or_competing_ink_stays_contact(self):
        pts=[(float(x),20.) for x in range(5,26) if x != 15]
        trace=Trace(pts,'raster',contact_removed_x=[15.])
        for width in (0,10,30):
            gray=np.full((60,40),255,np.uint8)
            if width:gray[20-width//2:20+width//2,15]=0
            got=evidence.recover_separate_contact_ink([trace],gray)[0]
            self.assertEqual(got.points_px,pts)
            self.assertEqual(got.contact_removed_x,[15.])
        gray=np.full((60,40),255,np.uint8);gray[19:22,15]=0
        self.assertEqual(len(evidence.recover_separate_contact_ink([trace],gray)[0].points_px),len(pts)+1)
        self.assertEqual(evidence.recover_separate_contact_ink([trace,Trace(pts,'raster')],gray)[0].points_px,pts)

    def test_f12_approximate_rows_have_panel_reasons(self):
        for name,p,d in [('AO3416_AOS',3,'5'),('AON7524_AOS',3,'5'),('IRLB4132_IFX',6,'12'),
                         ('IRLB8743_IFX',6,'12'),('IRLB8748_IFX',6,'12'),('IRLB8721_IFX',6,'12')]:
            row=panel(name,p,d)
            approximate=[a for a in row['validation']['anchors'] if a.get('condition_match')=='approximate_drain_current']
            self.assertTrue(approximate)
            self.assertEqual(len([r for r in row['reasons'] if r.startswith('approximate_current_anchor')]),len(approximate))
            self.assertEqual(row['status'],'review_required')

    def test_f08_overlay_label_placement(self):
        cap=captured('IRLB4132_IFX',6,'12')
        from datasheet_chart_digitizer.overlay import draw_axis_ticks
        import tempfile
        from pathlib import Path
        row=copy.deepcopy(cap['row'])
        descriptor=SimpleNamespace(**{k:row[k] for k in ('part','page','diagram','title')})
        with tempfile.TemporaryDirectory() as tmp, patch.object(report,'draw_axis_ticks',wraps=draw_axis_ticks) as spy:
            report.write_overlay(np.repeat(cap['gray'][:,:,None],3,axis=2),row,Path(tmp),descriptor,'test',cap['calibration'],'mOhm')
            self.assertTrue(spy.call_args.kwargs['x_labels_below'])
            self.assertTrue(spy.call_args.kwargs['y_labels_left'])
            self.assertTrue(all('clear of all ink' in label['placement'] for label in row['overlay_curve_labels']))
        calls=[]
        with patch('datasheet_chart_digitizer.overlay.cv2.putText',side_effect=lambda *a,**k:calls.append((a[1],a[2]))):
            draw_axis_ticks(np.full((1000,1200,3),255,np.uint8),cap['calibration'].plot,
                            [(cap['calibration'].plot.x0,3)],[(cap['calibration'].plot.y1,2)],
                            x_labels_below=True,y_labels_left=True)
        self.assertGreater(calls[0][1][1],calls[1][1][1]+15)

    def test_f08_sparse_steep_segments_are_occupied(self):
        import cv2
        from datasheet_chart_digitizer.capacitance_types import PlotBox
        canvas=np.full((700,700,3),255,np.uint8)
        curves=[{'curve_index':i,'temperature_c':25,'temperature_kind':'Tj','points_px':[[x,100],[x,600]],
                 'points':[[3,20],[3,2]],'gaps':[]} for i,x in enumerate((300,340))]
        placed=report._place_curve_labels(canvas,curves,PlotBox(0,0,699,699))
        ink=np.zeros(canvas.shape[:2],np.uint8)
        for c in curves:cv2.polylines(ink,[np.asarray(c['points_px'],np.int32)],False,255,1)
        for p in placed:
            x0,y0,x1,y1=p['box_px']
            self.assertFalse(ink[y0:y1+1,x0:x1+1].any())


class DuplicateTests(unittest.TestCase):
    """Real PDF panels; each negative is a named mutation of their evidence."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='rds-duplicates-')
        cls.root = Path(cls.tmp.name)
        cls.pdf = Path('/Users/fab/dev/ee/solar-charger-eval/ds/CSD17310Q5A_TI.pdf')
        if not cls.pdf.exists():
            raise unittest.SkipTest(f'real duplicate PDF missing: {cls.pdf}')
        located, _, words = rgv.locate_panels(cls.pdf, cls.root/'work')
        specs = parse_rdson_spec_rows(cls.pdf)
        cls.original = [rgv.digitize_panel(p, words[(p.page, p.diagram)], specs, cls.root) for p in located]
        assert len(cls.original) == 2

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.a, self.b = copy.deepcopy(self.original)

    def check(self, reason, decision='conflict'):
        result = duplicates.compare_panels(self.a, self.b, self.root)
        self.assertEqual(result['decision'], decision, result)
        self.assertTrue(any(reason in r for r in result['reasons']), result)
        kept, audit = duplicates.deduplicate_pdf([self.a, self.b], self.pdf, self.root)
        self.assertEqual(len(kept), 2, audit)
        self.assertFalse(audit['discarded_duplicates'])
        return result

    def test_real_pair_keeps_numbered_and_served_data(self):
        before = copy.deepcopy(self.b)
        kept, audit = duplicates.deduplicate_pdf([self.a, self.b], self.pdf, self.root)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]['diagram'], '7')
        notes = kept[0].pop('also_printed_at')
        self.assertEqual(kept[0], before)
        self.assertEqual((notes[0]['page'], notes[0]['diagram']), (1, 't537'))
        self.assertGreater(notes[0]['visual_score'], 0.90)
        self.assertLess(notes[0]['max_value_diff'], 0.005)
        self.assertEqual(len(audit['discarded_duplicates']), 1)
        duplicates.write_audit(audit, self.pdf, self.root)
        persisted = json.loads(next((self.root/'duplicate_checks').glob(f'{self.pdf.name}.*.json')).read_text())
        self.assertEqual(persisted['discarded_duplicates'], audit['discarded_duplicates'])
        self.assertEqual(len(persisted['source_sha256']), 64)

    def test_producer_and_cli_record_evidence(self):
        # Actual production integration and CLI manifest, not just the helper.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertEqual(rgv.main(['--pdf', str(self.pdf), '--out', str(out)]), 0)
            manifest = json.loads((out/'rdson_gate_voltage.json').read_text())
        self.assertEqual(len(manifest['panels']), 1)
        self.assertEqual(len(manifest['discarded_duplicates']), 1)
        self.assertEqual(manifest['duplicate_checks'][0]['decision'], 'duplicate')

    def test_cross_pdf_identical_plots_kept(self):
        import rds_digitize_cache as cache
        names = ['IRLB4132_IFX', 'IRLB8743_IFX']
        rows = [cache.digitize_pdf(self.pdf.parent/f'{n}.pdf', self.root)[0][0] for n in names]
        self.assertGreater(duplicates.visual_evidence(*rows, self.root)['visual_score'], 0.999)
        self.a, self.b = rows
        self.check('different_pdf', 'distinct')

    def test_real_nonduplicate_plots(self):
        # Closest genuinely different pair in the 42-panel v5 corpus.
        # Deliberately place their rows in one document to exercise the visual
        # gate as well as the separate same-PDF boundary test above.
        import rds_digitize_cache as cache
        rows = [cache.digitize_pdf(self.pdf.parent/f'{n}.pdf', self.root)[0][0]
                for n in ['SIS176LDN_Vishay', 'SISS76LDN_Vishay']]
        self.a, self.b = rows
        self.b['pdf'] = self.a['pdf']
        result = self.check('plot_ink_differs', 'distinct')
        self.assertLess(result['visual_score'], 0.70)

    def test_changed_plot_keeps_identical_data(self):
        import cv2
        bad = copy.deepcopy(self.a)
        image = cv2.imread(str(self.root/bad['crop_png']))
        box = bad['plot_box_px']; x0,y0,x1,y1 = [int(box[k]) for k in ('x0','y0','x1','y1')]
        image[y0:y1, x0:x1] = np.flip(image[y0:y1, x0:x1], axis=1)
        path = self.root/'reflected.png'; cv2.imwrite(str(path),image)
        self.b = bad | {'crop_png':path.name, 'page':9}
        self.check('plot_ink_differs', 'distinct')

    def test_bound_labels(self):
        for key, value in [('id_a', 21), ('temperature_c', 150), ('temperature_kind', 'Tj')]:
            with self.subTest(key=key):
                self.b = copy.deepcopy(self.original[1])
                self.b['curves'][0][key] = value
                result = self.check('bound_labels_differ')
                self.assertGreater(result['visual_score'], 0.9)

    def test_relabelled_real_pdf_is_not_merged(self):
        import pymupdf
        pdf = self.root/'relabelled_150C.pdf'
        with pymupdf.open(self.pdf) as doc:
            page = doc[0]
            word = next(w for w in page.get_text('words') if w[4] == '125°C')
            page.add_redact_annot(pymupdf.Rect(word[:4]), fill=(1,1,1))
            page.apply_redactions(images=0, graphics=0)
            page.insert_text((word[0], word[3]-1.5), '150°C', fontsize=6.7)
            doc.save(pdf, no_new_id=True)
        audit = {}
        rows, _ = rgv.digitize_pdf(pdf, self.root/'relabelled', duplicate_audit=audit)
        self.assertEqual(len(rows), 2)
        self.assertEqual({c['temperature_c'] for c in rows[0]['curves']}, {25,150})
        self.assertEqual({c['temperature_c'] for c in rows[1]['curves']}, {25,125})
        check = audit['duplicate_checks'][0]
        self.assertGreater(check['visual_score'], 0.90)
        self.assertEqual(check['decision'], 'conflict')
        self.assertIn('bound_labels_differ', check['reasons'])

    def test_unbound_labels(self):
        for key in ['id_a','temperature_c','temperature_kind']:
            with self.subTest(key=key):
                self.b = copy.deepcopy(self.original[1]); self.b['curves'][0][key] = None
                self.check('unbound_', 'unevaluable')

    def test_refused_or_unusable(self):
        self.b['status'] = 'refused'
        self.check('panel_refused', 'unevaluable')
        self.b = copy.deepcopy(self.original[1]); self.b['curves'][0]['usable'] = False
        self.check('curve_unusable', 'unevaluable')

    def test_status_and_verdict_conflict(self):
        self.b['status'] = 'ok'; self.check('panel_status_or_verdict')
        self.b = copy.deepcopy(self.original[1]); self.b['validation']['verdict'] = 'inconsistent'
        self.check('panel_status_or_verdict')

    def test_calibration_missing_and_unbound(self):
        self.b.pop('calibration'); self.check('calibration', 'unevaluable')
        self.b = copy.deepcopy(self.original[1]); self.b['calibration']['grid_binding'] = 'unverified'
        self.check('calibration_not_bound', 'unevaluable')
        self.b = copy.deepcopy(self.original[1]); self.b['calibration']['x_axis']['ticks'] = []
        self.check('too_few_calibration_ticks', 'unevaluable')

    def test_axis_model_ranges_units_and_ticks(self):
        for key, value, reason in [('model','log10','axis_models'),('m',0.01,'calibrated_ranges')]:
            self.b = copy.deepcopy(self.original[1]); self.b['calibration']['x_axis'][key] = value
            self.check(reason)
        self.b = copy.deepcopy(self.original[1]); self.b['calibration']['x_axis']['ticks'][1]['value'] = 1.1
        self.check('calibrated_ticks')
        self.b = copy.deepcopy(self.original[1]); self.b['calibration']['y_to_mohm'] = 1000
        self.check('axis_units_differ')

    def test_curve_count_and_ambiguity(self):
        self.b['curves'].pop(); self.check('curve_count')
        self.b = copy.deepcopy(self.original[1]); self.b['curves'][1] = copy.deepcopy(self.b['curves'][0])
        self.check('ambiguous_curve_identity','unevaluable')

    def test_readout_values_and_states(self):
        for factor in [1.006, 1.1, 10, 1000000]:
            self.b = copy.deepcopy(self.original[1])
            self.b['curves'][0]['readouts'][0]['rds_mohm'] *= factor
            self.check('curve_or_readout_value')
        self.b = copy.deepcopy(self.original[1]); self.b['curves'][0]['readouts'][0]['status'] = 'not_on_chart'
        self.check('readout_target_or_state')
        self.b = copy.deepcopy(self.original[1]); self.b['curves'][0]['readouts'] = []
        self.check('readouts_missing','unevaluable')

    def test_curve_values_all_samples_and_far_tail(self):
        for index in [0, 18, -1]:
            for factor in [1.02, 2, 10000]:
                self.b = copy.deepcopy(self.original[1])
                self.b['curves'][0]['points'][index][1] *= factor
                self.check('curve_or_readout_value')

    def test_curve_domain(self):
        self.b['curves'][0]['points'] = [[x+0.02,y] for x,y in self.b['curves'][0]['points']]
        self.check('curve_domain')

    def test_sample_count_gaps_and_missing(self):
        self.b['curves'][0]['points'] = self.b['curves'][0]['points'][:2]
        self.check('too_few_curve_samples','unevaluable')
        self.b = copy.deepcopy(self.original[1]); self.b['curves'][0]['gaps'] = [[3,4]]
        self.check('curve_unusable_or_incomplete','unevaluable')
        self.b = copy.deepcopy(self.original[1]); self.b['curves'] = []
        self.check('curves_missing','unevaluable')

    def test_nonfinite_data(self):
        for value in [float('nan'),float('inf'),float('-inf')]:
            for where in ['points','readout','axis','label']:
                self.b = copy.deepcopy(self.original[1])
                if where == 'points': self.b['curves'][0]['points'][10][1] = value
                elif where == 'readout': self.b['curves'][0]['readouts'][0]['rds_mohm'] = value
                elif where == 'axis': self.b['calibration']['x_axis']['m'] = value
                else: self.b['curves'][0]['id_a'] = value
                self.check('nonfinite','unevaluable')

    def test_visual_missing_blank_corrupt_and_box(self):
        import cv2
        for image in [np.full((900,1200),255,np.uint8),np.zeros((900,1200),np.uint8)]:
            path = self.root/'blank.png'; cv2.imwrite(str(path),image)
            self.b = copy.deepcopy(self.original[1]); self.b['crop_png'] = path.name
            self.check('degenerate_plot_ink','unevaluable')
        self.b = copy.deepcopy(self.original[1]); self.b['crop_png'] = 'nonexistent.png'
        self.check('FileNotFoundError','unevaluable')
        path = self.root/'corrupt.png'; path.write_bytes(b'not an image')
        self.b['crop_png'] = path.name; self.check('crop_not_decodable','unevaluable')
        self.b = copy.deepcopy(self.original[1]); self.b['plot_box_px']['x1'] = 10000
        self.check('plot_box_outside','unevaluable')

    def test_probe_exception_keeps_both(self):
        with patch.object(duplicates,'visual_evidence',side_effect=RuntimeError('probe failed')):
            self.check('probe failed','unevaluable')

    def test_resolution_preference_and_deterministic_order(self):
        self.a['diagram'],self.b['diagram'] = '8','7'
        kept, _ = duplicates.deduplicate_pdf([self.a,self.b],self.pdf,self.root)
        self.assertEqual(kept[0]['diagram'],'7')  # larger actual plot box
        # Equal data/crop/area: deterministic page/figure ordering.
        a,b = copy.deepcopy(self.original[1]),copy.deepcopy(self.original[1])
        a['diagram'],b['diagram']='9','8'
        for rows in [[a,b],[b,a]]:
            kept,_ = duplicates.deduplicate_pdf(copy.deepcopy(rows),self.pdf,self.root)
            self.assertEqual(kept[0]['diagram'],'8')

    def test_no_transitive_merge(self):
        # Real rows with a constructed similarity chain, A~B~C but A!~C.
        rows = [copy.deepcopy(self.b) | {'diagram':str(i)} for i in range(3)]
        real_compare = duplicates.compare_panels
        def compare(a,b,root):
            result = real_compare(a,b,root)
            if {a['diagram'],b['diagram']} == {'0','2'}:
                result['decision'] = 'unevaluable'
            return result
        with patch.object(duplicates,'compare_panels',compare):
            kept,_ = duplicates.deduplicate_pdf(rows,self.pdf,self.root)
        self.assertEqual(len(kept),2)
