"""Explicit-consent gate for cloud OCR — documents must never upload silently.

Cloud OCR (Paddle AI Studio or MinerU mineru.net) uploads the document off the
machine. This module decides whether that is permitted (an explicit flag or env
opt-in) and carries the user-facing notice. Pure logic, no I/O.
"""

from __future__ import annotations

import os

CLOUD_NOTICE = (
    "⚠️  即将使用云端 OCR：文档会上传至云端服务"
    "（Paddle→百度 AI Studio / MinerU→mineru.net）。\n"
    "    同意上传：设置 token 并加 --cloud-consent（或环境变量 MAKEITDOWN_CLOUD_CONSENT=1）。\n"
    "    不希望上传（本机性能足够）：加 --ocr-engine local（需安装本地版）。"
)

_TRUTHY = {"1", "true", "yes", "on"}


class CloudConsentRequired(RuntimeError):
    """Raised when a cloud OCR engine is selected but the user has not consented."""


def has_consent(flag: bool, env: dict | None = None) -> bool:
    """True if cloud upload is permitted via the flag or MAKEITDOWN_CLOUD_CONSENT."""
    if flag:
        return True
    env = os.environ if env is None else env
    return env.get("MAKEITDOWN_CLOUD_CONSENT", "").strip().lower() in _TRUTHY


def require_cloud_consent(flag: bool, env: dict | None = None) -> None:
    """Raise CloudConsentRequired(CLOUD_NOTICE) unless consent is present."""
    if not has_consent(flag, env):
        raise CloudConsentRequired(CLOUD_NOTICE)
