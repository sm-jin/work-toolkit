"""자동 테스트: 가짜 뷰어로 캡처 → PDF → OCR 흐름을 확인한다.

실제 화면 캡처나 키 입력은 하지 않고, 데이터와 결과 PDF는 임시 폴더에만 만든 뒤 지운다.
실행: .venv/bin/python tests/run_tests.py   (윈도우: .venv\\Scripts\\python tests\\run_tests.py)
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="shotpdf-test-"))
os.environ["SHOTPDF_DATA"] = str(TMP / "data")  # 실제 data 폴더는 건드리지 않는다
OUT = TMP / "out"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app import pdfbuild, screen, store  # noqa: E402
from app import runner as R  # noqa: E402

R.PAGE_TIMEOUT = 1.5  # 마지막 쪽 판단을 빨리 하도록

try:
    FONT = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 34)
except OSError:
    FONT = ImageFont.load_default(size=34)


class FakeViewer:
    """쪽을 넘기면 잠깐 로딩 화면을 보였다가 새 쪽을 그리는 가짜 문서 뷰어."""

    def __init__(self, pages: int):
        self.pages, self.page, self.turned_at, self.front = pages, 1, 0.0, "Viewer"

    def grab(self, x, y, w, h):
        img = Image.new("RGB", (w * 2, h * 2), "white")
        d = ImageDraw.Draw(img)
        if time.monotonic() - self.turned_at < 0.3:
            d.text((40, 40), "Loading...", fill="gray", font=FONT)
        else:
            d.text((60, 60), f"Quarterly Report - Page {self.page}", fill="black", font=FONT)
            for i in range(4):
                d.text((60, 140 + i * 50), f"Item {self.page}.{i} revenue and budget review", fill="black", font=FONT)
        return screen.Shot(img, dpi=144)

    def press_key(self, _name):
        if self.page < self.pages:
            self.page += 1
            self.turned_at = time.monotonic()


def install(viewer: FakeViewer) -> None:
    screen.grab = viewer.grab
    screen.press_key = viewer.press_key
    screen.click = lambda x, y: None
    screen.mouse_position = lambda: (500, 500)
    screen.frontmost_app = lambda: viewer.front
    screen.screen_locked = lambda: False


def wait(r: R.Runner) -> dict:
    while r.busy:
        time.sleep(0.1)
    return r.status()


def capture(pages: int = 3, **overrides):
    viewer = FakeViewer(pages)
    install(viewer)
    r = R.Runner()
    job = r.start_capture(dict(CFG, **overrides))
    return viewer, r, job


CFG = dict(region={"x": 0, "y": 0, "w": 400, "h": 250}, next_mode="key", next_key="right", next_point=None,
           delay=0.3, max_pages=50, start_delay=0, focus_click=True, ocr=True, ocr_lang="eng",
           title="테스트 보고서", output_dir=str(OUT))


def test_capture_until_last_page():
    _, r, job = capture(pages=4)
    s = wait(r)
    j = store.load_job(job["id"])
    assert s["state"] == "done", s
    assert j["pages"] == 4, f"4쪽이어야 하는데 {j['pages']}쪽"
    assert (OUT / "테스트 보고서.pdf").exists()
    assert not (store.job_dir(job["id"]) / "image-only.pdf").exists(), "중간 파일이 남음"
    return f"4쪽 캡처 후 자동 종료, '{j['pdf']}' 생성"


def test_ocr_text_layer():
    _, r, job = capture(pages=2, title="ocr")
    wait(r)
    j = store.load_job(job["id"])
    if not pdfbuild.ocr_available():
        return f"건너뜀 — OCR 도구 없음 ({pdfbuild.install_hint()})"
    assert j["ocr"], j
    return f"OCR 포함 PDF 생성 (엔진: {pdfbuild.ocr_engine()})"


def test_ocr_off():
    _, r, job = capture(pages=2, title="no-ocr", ocr=False)
    wait(r)
    j = store.load_job(job["id"])
    assert not j["ocr"] and (OUT / "no-ocr.pdf").exists()
    return "OCR 끄면 이미지만 든 PDF"


def test_same_name_gets_number():
    for _ in range(2):
        _, r, _job = capture(pages=1, title="dup", ocr=False)
        wait(r)
    assert (OUT / "dup.pdf").exists() and (OUT / "dup (2).pdf").exists()
    return "같은 이름이면 'dup (2).pdf'로 저장 (덮어쓰지 않음)"


def test_max_pages():
    _, r, job = capture(pages=10, title="max", ocr=False, max_pages=3)
    wait(r)
    assert store.load_job(job["id"])["pages"] == 3
    return "최대 쪽수(3)에서 멈춤"


def test_stop_button():
    _, r, job = capture(pages=100, title="stop", ocr=False)
    time.sleep(1.5)
    r.stop()
    s = wait(r)
    assert s["state"] == "stopped", s
    return f"중지 버튼 → {store.load_job(job['id'])['pages']}쪽에서 중지"


def test_front_app_change_stops():
    viewer, r, _job = capture(pages=100, title="front", ocr=False)
    time.sleep(1.2)
    viewer.front = "Other App"
    s = wait(r)
    assert s["state"] == "stopped" and "Other App" in s["message"], s
    return "다른 앱이 앞으로 오면 자동 중지"


def test_rebuild():
    _, r, job = capture(pages=2, title="rebuild", ocr=False)
    wait(r)
    r = R.Runner()
    r.start_build(job["id"], {"ocr": False})
    s = wait(r)
    assert s["state"] == "done" and (OUT / "rebuild.pdf").exists() and not (OUT / "rebuild (2).pdf").exists()
    return "PDF 다시 만들기는 같은 이름으로 교체"


def test_output_folder_check():
    for bad in ("relative/folder",):
        try:
            store.resolve_output_dir(bad)
        except ValueError:
            continue
        raise AssertionError(f"잘못된 폴더를 통과시킴: {bad}")
    assert store.resolve_output_dir("") == ""
    return "상대 경로 저장 폴더는 거부, 빈 값은 앱 기본 폴더"


TESTS = [test_capture_until_last_page, test_ocr_text_layer, test_ocr_off, test_same_name_gets_number,
         test_max_pages, test_stop_button, test_front_app_change_stops, test_rebuild, test_output_folder_check]


def main() -> int:
    store.ensure_dirs()
    failed = 0
    print(f"플랫폼: {screen.PLATFORM} / OCR: {pdfbuild.ocr_engine() or '없음'}\n")
    try:
        for test in TESTS:
            start = time.monotonic()
            try:
                result = test()
                print(f"[통과] {test.__name__}: {result} ({time.monotonic() - start:.1f}초)")
            except Exception as e:  # 실패해도 나머지 테스트는 계속
                failed += 1
                print(f"[실패] {test.__name__}: {e}")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
