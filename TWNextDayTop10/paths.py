"""Top10-owned current inference output paths; resolution never creates folders."""
from dataclasses import dataclass
from pathlib import Path

ENGINE_ID = "tw_nextday_top10"
DEFAULT_ROOT = Path(r"D:\TWStockData\program_data\tw_nextday_top10")


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    reports: Path


def runtime_paths(project_dir=None, namespace_root=None):
    root = Path(namespace_root) if namespace_root is not None else DEFAULT_ROOT
    return RuntimePaths(root=root, reports=root / "reports")
