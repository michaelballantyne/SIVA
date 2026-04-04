"""Pytest configuration and shared fixtures for VisLang tests."""

import os
import pytest


# Path to the wildfire dataset used by integration tests.
_WILDFIRE_DATA = "output.30000.vts"


def pytest_collection_modifyitems(config, items):
    """Skip integration tests when the required dataset is not present.

    test_integration.py was designed to be run as a standalone script with
    the wildfire dataset symlinked into the working directory.  When run via
    pytest (e.g. in CI) without the dataset, skip those tests so the rest of
    the suite can run cleanly.
    """
    dataset_available = os.path.exists(_WILDFIRE_DATA)

    skip_no_dataset = pytest.mark.skip(
        reason=f"Integration tests require '{_WILDFIRE_DATA}' in the working "
               "directory. Run from a session folder with the dataset symlinked."
    )

    for item in items:
        if "test_integration" in item.nodeid:
            if not dataset_available:
                item.add_marker(skip_no_dataset)
