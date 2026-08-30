"""
MCP adapter for reading documents.

Two tools, not twelve. The donor exposed one entry point with an `action`
string covering resize, convert, compress, transcribe, run and a dozen more
across twelve file types; that is a switchboard, and the model would have had
to know the action vocabulary of every format to use any of it.

Asking is cheaper than reading, so they are separate:

    documents_inspect   how many pages, which sheets, how big
    documents_extract   the text

Answering "how long is this PDF?" by extracting all of it is how a voice turn
spends thirty seconds on a question with a one-word answer.
"""

from __future__ import annotations

import os
from typing import TypedDict

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.toolsets import documents as D

_engine: PolicyEngine | None = None


def _get_engine() -> PolicyEngine:
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
        for tool_id in (t.strip() for t in
                        os.getenv("ADA_PREAPPROVED_TOOLS", "").split(",") if t.strip()):
            try:
                _engine.approve_for_session(tool_id)
            except PolicyError:
                continue
    return _engine


class Extracted(TypedDict):
    path: str
    kind: str
    text: str
    chars: int
    truncated: bool
    #: Whatever the format knows about itself: pages, sheets, slides, entries.
    #: A summary cannot reconstruct these, which is why they come back
    #: alongside the text rather than being folded into it.
    facts: dict
    #: A scanned PDF is images of text. "0 characters" is true and useless;
    #: this says which of the two happened.
    needs_ocr: bool
    error: str


class Described(TypedDict):
    path: str
    kind: str
    facts: dict
    error: str


def _blank_extract(error: str) -> Extracted:
    return {"path": "", "kind": "", "text": "", "chars": 0, "truncated": False,
            "facts": {}, "needs_ocr": False, "error": error}


def register(mcp):

    @mcp.tool()
    def documents_extract(path: str, pages: str = "", sheet: str = "",
                          max_chars: int = D.MAX_TEXT_CHARS) -> Extracted:
        """
        Read a PDF, Word document, spreadsheet, slide deck or zip as text.

        This is for the files `files_read` cannot open. Plain text, code,
        Markdown, CSV and JSON are `files_read`'s job and are faster there.

        `pages` narrows a PDF - "3", "2-5", "1,4,9" - and `sheet` names one
        sheet of a spreadsheet. Ask documents_inspect first when you need to
        know what is available; extracting a two hundred page report to find
        out it has two hundred pages is a slow way to count.

        A PDF of scanned images comes back with needs_ocr set and no text.
        That is not a failure to read the file - it is the file having no text
        in it, and Friday has no OCR.
        """
        result = D.documents_extract(
            c.Run.create(f"extract {path}", capability="documents"), path,
            pages=pages, sheet=sheet, max_chars=max_chars,
            engine=_get_engine())
        if result.status == c.FAILED or result.output is None:
            return _blank_extract(result.error or "extraction failed")

        output = dict(result.output)
        known = {"execution_scope", "path", "kind", "text", "chars",
                 "truncated"}
        return {
            "path": output.get("path", ""), "kind": output.get("kind", ""),
            "text": output.get("text", ""), "chars": output.get("chars", 0),
            "truncated": bool(output.get("truncated")),
            "facts": {k: v for k, v in output.items()
                      if k not in known and k != "looks_scanned"},
            "needs_ocr": bool(output.get("looks_scanned")),
            "error": "" if result.status == c.SUCCEEDED else (result.error or ""),
        }

    @mcp.tool()
    def documents_inspect(path: str) -> Described:
        """
        How many pages, which sheets, how many slides - without reading it.

        Cheap. Use it before extracting anything large, and to answer "how
        long is this?" without paying for the text.
        """
        result = D.documents_inspect(
            c.Run.create(f"inspect {path}", capability="documents"), path,
            engine=_get_engine())
        if result.status != c.SUCCEEDED or result.output is None:
            return {"path": "", "kind": "", "facts": {},
                    "error": result.error or "inspection failed"}
        output = dict(result.output)
        return {"path": output.pop("path", ""), "kind": output.pop("kind", ""),
                "facts": {k: v for k, v in output.items()
                          if k != "execution_scope"},
                "error": ""}
