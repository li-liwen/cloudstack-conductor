"""The complete local publication gate.

No step is optional for an owner publication: verify the repository and remote,
stage explicit public paths, scan staged content, commit with an approved
identity, scan all outgoing history, export the exact commit, build and test it
without private files, scan generated outputs, record a private manifest, and
push only the explicit validated ref.

A failed or unavailable scanner blocks the gate.
"""

from __future__ import annotations

# ruff: noqa: E402
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tools.privacy.scan import (
    VERSION as SCANNER_VERSION,
)
from tools.privacy.scan import (
    load_denylist,
    scan_bytes,
    scan_text,
    scan_tree,
)

DEFAULT_MANIFEST_DIR = ROOT.parent / "private" / "publication-manifests"
NOREPLY_PATTERN = r"^(?:\d+\+[A-Za-z0-9._-]+@users\.noreply\.github\.com|noreply@github\.com)$"


class GateError(RuntimeError):
    pass


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise GateError(f"git {' '.join(args)} failed: {result.stderr.strip()[:400]}")
    return result


def repository_root() -> Path:
    result = git("rev-parse", "--show-toplevel")
    root = Path(result.stdout.strip()).resolve()
    if root != ROOT.resolve():
        raise GateError("the gate must run from the repository root")
    if not (root / ".git").exists():
        raise GateError("the repository root has no .git directory")
    if (root.parent / ".git").exists():
        raise GateError("the parent directory is a Git repository; repository separation is not established")
    return root


def verify_origin(remote: str) -> str:
    result = git("remote", "get-url", remote, check=False)
    if result.returncode != 0:
        raise GateError(f"remote {remote!r} is not configured")
    url = result.stdout.strip()
    if not url or not url.startswith(("git@github.com:", "https://github.com/", "ssh://git@github.com/")):
        raise GateError("origin must be an approved GitHub remote")
    if " " in url or ".." in url:
        raise GateError("origin looks invalid")
    return url


def verify_identity() -> dict[str, str]:
    import re

    name = git("config", "user.name").stdout.strip()
    email = git("config", "user.email").stdout.strip()
    if not name or not email:
        raise GateError("a public commit identity is required")
    if not re.match(NOREPLY_PATTERN, email):
        raise GateError("commit email must be a GitHub noreply address")
    denylist, _ = load_denylist(os.environ.get("CONDUCTOR_PRIVATE_DENYLIST"))
    if any(value.lower() in f"{name} {email}".lower() for value in denylist if len(value) >= 3):
        raise GateError("commit identity contains a private value")
    return {"name": name, "email": email}


def scanner_path() -> str:
    candidate = os.environ.get("CONDUCTOR_SCANNER")
    if candidate and Path(candidate).is_file():
        return candidate
    return str(ROOT / "tools" / "privacy" / "scan.py")


