from __future__ import annotations

import json
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import pytest


SUITE_DIR = Path(__file__).resolve().parents[1]
if str(SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(SUITE_DIR))

import telegram_report_sender as sender


@pytest.mark.parametrize("version", ["1.1.0", "2.0.0"])
def test_load_path_contract_accepts_supported_versions(tmp_path: Path, version: str) -> None:
    path = tmp_path / "path_contract.json"
    payload = {
        "contract_version": version,
        "root": str(sender.EXPECTED_CONTRACT_ROOT),
        "managed_paths": {"telegram": r"D:\TWStockData\services\telegram"},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert sender.load_path_contract(path) == payload


def test_load_path_contract_rejects_unknown_version(tmp_path: Path) -> None:
    path = tmp_path / "path_contract.json"
    payload = {
        "contract_version": "3.0.0",
        "root": str(sender.EXPECTED_CONTRACT_ROOT),
        "managed_paths": {"telegram": r"D:\TWStockData\services\telegram"},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="路徑契約版本必須是"):
        sender.load_path_contract(path)


@pytest.mark.parametrize("version", ["2.1.0", "1.9.3"])
def test_load_path_contract_accepts_unknown_minor_of_supported_major_with_label(
    tmp_path: Path, version: str
) -> None:
    path = tmp_path / "path_contract.json"
    payload = {
        "contract_version": version,
        "root": str(sender.EXPECTED_CONTRACT_ROOT),
        "managed_paths": {"telegram": r"D:\TWStockData\services\telegram"},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert sender.load_path_contract(path) == payload
    labels = sender.path_contract_version_labels(version, path)
    assert [label["code"] for label in labels] == ["path_contract_version_unknown"]
    assert labels[0]["actual"] == version
    assert sender.path_contract_version_labels("2.0.0", path) == []
    for rejected in ("3.0.0", "x", "", None, "2.0"):
        with pytest.raises(RuntimeError, match="路徑契約版本必須是"):
            sender.path_contract_version_labels(rejected, path)
    # The root check is still a KEEP gate for any accepted version.
    path.write_text(json.dumps(dict(payload, root=str(tmp_path))), encoding="utf-8")
    with pytest.raises(RuntimeError, match="根目錄必須是"):
        sender.load_path_contract(path)


def test_default_paths_follow_central_contract() -> None:
    # The old D-stock full-market manifest sender (DEFAULT_MANIFEST_PATH / DEFAULT_STATE_PATH) was
    # retired; this module is now only the shared Telegram client and credential tool.
    assert sender.TELEGRAM_ROOT == Path(r"D:\TWStockData\services\telegram")
    assert sender.DEFAULT_LOG_DIR == Path(r"D:\TWStockData\services\telegram\logs")
    assert sender.DEFAULT_ENV_PATH == SUITE_DIR / ".ENV"
    for retired in ("DEFAULT_MANIFEST_PATH", "DEFAULT_STATE_PATH", "load_report_bundle", "deliver_bundle"):
        assert not hasattr(sender, retired), retired


def test_cli_path_validation_rejects_noncanonical_roots(tmp_path: Path) -> None:
    args = sender.build_parser().parse_args(["--check", "--log-dir", str(tmp_path / "logs")])
    with pytest.raises(sender.ConfigurationError, match="路徑契約根目錄"):
        sender.validate_cli_paths(args)
    canonical = sender.build_parser().parse_args(
        ["--check", "--log-dir", str(sender.TELEGRAM_ROOT / "logs")]
    )
    sender.validate_cli_paths(canonical)
    assert canonical.log_dir == (sender.TELEGRAM_ROOT / "logs").resolve(strict=False)
    # Only --configure / --check remain; the retired report-delivery options are refused.
    with pytest.raises(SystemExit):
        sender.build_parser().parse_args([])
    with pytest.raises(SystemExit):
        sender.build_parser().parse_args(["--check", "--manifest", str(tmp_path / "manifest.json")])


def test_env_update_replaces_duplicates_without_touching_other_keys(tmp_path: Path) -> None:
    env_path = tmp_path / ".ENV"
    env_path.write_text(
        "FINMIND_TOKEN=keep\nTELEGRAM_CHAT_ID=old\nTELEGRAM_CHAT_ID=duplicate\n",
        encoding="utf-8",
    )
    sender.update_env_file(
        env_path,
        {
            "TELEGRAM_DELIVERY_ENABLED": "true",
            "TELEGRAM_BOT_TOKEN": "123456:abcdefghijklmnopqrstuvwxyz_ABCDE",
            "TELEGRAM_CHAT_ID": "998877",
        },
    )
    loaded = sender.load_env_file(env_path)
    assert loaded["FINMIND_TOKEN"] == "keep"
    assert loaded["TELEGRAM_CHAT_ID"] == "998877"
    assert env_path.read_text(encoding="utf-8").count("TELEGRAM_CHAT_ID=") == 1


def test_configure_uses_existing_env_without_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = "123456:abcdefghijklmnopqrstuvwxyz_ABCDE"
    env_path = tmp_path / ".ENV"
    env_path.write_text(
        f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID=998877\n",
        encoding="utf-8",
    )

    class FakeConfigurationClient:
        def __init__(self, received_token: str, received_chat_id: str) -> None:
            assert received_token == token
            assert received_chat_id == "998877"

        def get_me(self) -> dict[str, Any]:
            return {"username": "test_bot"}

        def get_chat(self) -> dict[str, Any]:
            return {"type": "private"}

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(sender, "TelegramClient", FakeConfigurationClient)
    monkeypatch.setattr(sender.getpass, "getpass", lambda _: pytest.fail("不應詢問 Token"))
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("不應詢問 Chat ID"))
    log_messages: list[str] = []
    monkeypatch.setattr(
        sender.LOGGER,
        "info",
        lambda *args, **kwargs: log_messages.append(" ".join(str(value) for value in args)),
    )
    assert sender.configure_delivery(env_path) == 0
    assert sender.load_env_file(env_path)["TELEGRAM_DELIVERY_ENABLED"] == "true"
    logged = "\n".join(log_messages)
    assert token not in logged
    assert "998877" not in logged


def test_multipart_contains_document_and_unicode_filename(tmp_path: Path) -> None:
    document = tmp_path / "台股報告.xlsx"
    document.write_bytes(b"document-body")
    body, content_type = sender.build_multipart_form(
        {"chat_id": "123", "caption": "測試"},
        "document",
        document,
    )
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="chat_id"' in body
    assert b'name="document"' in body
    assert b"filename*=UTF-8''" in body
    assert b"document-body" in body
