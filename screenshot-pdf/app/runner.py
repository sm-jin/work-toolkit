"""캡처 작업 실행기.

한 번에 한 작업(캡처 또는 PDF 생성)만 백그라운드 스레드에서 돈다.
페이지 넘김 → 화면이 바뀌고 렌더링이 멈출 때까지 대기 → 저장을 반복하다가,
넘겨도 화면이 그대로면 마지막 페이지로 보고 PDF(기본으로 OCR 글자 층 포함)를 만든다.
"""
import collections
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

from PIL import Image, ImageChops

from . import pdfbuild, screen, store

STABLE_INTERVAL = 0.4  # 렌더링이 끝났는지 보려고 연달아 찍는 간격(초)
PAGE_TIMEOUT = 10.0    # 넘긴 뒤 이 시간 동안 화면이 그대로면 마지막 페이지로 판단(초)


class Busy(RuntimeError):
    pass


class Stopped(Exception):
    pass


def differs(a: Image.Image, b: Image.Image) -> bool:
    """두 캡처가 다른 화면인지. 쪽번호 한 글자 차이도 잡을 만큼 민감하게 본다."""
    if a.size != b.size:
        return True
    diff = ImageChops.difference(a.convert("L"), b.convert("L"))
    changed = diff.point(lambda v: 255 if v > 24 else 0).histogram()[255]
    return changed >= 12


def turn_page(cfg: dict) -> None:
    if cfg["next_mode"] == "click":
        screen.click(cfg["next_point"]["x"], cfg["next_point"]["y"])
    else:
        screen.press_key(cfg["next_key"])


