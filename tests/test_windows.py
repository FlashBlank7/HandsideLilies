from __future__ import annotations

import ctypes
from ctypes import wintypes

from lilies.core import windows


def test_window_list_excludes_cloaked_and_auxiliary_surfaces(monkeypatch):
    titles = {
        1: "正常应用",
        2: "被 DWM 隐藏的设置窗口",
        3: "工具浮窗",
        4: "被拥有的对话框",
        5: "显式任务栏应用",
    }

    class FakeUser32:
        @staticmethod
        def IsWindowVisible(_handle):
            return 1

        @staticmethod
        def GetWindowLongW(handle, _index):
            if handle == 3:
                return windows.WS_EX_TOOLWINDOW
            if handle == 5:
                return windows.WS_EX_TOOLWINDOW | windows.WS_EX_APPWINDOW
            return 0

        @staticmethod
        def GetWindow(handle, _command):
            return 99 if handle == 4 else 0

        @staticmethod
        def GetWindowTextLengthW(handle):
            return len(titles[handle])

        @staticmethod
        def GetWindowTextW(handle, buffer, _length):
            buffer.value = titles[handle]
            return len(titles[handle])

        @staticmethod
        def EnumWindows(callback, _lparam):
            for handle in titles:
                callback(handle, 0)
            return 1

    class FakeDwmApi:
        @staticmethod
        def DwmGetWindowAttribute(handle, _attribute, output, _size):
            target = ctypes.cast(output, ctypes.POINTER(wintypes.DWORD))
            raw_handle = int(getattr(handle, "value", handle) or 0)
            target.contents.value = 1 if raw_handle == 2 else 0
            return 0

    monkeypatch.setattr(windows, "user32", FakeUser32())
    monkeypatch.setattr(windows, "dwmapi", FakeDwmApi())

    found = windows.list_windows()

    assert [item["title"] for item in found] == ["正常应用", "显式任务栏应用"]
