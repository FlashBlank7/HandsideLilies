from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

from PIL import Image


POSES = (
    "perch-prone",
    "title-sit",
    "edge-peek",
    "listening-live",
)


def _remove_speckles(image: Image.Image) -> Image.Image:
    """Drop disconnected generation dust while preserving antialiased alpha."""

    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    width, height = rgba.size
    opaque = bytearray(1 if value >= 16 else 0 for value in alpha.tobytes())
    visited = bytearray(width * height)
    components: list[list[int]] = []
    for start, active in enumerate(opaque):
        if not active or visited[start]:
            continue
        queue = deque([start])
        visited[start] = 1
        component: list[int] = []
        while queue:
            index = queue.popleft()
            component.append(index)
            x = index % width
            y = index // width
            for neighbor in (
                index - 1 if x else -1,
                index + 1 if x + 1 < width else -1,
                index - width if y else -1,
                index + width if y + 1 < height else -1,
            ):
                if neighbor >= 0 and opaque[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    queue.append(neighbor)
        components.append(component)

    if not components:
        return rgba
    largest_component = max(components, key=len)
    keep = bytearray(width * height)
    for index in largest_component:
        keep[index] = 1
    cleaned = bytearray(alpha.tobytes())
    for index, retained in enumerate(keep):
        if not retained:
            cleaned[index] = 0
    rgba.putalpha(Image.frombytes("L", rgba.size, bytes(cleaned)))
    return rgba


def split_sheet(source: Path, output_dir: Path) -> list[Path]:
    sheet = Image.open(source).convert("RGBA")
    half_width = sheet.width // 2
    half_height = sheet.height // 2
    boxes = (
        (0, 0, half_width, half_height),
        (half_width, 0, sheet.width, half_height),
        (0, half_height, half_width, sheet.height),
        (half_width, half_height, sheet.width, sheet.height),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for pose, box in zip(POSES, boxes, strict=True):
        cell = _remove_speckles(sheet.crop(box))
        bounds = cell.getchannel("A").getbbox()
        if bounds is None:
            raise ValueError(f"pose quadrant is empty: {pose}")
        subject = cell.crop(bounds)
        padding = max(18, round(max(subject.size) * 0.04))
        canvas = Image.new(
            "RGBA",
            (subject.width + padding * 2, subject.height + padding * 2),
            (0, 0, 0, 0),
        )
        canvas.alpha_composite(subject, (padding, padding))
        target = output_dir / f"lilith-pose-{pose}-v1.png"
        canvas.save(target, optimize=True)
        outputs.append(target)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Split a transparent 2x2 Lilith pose sheet.")
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    for output in split_sheet(args.source.resolve(), args.output_dir.resolve()):
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
