"""설치 점검: 화면·입력·OCR 기능이 이 PC에서 동작하는지 확인한다. 키 입력이나 클릭은 보내지 않는다.

맥: .venv/bin/python -m app.check     윈도우: check.bat
"""
import sys
import traceback

from . import pdfbuild, screen


def step(label, fn):
    try:
        print(f"[OK]   {label}: {fn()}")
    except Exception as e:  # 진단용이라 어떤 오류든 보여주고 다음 항목으로
        print(f"[실패] {label}: {e}")
        traceback.print_exc(limit=2, file=sys.stdout)


def grab_sample():
    displays = screen.displays()
    main = next((d for d in displays if d["main"]), displays[0])
    shot = screen.grab(main["x"], main["y"], 200, 100)
    return f"{shot.image.size[0]}×{shot.image.size[1]} 픽셀, PNG {len(shot.png):,} bytes"


def keep_awake_sample():
    with screen.keep_awake():
        pass
    return "사용 가능"


def main() -> None:
    print(f"플랫폼: {screen.PLATFORM} / Python {sys.version.split()[0]}")
    step("권한", lambda: screen.permissions() or "별도 권한 필요 없음")
    for warning in screen.startup_warnings():
        print(f"[경고] {warning}")
    step("디스플레이", lambda: "; ".join(
        f"{d['name']} {d['w']}×{d['h']} @({d['x']},{d['y']})" for d in screen.displays()))
    step("화면 잠김", screen.screen_locked)
    step("맨 앞 창", screen.frontmost_app)
    step("마우스 위치", screen.mouse_position)
    step("캡처 (주 모니터 왼쪽 위 200×100)", grab_sample)
    step("절전 방지", keep_awake_sample)
    step("OCR 엔진", lambda: pdfbuild.ocr_engine() or f"없음 — {pdfbuild.install_hint()}")
    step("OCR 언어", pdfbuild.tesseract_langs)


if __name__ == "__main__":
    main()
