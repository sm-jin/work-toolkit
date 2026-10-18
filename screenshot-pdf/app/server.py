"""웹 서버: 좌표 설정 UI와 캡처 제어 API.

실행: ./run.sh (맥) / run.bat (윈도우)  — 환경변수 SHOTPDF_HOST, SHOTPDF_PORT로 주소 변경
"""
import os
import secrets
import socket
import subprocess
import threading
import time
from io import BytesIO
from typing import Literal, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from . import desktop, pdfbuild, screen, store
from .runner import Busy, Runner, differs, focus_viewer, turn_page

COOKIE = "shotpdf_token"
LANG_PATTERN = r"^[A-Za-z_]+(\+[A-Za-z_]+)*$"
NO_STORE = {"Cache-Control": "no-store"}

TOKEN = store.get_token()
runner = Runner()
manual_lock = threading.Lock()  # 테스트 캡처/넘김 테스트가 겹치지 않게
picker_lock = threading.Lock()  # 폴더 선택 창은 한 번에 하나만

app = FastAPI(title="Screenshot + PDF", docs_url=None, redoc_url=None, openapi_url=None)


class Region(BaseModel):
    x: int
    y: int
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class Point(BaseModel):
    x: int
    y: int


class CaptureConfig(BaseModel):
    display_id: Optional[int] = None
    region: Optional[Region] = None
    next_mode: Literal["key", "click"] = "key"
    next_key: str = "right"
    next_point: Optional[Point] = None
    delay: float = Field(1.5, ge=0.2, le=30)
    max_pages: int = Field(300, ge=1, le=3000)
    start_delay: int = Field(3, ge=0, le=60)
    focus_click: bool = True
    ocr: bool = True
    ocr_lang: str = Field("eng", pattern=LANG_PATTERN)
    title: str = Field("", max_length=100)       # PDF 파일 이름 (확장자 제외)
    output_dir: str = Field("", max_length=1000)  # 저장 폴더. 비우면 앱 내부 작업 폴더


class StartBody(BaseModel):
    config: CaptureConfig


class BuildBody(BaseModel):
    ocr: bool = True
    ocr_lang: str = Field("eng", pattern=LANG_PATTERN)


class LoginBody(BaseModel):
    token: str


class PickFolderBody(BaseModel):
    initial: str = Field("", max_length=1000)


def field_limits() -> dict:
    """숫자 설정의 허용 범위. 웹 화면이 이 값으로 범위를 표시하고 입력을 맞춘다."""
    limits = {}
    for name in ("delay", "max_pages", "start_delay"):
        lim = {}
        for rule in CaptureConfig.model_fields[name].metadata:
            if getattr(rule, "ge", None) is not None:
                lim["min"] = rule.ge
            if getattr(rule, "le", None) is not None:
                lim["max"] = rule.le
        limits[name] = lim
    return limits


def load_config() -> CaptureConfig:
    try:
        return CaptureConfig(**store.load_config())
    except ValidationError:
        return CaptureConfig()


def ready_config(cfg: CaptureConfig) -> dict:
    if cfg.region is None:
        raise HTTPException(400, "캡처 영역을 먼저 지정하세요")
    if cfg.next_mode == "click" and cfg.next_point is None:
        raise HTTPException(400, "'다음' 버튼 위치를 먼저 지정하세요")
    if cfg.next_key not in screen.KEY_CODES:
        raise HTTPException(400, f"지원하지 않는 키입니다: {cfg.next_key}")
    return cfg.model_dump()


def load_job_or_404(job_id: str) -> dict:
    try:
        return store.load_job(job_id)
    except FileNotFoundError:
        raise HTTPException(404, "작업을 찾을 수 없습니다")


def job_view(job: dict) -> dict:
    view = dict(job)
    path = store.output_path(job, "pdf")
    view["pdf_size"] = path.stat().st_size if path and path.exists() else None
    view["output_display"] = store.display_path(job["output_dir"]) if job.get("output_dir") else ""
    return view


