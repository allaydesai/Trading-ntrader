"""Package marker.

Required because tests/unit/services/test_session_service.py shares a basename
with tests/integration/db/test_session_service.py; without __init__.py in both
directories, pytest's rootless import mode raises an import file mismatch when
collecting the full tests/ tree. Do not delete.
"""
