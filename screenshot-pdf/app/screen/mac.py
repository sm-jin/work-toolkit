"""macOS 구현: screencapture 캡처, Quartz 키/클릭 입력, 권한·잠금 확인.

좌표는 macOS 전역 좌표계의 포인트 단위다. Retina 화면에서는 캡처 이미지가 포인트의 2배 픽셀로 나온다.
"""
import ctypes
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from io import BytesIO
from typing import Optional

import Quartz
from PIL import Image

from .base import CaptureError, Shot

PLATFORM = "mac"
COORD_UNIT = "pt"

KEY_CODES = {
    "right": 124,
    "left": 123,
    "down": 125,
    "up": 126,
    "pagedown": 121,
    "pageup": 116,
    "space": 49,
    "return": 36,
}

_CG = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
_AX = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
for _fn in (_CG.CGPreflightScreenCaptureAccess, _CG.CGRequestScreenCaptureAccess, _AX.AXIsProcessTrusted):
    _fn.restype = ctypes.c_bool
    _fn.argtypes = []


def permissions() -> dict:
    return {
        "screen": bool(_CG.CGPreflightScreenCaptureAccess()),
        "accessibility": bool(_AX.AXIsProcessTrusted()),
    }


def startup_warnings() -> list:
    perms = permissions()
    warnings = []
    if not perms["screen"]:
        _CG.CGRequestScreenCaptureAccess()  # 권한 요청 창을 띄운다
        warnings.append("화면 기록 권한 없음: 시스템 설정 > 개인정보 보호 및 보안 > 화면 및 시스템 오디오 녹음에서"
                        " 이 터미널 앱을 켠 뒤 터미널을 완전히 종료했다가 다시 실행하세요.")
    if not perms["accessibility"]:
        warnings.append("손쉬운 사용 권한 없음: 시스템 설정 > 개인정보 보호 및 보안 > 손쉬운 사용에서 이 터미널 앱을 켜세요.")
    return warnings


@contextmanager
def keep_awake():
    """캡처하는 동안 디스플레이가 꺼지거나 잠자기에 들어가지 않게 한다."""
    proc = subprocess.Popen(["caffeinate", "-d", "-i", "-u", "-t", "43200"])
    try:
        yield
    finally:
        proc.terminate()


def displays() -> list:
    err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
    if err:
        raise CaptureError(f"디스플레이 목록을 읽지 못했습니다 (오류 {err})")
    main = Quartz.CGMainDisplayID()
    result = []
    for i, did in enumerate(ids[:count]):
        b = Quartz.CGDisplayBounds(did)
        result.append({
            "id": int(did),
            "main": did == main,
            "name": f"디스플레이 {i + 1}" + (" (주 모니터)" if did == main else ""),
            "x": int(b.origin.x),
            "y": int(b.origin.y),
            "w": int(b.size.width),
            "h": int(b.size.height),
        })
    return result


def grab(x: int, y: int, w: int, h: int) -> Shot:
    """전역 좌표 영역을 캡처한다. 마우스 커서는 찍히지 않는다."""
    fd, path = tempfile.mkstemp(prefix="shotpdf-", suffix=".png")
    os.close(fd)
    try:
        r = subprocess.run(
            ["screencapture", "-x", "-t", "png", "-R", f"{x},{y},{w},{h}", path],
            capture_output=True, text=True, timeout=20,
        )
        with open(path, "rb") as f:
            data = f.read()
    finally:
        os.unlink(path)
    if r.returncode != 0 or not data:
        detail = r.stderr.strip() or "알 수 없는 오류"
        if not permissions()["screen"]:
            detail += " — 서버를 실행한 터미널 앱에 '화면 및 시스템 오디오 녹음' 권한이 없습니다"
        raise CaptureError(f"화면 캡처 실패: {detail}")
    image = Image.open(BytesIO(data))
    image.load()
    return Shot(image, data)


def _post(event) -> None:
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def press_key(name: str) -> None:
    code = KEY_CODES[name]
    for down in (True, False):
        _post(Quartz.CGEventCreateKeyboardEvent(None, code, down))
        time.sleep(0.03)


def mouse_position() -> tuple:
    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return loc.x, loc.y


def click(x: float, y: float) -> None:
    """(x, y)를 클릭한 뒤 마우스를 원래 자리로 돌려놓는다."""
    origin = mouse_position()
    target = (float(x), float(y))
    for kind in (Quartz.kCGEventMouseMoved, Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        event = Quartz.CGEventCreateMouseEvent(None, kind, target, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, 1)
        _post(event)
        time.sleep(0.05)
    _post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, origin, Quartz.kCGMouseButtonLeft))


def screen_locked() -> bool:
    info = Quartz.CGSessionCopyCurrentDictionary()
    return bool(info and info.get("CGSSessionScreenIsLocked"))


def frontmost_app() -> Optional[str]:
    """키 입력을 받는 앱 이름. 키 입력이 엉뚱한 앱으로 가지 않는지 감시하는 데 쓴다."""
    def run(*args):
        return subprocess.run(["lsappinfo", *args], capture_output=True, text=True, timeout=3).stdout.strip()

    try:
        asn = run("front")
        line = run("info", "-only", "name", asn) if asn else ""
    except (OSError, subprocess.TimeoutExpired):
        return None
    # 출력 형식: "LSDisplayName"="Google Chrome"
    _, _, name = line.partition("=")
    return name.strip().strip('"') or None
