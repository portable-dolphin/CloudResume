import pytest
import pdb


def pytest_collection_modifyitems(items):
    for item in items:
        item.add_marker(pytest.mark.timeout(timeout=600, method="signal"))
