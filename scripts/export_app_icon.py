"""Package the generated transparent master as a standard Windows ICO.

This performs format conversion / resolution export only, not artwork edits.
"""
from pathlib import Path

from PIL import Image


def export_icon(root: Path) -> Path:
    directory = root / "assets" / "icons"
    source = directory / "lilies-app.png"
    target = directory / "lilies-app.ico"
    with Image.open(source) as image:
        if image.width != image.height or image.mode != "RGBA":
            raise ValueError("The generated master must be a square RGBA PNG")
        if image.getchannel("A").getextrema() != (0, 255):
            raise ValueError("The generated master must include real transparency")
        image.save(target, format="ICO", sizes=[(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    return target


if __name__ == "__main__":
    print(export_icon(Path(__file__).resolve().parents[1]))
