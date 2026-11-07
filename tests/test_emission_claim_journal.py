"""S: interruption evidence must survive without overwriting completed-unit history."""
import json

from scripts.emission_editor.claim_recovery import Checkpoints, release_partial_claim
from test_emission_claim_recovery import Boundary, expected


def test_partial_release_evidence_retains_started_completed_and_unknown(tmp_path):
    # Replacing append-only events with one mutable status would lose the first write's proof.
    journal = Checkpoints(tmp_path, {'session': 'session', 'generation': 'claim',
                                   'evidence': 'command-receipts', 'source_hashes': {}})
    port = Boundary(); port.fail = True
    result = release_partial_claim(port, expected(), journal)
    events = [json.loads(p.read_text()) for p in sorted(tmp_path.glob('*.json'))]
    assert not result['success']
    assert any(e['unit'] == 'registry-busy' and e['state'] == 'completed' for e in events)
    assert any(e['unit'] == 'registry-publication' and e['state'] == 'started' for e in events)
    assert events[-1]['state'] == 'unknown'
    assert all(e['next_safe'] and e['context']['evidence'] == 'command-receipts' for e in events)
    before = {p.name: p.read_bytes() for p in tmp_path.glob('*.json')}
    Checkpoints(tmp_path, {'session': 'session'})(unit='review', state='started', next_safe='Observe, do not resume')
    assert all((tmp_path / p).read_bytes() == data for p, data in before.items())
