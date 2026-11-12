"""Every test run gets its own data folder, so no test reads or writes the real settings and library
(the main window saves settings, e.g. after sorting or moving a column). Tests may still set their own."""
import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _own_data_dir(tmp_path_factory):
    old = os.environ.get("KEYTOOL_DATA_DIR")
    os.environ["KEYTOOL_DATA_DIR"] = str(tmp_path_factory.mktemp("keytool-data"))
    yield
    if old is None:
        os.environ.pop("KEYTOOL_DATA_DIR", None)
    else:
        os.environ["KEYTOOL_DATA_DIR"] = old
