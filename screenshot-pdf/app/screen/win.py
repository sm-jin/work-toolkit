"""Windows 구현: Pillow ImageGrab 캡처, SendInput 키/클릭 입력, 잠금·전면 창 확인.

좌표는 가상 화면의 물리 픽셀이다. 디스플레이 배율(125%, 150% 등)과 상관없이 좌표가 맞도록
프로세스를 모니터별 DPI 인식 모드로 둔다. 별도 권한 설정은 필요 없다.
"""
import ctypes
import os
import time
from contextlib import contextmanager
from ctypes import wintypes
from typing import Optional

from PIL import ImageGrab

from .base import CaptureError, Shot

PLATFORM = "windows"
COORD_UNIT = "px"

KEY_CODES = {
    "right": 0x27,
    "left": 0x25,
    "down": 0x28,
    "up": 0x26,
    "pagedown": 0x22,
    "pageup": 0x21,
    "space": 0x20,
    "return": 0x0D,
}
_EXTENDED_KEYS = {0x21, 0x22, 0x25, 0x26, 0x27, 0x28}  # 화살표·Page 키는 확장 키 플래그가 필요

_INPUT_MOUSE, _INPUT_KEYBOARD = 0, 1
_KEYEVENTF_EXTENDEDKEY, _KEYEVENTF_KEYUP = 0x0001, 0x0002
_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
_ES_CONTINUOUS, _ES_SYSTEM_REQUIRED, _ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002
_GA_ROOTOWNER = 3
_MONITOR_DEFAULTTONEAREST = 2
_MONITORINFOF_PRIMARY = 1
_DESKTOP_READOBJECTS = 0x0001
_UOI_NAME = 2
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                      ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _fn(dll, name, restype, *argtypes):
    f = getattr(dll, name)
    f.restype = restype
    f.argtypes = argtypes
    return f


