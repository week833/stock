from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
SHARED_DIR = PROJECT_DIR.parent / "shared"

for path in (SHARED_DIR, SRC_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# Tests of modules or behaviour retired by the current-only redesign are no longer in this workspace;
# keep ignoring the folder name so a restored copy is never collected.
collect_ignore = ["_retired"]


ENTRYPOINT_GATE_ENV = (
    "ENABLE_FINMIND_FETCH",
    "DStock_ALLOW_LEGACY_FINMIND_CACHE",
    "DStock_SINGLE_STOCK_FORMAL_TRANSACTION",
)
AMBIENT_ENV_PREFIXES = ("DSTOCK_",)
AMBIENT_ENV_NAMES = frozenset(
    {
        "ENABLE_FINMIND_FETCH",
        "REFRESH_EXTERNAL_FACTORS",
        "STOCK_SHARED_DATA_ROOT",
        "STOCK_ANALYSIS_FORCE_NEXT_TRADE_DATE",
        "STOCK_DAILY_PREOPEN_ROOT",
    }
)


def _is_ambient_switch(name: str) -> bool:
    upper = name.upper()
    return upper.startswith(AMBIENT_ENV_PREFIXES) or upper in AMBIENT_ENV_NAMES


# A formal run's receipt pins, the network or legacy-cache switches and the shared-root override
# change source gating before any test sets its own values, so shell leftovers never reach a test.
for _name in [name for name in os.environ if _is_ambient_switch(name)]:
    del os.environ[_name]


@pytest.fixture(autouse=True)
def _restore_process_environment():
    # The sealed entrypoints lock the process to Central by writing os.environ
    # directly, which monkeypatch cannot undo; keep that out of later tests.
    with mock.patch.dict(os.environ):
        yield


@pytest.fixture(scope="session")
def _empty_preopen_root(tmp_path_factory):
    return tmp_path_factory.mktemp("preopen_empty_root")


@pytest.fixture(autouse=True)
def _isolated_preopen_root(_empty_preopen_root, monkeypatch):
    # H1 reports read the pre-open snapshot; tests never read the production root
    # (D:\TWStockData\program_data\stock_daily_preopen).  Tests that need a snapshot
    # write a fixture and point STOCK_DAILY_PREOPEN_ROOT at it themselves.
    monkeypatch.setenv("STOCK_DAILY_PREOPEN_ROOT", str(_empty_preopen_root))


@pytest.fixture
def no_universe_cross_section(monkeypatch):
    """Run a three-month entry without the universe cross-section step that newer analyzers perform.

    Those analyzers verify the cross-section from the pinned sources right after the stock resolution, but
    these fixtures pin no sources; an analyzer without the step is left as it is.
    """
    import tw_single_stock_3m_analyzer as analyzer

    if hasattr(analyzer, "_three_month_cross_section"):
        monkeypatch.setattr(analyzer, "_three_month_cross_section", lambda *args, **kwargs: None)


@pytest.fixture
def entrypoint_gate_env(monkeypatch):
    """Run an entrypoint test without the shell's network, legacy-cache or formal-transaction switches."""
    for name in ENTRYPOINT_GATE_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def ambient_directories_ignored(tmp_path_factory, monkeypatch):
    """Keep directories that no test owns out of the ancestor evidence of the local research modules.

    The sealer and the projection record the identity of every lexical ancestor up to the volume root,
    and a directory's modification time changes whenever any process creates or removes an entry in it.
    The system temp directory and the pytest base directory are written by unrelated processes all the
    time, so a test that straddles such a write fails at random.  Those ambient directories are reduced to
    device, inode and file type, which still shows a replaced directory; the test's own directories and
    files keep their full identity.
    """
    import tw_single_stock_local_research_projection as projection
    import tw_single_stock_local_research_snapshot as snapshot

    ambient = set()
    for root in (Path(tempfile.gettempdir()), tmp_path_factory.getbasetemp()):
        for directory in (root, *root.parents):
            try:
                info = directory.lstat()
            except OSError:
                continue
            ambient.add((info.st_dev, info.st_ino))

    projection_chain = projection._ancestor_identity_chain
    snapshot_chain = snapshot._ancestor_file_identity_chain
    snapshot_stable_keys = ("device", "inode", "file_type")

    def stable_projection_chain(path, *, label):
        return tuple(
            (name, identity[:3] if identity[:2] in ambient else identity)
            for name, identity in projection_chain(path, label=label)
        )

    def stable_snapshot_chain(path, *, label, error_type):
        return tuple(
            (
                name,
                {key: identity[key] for key in snapshot_stable_keys}
                if (identity["device"], identity["inode"]) in ambient
                else identity,
            )
            for name, identity in snapshot_chain(path, label=label, error_type=error_type)
        )

    monkeypatch.setattr(projection, "_ancestor_identity_chain", stable_projection_chain)
    monkeypatch.setattr(snapshot, "_ancestor_file_identity_chain", stable_snapshot_chain)


@pytest.fixture
def reparse_fixture(tmp_path, monkeypatch):
    """Bound reparse fixtures to this test's temporary tree without symlink privilege."""
    from contextlib import contextmanager
    import stat
    import subprocess

    root = tmp_path.resolve(strict=True)

    def owned_path(path):
        absolute = Path(os.path.abspath(path))
        assert absolute != root and absolute.is_relative_to(root)
        assert absolute.parent.resolve(strict=True).is_relative_to(root)
        return absolute

    def check_directory_link(link, target):
        owned_path(link)
        info = link.lstat()
        if os.name == "nt":
            assert info.st_file_attributes & 0x0400
            assert info.st_reparse_tag == 0xA0000003  # NTFS junction, not a hardlink.
        else:
            assert stat.S_ISLNK(info.st_mode)
        assert link.resolve(strict=True) == target
        assert target.is_relative_to(root)

    class ReparseStat:
        def __init__(self, info):
            self._info = info
            self.st_file_attributes = getattr(info, "st_file_attributes", 0) | 0x0400

        def __getattr__(self, name):
            return getattr(self._info, name)

        def __getitem__(self, key):
            return self._info[key]

        def __iter__(self):
            return iter(self._info)

        def __len__(self):
            return len(self._info)

    class ReparseFixtures:
        @contextmanager
        def directory_link(self, link, target):
            link = owned_path(link)
            target = Path(target).resolve(strict=True)
            assert target.is_relative_to(root) and target.is_dir()
            assert not os.path.lexists(link)
            try:
                if os.name == "nt":
                    # cmd's built-in mklink /J does not request symlink privilege.
                    assert not any(c in str(link) + str(target) for c in '&|<>^%!"\r\n')
                    cmd = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
                    result = subprocess.run(
                        [str(cmd), "/d", "/c", "mklink", "/J", str(link), str(target)],
                        capture_output=True,
                        check=False,
                    )
                    assert result.returncode == 0, (result.stdout, result.stderr)
                else:
                    link.symlink_to(target, target_is_directory=True)
                check_directory_link(link, target)
                yield link
            finally:
                if os.path.lexists(link):
                    check_directory_link(link, target)
                    # Native unlink of the owned link only; never recurse into its target.
                    if os.name == "nt":
                        os.rmdir(link)
                    else:
                        link.unlink()
                    assert not os.path.lexists(link)

        @contextmanager
        def file_lstat_reparse(self, path):
            selected = owned_path(path)
            original = Path.lstat
            assert stat.S_ISREG(original(selected).st_mode)
            assert not getattr(original(selected), "st_file_attributes", 0) & 0x0400
            observed = []

            def lstat(path, *args, **kwargs):
                info = original(path, *args, **kwargs)
                if Path(os.path.abspath(path)) == selected:
                    observed.append(path)
                    return ReparseStat(info)
                return info

            with monkeypatch.context() as patch:
                patch.setattr(Path, "lstat", lstat)
                yield
            assert observed, "the selected file-leaf lstat was not checked"

        @contextmanager
        def file_resolve_escape(self, path, target):
            selected = owned_path(path)
            target = owned_path(target).resolve(strict=True)
            original = Path.resolve
            assert selected.is_file() and target.is_file()
            assert original(selected, strict=True) == selected
            observed = []

            def resolve(path, strict=False):
                if strict and Path(os.path.abspath(path)) == selected:
                    observed.append(path)
                    return target
                return original(path, strict=strict)

            with monkeypatch.context() as patch:
                patch.setattr(Path, "resolve", resolve)
                yield
            assert observed, "the selected file-leaf resolve was not checked"

    return ReparseFixtures()
