"""설정, 접속 토큰, 작업 폴더 관리. 모든 데이터는 로컬에만 저장된다.

data/
  token.txt            웹 UI 접속 토큰
  config.json          마지막으로 쓴 캡처 설정
  jobs/<작업ID>/
    job.json           파일 이름, 저장 폴더, 쪽수, 상태, 결과 파일명
    pages/0001.png ... 캡처 원본
    <이름>.pdf         저장 폴더를 따로 정하지 않았을 때만 여기에 저장
"""
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SHOTPDF_DATA", str(BASE_DIR / "data")))
JOBS_DIR = DATA_DIR / "jobs"
CONFIG_FILE = DATA_DIR / "config.json"
TOKEN_FILE = DATA_DIR / "token.txt"

_JOB_ID = re.compile(r"^\d{8}-\d{6}$")


def ensure_dirs() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA_DIR, 0o700)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_config() -> dict:
    return _read_json(CONFIG_FILE, {})


def save_config(cfg: dict) -> None:
    _write_json(CONFIG_FILE, cfg)


def get_token() -> str:
    ensure_dirs()
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    token = secrets.token_urlsafe(18)
    TOKEN_FILE.write_text(token)
    os.chmod(TOKEN_FILE, 0o600)
    return token


def safe_name(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title).strip(" .")
    return name[:80] or "document"


def resolve_output_dir(raw: str) -> str:
    """사용자가 정한 저장 폴더를 만들고 실제로 써지는지 확인한 뒤 절대 경로로 돌려준다.

    빈 값이면 ""(앱 내부 작업 폴더에 저장). 캡처를 다 하고 나서 실패하지 않도록 시작 전에 부른다.
    """
    raw = raw.strip()
    if not raw:
        return ""
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        raise ValueError("저장 폴더는 전체 경로로 입력하세요 (예: ~/Documents/이사회자료)")
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError as e:
        raise ValueError(f"저장 폴더에 쓸 수 없습니다: {path} ({e.strerror or e})")
    return str(path)


def display_path(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def free_stem(folder: Path, stem: str) -> str:
    """같은 이름의 PDF가 이미 있으면 덮어쓰지 않고 '이름 (2)'처럼 번호를 붙인다."""
    candidate, n = stem, 2
    while (folder / f"{candidate}.pdf").exists():
        candidate = f"{stem} ({n})"
        n += 1
    return candidate


def job_dir(job_id: str) -> Path:
    if not _JOB_ID.match(job_id or ""):
        raise FileNotFoundError(job_id)
    return JOBS_DIR / job_id


def output_folder(job: dict) -> Path:
    return Path(job["output_dir"]) if job.get("output_dir") else job_dir(job["id"])


def output_path(job: dict, key: str) -> Optional[Path]:
    """key: 'pdf' (예전 버전 작업은 'ocr_pdf'도). 아직 만들지 않았으면 None."""
    return output_folder(job) / job[key] if job.get(key) else None


def new_job(title: str, output_dir: str = "") -> dict:
    job_id = time.strftime("%Y%m%d-%H%M%S")
    while (JOBS_DIR / job_id).exists():
        time.sleep(1)
        job_id = time.strftime("%Y%m%d-%H%M%S")
    (JOBS_DIR / job_id / "pages").mkdir(parents=True)
    job = {
        "id": job_id,
        "title": re.sub(r"\.pdf$", "", title.strip(), flags=re.IGNORECASE) or f"문서 {job_id}",
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "output_dir": output_dir,  # ""이면 이 작업 폴더에 저장
        "pages": 0,
        "status": "capturing",
        "message": "",
        "pdf": None,
        "ocr": False,  # PDF에 검색용 텍스트가 들어갔는지
    }
    save_job(job)
    return job


def load_job(job_id: str) -> dict:
    job = _read_json(job_dir(job_id) / "job.json", None)
    if job is None:
        raise FileNotFoundError(job_id)
    return job


def save_job(job: dict) -> None:
    _write_json(job_dir(job["id"]) / "job.json", job)


def list_jobs() -> list:
    jobs = []
    for d in sorted(JOBS_DIR.iterdir(), reverse=True):
        job = _read_json(d / "job.json", None) if _JOB_ID.match(d.name) else None
        if job:
            jobs.append(job)
    return jobs


def delete_job(job_id: str) -> None:
    """캡처 이미지와 작업 기록을 지운다. 사용자가 고른 저장 폴더의 PDF는 남긴다."""
    shutil.rmtree(job_dir(job_id))


def page_path(job_id: str, n: int) -> Path:
    return job_dir(job_id) / "pages" / f"{n:04d}.png"


def page_files(job_id: str) -> list:
    return sorted((job_dir(job_id) / "pages").glob("*.png"))


def recover_jobs() -> None:
    """서버가 작업 도중 꺼졌다면 '중단됨'으로 표시한다. 찍어 둔 쪽은 'PDF 다시 만들기'로 PDF가 된다."""
    for job in list_jobs():
        if job["status"] in ("capturing", "building", "ocr"):
            job["status"] = "stopped"
            job["message"] = "서버가 재시작되어 중단됨"
            job["pages"] = len(page_files(job["id"]))
            save_job(job)
