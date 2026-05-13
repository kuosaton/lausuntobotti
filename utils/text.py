from __future__ import annotations

import re
from html import unescape


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    return unescape(re.sub(r"<[^>]+>", " ", value)).strip()