@app.middleware("http")
async def require_token(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path != "/api/login":
        token = request.cookies.get(COOKIE, "")
        if not secrets.compare_digest(token.encode(), TOKEN.encode()):
            return JSONResponse({"detail": "로그인이 필요합니다"}, status_code=401)
    return await call_next(request)


@app.exception_handler(screen.CaptureError)
async def on_capture_error(_request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=500)


@app.exception_handler(Busy)
async def on_busy(_request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


# ------------------------------------------------------------ 인증 · 정보

@app.post("/api/login")
def login(body: LoginBody):
    if not secrets.compare_digest(body.token.strip().encode(), TOKEN.encode()):
        time.sleep(1)
        raise HTTPException(401, "토큰이 올바르지 않습니다")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, TOKEN, max_age=30 * 86400, httponly=True, samesite="strict")
    return resp


@app.get("/api/info")
def info():
    return {
        "platform": screen.PLATFORM,
        "coord_unit": screen.COORD_UNIT,
        "ocr_hint": pdfbuild.install_hint(),
        "permissions": screen.permissions(),
        "displays": screen.displays(),
        "ocr_available": pdfbuild.ocr_available(),
        "ocr_langs": pdfbuild.tesseract_langs(),
        "config": load_config().model_dump(),
        "limits": field_limits(),
    }


@app.get("/api/config")
def get_config():
    return load_config().model_dump()


@app.put("/api/config")
def put_config(cfg: CaptureConfig):
    store.save_config(cfg.model_dump())
    return {"ok": True}


# ------------------------------------------------------------ 좌표 설정 · 테스트

# delay: 웹 화면을 캡처할 PC에서 직접 열었을 때, 그동안 캡처할 창을 앞으로 가져올 시간
GrabDelay = Query(0, ge=0, le=10)


@app.get("/api/screen")
def screen_image(display_id: Optional[int] = None, delay: float = GrabDelay):
    displays = screen.displays()
    disp = next((d for d in displays if d["id"] == display_id), None) or next((d for d in displays if d["main"]), displays[0])
    time.sleep(delay)
    img = screen.grab(disp["x"], disp["y"], disp["w"], disp["h"]).image.convert("RGB")
    img.thumbnail((2560, 2560))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=80)
    return Response(buf.getvalue(), media_type="image/jpeg", headers=NO_STORE)


def _manual_test(fn):
    if runner.busy or not manual_lock.acquire(blocking=False):
        raise HTTPException(409, "다른 작업이 실행 중입니다")
    try:
        return fn()
    finally:
        manual_lock.release()


@app.post("/api/test/capture")
def test_capture(region: Region, delay: float = GrabDelay):
    def run():
        time.sleep(delay)
        return screen.grab(**region.model_dump())

    shot = _manual_test(run)
    return Response(shot.png, media_type="image/png", headers=NO_STORE)


@app.post("/api/test/turn")
def test_turn(body: CaptureConfig):
    cfg = ready_config(body)

    def run():
        focus_viewer(cfg)
        before = screen.grab(**cfg["region"])
        turn_page(cfg)
        time.sleep(cfg["delay"])
        return before, screen.grab(**cfg["region"])

    before, after = _manual_test(run)
    changed = differs(before.image, after.image)
    return Response(after.png, media_type="image/png",
                    headers={**NO_STORE, "X-Page-Changed": "1" if changed else "0"})


@app.post("/api/pick-folder")
def pick_folder(body: PickFolderBody):
    if not picker_lock.acquire(blocking=False):
        raise HTTPException(409, "폴더 선택 창이 이미 열려 있습니다 (캡처 PC 화면을 확인하세요)")
    try:
        path = desktop.pick_folder(body.initial)
    except desktop.DesktopError as e:
        raise HTTPException(500, str(e))
    finally:
        picker_lock.release()
    return {"path": path}


# ------------------------------------------------------------ 실행

