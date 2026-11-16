import struct

import pytest

from src.core import drone_group_recovery as recovery
from src.core import shared_drone_settings as codec


def text(value):
    return codec.Value(0x2e,value.encode())


def integer(value, variable=False):
    return codec.Value(0x2f,value.to_bytes(6,'little',signed=True)) if variable else codec.Value(3,struct.pack('<q',value))


def dictionary(values):
    return codec.Value(0x16,children=[node for key,value in values.items() for node in (value,text(key))])


def preferences(groups, variable=False, layout='current layout'):
    stamp=lambda value: codec.Value(0x2c,children=[integer(100),value])
    native={name:dictionary({'id':codec.Value(0x2c,children=[text(name),text(identity)]),
        'droneIDs':codec.Value(0x15,children=[integer(item,variable) for item in identifiers]),
        'label':text(name)}) for name,(identity,identifiers) in groups.items()}
    return codec.dumps(dictionary({'ui':dictionary({'droneBlah2':stamp(dictionary(native)),
        'layout':stamp(text(layout)), 'drones_favoriteGroupID_100':stamp(text('current favourite'))}),
        'unrelated':text('keep this too')}))


def memberships(data):
    return {name:set(identifiers) for name,identifiers in recovery.native_memberships(data).values()}


def test_restores_exact_owned_historical_memberships_for_arbitrary_group_names():
    current=preferences({'Blue squad':('original-a',[900]),'採掘':('original-b',[901])})
    history=preferences({'Blue squad':('original-a',[900,100]),'採掘':('original-b',[901,200,201])},layout='old layout')
    plan=recovery.plan_recovery(current,[history],{100,200,201})
    assert memberships(plan.candidate)=={'Blue squad':{900,100},'採掘':{901,200,201}}
    assert {g.name:g.drone_ids for g in plan.groups}=={'Blue squad':(100,),'採掘':(200,201)}
    root=codec.loads(plan.candidate)
    assert codec.get(root,'unrelated').text()=='keep this too'
    ui=codec.get(root,'ui')
    assert codec.get(ui,'layout').children[1].text()=='current layout'
    assert codec.get(ui,'drones_favoriteGroupID_100').children[1].text()=='current favourite'
    assert recovery.plan_recovery(plan.candidate,[history],{100,200,201}) is None


def test_ghost_group_names_do_not_assign_new_or_foreign_drones():
    current=preferences({'Miners':('same-key',[1])})
    history=preferences({'Miners':('same-key',[2,3])})
    assert recovery.plan_recovery(current,[history],{4,5}) is None
    plan=recovery.plan_recovery(current,[history],{2})
    assert memberships(plan.candidate)=={'Miners':{1,2}}


def test_matching_names_with_different_native_group_identity_are_not_recovered():
    current=preferences({'Miners':('current-key',[])})
    history=preferences({'Miners':('other-key',[100])})
    assert recovery.plan_recovery(current,[history],{100}) is None


def test_deleted_historical_group_does_not_override_a_retained_group_identity():
    current=preferences({'Miners':('retained-key',[])})
    obsolete=preferences({'Miners':('deleted-key',[100])})
    retained=preferences({'Miners':('retained-key',[100])})
    plan=recovery.plan_recovery(current,[obsolete,retained],{100})
    assert memberships(plan.candidate)=={'Miners':{100}}
    assert not plan.ambiguous_ids


def test_native_text_encodings_do_not_change_group_identity():
    current=preferences({'採掘':('same-key',[])})
    root=codec.loads(preferences({'採掘':('same-key',[100])}))
    setting=codec.get(codec.get(root,'ui'),'droneBlah2')
    group=codec.get(setting.children[1],'採掘')
    identity=codec.get(group,'id')
    identity.children=[codec.Value(0x12,node.text().encode('utf-16-le')) for node in identity.children]
    plan=recovery.plan_recovery(current,[codec.dumps(root)],{100})
    assert memberships(plan.candidate)=={'採掘':{100}}


def test_explicitly_cleared_groups_are_not_resurrected():
    assert recovery.plan_recovery(preferences({}),[preferences({'Old':('old-key',[100])})],{100}) is None


def test_uninitialized_profiles_get_definitions_but_only_verified_owned_members():
    history=preferences({'Own':('saved-key',[100,200])})
    seed=recovery.seed_missing_groups(None,history)
    assert memberships(seed)=={'Own':set()}
    plan=recovery.plan_recovery(seed,[history],{100})
    assert memberships(plan.candidate)=={'Own':{100}}
    assert recovery.seed_missing_groups(preferences({}),history) is None


def test_definition_recovery_preserves_current_non_drone_choices():
    current=codec.loads(preferences({},layout='fresh current layout'))
    ui=codec.get(current,'ui')
    ui.children=[node for k,v in codec.entries(ui) if k.text()!='droneBlah2' for node in (v,k)]
    seed=recovery.seed_missing_groups(codec.dumps(current),preferences({'Saved':('key',[100])},layout='old-layout'))
    assert codec.get(codec.get(codec.loads(seed),'ui'),'layout').children[1].text()=='fresh current layout'
    assert memberships(seed)=={'Saved':set()}


def test_current_assignment_wins_over_old_assignment_to_another_group():
    current=preferences({'A':('a',[100]),'B':('b',[])})
    history=preferences({'A':('a',[]),'B':('b',[100])})
    assert recovery.plan_recovery(current,[history],{100}) is None


def test_conflicting_history_is_reported_without_guessing_an_assignment():
    current=preferences({'A':('a',[]),'B':('b',[])})
    first=preferences({'A':('a',[100]),'B':('b',[])})
    second=preferences({'A':('a',[]),'B':('b',[100])})
    plan=recovery.plan_recovery(current,[first,second],{100})
    assert plan.candidate==current
    assert plan.groups==()
    assert plan.ambiguous_ids==(100,)


def test_native_long_item_ids_keep_their_encoding_and_do_not_overflow():
    drone=9_000_000_000_001
    current=preferences({'Working':('same-key',[])},variable=True)
    history=preferences({'Working':('same-key',[drone])},variable=True)
    plan=recovery.plan_recovery(current,[history],{drone})
    assert memberships(plan.candidate)=={'Working':{drone}}
    assert bytes.fromhex('2f06')+drone.to_bytes(6,'little',signed=True) in plan.candidate


def test_invalid_history_aborts_the_plan_without_mutating_current_data():
    current=preferences({'A':('a',[])})
    original=current[:]
    with pytest.raises(codec.SettingsFormatError):
        recovery.plan_recovery(current,[b'not native preference data'],{100})
    assert current==original


@pytest.mark.parametrize('owned',[[True],[1,True],[0],[-1],['100'],[2**63]])
def test_untrusted_owned_id_values_are_rejected(owned):
    with pytest.raises(ValueError):
        recovery.plan_recovery(preferences({'A':('a',[])}),[],owned)
