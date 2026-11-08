"""Initial private baseline semantics, distinct from current read/phase data.

Source grounding for CLI 0.2.0 + the exact native DLL model:
ValueCodec.ColorX initializes Link.colorX and sets RGBA but not Profile.
ResoniteLinkTypeMapper.ToEngine(colorX) uses ColorProfile enum 1 when Profile is
null; Renderite.Shared.ColorProfile.sRGB = 1. This is NOT an observed default.
The controller authenticates the seal and native ownership before this check.
This pure comparison grants no execution permission or native-success flag.
"""
from copy import deepcopy

from .drivegate_evidence import Verdict, _PHASE_TYPES, compare_typed
from .drivegate_layout import _model


def compare_initial_fields(seal: dict, fields: dict) -> Verdict:
    """Require every declared initial value, not merely Command/Done unused."""
    try:
        if seal['cliVersion'] != '0.2.0':
            raise ValueError('missing:initial-profile-source-for-cli')
        _model(seal['dllSha256'])  # Do not apply this source interpretation to another native ABI.
        declared = next(n for n in seal['normalizedFixture']['children']
                        if n['slot']['key'] == 'drivegate-fields')
        entries = {c['key'].removeprefix('drivegate-'):c for c in declared['components']}
        if set(entries) != set(_PHASE_TYPES) or set(fields) != set(_PHASE_TYPES):
            raise ValueError('initial-private-field-coverage')
        for name, typ in _PHASE_TYPES.items():
            raw = entries[name]['initialFields']['Value']
            if typ == 'ResoniteLink.colorX':
                if not isinstance(raw,list) or len(raw) != 4:
                    raise ValueError('initial-color-declaration')
                value = dict(zip(('r','g','b','a','profile'),[*raw,'sRGB']))
            else:
                value = deepcopy(raw)
            expected = {'kind':'field','id':fields[name]['id'],'type':typ,'value':value}
            checked = compare_typed(expected, fields[name])
            if checked.status != 'passed':
                return Verdict(checked.status, ('initial-private-value:' + name, *checked.issues))
        return Verdict('passed')
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration) as exc:
        return Verdict('failed', ('initial-private-evidence:' + str(exc),))
