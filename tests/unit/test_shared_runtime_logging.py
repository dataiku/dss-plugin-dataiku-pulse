from __future__ import annotations

import logging

from shared_runtime_logging import suppress_inherited_provider_debug_logging


def test_shared_runtime_logging_suppresses_explicit_and_inherited_debug_without_changing_root():
    root_logger = logging.getLogger()
    botocore_logger = logging.getLogger("botocore")
    urllib3_logger = logging.getLogger("urllib3")
    original_root_level = root_logger.level
    original_botocore_level = botocore_logger.level
    original_urllib3_level = urllib3_logger.level

    try:
        root_logger.setLevel(logging.DEBUG)
        botocore_logger.setLevel(logging.NOTSET)
        urllib3_logger.setLevel(logging.DEBUG)

        suppress_inherited_provider_debug_logging()

        assert root_logger.level == logging.DEBUG
        assert botocore_logger.level == logging.WARNING
        assert urllib3_logger.level == logging.WARNING
    finally:
        root_logger.setLevel(original_root_level)
        botocore_logger.setLevel(original_botocore_level)
        urllib3_logger.setLevel(original_urllib3_level)


def test_shared_runtime_logging_preserves_higher_effective_levels():
    root_logger = logging.getLogger()
    botocore_logger = logging.getLogger("botocore")
    urllib3_logger = logging.getLogger("urllib3")
    original_root_level = root_logger.level
    original_botocore_level = botocore_logger.level
    original_urllib3_level = urllib3_logger.level

    try:
        root_logger.setLevel(logging.INFO)
        botocore_logger.setLevel(logging.ERROR)
        urllib3_logger.setLevel(logging.INFO)

        suppress_inherited_provider_debug_logging()

        assert root_logger.level == logging.INFO
        assert botocore_logger.level == logging.ERROR
        assert urllib3_logger.level == logging.INFO
    finally:
        root_logger.setLevel(original_root_level)
        botocore_logger.setLevel(original_botocore_level)
        urllib3_logger.setLevel(original_urllib3_level)