_SendInput = _fn(_user32, "SendInput", wintypes.UINT, wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
_MapVirtualKeyW = _fn(_user32, "MapVirtualKeyW", wintypes.UINT, wintypes.UINT, wintypes.UINT)
_GetCursorPos = _fn(_user32, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
_SetCursorPos = _fn(_user32, "SetCursorPos", wintypes.BOOL, ctypes.c_int, ctypes.c_int)
_GetForegroundWindow = _fn(_user32, "GetForegroundWindow", wintypes.HWND)
_GetAncestor = _fn(_user32, "GetAncestor", wintypes.HWND, wintypes.HWND, wintypes.UINT)
_GetWindowThreadProcessId = _fn(_user32, "GetWindowThreadProcessId", wintypes.DWORD,
                                wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_EnumDisplayMonitors = _fn(_user32, "EnumDisplayMonitors", wintypes.BOOL,
                           wintypes.HDC, ctypes.POINTER(wintypes.RECT), _MONITORENUMPROC, wintypes.LPARAM)
_GetMonitorInfoW = _fn(_user32, "GetMonitorInfoW", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_MONITORINFOEXW))
_MonitorFromPoint = _fn(_user32, "MonitorFromPoint", wintypes.HANDLE, wintypes.POINT, wintypes.DWORD)
_OpenInputDesktop = _fn(_user32, "OpenInputDesktop", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_CloseDesktop = _fn(_user32, "CloseDesktop", wintypes.BOOL, wintypes.HANDLE)
_GetUserObjectInformationW = _fn(_user32, "GetUserObjectInformationW", wintypes.BOOL, wintypes.HANDLE,
                                 ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
_OpenProcess = _fn(_kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_QueryFullProcessImageNameW = _fn(_kernel32, "QueryFullProcessImageNameW", wintypes.BOOL,
                                  wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_CloseHandle = _fn(_kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
_SetThreadExecutionState = _fn(_kernel32, "SetThreadExecutionState", wintypes.DWORD, wintypes.DWORD)

try:
    _GetDpiForMonitor = _fn(ctypes.WinDLL("shcore"), "GetDpiForMonitor", ctypes.c_long, wintypes.HANDLE,
                            ctypes.c_int, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT))
except (OSError, AttributeError):
    _GetDpiForMonitor = None


def _set_dpi_awareness() -> None:
    try:
        f = _fn(_user32, "SetProcessDpiAwarenessContext", wintypes.BOOL, ctypes.c_ssize_t)
        if f(-4):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (Windows 10 1703+)
            return
    except AttributeError:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE (Windows 8.1+)
    except (OSError, AttributeError):
        _user32.SetProcessDPIAware()


_set_dpi_awareness()


def permissions() -> dict:
    return {}  # 윈도우는 화면 캡처·입력에 별도 권한이 필요 없다


def startup_warnings() -> list:
    return []


@contextmanager
def keep_awake():
    """캡처하는 동안 디스플레이가 꺼지거나 절전에 들어가지 않게 한다 (호출한 스레드 기준)."""
    _SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED)
    try:
        yield
    finally:
        _SetThreadExecutionState(_ES_CONTINUOUS)


def displays() -> list:
    infos = []

    def on_monitor(hmon, _hdc, _rect, _data):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if _GetMonitorInfoW(hmon, ctypes.byref(info)):
            infos.append(info)
        return True

    if not _EnumDisplayMonitors(None, None, _MONITORENUMPROC(on_monitor), 0):
        raise CaptureError("디스플레이 목록을 읽지 못했습니다")
    result = []
    for i, info in enumerate(infos):
        r = info.rcMonitor
        main = bool(info.dwFlags & _MONITORINFOF_PRIMARY)
        digits = "".join(ch for ch in info.szDevice if ch.isdigit())  # "\\.\DISPLAY2" → 2
        result.append({
            "id": int(digits) if digits else i + 1,
            "main": main,
            "name": f"디스플레이 {i + 1}" + (" (주 모니터)" if main else ""),
            "x": r.left,
            "y": r.top,
            "w": r.right - r.left,
            "h": r.bottom - r.top,
        })
    return result


def _dpi_at(x: int, y: int) -> int:
    if _GetDpiForMonitor:
        hmon = _MonitorFromPoint(wintypes.POINT(int(x), int(y)), _MONITOR_DEFAULTTONEAREST)
        dx, dy = wintypes.UINT(), wintypes.UINT()
        if _GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0 and dx.value:
            return dx.value
    return 96


def grab(x: int, y: int, w: int, h: int) -> Shot:
    try:
        image = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    except OSError as e:
        raise CaptureError(f"화면 캡처 실패: {e} — 화면이 잠겼거나 원격 데스크톱 창이 최소화된 상태일 수 있습니다")
    return Shot(image, dpi=_dpi_at(x + w // 2, y + h // 2))


def _send(inp: _INPUT) -> None:
    if _SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) != 1:
        raise CaptureError("키보드/마우스 입력이 거부되었습니다 (관리자 권한으로 실행 중인 창에는 입력할 수 없습니다)")


def press_key(name: str) -> None:
    vk = KEY_CODES[name]
    flags = _KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_KEYS else 0
    for up in (0, _KEYEVENTF_KEYUP):
        inp = _INPUT(type=_INPUT_KEYBOARD)
        inp.u.ki = _KEYBDINPUT(wVk=vk, wScan=_MapVirtualKeyW(vk, 0), dwFlags=flags | up)
        _send(inp)
        time.sleep(0.03)


def mouse_position() -> tuple:
    pt = wintypes.POINT()
    _GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def click(x: float, y: float) -> None:
    """(x, y)를 클릭한 뒤 마우스를 원래 자리로 돌려놓는다."""
    origin = mouse_position()
    _SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    for flag in (_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP):
        inp = _INPUT(type=_INPUT_MOUSE)
        inp.u.mi = _MOUSEINPUT(dwFlags=flag)
        _send(inp)
        time.sleep(0.05)
    _SetCursorPos(*origin)


def screen_locked() -> bool:
    """잠금 화면·로그인 화면·화면 보호기 상태면 입력 데스크톱이 'Default'가 아니다."""
    desk = _OpenInputDesktop(0, False, _DESKTOP_READOBJECTS)
    if not desk:
        return True
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        if not _GetUserObjectInformationW(desk, _UOI_NAME, name, ctypes.sizeof(name), ctypes.byref(needed)):
            return False
        return name.value.lower() != "default"
    finally:
        _CloseDesktop(desk)


def _process_name(pid: int) -> Optional[str]:
    handle = _OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if _QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return None
    finally:
        _CloseHandle(handle)


def frontmost_app() -> Optional[str]:
    """키 입력을 받는 창. 같은 Chrome이라도 다른 창이면 다르게 나오도록 창 번호를 붙인다."""
    hwnd = _GetForegroundWindow()
    if not hwnd:
        return None
    root = _GetAncestor(hwnd, _GA_ROOTOWNER) or hwnd  # 드롭다운 같은 팝업은 주인 창으로
    pid = wintypes.DWORD()
    _GetWindowThreadProcessId(root, ctypes.byref(pid))
    return f"{_process_name(pid.value) or '알 수 없는 앱'} (창 {root:#x})"
