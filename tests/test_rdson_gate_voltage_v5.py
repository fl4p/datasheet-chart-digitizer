"""Independent-review F01-F12: real producer output and constructed failures."""

import copy
import math
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from datasheet_chart_digitizer import rdson_gate_voltage as rgv
from datasheet_chart_digitizer import rdson_gate_voltage_report as report
from datasheet_chart_digitizer import rdson_gate_voltage_evidence as evidence
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
            ticks=panel(name,page,figure)['calibration']['printed_tick_evidence'][axis]['ticks']
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
