"""Render only owned Lilies fixtures: never capture or inspect user windows."""
import argparse
import json
import math
import os
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--root', type=Path, default=Path(r'F:\code\Lilies in the box'))
p.add_argument('--output', type=Path, required=True)
p.add_argument('--gpu', action='store_true')
args = p.parse_args()
os.environ['QML_DISABLE_DISK_CACHE'] = '1'
os.environ['QT_QPA_PLATFORM'] = 'windows' if args.gpu else 'offscreen'
if args.gpu:
    os.environ['QSG_RHI_BACKEND'] = 'd3d11'
    os.environ.pop('QT_QUICK_BACKEND', None)
else:
    os.environ['QT_QUICK_BACKEND'] = 'software'

from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl, QPointF
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

class Backend(QObject):
    changed = Signal()
    def __init__(self):
        super().__init__()
        self.manifest = json.loads((args.root/'themes/first-encounter/theme.json').read_text(encoding='utf-8'))
        self.habitat = {}
        self.outfit = 'first-encounter'
    @Property('QVariantMap', constant=True)
    def themeManifest(self): return self.manifest
    @Property('QVariantMap', notify=changed)
    def habitatState(self): return self.habitat
    @Property('QVariantMap', notify=changed)
    def wardrobeState(self): return {'current': {'outfit_id':self.outfit}}
    @Property('QVariantMap', constant=True)
    def inputPulse(self): return {'cursorDirection':'none'}
    @Slot(str, result=QUrl)
    def assetUrl(self,key):
        value = self.manifest['assets'].get(key)
        return QUrl.fromLocalFile(str(args.root/'themes/first-encounter'/value)) if value else QUrl()

app = QGuiApplication([])
backend = Backend()
engine = QQmlApplicationEngine()
warnings = []
engine.warnings.connect(lambda values: warnings.extend(v.toString() for v in values))
engine.rootContext().setContextProperty('backend',backend)
engine.loadData(b'''import QtQuick
import QtQuick.Window
Window {
    width: 700; height: 620; x: -10000; y: -10000
    visible: true; color: "#404955"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
    Rectangle { x: 180; y: 330; width: 500; height: 250; color: "#596572" }
    V03PetBody {
        objectName: "subject"; width: 700; height: 620
        appBackend: backend; characterHeight: 340; paused: true
        pose: String(backend.habitatState.testPose || "idle-prayer")
    }
}''',QUrl.fromLocalFile(str(args.root/'qml/PoseRepairHarness.qml')))
assert engine.rootObjects(), warnings
window = engine.rootObjects()[0]
pet = window.findChild(QQuickItem,'subject')
frame = window.findChild(QQuickItem,'petPoseArtworkFrame')
assert pet and frame
args.output.mkdir(parents=True,exist_ok=True)
cases = [
 ('corner','micro-window-edge','micro-corner-grip','edge-peek-live','poseMicroCornerGripV1'),
 ('curl','small-title','compact-title-curl','title-sit','poseWindowProneV2'),
 ('dangle','medium-perch','window-perch','perch-prone','poseWindowDangleV1'),
 ('prone','large-perch','wide-window-sprawl','perch-prone','poseWideWindowSprawlV1'),
 ('wall','maximized-edge','screen-edge-watch','edge-peek-live','poseEdgeLeanV1'),
]
results = []
for name,profile,variant,pose,key in cases:
    for mirror in (False,True):
        backend.habitat = dict(attached=True,profile=profile,poseVariant=variant,
                               testPose=pose,mirror=mirror,characterScale=.85,
                               anchorNormX=.50,anchorNormY=330/620,
                               motionStyle='quiet-breathe')
        backend.changed.emit()
        for _ in range(60):
            QTest.qWait(50)
            if frame.property('activeSlotReady') and pet.property('renderedArtworkBlend') > .99 and not pet.property('poseTransitionRunning'):
                break
        QTest.qWait(150)
        spec = backend.manifest['character']['poseArtworkSpecs'][key]
        anchor = list(spec['anchor'])
        # The image itself always mirrors: every asymmetric contact must do so too.
        if mirror: anchor[0]=1-anchor[0]
        contact=frame.mapToItem(pet,QPointF(frame.width()*anchor[0],frame.height()*anchor[1]))
        error=math.hypot(contact.x()-350,contact.y()-330)
        cord=pet.property('supportCordPoint')
        record=dict(name=name,mirror=mirror,asset=str(pet.property('poseArtworkKey')),
                    ready=bool(frame.property('activeSlotReady')),blend=pet.property('renderedArtworkBlend'),
                    contactError=error, frame=[frame.x(),frame.y(),frame.width(),frame.height()],
                    aspectError=abs(frame.width()/frame.height()-spec['pixelSize'][0]/spec['pixelSize'][1]),
                    cordInside=bool(pet.containsCharacterInteractionPoint(cord.x(),cord.y())),
                    cornerTransparent=not bool(pet.containsCharacterPoint(frame.x()+1,frame.y()+1)))
        record['passed']=record['ready'] and record['blend']>.99 and record['asset']==key and error<.5 and record['aspectError']<.001 and record['cordInside'] and record['cornerTransparent']
        results.append(record)
        shot=window.grabWindow()
        assert not shot.isNull()
        shot.save(str(args.output/f'{name}-{int(mirror)}.png'))

# Other clothes must remain intact and must not be rotated 72 degrees to fake a prone pose.
backend.habitat=dict(attached=True,profile='large-perch',poseVariant='wide-window-sprawl',
                     testPose='perch-prone',mirror=False,characterScale=.85,
                     anchorNormX=.5,anchorNormY=.5)
outfits=[]
allowed=backend.manifest['character']['habitatPoseVariants']['wide-window-sprawl']['artworkOutfits']
for outfit in backend.manifest['character']['outfits']:
    backend.outfit=outfit
    backend.changed.emit()
    QTest.qWait(600)
    fallback=dict(outfit=outfit,usesArtwork=pet.property('usesPoseArtwork'),rotation=pet.property('renderedLayeredBodyRotation'))
    fallback['passed']=fallback['usesArtwork']==(outfit in allowed) and abs(fallback['rotation'])<10
    outfits.append(fallback)
report=dict(gpu=args.gpu,poses=results,outfits=outfits,warnings=warnings)
report['passed']=all(v['passed'] for v in results+outfits) and not warnings
(args.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
window.close()
raise SystemExit(0 if report['passed'] else 1)