def focus_viewer(cfg: dict) -> None:
    """캡처 영역 가운데를 한 번 클릭해 뷰어 창이 키 입력을 받게 한다."""
    if cfg.get("focus_click"):
        r = cfg["region"]
        screen.click(r["x"] + r["w"] // 2, r["y"] + r["h"] // 2)
        time.sleep(0.5)


class Runner:
    def __init__(self):
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._log = collections.deque(maxlen=200)
        self._status = {"state": "idle", "job_id": None, "pages": 0, "countdown": 0, "message": "대기 중"}

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        with self._lock:
            s = dict(self._status)
            s["log"] = list(self._log)[-40:]
        s["busy"] = self.busy
        return s

    def stop(self) -> None:
        self._stop.set()

    def start_capture(self, cfg: dict) -> dict:
        with self._start_lock:
            if self.busy:
                raise Busy("이미 실행 중인 작업이 있습니다")
            job = store.new_job(cfg.get("title", ""), cfg.get("output_dir", ""))
            self._launch(self._capture_job, job, cfg)
            return job

    def start_build(self, job_id: str, cfg: dict) -> dict:
        with self._start_lock:
            if self.busy:
                raise Busy("이미 실행 중인 작업이 있습니다")
            job = store.load_job(job_id)
            self._launch(self._build_job, job, cfg)
            return job

    # ------------------------------------------------------------ 내부

    def _launch(self, target, job: dict, cfg: dict) -> None:
        self._stop.clear()
        with self._lock:
            self._status = {"state": "starting", "job_id": job["id"], "pages": job["pages"],
                            "countdown": 0, "message": "준비 중"}
        self._thread = threading.Thread(target=self._guard, args=(target, job, cfg), daemon=True)
        self._thread.start()

    def _guard(self, target, job: dict, cfg: dict) -> None:
        try:
            target(job, cfg)
        except Stopped as e:
            msg = str(e) or "사용자가 중지했습니다"
            job.update(status="stopped", message=msg)
            self._set(state="stopped", message=msg)
            self._say(msg)
        except Exception as e:
            traceback.print_exc()
            job.update(status="error", message=str(e))
            self._set(state="error", message=str(e))
            self._say(f"오류: {e}")
        finally:
            job["pages"] = len(store.page_files(job["id"]))
            store.save_job(job)

    def _set(self, **kw) -> None:
        with self._lock:
            self._status.update(kw)

    def _say(self, msg: str) -> None:
        with self._lock:
            self._log.append(f"{time.strftime('%H:%M:%S')} {msg}")

    def _sleep(self, sec: float) -> None:
        if self._stop.wait(sec):
            raise Stopped()

    def _capture_job(self, job: dict, cfg: dict) -> None:
        region = cfg["region"]
        count = 0
        prev = None  # 직전에 저장한 쪽 (다음 쪽으로 넘어갔는지 비교용)
        self._say(f"'{job['title']}' 캡처 준비")

        with screen.keep_awake():
            for s in range(int(cfg["start_delay"]), 0, -1):
                self._set(state="countdown", countdown=s, message=f"{s}초 후 시작")
                self._sleep(1)
            self._set(state="capturing", countdown=0, message="캡처 중")
            if screen.screen_locked():
                raise RuntimeError("화면이 잠겨 있습니다. 잠금을 풀고 다시 시작하세요.")
            focus_viewer(cfg)
            front = screen.frontmost_app()
            self._say(f"캡처 시작 (맨 앞 앱: {front or '알 수 없음'})")

            max_pages = int(cfg["max_pages"])
            while count < max_pages:
                shot = self._wait_for_page(region, prev, float(cfg["delay"]))
                if shot is None:
                    self._say(f"넘겨도 화면이 바뀌지 않아 마지막 쪽으로 판단했습니다 (총 {count}쪽)")
                    break
                count += 1
                store.page_path(job["id"], count).write_bytes(shot.png)
                job["pages"] = count
                store.save_job(job)
                self._set(pages=count, message=f"{count}쪽 저장")
                prev = shot.image
                if count >= max_pages:
                    self._say(f"최대 쪽수({max_pages})에 도달해 멈췄습니다. 쪽이 더 있으면 최대 쪽수를 늘려 다시 캡처하세요.")
                    break
                self._check_safety(front)
                turn_page(cfg)

        if count == 0:
            raise RuntimeError("캡처된 쪽이 없습니다")
        self._build_job(job, cfg)

    def _wait_for_page(self, region: dict, prev: Optional[Image.Image], delay: float):
        """새 쪽이 다 그려질 때까지 기다렸다가 캡처를 돌려준다. 끝내 안 바뀌면 None."""
        self._sleep(delay if prev is not None else 0)
        deadline = time.monotonic() + max(PAGE_TIMEOUT, delay * 3)
        last = screen.grab(**region)
        while True:
            if prev is None or differs(last.image, prev):
                # 바뀌었으면 한 번 더 찍어서 렌더링이 멈췄는지 확인
                self._sleep(STABLE_INTERVAL)
                cur = screen.grab(**region)
                if not differs(cur.image, last.image) or time.monotonic() > deadline:
                    return cur
                last = cur
            else:
                if time.monotonic() > deadline:
                    return None
                self._sleep(0.5)
                last = screen.grab(**region)

    def _check_safety(self, front: Optional[str]) -> None:
        x, y = screen.mouse_position()
        if x <= 2 and y <= 2:
            raise Stopped("마우스가 화면 왼쪽 위 모서리로 옮겨져 중지했습니다")
        if screen.screen_locked():
            raise Stopped("화면이 잠겨 중지했습니다")
        now = screen.frontmost_app()
        if front and now and now != front:
            raise Stopped(f"맨 앞 앱이 '{front}'에서 '{now}'(으)로 바뀌어 중지했습니다 (키 입력이 다른 앱으로 가지 않도록)")

    def _build_job(self, job: dict, cfg: dict) -> None:
        pages = store.page_files(job["id"])
        if not pages:
            raise RuntimeError("PDF로 만들 쪽이 없습니다")
        # 다시 만들 때는 이 작업이 전에 만든 파일만 지운다 (ocr_pdf는 PDF를 두 개 만들던 예전 버전의 작업)
        for key in ("pdf", "ocr_pdf"):
            old = store.output_path(job, key)
            if old:
                old.unlink(missing_ok=True)
                job[key] = None
        folder = store.output_folder(job)
        folder.mkdir(parents=True, exist_ok=True)
        wanted = store.safe_name(job["title"])
        name = store.free_stem(folder, wanted)
        if name != wanted:
            self._say(f"같은 이름의 파일이 있어 '{name}'(으)로 저장합니다")
        out = folder / f"{name}.pdf"

        job.update(status="building", pages=len(pages), message="", ocr=False)
        store.save_job(job)
        self._set(state="building", pages=len(pages), message=f"PDF 생성 중 ({len(pages)}쪽)")
        # 이미지만 든 PDF: OCR의 입력이자, OCR을 안 하거나 실패했을 때의 결과물
        plain = store.job_dir(job["id"]) / "image-only.pdf"
        pdfbuild.build_pdf(pages, plain)
        try:
            job["ocr"] = self._ocr(job, cfg, pages, plain, out)
        finally:
            if not out.exists():
                shutil.move(str(plain), str(out))
            plain.unlink(missing_ok=True)
            job["pdf"] = out.name

        self._say(f"PDF 저장: {out} ({len(pages)}쪽" + (", 텍스트 검색 가능)" if job["ocr"] else ")"))
        job["status"] = "done"
        self._set(state="done", message=f"완료 — {len(pages)}쪽")

    def _ocr(self, job: dict, cfg: dict, pages: list, plain: Path, out: Path) -> bool:
        """검색용 텍스트 층을 넣은 PDF를 out에 만든다. 못 했으면 False (이미지만 든 PDF로 대신 저장된다)."""
        if not cfg.get("ocr"):
            return False
        if not pdfbuild.ocr_available():
            self._say(f"OCR 도구가 없어 텍스트 검색 없이 저장합니다 ({pdfbuild.install_hint()})")
            return False
        job["status"] = "ocr"
        store.save_job(job)
        lang = cfg.get("ocr_lang") or "eng"
        self._set(state="ocr", message=f"OCR 중 ({len(pages)}쪽, {lang}) — 쪽수에 따라 몇 분 걸립니다")
        try:
            done = pdfbuild.build_ocr(pages, plain, out, lang, should_stop=self._stop.is_set)
        except RuntimeError as e:
            self._say(f"{e} — 텍스트 검색 없이 저장합니다")
            job["message"] = "OCR 실패: 텍스트 검색 없이 저장됨"
            return False
        if not done:
            raise Stopped("OCR을 중지해 텍스트 검색 없이 저장했습니다")
        return True
