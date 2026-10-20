"""Exercise Main.qml's real deferred release finalizer on an owned offscreen UI."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['QT_QUICK_BACKEND']='software'
os.environ['QT_QUICK_CONTROLS_STYLE']='Basic'
os.environ['QT_QPA_OFFSCREEN_SIZE']='1920x1080'
os.environ['QML_DISABLE_DISK_CACHE']='1'
sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QPoint
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem,QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from verify_compact_ui import OffscreenBackend,load_windows_ui_fonts
from lilies.paths import qml_path

output=ROOT/'artifacts/edge-drop-ui'
output.mkdir(parents=True,exist_ok=True)
temporary=tempfile.TemporaryDirectory(prefix='data-',dir=output)
os.environ['LILIES_DATA_DIR']=temporary.name
app=QApplication([])
load_windows_ui_fonts()
backend=OffscreenBackend(smoke=True,force_compact=True)
backend._v03_timer.stop()
backend._shell_monitor.stop()
area=dict(left=0,top=0,right=1920,bottom=1040,width=1920,height=1040,name='owned',devicePixelRatio=1)
backend.offscreen_work_areas=[area]
engine=QQmlApplicationEngine()
warnings=[]
engine.warnings.connect(lambda values:warnings.extend(v.toString() for v in values))
engine.rootContext().setContextProperty('backend',backend)
engine.rootContext().setContextProperty('diagnosticWindowProbe',False)
engine.load(str(qml_path()))
assert engine.rootObjects(),warnings
root=engine.rootObjects()[0]
window=root.findChild(QQuickWindow,'petWindow')
pet=root.findChild(QQuickItem,'compactLilith')
assert window and pet
results=[]
host=dict(handle=98765,processId=777,appId='owned-fixture',visible=True,minimized=False,
          rect=[300,260,1550,870],workArea=area,dpi=96)
for name,edge,target_x,target_y,has_host in (
    ('screen-left','left',4,400,False),('screen-right','right',1916,400,False),
    ('screen-top','top',800,4,False),('screen-bottom','bottom',800,1036,False),
    ('window-top','top',850,257,True),('window-left','left',295,500,True),
    ('window-right','right',1555,500,True),('window-bottom','bottom',850,873,True),
    ('free','none',850,600,True),
):
    backend.pet_habitat.update_foreground(host if has_host else None)
    backend.detachPetHabitat(500,350)
    QTest.qWait(550)
    w=float(pet.property('figureWidth'));h=float(pet.property('figureHeight'))
    figure_left=target_x-(w if edge=='right' else 0 if edge=='left' else w/2)
    figure_top=target_y-(h if edge in ('bottom',) or name=='window-top' else 0 if edge=='top' else h/2)
    window.setPosition(QPoint(round(figure_left-pet.property('figureLeft')),round(figure_top-pet.property('figureTop'))))
    window.setProperty('dragFinalizePending',True)
    assert window.finalizeMovedCharacterGesture()
    QTest.qWait(700)
    window.grabWindow() # own offscreen surface only; encourage animation completion
    QTest.qWait(150)
    state=backend.habitatState
    bounds=[window.x()+pet.property('figureLeft'),window.y()+pet.property('figureTop'),
            window.x()+pet.property('figureLeft')+pet.property('figureWidth'),
            window.y()+pet.property('figureTop')+pet.property('figureHeight')]
    record=dict(name=name,state=state['state'],reason=state['reason'],side=state['side'],
                pose=pet.property('pose'),variant=state['poseVariant'],
                usesArtwork=pet.property('usesPoseArtwork'),bounds=bounds)
    if edge=='none': record['passed']=not state['attached']
    else:
        record['passed']=state['attached'] and state['reason']=='edge-drop' and state['side']==edge and record['usesArtwork']
        record['inside']=bounds[0]>=-1 and bounds[1]>=-1 and bounds[2]<=1921 and bounds[3]<=1041
        record['passed']=record['passed'] and record['inside']
    results.append(record)
report=dict(passed=all(r['passed'] for r in results) and not warnings,cases=results,warnings=warnings)
(output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
for surface in app.topLevelWindows(): surface.close()
backend.shutdown()
QTest.qWait(50)
print(json.dumps(report,ensure_ascii=True))
raise SystemExit(0 if report['passed'] else 1)
