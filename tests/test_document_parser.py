from __future__ import annotations

import pytest

from processing import document_parser


def test_document_parser_imports_but_extraction_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="planned for version 0.5.0"):
        document_parser.extract_attachment_text()
