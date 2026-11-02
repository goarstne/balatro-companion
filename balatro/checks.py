"""The same test selection is used by the local runner and the autolearner."""
from pathlib import Path


def modules(core=False):
    directory = Path(__file__).resolve().parent.parent / "tests"
    if not (directory / "test_engine.py").is_file():
        return []
    return [p.stem for p in sorted(directory.glob("test_*.py"))
            if not (core and p.stem.startswith("test_local_"))]
