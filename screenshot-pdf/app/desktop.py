"""캡처 PC의 바탕화면 기능: 폴더 선택 창, Finder/탐색기에서 보기.

창은 서버가 도는 PC(캡처 PC) 화면에 뜬다. 웹 화면을 다른 기기에서 열었다면 경로를 직접 입력하는 편이 낫다.
"""
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

PROMPT = "PDF를 저장할 폴더를 고르세요"

# 경로는 인자로 넘겨 AppleScript 문자열에 끼워 넣지 않는다
_MAC_SCRIPT = """
on run argv
  activate
  if (count of argv) > 0 then
    return POSIX path of (choose folder with prompt "%s" default location (POSIX file (item 1 of argv)))
  end if
  return POSIX path of (choose folder with prompt "%s")
end run
""" % (PROMPT, PROMPT)

# 서버 스레드에서 Tk를 띄우면 불안정하므로 별도 프로세스로 실행한다
_WIN_SCRIPT = """
import sys, tkinter
from tkinter import filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
path = filedialog.askdirectory(parent=root, title=sys.argv[1], initialdir=sys.argv[2] or None, mustexist=False)
sys.stdout.buffer.write((path or "").encode("utf-8"))
"""


class DesktopError(RuntimeError):
    pass


def pick_folder(initial: str = "") -> Optional[str]:
    """폴더 선택 창을 띄우고 고른 경로를 돌려준다. 취소하면 None."""
    initial = os.path.expanduser(initial.strip()) if initial else ""
    if not os.path.isdir(initial):
        initial = ""
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["osascript", "-e", _MAC_SCRIPT] + ([initial] if initial else []),
                               capture_output=True, text=True, timeout=600)
            path = r.stdout.strip() if r.returncode == 0 else ""  # 취소하면 오류 코드(-128)로 끝난다
        else:
            r = subprocess.run([sys.executable, "-c", _WIN_SCRIPT, PROMPT, initial],
                               capture_output=True, timeout=600)
            if r.returncode != 0:
                raise DesktopError("폴더 선택 창을 열 수 없습니다. 경로를 직접 입력하세요.")
            path = r.stdout.decode("utf-8", "replace").strip()
    except subprocess.TimeoutExpired:
        return None
    return os.path.normpath(path) if path else None


def reveal(path: Path) -> None:
    """파일이면 그 파일을 선택한 채로, 폴더면 폴더를 Finder/탐색기에서 연다."""
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)] if path.is_file() else ["open", str(path)], timeout=10)
    else:
        # explorer는 성공해도 종료 코드 1을 돌려주므로 결과는 보지 않는다
        subprocess.run(f'explorer /select,"{path}"' if path.is_file() else f'explorer "{path}"', timeout=10)
