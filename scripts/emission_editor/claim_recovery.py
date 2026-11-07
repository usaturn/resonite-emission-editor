"""Explicit inactive-claim recovery. No adoption, deletion, or automatic resume."""

IDLE = {
    'registry-active': '', 'registry-generation': '',
    'registry-ready': False, 'ui-runtime-ready': False,
    'registry-operator': '', 'registry-pending': False,
    'registry-preview-active': False, 'registry-operation-stage': 0,
    'ui-operator': None, 'ui-selected-root': None, 'ui-intent': 0,
}


def release_partial_claim(port, expected, checkpoint):
    """Only release Busy then publication; any uncertain effect requires human review.

    port.observe must prove current native session/ownership/record ancestry; set_owned
    uses the existing session/ownership checked writer. No cleanup writes on failure.
    A process exit after a started event is UNKNOWN, never permission to retry.
    """
    def verify(busy, publication):
        session, values = port.observe()
        wanted = {**IDLE, **expected['records'],
                  'registry-busy': busy, 'registry-publication': publication}
        if session != expected['session'] or not expected['publication'] or not expected['records']:
            raise ValueError('claim-context-mismatch')
        if any(type(values.get(k)) is not type(v) or values.get(k) != v
               for k, v in wanted.items()):
            raise ValueError('claim-not-exact-idle-owned-partial')
        return values

    latest = None
    try:
        latest = verify(True, expected['publication'])
        checkpoint(unit='release', state='started', observation=latest,
                   next_safe='Reobserve exact claim; never automatically resume.')
        for key, value, busy, publication in (
            ('registry-busy', False, True, expected['publication']),
            ('registry-publication', '', False, expected['publication']),
        ):
            latest = verify(busy, publication)
            checkpoint(unit=key, state='started', observation=latest,
                       next_safe='Read both claim fields and all idle guards before any further write.')
            port.set_owned(key, value)
            latest = verify(False, expected['publication'] if key == 'registry-busy' else '')
            checkpoint(unit=key, state='completed', observation=latest,
                       next_safe='Partial release is NOT success; retain all staged records.')
        checkpoint(unit='release', state='completed', observation=latest,
                   next_safe='Fresh current capture and ordinary registration only; retain old generation.')
        return {'success': True, 'state': 'released'}
    except Exception as exc:
        checkpoint(unit='release', state='unknown', observation=latest, error=str(exc),
                   next_safe='Stop writes. Reobserve native session, ownership, both claim fields and records; controller review.')
        return {'success': False, 'state': 'unknown', 'error': str(exc)}


class Checkpoints:
    """Exclusive per-event files: an unmatched started event always means UNKNOWN."""
    def __init__(self, directory, context):
        from pathlib import Path
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.context = context

    def __call__(self, **event):
        from datetime import UTC, datetime
        from uuid import uuid4
        from .resoloop_io import save_new
        now = datetime.now(UTC).isoformat()
        save_new(self.directory / (now.replace(':', '-') + '-' + uuid4().hex + '.json'),
                 {'at': now, 'context': dict(self.context), **event})
