"""화면 캡처, 키보드/마우스 입력, 권한·잠금 확인. 운영체제에 맞는 구현을 고른다.

좌표는 모두 전역 화면 좌표다(맥: 포인트, 윈도우: 물리 픽셀). 주 모니터의 왼쪽 위가 (0, 0).
"""
import sys

from .base import CaptureError, Shot

__all__ = ["CaptureError", "Shot", "COORD_UNIT", "KEY_CODES", "PLATFORM", "click", "displays", "frontmost_app",
           "grab", "keep_awake", "mouse_position", "permissions", "press_key", "screen_locked", "startup_warnings"]

if sys.platform == "darwin":
    from .mac import (COORD_UNIT, KEY_CODES, PLATFORM, click, displays, frontmost_app, grab, keep_awake,
                      mouse_position, permissions, press_key, screen_locked, startup_warnings)
elif sys.platform == "win32":
    from .win import (COORD_UNIT, KEY_CODES, PLATFORM, click, displays, frontmost_app, grab, keep_awake,
                      mouse_position, permissions, press_key, screen_locked, startup_warnings)
else:
    raise ImportError("macOS와 Windows만 지원합니다")
