"""Read generated PNGs without editing pixels; derive measured input masks."""
import hashlib
import json
from pathlib import Path
from PIL import Image

ROOT = Path(r'F:\code\Lilies in the box\themes\first-encounter\assets')
ASSETS = {
    'poseMicroCornerGripV1': ('lilith-pose-corner-grip-v2.png', [.211, .399], [.420, .419]),
    'poseWindowProneV2': ('lilith-pose-window-curl-v3.png', [.500, .857], [.525, .514]),
    'poseWindowDangleV1': ('lilith-pose-window-dangle-v2.png', [.500, .502], [.505, .413]),
    'poseWideWindowSprawlV1': ('lilith-pose-window-prone-v3.png', [.305, .938], [.301, .811]),
    'poseEdgeLeanV1': ('lilith-pose-edge-lean-v2.png', [.042, .348], [.308, .408]),
}
result = {}
for key, (filename, anchor, cord) in ASSETS.items():
    path = ROOT / filename
    with Image.open(path) as im:
        assert im.mode == 'RGBA'
        w, h = im.size
        alpha = im.getchannel('A')
        px = alpha.load()
        # Conservative silhouette envelope with transparent canvas corners
        # excluded. No pixel, matte or generated artwork is altered here.
        rows = []
        step = max(1, h // 40)
        for y in range(0, h, step):
            xs = [x for x in range(w) if max(px[x, yy] for yy in range(y, min(h, y+step), 3)) >= 32]
            if len(xs) >= 6:
                rows.append((max(0, min(xs)-2)/w, min(1, (max(xs)+3)/w), y/h, min(h, y+step)/h))
        poly = [[round(a, 5), round(y, 5)] for a,b,y,bot in rows]
        poly += [[round(a, 5), round(bot, 5)] for a,b,y,bot in rows[-1:]]
        poly += [[round(b, 5), round(bot, 5)] for a,b,y,bot in reversed(rows)]
        poly += [[round(b, 5), round(y, 5)] for a,b,y,bot in rows[:1]]
        result[key] = dict(file=filename, pixelSize=[w,h], minimumSize=[w,h],
                           aspectRatio=w/h, anchor=anchor, cordAnchor=cord,
                           clickMask=dict(type='composite', polygons=[poly]),
                           sha256=hashlib.sha256(path.read_bytes()).hexdigest())
print(json.dumps(result))
