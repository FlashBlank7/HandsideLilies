"""Pure, logical-coordinate release snapping. No cursor hooks or window writes."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from .window_catalog import WindowRect


@dataclass(frozen=True)
class EdgeAttachment:
    edge: str
    fraction: float
    work_area: WindowRect
    host_handle: int = 0
    host_identity: str = ""


def logical_host(value: Mapping, screens: Sequence[Mapping]) -> dict:
    """Qt on Windows scales screen sizes, not screen origins (screen islands).

    Catalogue rectangles are native pixels. Only the habitat projection is
    converted; Dock and native window APIs keep their original coordinates.
    """
    result = dict(value)
    rect = WindowRect.from_value(value.get('rect'))
    work = WindowRect.from_value(value.get('workArea'))
    if rect is None or not screens:
        return result
    reference = work or rect
    def overlap(screen):
        origin_x, origin_y = screen['left'], screen['top']
        ratio = float(screen['dpr'])
        right = origin_x + screen['width'] * ratio
        bottom = origin_y + screen['height'] * ratio
        return max(0, min(reference.right, right)-max(reference.left, origin_x)) * max(0, min(reference.bottom, bottom)-max(reference.top, origin_y))
    screen = max(screens, key=overlap)
    if overlap(screen) <= 0:
        return result
    ratio = max(.5, float(screen['dpr']))
    ox, oy = screen['left'], screen['top']
    def converted(bounds):
        return WindowRect(round(ox+(bounds.left-ox)/ratio),round(oy+(bounds.top-oy)/ratio),
                          round(ox+(bounds.right-ox)/ratio),round(oy+(bounds.bottom-oy)/ratio)).to_dict()
    result['rect'] = converted(rect)
    if work is not None: result['workArea'] = converted(work)
    if result.get('titleBarHeight') is not None:
        result['titleBarHeight'] = float(result['titleBarHeight']) / ratio
    result['nativeDpi'] = result.get('dpi',96)
    result['dpi'] = 96  # geometry is now already in logical pixels
    return result


def choose_drop_edge(figure: WindowRect, work: WindowRect, host=None, *, threshold=32.0):
    """Snap to the visible character, not the large transparent Tool window.

    Only the current work window is eligible: hidden/background windows must
    not attract a release through a foreground application.
    """
    if figure.width <= 0 or figure.height <= 0 or work.width <= 0 or work.height <= 0:
        return None
    cx=(figure.left+figure.right)/2
    cy=(figure.top+figure.bottom)/2
    choices=[]
    for edge,distance,fraction in (
        ('left',abs(figure.left-work.left),(cy-work.top)/work.height),
        ('right',abs(figure.right-work.right),(cy-work.top)/work.height),
        ('top',abs(figure.top-work.top),(cx-work.left)/work.width),
        ('bottom',abs(figure.bottom-work.bottom),(cx-work.left)/work.width),
    ):
        choices.append((distance, 0, EdgeAttachment(edge,max(0,min(1,fraction)),work)))
    if host is not None and host.rect is not None and host.visible and not (
            host.minimized or host.maximized or host.full_screen or host.sensitive):
        rect=host.rect
        # A release touching a caption button is not a request to cover it.
        for edge,coordinate,start,end,p,q in (
            ('left',rect.left,rect.top,rect.bottom,cx,cy),
            ('right',rect.right,rect.top,rect.bottom,cx,cy),
            ('top',rect.top,rect.left,rect.right-112,cy,cx),
            ('bottom',rect.bottom,rect.left,rect.right,cy,cx),
        ):
            lo,hi=(figure.left,figure.right) if edge in ('left','right') else (figure.top,figure.bottom)
            # Edge crossing or an outer silhouette within 32 logical px.
            gap=max(lo-coordinate,coordinate-hi,0)
            if end <= start or q < start or q > end: continue
            if not (work.left <= cx <= work.right and work.top <= cy <= work.bottom): continue
            length=rect.height if edge in ('left','right') else rect.width
            origin=rect.top if edge in ('left','right') else rect.left
            choices.append((gap,1,EdgeAttachment(edge,max(0,min(1,(q-origin)/length)),work,
                                                host.handle,host.stable_app_identity)))
    distance,_,result=min(choices,key=lambda value:(value[0],value[1]))
    return result if math.isfinite(distance) and distance <= threshold else None
