"""The installed optional FAISS capability must work without native import warnings.

@author Rodrigo Mason
"""

import subprocess
import sys
from importlib.util import find_spec

import pytest


@pytest.mark.skipif(find_spec("faiss") is None, reason="Optional FAISS extra is not installed")
def test_faiss_import_search_and_serialization_without_warnings():
    code = """
import faiss
import numpy as np
index = faiss.IndexFlatL2(2)
index.add(np.array([[1, 2], [3, 4]], dtype='float32'))
restored = faiss.deserialize_index(faiss.serialize_index(index))
distances, labels = restored.search(np.array([[1, 2]], dtype='float32'), 1)
assert restored.ntotal == 2
assert labels.tolist() == [[0]] and distances.tolist() == [[0.0]]
"""
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
