import threading
from pathlib import Path

from .models import ConversionResult, OCRUnavailableError
from .ocr_cloud import CloudOCR
from .ocr_crosscheck import compare
from .ocr_local import LocalOCR
from .ocr_mineru import MinerULocal

# Private alias that keeps the original class reference even when the module-level
# `LocalOCR` name is replaced by monkeypatching in tests.  Availability checks
# go through this alias so `is_available()` remains on the real class.
_LocalOCR_cls = LocalOCR

_INSTALL_HINT = (
    "No OCR backend available. Either install the local engine "
    "(`pip install \"makeitdown[local]\"`) or set a cloud token "
    "(env PADDLEOCR_AISTUDIO_TOKEN or --cloud-token)."
)
_CLOUD_HINT = (
    "Cloud OCR selected but no token. Set env PADDLEOCR_AISTUDIO_TOKEN "
    "or pass --cloud-token."
)


class OCRDispatcher:
    """Selects and caches an OCR backend per the chosen engine mode."""

    def __init__(
        self,
        engine: str = "auto",
        model: str | None = None,
        token: str | None = None,
        poll_interval: float = 5.0,
        cross_check: bool = False,
        cross_check_ratio: float = 0.1,
    ):
        self.engine = engine
        self.model = model
        self.token = token
        self.poll_interval = poll_interval
        self.cross_check = cross_check
        self.cross_check_ratio = cross_check_ratio
        self._backend = None
        self._lock = threading.Lock()

    def _make_cloud(self) -> CloudOCR:
        return CloudOCR(token=self.token, model=self.model, poll_interval=self.poll_interval)

    def _resolve_backend(self):
        # Resolved once and cached; the lock keeps concurrent workers from
        # racing to build two backends on the first conversion.
        if self._backend is not None:
            return self._backend
        with self._lock:
            if self._backend is not None:
                return self._backend
            if self.engine == "local":
                if not _LocalOCR_cls.is_available():
                    raise OCRUnavailableError(_INSTALL_HINT)
                self._backend = LocalOCR(model=self.model)
            elif self.engine == "cloud":
                if not self.token:
                    raise OCRUnavailableError(_CLOUD_HINT)
                self._backend = self._make_cloud()
            elif self.engine == "auto":
                if _LocalOCR_cls.is_available():
                    self._backend = LocalOCR(model=self.model)
                elif self.token:
                    self._backend = self._make_cloud()
                else:
                    raise OCRUnavailableError(_INSTALL_HINT)
            else:
                raise ValueError(f"unknown ocr engine: {self.engine}")
        return self._backend

    def _make_verifier(self):
        """The cross-check verifier engine (MinerU), or None if unavailable."""
        if not MinerULocal.is_available():
            return None
        return MinerULocal()

    def convert(self, path: Path) -> ConversionResult:
        result = self._resolve_backend().convert(path)
        if not self.cross_check:
            return result
        verifier = self._make_verifier()
        if verifier is None:
            result.cross_check_reasons = ["双OCR互校跳过：校验引擎 MinerU 不可用"]
            return result
        try:
            other = verifier.convert(path)
            cc = compare(result.text, other.text, ratio_threshold=self.cross_check_ratio)
            result.cross_check_reasons = cc.reasons
            # other.engine is the verifier's label (MinerULocal.convert sets it to engine_label="mineru")
            result.engine = f"{result.engine} × {other.engine}"
        except Exception as e:  # never lose the primary conversion
            result.cross_check_reasons = [f"双OCR互校失败（已保留主引擎结果）：{type(e).__name__}"]
        return result
