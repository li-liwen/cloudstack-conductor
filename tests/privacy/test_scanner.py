"""Negative tests for the privacy scanner.

Canaries are generated at runtime inside temporary repositories. The public
repository therefore contains no real private value used as a fixture.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNER = ROOT / "tools" / "privacy" / "scan.py"


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path: Path, *, email: str = "123456+example@users.noreply.github.com") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Example Contributor")
    _git(repo, "config", "user.email", email)
    return repo


def _private_ip() -> str:
    # Constructed from parts so the public test file contains no literal address.
    return ".".join(("10", "20", "30", "40"))


def test_tree_scan_blocks_private_ip_without_echoing_it(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    canary = _private_ip()
    (repo / "config.yml").write_text(f"host: {canary}\n", encoding="utf-8")
    result = _run(["--root", str(repo), "--scope", "tree", "--json"])
    assert result.returncode == 1
    assert "private_ipv4" in result.stdout
    assert canary not in result.stdout
    assert canary not in result.stderr


def test_tree_scan_allows_documentation_addresses(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "example.md").write_text(
        "Use 192.0.2.10, 198.51.100.20, or 203.0.113.30 in examples.\n",
        encoding="utf-8",
    )
    result = _run(["--root", str(repo), "--scope", "tree"])
    assert result.returncode == 0, result.stdout


def test_staged_scan_blocks_before_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    canary = _private_ip()
    (repo / "leak.txt").write_text(f"address={canary}\n", encoding="utf-8")
    _git(repo, "add", "leak.txt")
    result = _run(["--root", str(repo), "--scope", "staged"])
    assert result.returncode == 1
    assert "private_ipv4" in result.stdout
    assert canary not in result.stdout


def test_history_scan_finds_deleted_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    canary = _private_ip()
    (repo / "temporary.txt").write_text(f"server: {canary}\n", encoding="utf-8")
    _git(repo, "add", "temporary.txt")
    _git(repo, "commit", "-q", "-m", "add fixture")
    (repo / "temporary.txt").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove fixture")
    result = _run(["--root", str(repo), "--scope", "history"])
    assert result.returncode == 1
    assert "private_ipv4" in result.stdout
    assert canary not in result.stdout


def test_history_scan_checks_commit_messages_and_denylist(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    denylist = tmp_path / "denylist.txt"
    value = "SYNTHETIC-PRIVATE-NAME"
    denylist.write_text(value + "\n", encoding="utf-8")
    (repo / "README.md").write_text("Public content.\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", f"mention {value}")
    result = _run(["--root", str(repo), "--scope", "history", "--denylist", str(denylist)])
    assert result.returncode == 1
    assert "private_denylist" in result.stdout
    assert value not in result.stdout


def test_history_scan_requires_noreply_identity(tmp_path: Path) -> None:
    repo = _repo(tmp_path, email="person@example.org")
    (repo / "README.md").write_text("Public content.\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    result = _run(["--root", str(repo), "--scope", "history"])
    assert result.returncode == 1
    assert "author_email" in result.stdout


def test_absolute_symlink_is_blocked(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "link").symlink_to("/etc/passwd")
    result = _run(["--root", str(repo), "--scope", "tree"])
    assert result.returncode == 1
    assert "absolute_symlink" in result.stdout


def test_missing_required_denylist_blocks(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("Public content.\n", encoding="utf-8")
    result = _run(
        ["--root", str(repo), "--scope", "tree", "--denylist", str(tmp_path / "missing.txt"), "--require-denylist"]
    )
    assert result.returncode == 1
    assert "private_policy_missing" in result.stdout


def test_blocked_file_types(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "pricebook.xlsx").write_bytes(b"PK\x03\x04synthetic")
    result = _run(["--root", str(repo), "--scope", "tree"])
    assert result.returncode == 1
    assert "blocked_file_type" in result.stdout


def test_clean_repository_passes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("# Example\n\nUses example.com and 192.0.2.1.\n", encoding="utf-8")
    result = _run(["--root", str(repo), "--scope", "tree"])
    assert result.returncode == 0, result.stdout


def test_javascript_internal_property_is_not_a_hostname(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "bundle.js").write_text("const state = this.internal; const value = t.internal;\n", encoding="utf-8")
    result = _run(["--root", str(repo), "--scope", "tree"])
    assert result.returncode == 0, result.stdout
