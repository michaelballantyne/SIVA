"""Fixture spec with a misspelled DSL verb.

Used by tests/test_editor_diagnostics.py to assert pyright still catches a
genuinely undefined name -- proving the stub isn't just suppressing every
diagnostic.
"""

from siva.spec_api import *

data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
iso = contuor(input=data, ContourBy="temperature", Isosurfaces=[500.0])
show(iso, "iso", color_by="temperature")
