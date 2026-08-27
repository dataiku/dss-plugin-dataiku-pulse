from __future__ import annotations

import logging


NOISY_DEBUG_LOGGERS = ("botocore", "urllib3")


def suppress_inherited_provider_debug_logging() -> None:
    for logger_name in NOISY_DEBUG_LOGGERS:
        named_logger = logging.getLogger(logger_name)
        if named_logger.getEffectiveLevel() <= logging.DEBUG:
            named_logger.setLevel(logging.WARNING)


__all__ = ["NOISY_DEBUG_LOGGERS", "suppress_inherited_provider_debug_logging"]
