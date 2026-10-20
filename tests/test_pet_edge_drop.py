from types import SimpleNamespace
import math
import pytest

from lilies.core.pet_habitat import PetHabitatController, HabitatHost
from lilies.core.pet_snap import logical_host, choose_drop_edge
from lilies.core.window_catalog import WindowRect

WORK=WindowRect(0,0,1920,1040)
HOST=dict(handle=42,appId='test-editor',processId=7,rect=[300,250,1600,900],workArea=WORK.to_dict())

@pytest.mark.parametrize('edge,figure',[
    ('left',WindowRect(4,400,130,650)),
    ('right',WindowRect(1790,400,1917,650)),
    ('top',WindowRect(600,4,730,254)),
    ('bottom',WindowRect(600,788,730,1037)),
])
def test_screen_drop_immediately_reattaches_and_changes_pose(edge,figure):
    pet=PetHabitatController(clock=lambda:0,pet_width=480,pet_height=450)
    pet.detach()
    assert pet.release_near_edge(10,20,figure,WORK)
    state=pet.status()
    assert state['attached'] and state['reason']=='edge-drop'
    assert state['side']==edge and state['poseVariant']!='desktop-prayer'
    assert state['mirror']==(edge=='right')
    assert all(math.isfinite(float(state[key])) for key in ('x','y','anchorNormX','anchorNormY'))
    assert not pet.set_avoidance_position(600,400,WORK) # chosen edge must not run away

@pytest.mark.parametrize('edge,figure',[
    ('left',WindowRect(278,480,410,650)),
    ('right',WindowRect(1575,480,1705,650)),
    ('top',WindowRect(650,45,780,248)),
    ('bottom',WindowRect(650,880,780,1030)),
])
def test_work_window_drop_ignores_stability_delay_and_follows_move(edge,figure):
    pet=PetHabitatController(clock=lambda:0,pet_width=480,pet_height=450)
    pet.update_foreground(HOST)
    pet.detach()
    assert pet.release_near_edge(0,0,figure,WORK)
    before=pet.status()
    assert before['side']==edge and before['reason']=='edge-drop'
    assert before['stableSeconds']==0
    moved={**HOST,'rect':[350,280,1650,930]}
    pet.update_foreground(moved)
    after=pet.status()
    if edge in ('left','right'): assert after['anchorX']==before['anchorX']+50
    elif edge=='top': assert after['anchorY']==before['anchorY']+30

def test_drop_in_middle_stays_manual_and_click_does_not_reattach():
    pet=PetHabitatController(clock=lambda:99)
    pet.update_foreground(HOST,now=0)
    assert not pet.release_near_edge(500,500,WindowRect(750,450,870,650),WORK)
    assert pet.status()['reason']=='manual-detach'
    assert pet.desktop_x==500

@pytest.mark.parametrize('policy',['blocked','silent'])
def test_privacy_and_game_presence_never_snap(policy):
    pet=PetHabitatController()
    pet.set_presence(policy)
    assert not pet.release_near_edge(0,0,WindowRect(0,0,120,200),WORK)
    assert not pet.status()['visible']

@pytest.mark.parametrize('change',[{'minimized':True},{'visible':False},{'sensitive':True},{'fullScreen':True}])
def test_ineligible_work_window_never_attracts_drop(change):
    host=HabitatHost.from_value({**HOST,**change})
    assert choose_drop_edge(WindowRect(650,45,780,248),WORK,host) is None

def test_caption_buttons_do_not_attract_drop():
    host=HabitatHost.from_value(HOST)
    assert choose_drop_edge(WindowRect(1490,80,1570,245),WORK,host) is None

def test_new_drag_and_destroy_remove_old_attachment():
    pet=PetHabitatController()
    pet.update_foreground(HOST)
    pet.release_near_edge(0,0,WindowRect(650,45,780,248),WORK)
    pet.detach()
    assert not pet.status()['attached']
    pet.release_near_edge(0,0,WindowRect(650,45,780,248),WORK)
    pet.remove_host(42)
    assert not pet.status()['attached']

def test_hwnd_reuse_does_not_keep_manual_attachment():
    pet=PetHabitatController(clock=lambda:0)
    pet.update_foreground(HOST)
    pet.release_near_edge(0,0,WindowRect(650,45,780,248),WORK)
    pet.update_foreground({**HOST,'processId':88,'appId':'another-app'})
    assert pet.status()['reason']!='edge-drop'

@pytest.mark.parametrize('dpr',[1,1.25,1.5,2])
def test_catalog_to_qt_geometry_scales_once(dpr):
    value={**HOST,'rect':[300*dpr,250*dpr,1600*dpr,900*dpr],
           'workArea':[0,0,1920*dpr,1040*dpr],'titleBarHeight':32*dpr,'dpi':96*dpr}
    result=logical_host(value,[dict(left=0,top=0,width=1920,height=1080,dpr=dpr)])
    assert WindowRect.from_value(result['rect'])==WindowRect(300,250,1600,900)
    assert result['titleBarHeight']==32 and result['dpi']==96
    assert value['dpi']==96*dpr # catalogue object stays native

def test_mixed_dpi_negative_monitor_origin_is_not_scaled():
    value={**HOST,'rect':[-2360,400,-960,1600],'workArea':[-2560,0,0,2080],'dpi':192}
    result=logical_host(value,[dict(left=-2560,top=0,width=1280,height=1080,dpr=2),
                               dict(left=0,top=0,width=1920,height=1080,dpr=1)])
    assert WindowRect.from_value(result['rect'])==WindowRect(-2460,200,-1760,800)

def test_backend_release_validates_geometry_and_does_not_scan_windows():
    from lilies.backend import Backend
    pet=PetHabitatController(clock=lambda:0)
    fake=SimpleNamespace(pet_habitat=pet,
        screenWorkAreaAt=lambda x,y: WORK.to_dict(),_sync_habitat_state=lambda:None)
    placement=dict(x=0,y=100,width=480,height=450,figureLeft=0,figureTop=100,figureWidth=140,figureHeight=260)
    assert Backend.finishPetPlacement(fake,placement)
    assert pet.status()['reason']=='edge-drop'
    assert not Backend.finishPetPlacement(fake,{**placement,'x':float('nan')})
    assert not Backend.finishPetPlacement(fake,{**placement,'width':-1})
