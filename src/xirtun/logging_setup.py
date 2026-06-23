"""Logging configuration. Call setup_logging() once at startup; use
`logging.getLogger(__name__)` everywhere else. Never use print().
"""

from __future__ import annotations

import logging


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # don't log request URLs
    logging.getLogger("google_genai").setLevel(logging.WARNING)  # don't log request URLs