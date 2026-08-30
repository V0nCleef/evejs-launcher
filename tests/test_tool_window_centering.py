"""Synthetic PID/HWND attribution tests for external tool window placement."""

from __future__ import annotations

from typing import Any

import pytest

from src.core import platform_win


def _value(raw: object) -> int:
    value = getattr(raw, "value", raw)
    return 0 if value is None else int(value)


class _SnapshotKernel:
    def __init__(self, entries: list[tuple[int, int]]) -> None:
        self._entries = entries
        self._index = 0
        self.closed: list[int] = []

    def CreateToolhelp32Snapshot(self, _flags: object, _pid: object) -> int:  # noqa: N802
        return 5150

    def _write_entry(self, pointer: Any) -> bool:
        if self._index >= len(self._entries):
            return False
        pid, parent_pid = self._entries[self._index]
        self._index += 1
        pointer._obj.th32ProcessID = pid
        pointer._obj.th32ParentProcessID = parent_pid
        return True

    def Process32FirstW(self, _snapshot: object, pointer: Any) -> bool:  # noqa: N802
        self._index = 0
        return self._write_entry(pointer)

    def Process32NextW(self, _snapshot: object, pointer: Any) -> bool:  # noqa: N802
        return self._write_entry(pointer)

    def CloseHandle(self, handle: object) -> bool:  # noqa: N802
        self.closed.append(_value(handle))
        return True


class _WindowApi:
    def __init__(
        self,
        windows: list[dict[str, object]],
        work_area: tuple[int, int, int, int],
    ) -> None:
        self._windows = {int(window["hwnd"]): window for window in windows}
        self._order = [int(window["hwnd"]) for window in windows]
        self._work_area = work_area
        self.events: list[tuple[object, ...]] = []

    def EnumWindows(self, callback: object, lparam: object) -> bool:  # noqa: N802
        self.events.append(("enumerate",))
        for hwnd in self._order:
            if not callback(hwnd, lparam):  # type: ignore[operator]
                break
        return True

    def IsWindowVisible(self, hwnd: object) -> bool:  # noqa: N802
        return bool(self._windows[_value(hwnd)]["visible"])

    def GetWindowTextLengthW(self, hwnd: object) -> int:  # noqa: N802
        return len(str(self._windows[_value(hwnd)]["title"]))

    def GetWindowTextW(self, hwnd: object, buffer: Any, _size: int) -> int:  # noqa: N802
        title = str(self._windows[_value(hwnd)]["title"])
        buffer.value = title
        return len(title)

    def GetClassNameW(self, hwnd: object, buffer: Any, _size: int) -> int:  # noqa: N802
        class_name = str(self._windows[_value(hwnd)]["class_name"])
        buffer.value = class_name
        return len(class_name)

    def GetWindowThreadProcessId(self, hwnd: object, owner_pid: Any) -> int:  # noqa: N802
        owner_pid._obj.value = int(self._windows[_value(hwnd)]["pid"])
        return 1

    def GetWindowRect(self, hwnd: object, rect: Any) -> bool:  # noqa: N802
        left, top, right, bottom = self._windows[_value(hwnd)]["rect"]
        rect._obj.left = left
        rect._obj.top = top
        rect._obj.right = right
        rect._obj.bottom = bottom
        return True

    def MonitorFromWindow(self, hwnd: object, default: object) -> int:  # noqa: N802
        self.events.append(("monitor", _value(hwnd), _value(default)))
        return 9090

    def GetMonitorInfoW(self, monitor: object, info: Any) -> bool:  # noqa: N802
        self.events.append(("work-area", _value(monitor)))
        left, top, right, bottom = self._work_area
        info._obj.rcWork.left = left
        info._obj.rcWork.top = top
        info._obj.rcWork.right = right
        info._obj.rcWork.bottom = bottom
        return True

    def SetWindowPos(  # noqa: N802
        self,
        hwnd: object,
        _insert_after: object,
        x: int,
        y: int,
        width: int,
        height: int,
        flags: object,
    ) -> bool:
        self.events.append(
            ("position", _value(hwnd), x, y, width, height, _value(flags))
        )
        return True


def test_process_tree_snapshot_finds_out_of_order_nested_descendants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = _SnapshotKernel(
        [
            (303, 202),
            (999, 1),
            (202, 101),
            (101, 1),
        ]
    )
    monkeypatch.setattr(platform_win, "kernel32", kernel)

    assert platform_win._process_tree_pids(101) == {101, 202, 303}
    assert kernel.closed == [5150]


def test_tool_window_centering_requires_exact_title_and_owned_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _WindowApi(
        [
            {
                "hwnd": 11,
                "pid": 8000,
                "visible": True,
                "title": "EVE Client Code Grabber",
                "class_name": "TkTopLevel",
                "rect": (0, 0, 960, 940),
            },
            {
                "hwnd": 12,
                "pid": 4201,
                "visible": True,
                "title": "EVE Client Code Grabber",
                "class_name": "ConsoleWindowClass",
                "rect": (0, 0, 960, 940),
            },
            {
                "hwnd": 14,
                "pid": 4201,
                "visible": True,
                "title": "EVE Client Code Grabber - other",
                "class_name": "TkTopLevel",
                "rect": (0, 0, 960, 940),
            },
            {
                "hwnd": 13,
                "pid": 4201,
                "visible": True,
                "title": "EVE Client Code Grabber",
                "class_name": "TkTopLevel",
                "rect": (-120, -90, 840, 850),
            },
        ],
        work_area=(1920, 40, 3200, 760),
    )
    monkeypatch.setattr(platform_win, "user32", api)
    monkeypatch.setattr(
        platform_win,
        "_process_tree_pids",
        lambda root_pid: {root_pid, 4201},
    )

    assert platform_win.center_tool_window_for_process_tree(
        4200,
        "EVE Client Code Grabber",
        "TkTopLevel",
        anchor_hwnd=707,
    )
    assert api.events == [
        ("enumerate",),
        ("monitor", 707, 2),
        ("work-area", 9090),
        ("position", 13, 2080, 40, 960, 720, 0x14),
    ]


def test_tool_window_centering_never_moves_same_title_outside_owned_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _WindowApi(
        [
            {
                "hwnd": 88,
                "pid": 9999,
                "visible": True,
                "title": "EVE Client Code Grabber",
                "class_name": "TkTopLevel",
                "rect": (0, 0, 960, 940),
            }
        ],
        work_area=(0, 0, 1366, 728),
    )
    monkeypatch.setattr(platform_win, "user32", api)
    monkeypatch.setattr(platform_win, "_process_tree_pids", lambda _pid: {4200})

    assert not platform_win.center_tool_window_for_process_tree(
        4200,
        "EVE Client Code Grabber",
        "TkTopLevel",
        anchor_hwnd=707,
    )
    assert api.events == [("enumerate",)]


@pytest.mark.parametrize("root_pid", [0, -1, True, "42"])
def test_tool_window_centering_rejects_invalid_root_pid(root_pid: object) -> None:
    with pytest.raises(ValueError, match="positive process ID"):
        platform_win.center_tool_window_for_process_tree(  # type: ignore[arg-type]
            root_pid,
            "EVE Client Code Grabber",
            "TkTopLevel",
        )
