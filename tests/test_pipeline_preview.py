"""Unit tests for the version-list preview line (siva.server._pipeline_preview_line).

The preview skips the boilerplate every spec shares — the mandatory
``from siva.spec_api import *`` header and blank lines — but shows
human-written annotations: a leading comment as-is, a leading module
docstring as its summary line. Only then does the first code line show.
"""

import unittest

from siva.server import _pipeline_preview_line

HEADER = "from siva.spec_api import *"


class TestPipelinePreviewLine(unittest.TestCase):
    def test_skips_header_and_blanks(self):
        code = f'{HEADER}\n\ndata = source("vtkXMLImageDataReader")\n'
        self.assertEqual(
            _pipeline_preview_line(code), 'data = source("vtkXMLImageDataReader")'
        )

    def test_leading_docstring_shows_summary_line(self):
        code = (
            '"""Plume view: threshold + volume rendering."""\n'
            f"{HEADER}\n\n"
            "region = threshold(input=data)\n"
        )
        self.assertEqual(
            _pipeline_preview_line(code), "Plume view: threshold + volume rendering."
        )

    def test_multiline_docstring_shows_first_text_line(self):
        code = (
            '"""Plume view.\n\nSecond paragraph.\n"""\n'
            f"{HEADER}\n"
            "iso = contour(input=data)\n"
        )
        self.assertEqual(_pipeline_preview_line(code), "Plume view.")

    def test_leading_comment_shows_as_is(self):
        code = f"{HEADER}\n# load the data\ndata = source()\n"
        self.assertEqual(_pipeline_preview_line(code), "# load the data")

    def test_header_only_is_empty(self):
        self.assertEqual(_pipeline_preview_line(f"{HEADER}\n"), "(empty)")

    def test_multiline_header_form_is_skipped(self):
        code = "from siva.spec_api import (\n    source,\n)\ndata = source()\n"
        self.assertEqual(_pipeline_preview_line(code), "data = source()")

    def test_blank_file_is_empty(self):
        self.assertEqual(_pipeline_preview_line(""), "(empty)")

    def test_unparseable_falls_back_to_line_scan(self):
        code = f"{HEADER}\ndata = source(\n"  # unterminated call
        self.assertEqual(_pipeline_preview_line(code), "data = source(")
