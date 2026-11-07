from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools.publication_gate import GateError, scan_message, write_manifest


def test_commit_message_scan_uses_private_denylist(tmp_path: Path) -> None:
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("SYNTHETIC-PRIVATE-VALUE\n", encoding="utf-8")
    with pytest.raises(GateError):
        scan_message("mention SYNTHETIC-PRIVATE-VALUE", str(denylist))
    scan_message("public release commit", str(denylist))


def test_manifest_must_live_outside_repository(tmp_path: Path) -> None:
    with pytest.raises(GateError):
        write_manifest(
            manifest_dir=Path("publication-manifests"),
            commit="0" * 40,
            branch="main",
            remote="git@github.com:example/example.git",
            identity={"name": "Example", "email": "123456+example@users.noreply.github.com"},
            tests={"tests": "1 passed"},
            artefacts={},
            gitleaks_version="8.30.1",
        )


def test_manifest_is_private_and_complete(tmp_path: Path) -> None:
    path = write_manifest(
        manifest_dir=tmp_path / "manifests",
        commit="a" * 40,
        branch="main",
        remote="git@github.com:example/example.git",
        identity={"name": "Example", "email": "123456+example@users.noreply.github.com"},
        tests={"tests": "1 passed"},
        artefacts={"pyproject.toml": "b" * 64},
        gitleaks_version="8.30.1",
    )
    assert path.is_file()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["commit"] == "a" * 40
    assert payload["scanner"]["version"]
    assert payload["gitleaks"] == "8.30.1"
