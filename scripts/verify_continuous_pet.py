"""Owned QML fixture only; no user windows or data are read."""
import argparse
import json
import os
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--gpu', action='store_true')
p.add_argument('--root', type=Path, default=Path(r'F:\code\Lilies in the box'))
p.add_argument('--output', type=Path, required=True)
args = p.parse_args()
os.environ['QML_DISABLE_DISK_CACHE'] = '1'
if args.gpu:
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    os.environ['QSG_RHI_BACKEND'] = 'd3d11'
    os.environ.pop('QT_QUICK_BACKEND', None)
else:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['QT_QUICK_BACKEND'] = 'software'

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

app = QGuiApplication([])
engine = QQmlApplicationEngine()
warnings = []
engine.warnings.connect(lambda values: warnings.extend(v.toString() for v in values))
engine.rootContext().setContextProperty('testAsset', QUrl.fromLocalFile(str(args.root / 'themes/first-encounter/assets/lilith-desktop-pet-chibi-v4.png')))
engine.loadData(b'''import QtQuick
import QtQuick.Window
Window {
    width: 360; height: 560; x: -10000; y: -10000
    visible: true; color: "#404955"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
    ContinuousPetImage {
        objectName: "subject"; x: 39; y: 29
        width: 282; height: 502; source: testAsset
    }
}''', QUrl.fromLocalFile(str(args.root / 'qml/ContinuousPetHarness.qml')))
assert engine.rootObjects(), warnings
window = engine.rootObjects()[0]
subject = window.findChild(QQuickItem, 'subject')
for _ in range(80):
    QTest.qWait(50)
    if subject.property('imageStatus') == 1:
        break
assert subject.property('imageStatus') == 1, warnings
args.output.mkdir(parents=True, exist_ok=True)
directions = [(0,0),(0,-1),(1,-1),(1,0),(1,1),(0,1),(-1,1),(-1,0),(-1,-1)]
frames = []
from PySide6.QtCore import QPointF
for i, (x, y) in enumerate(directions):
    subject.setProperty('direction', QPointF(x,y))
    subject.setProperty('phase', i * .65)
    QTest.qWait(90)
    shot = window.grabWindow()
    assert not shot.isNull(), f'No frame: {i}'
    points = [(x,y) for y in range(0,shot.height(),6) for x in range(0,shot.width(),6)
              if shot.pixelColor(x,y).red() > 155 and shot.pixelColor(x,y).green() > 155]
    assert points, f'No character pixels: {i}'
    bounds = [min(x for x,y in points)/shot.width(), min(y for x,y in points)/shot.height(),
              max(x for x,y in points)/shot.width(), max(y for x,y in points)/shot.height()]
    frames.append(dict(direction=[x,y], bounds=bounds,
                       intact=bounds[0] > .10 and bounds[2] < .90 and bounds[1] > .025 and bounds[3] > .85))
    if i in (0,4,8):
        assert shot.save(str(args.output / f'continuous-{i}.png'))
report = dict(gpuRequested=args.gpu, gpuAvailable=subject.property('gpuAvailable'),
              deformationReady=subject.property('deformationReady'),
              imageReady=subject.property('imageStatus') == 1,
              shaderLog=subject.property('shaderLog'), directionsChecked=len(directions), frames=frames, warnings=warnings)
report['geometry'] = dict(window=[window.width(),window.height(),window.devicePixelRatio()],
                         subject=[subject.x(),subject.y(),subject.width(),subject.height()],
                         children=[dict(name=c.objectName(), x=c.x(),y=c.y(),w=c.width(),h=c.height())
                                   for c in subject.childItems()])
report['passed'] = not warnings and all(f['intact'] for f in frames) and report['imageReady'] and (not args.gpu or report['deformationReady'])
(args.output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
window.close()
raise SystemExit(0 if report['passed'] else 1)
