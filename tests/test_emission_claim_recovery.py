"""S: recovery may release only its own idle claim, never recovery data or readiness."""
import copy

from scripts.emission_editor.claim_recovery import release_partial_claim


class Boundary:
    """External owned observation/write boundary, not a replacement for recovery logic."""
    session = 'session'
    def __init__(self):
        self.values = {'registry-busy': True, 'registry-publication': 'claim',
                       'registry-active': '', 'registry-generation': '',
                       'registry-ready': False, 'ui-runtime-ready': False,
                       'registry-operator': '', 'registry-pending': False,
                       'registry-preview-active': False, 'registry-operation-stage': 0,
                       'ui-operator': None, 'ui-selected-root': None, 'ui-intent': 0,
                       'reg-claim-data': 'record', 'reg-claim-source': 'field',
                       'original': 'untouched', 'last-applied': 'untouched'}
        self.owned = True
        self.writes = []
        self.fail = False
        self.race = False

    def observe(self):
        if not self.owned:
            raise ValueError('foreign-owner')
        return self.session, copy.deepcopy(self.values)

    def set_owned(self, key, value):
        self.writes.append((key, value))
        if self.fail and key == 'registry-publication':
            raise RuntimeError('transport-stopped')
        self.values[key] = value
        if self.race:
            self.values['registry-publication'] = 'foreign'


def expected():
    return {'session': 'session', 'publication': 'claim',
            'records': {'reg-claim-data': 'record', 'reg-claim-source': 'field'}}


def test_release_changes_only_two_claim_fields_and_preserves_staged_records():
    # Removing the exact write allowlist or clearing staged data must fail this test.
    port = Boundary()
    before = copy.deepcopy(port.values)
    events = []
    result = release_partial_claim(port, expected(), lambda **e: events.append(e))
    assert result['success'] is True
    assert port.writes == [('registry-busy', False), ('registry-publication', '')]
    assert port.values == {**before, 'registry-busy': False, 'registry-publication': ''}
    assert events[-1]['state'] == 'completed'


def test_unsafe_or_foreign_context_never_releases_claim():
    # Omitting any independent safety boundary silently releases another operation.
    for field, value in [('registry-publication', 'foreign'), ('registry-active', 'claim'),
                         ('registry-ready', True), ('ui-runtime-ready', True),
                         ('registry-pending', True), ('registry-preview-active', True),
                         ('registry-operation-stage', 2), ('ui-operator', 'user'),
                         ('ui-selected-root', 'avatar'), ('registry-operator', 'user'),
                         ('reg-claim-source', 'different-field')]:
        port = Boundary(); port.values[field] = value
        assert not release_partial_claim(port, expected(), lambda **e: None)['success']
        assert port.writes == []
    for change in ('session', 'owner'):
        port = Boundary()
        if change == 'session': port.session = 'foreign-session'
        else: port.owned = False
        assert not release_partial_claim(port, expected(), lambda **e: None)['success']
        assert port.writes == []


def test_partial_release_or_race_is_unknown_and_never_continues():
    # Removing the between-write guard or reporting transport failure as success is unsafe.
    for race in (False, True):
        port = Boundary(); port.fail = not race; port.race = race
        events = []
        result = release_partial_claim(port, expected(), lambda **e: events.append(e))
        assert not result['success']
        assert port.values['registry-busy'] is False
        assert port.values['registry-publication'] == ('foreign' if race else 'claim')
        assert events[-1]['state'] == 'unknown'
        if race: assert port.writes == [('registry-busy', False)]
