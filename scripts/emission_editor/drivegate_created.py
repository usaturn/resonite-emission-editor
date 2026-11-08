"""Source-grounded created-only fixture graph, NOT deploy/native permission.

Only alias IDs come from the observation. Values/types/metadata come from the
sealed normalized declaration, exact-DLL slot model and independently captured
ValueField/ObjectRoot/AI_GeneratedContent GetSyncMember/ComponentBase initializers.
CLI InspectAsync(ID) starts AddPaths at the inspected slot Name, not at Root.
"""
from copy import deepcopy
import json

from .drivegate_evidence import Verdict, _tree, compare_subtree
from .drivegate_initial import compare_initial_fields
from .drivegate_layout import _model
from .drivegate_package import GENERATED_CONTENT_KEY, verify_package
from .drivegate_runtime import _owned_scope


_COMMON = {'persistent':('System.Boolean',True), 'UpdateOrder':('System.Int32',0),
           'Enabled':('System.Boolean',True)}


def compare_created_fixture(tree, *, package, state_bytes, native_dll_sha256) -> Verdict:
    """Require exactly the complete declared fixture with all module parents empty.

    This pure graph check does not prove fresh session, absence-before-create,
    process/write provenance or protected preservation. CreatedStage supplies the
    original session/diff evidence; the create controller still owes preflight.
    """
    try:
        seal = verify_package(package)
        if native_dll_sha256 != seal['dllSha256']:
            raise ValueError('created-native-inventory')
        model = _model(native_dll_sha256)
        fields, stable, _, _ = _owned_scope(package,tree,state_bytes,())
        initial = compare_initial_fields(seal,fields)
        if initial.status != 'passed':
            return initial
        slots, components, issues = _tree(tree)
        if issues:
            raise ValueError('created-complete-tree')
        used = {'Root', *slots, *components}
        # Include stable slot members in the global bijection, not only component
        # members already covered by _owned_scope. No private/external ID alias.
        for node in [*(n for n,_ in slots.values()), *(n for n,_ in components.values())]:
            for member in node['members'].values():
                ident = member.get('id')
                if not isinstance(ident,str) or not ident or ident in used:
                    raise ValueError('created-duplicate-member-identity')
                used.add(ident)
        state = json.loads(state_bytes)
        declaration = seal['normalizedFixture']
        def field(typ,value,ident):
            return {'kind':'field','id':ident,'type':typ,'value':deepcopy(value)}
        def node(declared,parent,path):
            ident = state['slots'][declared['slot']['key']]['id']
            actual = slots[ident][0]
            result = {'id':ident,'name':declared['slot']['name'],'parentId':parent,
                      'path':path,'isReferenceOnly':False,'components':[],'children':[],'members':{}}
            if actual['members'].keys() != model['slotMembers'].keys():
                raise ValueError('created-slot-member-coverage')
            for name,shape in model['slotMembers'].items():
                member_id = actual['members'][name]['id']
                if name == 'Parent':
                    value = {'kind':'reference','id':member_id,'targetId':parent,
                             'targetType':'[FrooxEngine]FrooxEngine.Slot'}
                else:
                    raw = declared['slot']['name'] if name == 'Name' else shape['default']
                    value = field(shape['type'],raw,member_id)
                    if name != 'OrderOffset' and raw is not None:
                        result[name[0].lower()+name[1:]] = deepcopy(raw)
                result['members'][name] = value
            for comp in declared['components']:
                entry = state['components'][comp['key']]
                actual_comp = components[entry['id']][0]
                members = {k:field(t,v,actual_comp['members'][k]['id']) for k,(t,v) in _COMMON.items()}
                if comp['key'] == GENERATED_CONTENT_KEY:
                    members['Source'] = field('System.String',comp['fields']['Source'],actual_comp['members']['Source']['id'])
                elif comp['key'] != 'drivegate-objectroot':
                    name = comp['key'].removeprefix('drivegate-')
                    members['Value'] = deepcopy(initial.data[0][name])
                result['components'].append({'id':entry['id'],'type':entry['type'],'members':members})
            for child in declared.get('children',[]):
                result['children'].append(node(child,ident,path+'/'+child['slot']['name']))
            return result
        expected = node(declaration,'Root',declaration['slot']['name'])
        checked = compare_subtree(expected,tree)
        return Verdict(checked.status,checked.issues,(expected,fields,stable) if checked.status=='passed' else ())
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
        return Verdict('failed',('created-fixture:' + str(exc),))
