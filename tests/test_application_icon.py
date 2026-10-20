"""Icon artwork must reach both Qt and the packaged Windows executable."""
import ast
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

ROOT = Path(__file__).resolve().parents[1]
SIZES = {16, 20, 24, 32, 40, 48, 64, 128, 256}


def load_tray_function(root):
    # Isolate the pure icon loader; do not start services or read user data.
    tree = ast.parse((ROOT / "src/lilies/app.py").read_text("utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "tray_icon")
    scope = dict(QColor=QColor, QIcon=QIcon, QPainter=QPainter, QPen=QPen,
                 QPixmap=QPixmap, project_root=lambda: root)
    exec(compile(ast.Module(body=[function], type_ignores=[]), "icon-loader", "exec"), scope)
    return scope["tray_icon"]


def test_master_has_real_transparency():
    with Image.open(ROOT / "assets/icons/lilies-app.png") as image:
        assert image.width == image.height and image.width >= 1024
        assert image.mode == "RGBA"
        assert image.getchannel("A").getextrema() == (0, 255)
        assert image.getpixel((0, 0))[3] == 0


def test_ico_contains_small_taskbar_and_high_dpi_resolutions():
    with Image.open(ROOT / "assets/icons/lilies-app.ico") as image:
        assert image.ico.sizes() == {(n, n) for n in SIZES}
        for size in image.ico.sizes():
            assert image.ico.getimage(size).mode == "RGBA"


def test_qt_loads_the_same_artwork():
    icon = load_tray_function(ROOT)()
    assert {s.width() for s in icon.availableSizes()} == SIZES
    expected = QIcon(str(ROOT / "assets/icons/lilies-app.ico"))
    for n in SIZES:
        assert icon.pixmap(QSize(n, n)).toImage() == expected.pixmap(QSize(n, n)).toImage()


def test_incomplete_checkout_has_safe_fallback(tmp_path):
    assert not load_tray_function(tmp_path)().isNull()


def test_packaging_embeds_the_icon():
    spec = (ROOT / "LiliesInTheBox.spec").read_text("utf-8")
    assert "icon=str(Path(SPECPATH) / 'assets' / 'icons' / 'lilies-app.ico')" in spec
    app = (ROOT / "src/lilies/app.py").read_text("utf-8")
    assert "app.setWindowIcon(tray_icon())" in app
    assert "QSystemTrayIcon(tray_icon(), app)" in app
