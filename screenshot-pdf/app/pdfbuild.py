"""캡처 이미지 → PDF, 그리고 OCR로 글자 층 얹기 (로컬 처리).

OCR 엔진: ocrmypdf가 있으면 쓰고(맥 권장), 없으면 tesseract로 바로 PDF를 만든다(윈도우).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import img2pdf


def _tesseract() -> Optional[str]:
    exe = os.environ.get("SHOTPDF_TESSERACT") or shutil.which("tesseract")
    if exe:
        return exe
    if sys.platform == "win32":  # 설치 프로그램이 PATH에 안 넣는 경우가 많다
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
            path = os.path.join(base, "Tesseract-OCR", "tesseract.exe")
            if os.path.isfile(path):
                return path
    return None


def ocr_engine() -> Optional[str]:
    if shutil.which("ocrmypdf"):
        return "ocrmypdf"
    if _tesseract():
        return "tesseract"
    return None


def ocr_available() -> bool:
    return ocr_engine() is not None


def install_hint() -> str:
    if sys.platform == "win32":
        return "winget install -e --id UB-Mannheim.TesseractOCR (또는 https://github.com/UB-Mannheim/tesseract/wiki)"
    return "brew install ocrmypdf"


def tesseract_langs() -> list:
    exe = _tesseract()
    if not exe:
        return []
    r = subprocess.run([exe, "--list-langs"], capture_output=True, text=True)
    lines = (r.stdout or r.stderr).splitlines()[1:]  # 첫 줄은 'List of available languages ...'
    return [s.strip() for s in lines if s.strip() and s.strip() != "osd"]


def build_pdf(images: list, out: Path) -> None:
    """PNG를 재압축 없이 그대로 넣는다. 쪽 크기는 PNG의 DPI 정보를 따른다."""
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(img2pdf.convert([str(p) for p in images]))
    os.replace(tmp, out)


def build_ocr(images: list, pdf: Path, dst: Path, lang: str, should_stop=lambda: False) -> bool:
    """텍스트 레이어를 얹은 PDF를 만든다. 중간에 중지되면 False."""
    if ocr_engine() == "ocrmypdf":
        tmp = dst.with_name(dst.stem + ".tmp.pdf")
        cmd = [
            "ocrmypdf",
            "-l", lang,
            "--output-type", "pdf",  # PDF/A 변환을 건너뛰어 이미지 품질을 그대로 유지
            "--optimize", "1",
            "--jobs", str(max(1, (os.cpu_count() or 2) - 1)),
            str(pdf), str(tmp),
        ]
        return _run(cmd, None, tmp, dst, should_stop)

    # tesseract는 이미지 목록 파일을 받아 여러 쪽짜리 PDF 하나를 만든다.
    # 경로에 한글이 섞여도 문제없도록 이미지 폴더 안에서 파일 이름만으로 실행한다.
    workdir = Path(images[0]).parent
    listfile = workdir / "ocr_list.txt"
    listfile.write_text("\n".join(Path(p).name for p in images) + "\n", encoding="utf-8")
    try:
        cmd = [_tesseract(), listfile.name, "ocr_out", "-l", lang, "pdf"]
        return _run(cmd, workdir, workdir / "ocr_out.pdf", dst, should_stop)
    finally:
        listfile.unlink(missing_ok=True)


def _run(cmd: list, cwd: Optional[Path], produced: Path, dst: Path, should_stop) -> bool:
    with tempfile.TemporaryFile(mode="w+", errors="replace") as err:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=err)
        while proc.poll() is None:
            if should_stop():
                proc.terminate()
                proc.wait()
                produced.unlink(missing_ok=True)
                return False
            time.sleep(0.5)
        if proc.returncode != 0:
            err.seek(0)
            lines = [s for s in err.read().splitlines() if s.strip()]
            produced.unlink(missing_ok=True)
            raise RuntimeError("OCR 실패: " + (lines[-1] if lines else f"종료 코드 {proc.returncode}"))
    shutil.move(str(produced), str(dst))  # 저장 폴더가 다른 디스크일 수도 있어 os.replace 대신
    return True