@app.post("/api/start")
def start(body: StartBody):
    cfg = ready_config(body.config)
    try:
        cfg["output_dir"] = store.resolve_output_dir(cfg["output_dir"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.save_config(cfg)
    if manual_lock.locked():
        raise HTTPException(409, "테스트가 끝난 뒤 시작하세요")
    job = runner.start_capture(cfg)
    return job_view(job)


@app.post("/api/stop")
def stop():
    runner.stop()
    return {"ok": True}


@app.get("/api/status")
def status():
    return runner.status()


# ------------------------------------------------------------ 작업 목록 · 결과물

@app.get("/api/jobs")
def jobs():
    return [job_view(j) for j in store.list_jobs()]


@app.get("/api/jobs/{job_id}/pdf")
def download(job_id: str):
    path = store.output_path(load_job_or_404(job_id), "pdf")
    if not path or not path.exists():
        raise HTTPException(404, "파일이 없습니다 (저장 폴더에서 옮기거나 지웠을 수 있습니다)")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.post("/api/jobs/{job_id}/reveal")
def reveal(job_id: str):
    job = load_job_or_404(job_id)
    pdf = store.output_path(job, "pdf")
    desktop.reveal(pdf if pdf and pdf.exists() else store.output_folder(job))
    return {"ok": True}


@app.get("/api/jobs/{job_id}/thumb/{page}")
def thumbnail(job_id: str, page: str):
    load_job_or_404(job_id)
    files = store.page_files(job_id)
    if page == "last":
        path = files[-1] if files else None
    else:
        path = store.page_path(job_id, int(page)) if page.isdigit() else None
    if not path or not path.exists():
        raise HTTPException(404, "쪽이 없습니다")
    img = Image.open(path).convert("RGB")
    img.thumbnail((720, 720))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=80)
    return Response(buf.getvalue(), media_type="image/jpeg", headers=NO_STORE)


@app.post("/api/jobs/{job_id}/build")
def rebuild(job_id: str, body: BuildBody):
    load_job_or_404(job_id)
    return job_view(runner.start_build(job_id, body.model_dump()))


@app.delete("/api/jobs/{job_id}")
def delete(job_id: str):
    load_job_or_404(job_id)
    if runner.busy and runner.status()["job_id"] == job_id:
        raise HTTPException(409, "실행 중인 작업은 삭제할 수 없습니다")
    store.delete_job(job_id)
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(store.BASE_DIR / "static"), html=True), name="static")


# ------------------------------------------------------------ 진입점

def _local_urls(port: int) -> list:
    urls = [f"http://localhost:{port}"]
    if screen.PLATFORM == "mac":
        try:
            name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True,
                                  timeout=3).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            name = ""
        if name:
            urls.append(f"http://{name}.local:{port}")
    else:
        urls.append(f"http://{socket.gethostname()}:{port}")
    try:  # 외부로 패킷을 보내지 않고 기본 경로의 LAN IP만 알아낸다
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))
            urls.append(f"http://{s.getsockname()[0]}:{port}")
    except OSError:
        pass
    return urls


def main() -> None:
    # 맥미니는 집 안 다른 기기에서 접속하는 홈서버, 윈도우는 그 PC에서만 쓰는 것이 기본
    host = os.environ.get("SHOTPDF_HOST", "0.0.0.0" if screen.PLATFORM == "mac" else "127.0.0.1")
    port = int(os.environ.get("SHOTPDF_PORT", "8765"))
    store.ensure_dirs()
    store.recover_jobs()

    print("\n=== Screenshot + PDF 서버 ===")
    for warning in screen.startup_warnings():
        print(f"! {warning}")
    if not pdfbuild.ocr_available():
        print(f"! OCR 도구 없음 — 글자 검색이 되는 PDF를 만들려면 설치 후 다시 실행하세요: {pdfbuild.install_hint()}")
    print("\n접속 주소 (토큰 포함, 처음 한 번만 이 주소로 열면 30일간 유지):")
    for url in _local_urls(port) if host in ("0.0.0.0", "::") else [f"http://{host}:{port}"]:
        print(f"  {url}/?token={TOKEN}")
    print("\n종료: Ctrl+C\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
