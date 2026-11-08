"""S: a typed unused native read must not invent the initial Before/Target."""
from copy import deepcopy
import json
from pathlib import Path

from scripts.emission_editor import drivegate_initial as initial
from scripts.emission_editor.drivegate_evidence import _PHASE_TYPES
from scripts.emission_editor.drivegate_package import FIELDS, normalized_fixture
from test_emission_drivegate_evidence import field


def test_initial_baseline_is_bound_to_declared_full_values_and_source_model():
    # S: otherwise an arbitrary observed Target or overwritten Before could
    # become a plausible new baseline and conceal incomplete capture/restore.
    model = json.loads((Path(initial.__file__).parent/'drivegate_native_model.json').read_bytes())
    seal = {'cliVersion':'0.2.0','dllSha256':model['nativeDllSha256'],
            'normalizedFixture':normalized_fixture('unit','0.2.0')}
    fields = {}
    for i,(name,(_,raw)) in enumerate(FIELDS.items()):
        value = dict(zip(('r','g','b','a','profile'),[*raw,'sRGB'])) if isinstance(raw,list) else raw
        fields[name] = field(_PHASE_TYPES[name], value, 'Reso_' + format(i+128,'X'))
    assert initial.compare_initial_fields(seal, fields).status == 'passed'
    changed = deepcopy(fields)
    changed['BeforeColor']['value']['profile'] = 'Linear'
    assert initial.compare_initial_fields(seal, changed).status != 'passed'
    changed = deepcopy(fields)
    changed['ColorTarget']['value']['a'] = .5
    assert initial.compare_initial_fields(seal, changed).status != 'passed'
    changed = deepcopy(fields)
    changed['BeforeBool']['value'] = 0
    assert initial.compare_initial_fields(seal, changed).status != 'passed'
