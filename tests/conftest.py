"""Load ``server/app/parser.py`` as a standalone module for unit tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_PARSER_PATH = _REPO_ROOT / "server" / "app" / "parser.py"


@pytest.fixture
def parser():
    spec = importlib.util.spec_from_file_location("command_parser", _PARSER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # kreves for dataclasses med string-annotasjoner
    spec.loader.exec_module(module)
    return module
