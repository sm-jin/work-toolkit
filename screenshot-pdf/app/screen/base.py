from io import BytesIO
from typing import Optional

from PIL import Image


class CaptureError(RuntimeError):
    pass


class Shot:
    """캡처 한 장. 저장용 PNG는 실제로 저장할 때만 만든다 (윈도우는 캡처마다 인코딩하면 느림)."""

    def __init__(self, image: Image.Image, png: Optional[bytes] = None, dpi: Optional[int] = None):
        self.image = image
        self._png = png
        self._dpi = dpi

    @property
    def png(self) -> bytes:
        if self._png is None:
            buf = BytesIO()
            self.image.save(buf, "PNG", dpi=(self._dpi, self._dpi) if self._dpi else None)
            self._png = buf.getvalue()
        return self._png