def gitleaks_path() -> str:
    candidates = [
        os.environ.get("GITLEAKS_BIN"),
        shutil.which("gitleaks"),
        str(ROOT.parent / "private" / "tools" / "gitleaks"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise GateError("Gitleaks is unavailable; install the pinned binary with tools/privacy/install_gitleaks.sh")


def run_privacy_scan(scopes: list[str], *, require_denylist: bool) -> None:
    denylist = os.environ.get("CONDUCTOR_PRIVATE_DENYLIST") or str(ROOT.parent / "private" / "privacy-denylist.txt")
    command = [
        sys.executable,
        scanner_path(),
        "--root",
        str(ROOT),
        "--denylist",
        denylist,
    ]
    for scope in scopes:
        command.extend(["--scope", scope])
    if require_denylist:
        command.append("--require-denylist")
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    print(result.stdout.rstrip())
    if result.returncode != 0:
        raise GateError("the privacy scanner blocked publication")


def run_gitleaks(staged: bool, *, report_dir: Path | None = None) -> None:
    binary = gitleaks_path()
    version = subprocess.run([binary, "version"], capture_output=True, text=True)
    print(f"gitleaks: {version.stdout.strip()}")
    report = None
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        report = report_dir / f"gitleaks-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    command = [
        binary,
        "git",
        "--redact",
        "--no-banner",
        "--config",
        str(ROOT / ".gitleaks.toml"),
    ]
    if staged:
        command.append("--staged")
    if report is not None:
        command.extend(["--report-format", "json", "--report-path", str(report)])
    command.append(str(ROOT))
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GateError("Gitleaks blocked publication" + (f"; private report: {report}" if report else ""))
    print("gitleaks: no findings")


def staged_files() -> list[str]:
    result = git("diff", "--cached", "--name-only", "--diff-filter=ACMRD")
    return [line for line in result.stdout.splitlines() if line]


def stage_explicit(paths: list[str]) -> None:
    if not paths:
        raise GateError("explicit public paths are required (--paths)")
    normalised = []
    for path in paths:
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise GateError(f"refusing to stage path outside the repository: {path}")
        if path in {".", "./"}:
            raise GateError("refusing to stage the whole repository generically; list public paths")
        normalised.append(path)
    git("add", "-A", "--", *normalised)
    allowed = tuple(path.rstrip("/") + "/" for path in normalised)
    for staged in staged_files():
        if any(staged == path for path in normalised) or staged.startswith(allowed):
            continue
        raise GateError(f"a staged file is outside the explicit public paths: {staged}")


def scan_message(message: str, denylist_path: str | None) -> None:
    if not message.strip():
        raise GateError("a commit message is required")
    denylist, _ = load_denylist(denylist_path)
    findings = scan_text(message, "commit-message", denylist)
    if findings:
        raise GateError(
            "the commit message contains private-looking content: "
            + ", ".join(sorted({item.rule for item in findings}))
        )


def export_commit(commit: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination.parent / "candidate.tar"
    subprocess.run(
        ["git", "archive", "--format=tar", "-o", str(archive), commit],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter="data")
    archive.unlink(missing_ok=True)
    if (destination / ".git").exists():
        raise GateError("the exported candidate contains .git")


def scan_generated(directory: Path) -> None:
    if not directory.exists():
        return
    denylist, _ = load_denylist(os.environ.get("CONDUCTOR_PRIVATE_DENYLIST"))
    findings = []
    for path in directory.rglob("*"):
        if not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
            continue
        relative = path.relative_to(directory).as_posix()
        findings.extend(scan_bytes(path.read_bytes(), relative, denylist))
        findings.extend(__import__("tools.privacy.scan", fromlist=["scan_filename"]).scan_filename(relative, denylist))
    if findings:
        rules = ", ".join(sorted({item.rule for item in findings}))
        raise GateError(f"generated outputs contain private-looking content: {rules}")


def run_command(command: list[str], *, cwd: Path, label: str, timeout: int = 1800) -> str:
    print(f"$ {label}: {' '.join(command)}")
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout)
    if result.returncode != 0:
        tail = (result.stdout + "\n" + result.stderr).strip().splitlines()[-20:]
        raise GateError(f"{label} failed:\n" + "\n".join(tail))
    return result.stdout


def build_and_test(export: Path, *, skip_tests: bool, skip_portal: bool) -> dict:
    results: dict[str, object] = {}
    if skip_tests:
        return {"skipped": True}
    uv = shutil.which("uv")
    if uv:
        run_command([uv, "sync", "--extra", "dev"], cwd=export, label="uv sync")
        output = run_command(
            [uv, "run", "pytest", "-q", "-m", "not requires_db and not e2e"],
            cwd=export,
            label="pytest",
        )
    else:
        venv = export / ".gate-venv"
        run_command([sys.executable, "-m", "venv", str(venv)], cwd=export, label="venv")
        python = venv / "bin" / "python"
        run_command([str(python), "-m", "pip", "install", "-q", "-e", ".[dev]"], cwd=export, label="pip install")
        output = run_command(
            [str(python), "-m", "pytest", "-q", "-m", "not requires_db and not e2e"],
            cwd=export,
            label="pytest",
        )
    results["tests"] = output.strip().splitlines()[-1] if output.strip() else "passed"
    portal = export / "apps" / "portal"
    if (portal / "package.json").is_file() and not skip_portal:
        npm = shutil.which("npm")
        if not npm:
            raise GateError("Node.js/npm is required to build the portal in a clean export")
        run_command([npm, "ci", "--no-audit", "--no-fund"], cwd=portal, label="npm ci")
        run_command([npm, "run", "build"], cwd=portal, label="npm build")
        results["portal"] = "built"
        scan_generated(portal / "dist")
    elif skip_portal:
        results["portal"] = "skipped by flag"
    results["export"] = str(export)
    return results


def artefact_hashes(export: Path) -> dict[str, str]:
    hashes = {}
    for relative in (
        "pyproject.toml",
        "uv.lock",
        "apps/portal/package-lock.json",
        "apps/portal/dist/index.html",
    ):
        path = export / relative
        if path.is_file():
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def write_manifest(
    *,
    manifest_dir: Path,
    commit: str,
    branch: str,
    remote: str,
    identity: dict[str, str],
    tests: dict,
    artefacts: dict[str, str],
    gitleaks_version: str,
) -> Path:
    manifest_dir = manifest_dir.resolve()
    if ROOT.resolve() in manifest_dir.parents or manifest_dir == ROOT.resolve():
        raise GateError("the publication manifest must live outside the public repository")
    manifest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = {
        "repository": str(ROOT),
        "commit": commit,
        "branch": branch,
        "remote": remote,
        "identity": identity,
        "scanner": {"name": "privacy-scan", "version": SCANNER_VERSION},
        "gitleaks": gitleaks_version,
        "tests": tests,
        "artefacts": artefacts,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "prepared_by": "tools/publication_gate.py",
    }
    path = manifest_dir / f"{commit[:12]}-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def check_only(*, public_ci: bool) -> int:
    repository_root()
    verify_origin("origin")
    require = not public_ci
    gitleaks_path()
    run_privacy_scan(["staged", "tree", "history"], require_denylist=require)
    run_gitleaks(staged=True)
    run_gitleaks(staged=False)
    print("publication gate check-only: PASSED")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.check_only:
        return check_only(public_ci=args.public_ci)
    if args.public_ci:
        raise GateError("--public-ci is only valid with --check-only; it cannot publish")
    if not args.message:
        raise GateError("--message is required when committing")
    if args.skip_tests and args.push:
        raise GateError("tests cannot be skipped for a real publication")
    repository_root()
    remote_url = verify_origin(args.remote)
    approved = os.environ.get("CONDUCTOR_APPROVED_REMOTE")
    if args.push and (not approved or approved != remote_url):
        raise GateError("CONDUCTOR_APPROVED_REMOTE must exactly match origin before pushing")
    denylist = os.environ.get("CONDUCTOR_PRIVATE_DENYLIST") or str(ROOT.parent / "private" / "privacy-denylist.txt")
    os.environ["CONDUCTOR_PRIVATE_DENYLIST"] = denylist
    stage_explicit(args.paths)
    run_privacy_scan(["staged", "tree"], require_denylist=True)
    manifest_dir = Path(args.manifest_dir)
    run_gitleaks(staged=True, report_dir=manifest_dir)
    identity = verify_identity()
    scan_message(args.message, denylist)
    git("commit", "-m", args.message)
    commit = git("rev-parse", "HEAD").stdout.strip()
    print(f"created commit {commit}")
    run_privacy_scan(["history"], require_denylist=True)
    run_gitleaks(staged=False, report_dir=manifest_dir)
    with tempfile.TemporaryDirectory(prefix="conductor-publication-") as temporary:
        export = Path(args.export_dir) if args.export_dir else Path(temporary) / "export"
        export_commit(commit, export)
        scan_tree(export, load_denylist(denylist)[0])
        tests = build_and_test(export, skip_tests=args.skip_tests, skip_portal=args.skip_portal)
        scan_tree(export, load_denylist(denylist)[0])
        artefacts = artefact_hashes(export)
        gitleaks_version = subprocess.run([gitleaks_path(), "version"], capture_output=True, text=True).stdout.strip()
        manifest = write_manifest(
            manifest_dir=manifest_dir,
            commit=commit,
            branch=args.branch,
            remote=remote_url,
            identity=identity,
            tests=tests,
            artefacts=artefacts,
            gitleaks_version=gitleaks_version,
        )
        if args.push:
            git("push", "--porcelain", args.remote, f"{commit}:refs/heads/{args.branch}")
            print(f"pushed {commit} to {args.remote} refs/heads/{args.branch}")
        print(f"publication manifest: {manifest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the complete local publication gate.")
    parser.add_argument("--check-only", action="store_true", help="scan only; never commit or push")
    parser.add_argument("--public-ci", action="store_true", help="allow a missing private denylist in CI")
    parser.add_argument("--paths", nargs="*", default=[], help="explicit public paths to stage")
    parser.add_argument("--message", help="public commit message")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--manifest-dir", default=str(DEFAULT_MANIFEST_DIR))
    parser.add_argument("--export-dir", help="keep the clean export at this path (must be outside the repo)")
    parser.add_argument("--skip-tests", action="store_true", help="not permitted for a real publication")
    parser.add_argument("--skip-portal", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except GateError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
