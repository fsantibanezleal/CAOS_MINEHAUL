"""Test package. The __init__ makes `tests` a real package so pytest (bare invocation, prepend
import mode) inserts the REPO ROOT into sys.path — cross-module test imports like
`from tests.test_sim import ...` then resolve identically under `pytest` and `python -m pytest`.
"""
