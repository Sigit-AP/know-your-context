"""Tests for widget.py (python_test_prefix rule)."""

from widget import Widget


def test_render():
    assert Widget().render() == "widget"
