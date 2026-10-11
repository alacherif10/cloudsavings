"""Smoke tests for the development environment.

These tests do not exercise business logic. Their only job is to prove
that the four core dependencies are installed and importable, and that
we are running on the expected Python version.
"""
from __future__ import annotations

import sys

import pandas as pd
import plotly.express as px
import streamlit


def test_python_version_is_3_12() -> None:
    """The project must run on Python 3.12."""
    assert sys.version_info[:2] == (3, 12), f"Expected Python 3.12, got {sys.version_info[:2]}"


def test_pandas_is_usable() -> None:
    df = pd.DataFrame({"a": [1, 2, 3]})
    assert df["a"].sum() == 6


def test_plotly_is_usable() -> None:
    fig = px.bar(x=[1, 2, 3], y=[3, 2, 1])
    assert fig is not None


def test_streamlit_is_importable() -> None:
    assert hasattr(streamlit, "__version__")