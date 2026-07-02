"""Determinism of the stream manager — the package's foundational guarantee."""
import numpy as np
import pytest

from minehaulsim import RngManager


def test_same_seed_same_purpose_identical_sequence():
    a = RngManager(42).stream("load-times").random(100)
    b = RngManager(42).stream("load-times").random(100)
    assert np.array_equal(a, b)


def test_purposes_are_independent_streams():
    m = RngManager(42)
    seq_before = m.fresh("load-times").random(10)
    # consuming ANOTHER purpose must not shift this one
    m.stream("pit-geometry").random(1000)
    seq_after = m.fresh("load-times").random(10)
    assert np.array_equal(seq_before, seq_after)
    # and different purposes differ
    assert not np.array_equal(m.fresh("load-times").random(10), m.fresh("pit-geometry").random(10))


def test_different_seeds_differ():
    assert not np.array_equal(RngManager(1).stream("x").random(10), RngManager(2).stream("x").random(10))


def test_stream_is_cached_fresh_is_not():
    m = RngManager(7)
    assert m.stream("a") is m.stream("a")
    assert m.fresh("a") is not m.fresh("a")


def test_invalid_seed_rejected():
    with pytest.raises(ValueError):
        RngManager(-1)
