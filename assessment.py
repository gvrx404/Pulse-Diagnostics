"""Evidence-based, 100-point scoring for Pulse Diagnostics.

The score is a weighted summary of checks that actually returned PASS, WARN
or FAIL. INFO and SKIP cannot improve a score. Coverage and completion are
reported separately; an untested or unsupported component is never a pass.
"""
from __future__ import annotations

WEIGHTS = {
    'info': 5, 'battery': 10, 'ram': 20, 'disk': 15,
    'cpu': 25, 'gpu': 10, 'network': 10, 'processes': 5,
}
VALUE = {'PASS': 1.0, 'WARN': 0.55, 'FAIL': 0.0}
TITLES = {
    'info': 'System overview', 'battery': 'Battery health', 'ram': 'Memory',
    'disk': 'Storage', 'cpu': 'CPU thermals', 'gpu': 'Graphics',
    'network': 'Connectivity', 'processes': 'Processes',
}


def calculate_assessment(selected, completed, cancelled=False):
    """completed: list of {id: str, items: [{status, ...}]} from actual tests."""
    selected = list(dict.fromkeys(t for t in selected if t in WEIGHTS))
    by_id = {t['id']: t.get('items', []) for t in completed if t.get('id') in selected}
    requested_weight = sum(WEIGHTS[t] for t in selected)
    assessed_weight = 0
    weighted_value = 0.0
    rows = []
    for key in selected:
        items = by_id.get(key)
        checks = [i['status'] for i in (items or []) if i.get('status') in VALUE]
        if items is None:
            state = 'NOT RUN'
            score = None
        elif not checks:
            state = 'NOT SCORED'
            score = None
        else:
            score = round(100 * sum(VALUE[x] for x in checks) / len(checks))
            state = 'FAIL' if 'FAIL' in checks else ('WARN' if 'WARN' in checks else 'PASS')
            assessed_weight += WEIGHTS[key]
            weighted_value += WEIGHTS[key] * sum(VALUE[x] for x in checks) / len(checks)
        rows.append({'id': key, 'title': TITLES[key], 'weight': WEIGHTS[key],
                     'status': state, 'score': score, 'graded_checks': len(checks)})

    score = round(100 * weighted_value / assessed_weight) if assessed_weight else None
    completed_count = len(by_id)
    return {
        'score': score,
        'score_label': 'NOT ASSESSED' if score is None else ('PARTIAL EVIDENCE' if assessed_weight < requested_weight or cancelled else 'ASSESSED'),
        'coverage': round(100 * assessed_weight / requested_weight) if requested_weight else 0,
        'completed': completed_count,
        'total': len(selected),
        'completion': round(100 * completed_count / len(selected)) if selected else 0,
        'cancelled': bool(cancelled),
        'categories': rows,
        'method': 'Weight-adjusted mean of observed PASS (100), WARN (55) and FAIL (0) checks. INFO/SKIP are unscored. Scores are not a hardware benchmark or guarantee of health.',
    }
