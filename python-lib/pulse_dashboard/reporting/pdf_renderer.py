from __future__ import annotations

from weasyprint import HTML


def html_to_pdf_bytes(html: str, *, base_url: str | None = None) -> bytes:
    return HTML(string=html, base_url=base_url).write_pdf()
