import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from assessment import calculate_assessment


def outcome(name, *statuses):
    return {'id': name, 'items': [{'status': v} for v in statuses]}


class AssessmentTests(unittest.TestCase):
    def test_perfect_measured_check(self):
        result = calculate_assessment(['cpu'], [outcome('cpu', 'PASS', 'PASS')])
        self.assertEqual(result['score'], 100)
        self.assertEqual(result['coverage'], 100)
        self.assertEqual(result['completion'], 100)

    def test_failed_check_not_passing(self):
        result = calculate_assessment(['cpu'], [outcome('cpu', 'FAIL')])
        self.assertEqual(result['score'], 0)

    def test_warn_reduces_score(self):
        result = calculate_assessment(['cpu'], [outcome('cpu', 'PASS', 'WARN')])
        self.assertEqual(result['score'], 78)

    def test_info_and_skip_never_count_as_pass(self):
        result = calculate_assessment(['info', 'cpu'], [outcome('info', 'INFO'), outcome('cpu', 'SKIP')])
        self.assertIsNone(result['score'])
        self.assertEqual(result['coverage'], 0)
        self.assertEqual(result['completed'], 2)

    def test_partial_coverage_kept_separate(self):
        result = calculate_assessment(['info', 'cpu'], [outcome('cpu', 'PASS')])
        self.assertEqual(result['score'], 100)
        self.assertEqual(result['coverage'], 83)
        self.assertEqual(result['completion'], 50)
        self.assertEqual(result['score_label'], 'PARTIAL EVIDENCE')

    def test_real_gui_event_and_report_pipeline(self):
        # UI-backend interface test with synthetic checks. Does not run hardware tests.
        try:
            import desktop_backend as gui
        except ImportError as exc:
            self.skipTest(f'GUI dependencies missing in this environment: {exc}')
        def info(rep, args):
            rep.section('System overview')
            rep.add('OS', 'Windows Test', 'INFO')
        def cpu(rep, args):
            rep.section('CPU thermals')
            rep.add('Idle temperature', '55 °C', 'PASS')
            rep.add('Thermal throttling', 'Not detected', 'PASS')
        with tempfile.TemporaryDirectory() as tempdir:
            with patch.object(gui.diag, 'TESTS', {'info':info, 'cpu':cpu}), patch.object(gui, 'REPORT_DIR', tempdir):
                api=gui.Api()
                self.assertTrue(api.start({'tests':['info','cpu'], 'quick':True})['ok'])
                events=[]
                for _ in range(100):
                    r=api.poll()
                    events.extend(r['events'])
                    if any(e['t']=='done' for e in events):
                        break
                    time.sleep(.02)
                self.assertTrue(any(e['t']=='test_end' for e in events))
                done=next((e for e in events if e['t']=='done'), None)
                self.assertIsNotNone(done)
                self.assertEqual(done['assessment']['score'],100)
                self.assertEqual(done['assessment']['coverage'],83)
                self.assertIn('PARTIAL',done['verdict'])
                report=api.save_report()
                self.assertTrue(report['ok'])
                jsfile=next(Path(tempdir).glob('*.json'))
                htmlfile=next(Path(tempdir).glob('*.html'))
                obj=json.loads(jsfile.read_text(encoding='utf-8'))
                self.assertEqual(obj['meta']['assessment']['score'],100)
                self.assertIn('PARTIAL', obj['meta']['verdict'])
                self.assertIn('Assessment score: 100 / 100',htmlfile.read_text(encoding='utf-8'))


if __name__=='__main__':
    unittest.main()
