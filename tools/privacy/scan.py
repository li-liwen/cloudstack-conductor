"""Privacy and infrastructure scanner for the public repository.

This complements Gitleaks. It looks for private addresses, deployment hostnames,
workstation paths, private identifiers (via an external supplemental denylist),
unexpected binaries, blocked file types, unsafe symlinks, and unsafe commit
metadata. Findings never echo the matched value; only a rule, path, location,
and short fingerprint are reported.

Scopes:
  tree     working tree (excluding ignored build/dependency directories)
  staged   Git index (what would be committed)
  history  every reachable commit, blob, author/committer, commit message, tag
  all      tree + staged + history
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

VERSION = "1.0.0"

DOCUMENTATION_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".tox",
    "htmlcov",
    "dist",
    "build",
    "coverage",
    "publication-manifests",
    "private",
    "secrets",
    "state",
}

TEXT_SUFFIXES = {
    ".py",
    ".pyi",
    ".md",
    ".txt",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".sh",
    ".bash",
    ".zsh",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".scss",
    ".html",
    ".svg",
    ".xml",
    ".sql",
    ".mako",
    ".env",
    ".example",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
    ".prettierrc",
    ".eslintrc",
    ".lock",
    ".mjs",
    ".cjs",
    ".graphql",
    ".proto",
    ".rst",
}

BLOCKED_SUFFIXES = {
    ".xlsx",
    ".xlsm",
    ".xls",
    ".ods",
    ".csv",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".dump",
    ".backup",
    ".bak",
    ".har",
    ".pcap",
    ".pcapng",
    ".trace",
    ".log",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".7z",
    ".rar",
    ".qcow2",
    ".img",
    ".iso",
    ".pdf",
    ".docx",
    ".pptx",
    ".key",
    ".pem",
    ".p12",
    ".jks",
    ".mo",
    ".pyc",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tiff",
    ".ico",
}

ALLOWED_BINARY_SUFFIXES: set[str] = set()

IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
IPV6_RE = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(?![\w:])")
WORKSTATION_RE = re.compile(r"(?<![\w/])/(?:home|Users)/([A-Za-z0-9._-]+)")
WINDOWS_USER_RE = re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
BEARER_RE = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{24,})")
CREDENTIAL_ASSIGN_RE = re.compile(
    r"""(?ix)
    \b(api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token|refresh[_-]?token|password)
    \b\s*[:=]\s*["']?
    ([A-Za-z0-9_./+=:@-]{12,})
    """
)
SIGNATURE_RE = re.compile(r"(?i)\bsignature=([A-Za-z0-9%+/=]{20,})")
INTERNAL_SUFFIX_RE = re.compile(r"(?i)\b[a-z0-9][a-z0-9._-]*\.(?:internal|local|lan|corp|home\.arpa)\b")

SAFE_PLACEHOLDERS = {
    "replace-with",
    "replace_me",
    "change-me",
    "change_me",
    "example",
    "sample",
    "placeholder",
    "synthetic",
    "demo-",
    "not-a-secret",
    "your-",
    "xxxx",
    "null",
    "none",
    "true",
    "false",
}

ALLOWED_WORKSTATION_USERS = {"runner", "app", "example", "user", "container", "svc"}


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    path: str
    line: int | None = None
    column: int | None = None
    fingerprint: str = ""
    message: str = ""


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def load_denylist(path: str | Path | None) -> tuple[list[str], bool]:
    if not path:
        return [], False
    candidate = Path(path)
    if not candidate.is_file():
        return [], False
    values = []
    for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        values.append(value)
    return values, bool(values)


def redact_path(path: str, denylist: Iterable[str]) -> str:
    result = path
    for value in denylist:
        if len(value) >= 3 and value.lower() in result.lower():
            result = re.sub(re.escape(value), "[redacted]", result, flags=re.IGNORECASE)
    return result


def scan_text(text: str, path: str, denylist: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    denylist = list(denylist)

    def add(rule: str, line: int | None, column: int | None, value: str, message: str = "") -> None:
        findings.append(
            Finding(
                rule=rule,
                severity="error",
                path=path,
                line=line,
                column=column,
                fingerprint=fingerprint(value),
                message=message,
            )
        )

    def line_col(offset: int) -> tuple[int, int]:
        line = text.count("\n", 0, offset) + 1
        column = offset - text.rfind("\n", 0, offset)
        return line, column

    for match in IPV4_RE.finditer(text):
        candidate = match.group(0)
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version != 4:
            continue
        if address.is_loopback or address.is_unspecified:
            continue
        if any(address in network for network in DOCUMENTATION_NETWORKS):
            continue
        if _is_netmask(candidate):
            continue
        if address.is_private or address.is_link_local or address.is_multicast:
            line, column = line_col(match.start())
            add("private_ipv4", line, column, candidate)

    for match in IPV6_RE.finditer(text):
        candidate = match.group(0)
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_loopback or address.is_unspecified:
            continue
        if address.is_private or address.is_link_local:
            line, column = line_col(match.start())
            add("private_ipv6", line, column, candidate)

    for match in WORKSTATION_RE.finditer(text):
        user = match.group(1).lower()
        if user in ALLOWED_WORKSTATION_USERS:
            continue
        line, column = line_col(match.start())
        add("workstation_path", line, column, match.group(0))

    for match in WINDOWS_USER_RE.finditer(text):
        line, column = line_col(match.start())
        add("workstation_path", line, column, match.group(0))

    for match in PRIVATE_KEY_RE.finditer(text):
        line, column = line_col(match.start())
        add("private_key", line, column, match.group(0))

    for match in BEARER_RE.finditer(text):
        candidate = match.group(1)
        if _looks_placeholder(candidate):
            continue
        line, column = line_col(match.start())
        add("bearer_token", line, column, candidate)

    for match in CREDENTIAL_ASSIGN_RE.finditer(text):
        candidate = match.group(2)
        if _looks_placeholder(candidate) or _is_code_reference(candidate):
            continue
        line, column = line_col(match.start())
        add("credential_assignment", line, column, candidate)

    for match in SIGNATURE_RE.finditer(text):
        candidate = match.group(1)
        if _looks_placeholder(candidate):
            continue
        line, column = line_col(match.start())
        add("cloudstack_signature", line, column, candidate)

    for match in INTERNAL_SUFFIX_RE.finditer(text):
        candidate = match.group(0)
        if candidate.lower().endswith(("example.com", "example.org", "example.net")):
            continue
        line, column = line_col(match.start())
        add("internal_hostname", line, column, candidate)

    lowered = text.lower()
    for value in denylist:
        needle = value.lower()
        if len(needle) < 3:
            continue
        start = 0
        while True:
            index = lowered.find(needle, start)
            if index < 0:
                break
            line, column = line_col(index)
            add("private_denylist", line, column, value, "matched a value from the private supplemental denylist")
            start = index + len(needle)

    return findings


CODE_REFERENCE_PREFIXES = (
    "os.",
    "read_",
    "settings.",
    "args.",
    "credentials.",
    "principal.",
    "self.",
    "getenv",
    "legacy_password",
    "synthetic_password",
    "password_value",
    "other_password",
    "config.",
    "vault.",
    "payload",
    "row.",
    "record.",
    "mapping.",
    "request.",
    "response.",
    "headers.",
    "params.",
    "data.",
    "body.",
    "value",
    "str",
)


def _is_code_reference(candidate: str) -> bool:
    lowered = candidate.lower()
    return lowered.startswith(CODE_REFERENCE_PREFIXES) or "(" in candidate


def _is_netmask(candidate: str) -> bool:
    try:
        value = int(ipaddress.IPv4Address(candidate))
    except ValueError:
        return False
    inverse = ~value & 0xFFFFFFFF
    return bool((inverse & (inverse + 1)) == 0)


def _looks_placeholder(candidate: str) -> bool:
    lowered = candidate.lower()
    if any(marker in lowered for marker in SAFE_PLACEHOLDERS):
        return True
    if candidate.startswith(("${", "$(", "{{", "<", "%:")):
        return True
    return set(candidate) <= {"x", "X", "*", ".", "-", "_", "0"}


def scan_filename(path: str, denylist: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    name = Path(path).name
    suffix = Path(path).suffix.lower()
    lowered_name = name.lower()
    if suffix in BLOCKED_SUFFIXES and suffix not in ALLOWED_BINARY_SUFFIXES:
        findings.append(Finding("blocked_file_type", "error", path, message=f"file type {suffix} is blocked"))
    if (
        lowered_name == ".env" or (lowered_name.startswith(".env.") and lowered_name != ".env.example")
    ) or lowered_name.endswith(".env"):
        findings.append(Finding("environment_file", "error", path, message="environment files are blocked"))
    if re.search(r"(?i)(^|[._-])(secret|credential|token|password)s?([._-]|$)", lowered_name):
        findings.append(Finding("suspicious_filename", "error", path, message="file name suggests stored secrets"))
    redacted = redact_path(path, denylist)
    for value in denylist:
        if len(value) >= 3 and value.lower() in path.lower():
            findings.append(
                Finding(
                    "private_filename",
                    "error",
                    redacted,
                    fingerprint=fingerprint(value),
                    message="path contains a value from the private supplemental denylist",
                )
            )
            break
    return findings


def scan_bytes(data: bytes, path: str, denylist: Iterable[str]) -> list[Finding]:
    findings = scan_filename(path, denylist)
    suffix = Path(path).suffix.lower()
    if b"\x00" in data and suffix not in ALLOWED_BINARY_SUFFIXES:
        findings.append(Finding("unexpected_binary", "error", path, message="unexpected binary content"))
        return findings
    if len(data) > 8 * 1024 * 1024:
        findings.append(Finding("oversized_file", "error", path, message="file is too large for review"))
        return findings
    if b"\x00" not in data:
        text = data.decode("utf-8", errors="replace")
        findings.extend(scan_text(text, path, denylist))
    return findings


def scan_symlink(target: str, path: str, root: Path, denylist: Iterable[str]) -> list[Finding]:
    findings = scan_filename(path, denylist)
    if target.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", target):
        findings.append(Finding("absolute_symlink", "error", path, message="symlink target is absolute"))
        return findings
    resolved = (root / path).parent / target
    try:
        resolved = resolved.resolve()
        root_resolved = root.resolve()
        if root_resolved not in resolved.parents and resolved != root_resolved:
            findings.append(
                Finding(
                    "escaping_symlink",
                    "error",
                    path,
                    message="symlink escapes the repository",
                )
            )
    except OSError:
        findings.append(Finding("broken_symlink", "error", path, message="symlink could not be resolved"))
    return findings


def scan_tree(root: Path, denylist: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [directory for directory in dirs if directory not in SKIP_DIRS and not directory.startswith(".venv")]
        for filename in files:
            path = Path(current) / filename
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                findings.extend(scan_symlink(os.readlink(path), relative, root, denylist))
                continue
            try:
                data = path.read_bytes()
            except OSError:
                findings.append(Finding("unreadable_file", "error", relative, message="file could not be read"))
                continue
            findings.extend(scan_bytes(data, relative, denylist))
    return findings


def scan_staged(root: Path, denylist: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    staged = _git(root, ["diff", "--cached", "--name-only", "--diff-filter=ACMRT"])
    if staged.returncode != 0:
        return [Finding("git_error", "error", "", message="could not read the Git index")]
    for line in staged.stdout.decode("utf-8", errors="replace").splitlines():
        if not line:
            continue
        info = _git(root, ["ls-files", "-s", "--", line])
        mode = info.stdout.decode("utf-8", errors="replace").split(" ", 1)[0] if info.returncode == 0 else "100644"
        if mode == "120000":
            target = _git(root, ["show", f":{line}"])
            findings.extend(
                scan_symlink(
                    target.stdout.decode("utf-8", errors="replace").strip(),
                    line,
                    root,
                    denylist,
                )
            )
            continue
        blob = _git(root, ["show", f":{line}"])
        if blob.returncode != 0:
            findings.append(Finding("git_error", "error", line, message="staged content could not be read"))
            continue
        findings.extend(scan_bytes(blob.stdout, line, denylist))
    return findings


COMMIT_AUTHOR_RE = re.compile(r"^(author|committer) (.*) <(.*)> (\d+) ([+-]\d{4})$")
NOREPLY_RE = re.compile(r"^(?:\d+\+[A-Za-z0-9._-]+@users\.noreply\.github\.com|noreply@github\.com)$")


def scan_history(root: Path, denylist: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    commits = _git(root, ["rev-list", "--all"])
    if commits.returncode != 0:
        return [Finding("git_error", "error", "", message="could not list Git history")]
    seen_blobs: dict[str, list[Finding]] = {}
    for commit in commits.stdout.decode().split():
        raw = _git(root, ["cat-file", "-p", commit])
        if raw.returncode != 0:
            continue
        text = raw.stdout.decode("utf-8", errors="replace")
        message = text.split("\n\n", 1)[1] if "\n\n" in text else ""
        for finding in scan_text(message, f"commit:{commit[:12]}:message", denylist):
            findings.append(finding)
        for line in text.splitlines():
            match = COMMIT_AUTHOR_RE.match(line)
            if not match:
                continue
            role, name, email = match.group(1), match.group(2), match.group(3)
            if not NOREPLY_RE.match(email):
                findings.append(
                    Finding(
                        "author_email",
                        "error",
                        f"commit:{commit[:12]}:{role}",
                        message="commit identity must use a GitHub noreply address",
                    )
                )
            for finding in scan_text(f"{name} {email}", f"commit:{commit[:12]}:{role}", denylist):
                findings.append(finding)
        tree = _git(root, ["ls-tree", "-r", "-z", commit])
        if tree.returncode != 0:
            continue
        for entry in tree.stdout.split(b"\x00"):
            if not entry:
                continue
            try:
                header, raw_path = entry.split(b"\t", 1)
                mode, _kind, blob_hash = header.decode().split()
            except ValueError:
                continue
            path = raw_path.decode("utf-8", errors="replace")
            if mode == "120000":
                target = _git(root, ["cat-file", "blob", blob_hash])
                findings.extend(
                    scan_symlink(
                        target.stdout.decode("utf-8", errors="replace").strip(),
                        path,
                        root,
                        denylist,
                    )
                )
                continue
            if blob_hash in seen_blobs:
                for cached in seen_blobs[blob_hash]:
                    findings.append(
                        Finding(
                            cached.rule,
                            cached.severity,
                            f"commit:{commit[:12]}:{path}",
                            cached.line,
                            cached.column,
                            cached.fingerprint,
                            cached.message,
                        )
                    )
                continue
            blob = _git(root, ["cat-file", "blob", blob_hash])
            if blob.returncode != 0:
                continue
            blob_findings = scan_bytes(blob.stdout, path, denylist)
            seen_blobs[blob_hash] = [
                Finding(item.rule, item.severity, item.path, item.line, item.column, item.fingerprint, item.message)
                for item in blob_findings
            ]
            for item in blob_findings:
                findings.append(
                    Finding(
                        item.rule,
                        item.severity,
                        f"commit:{commit[:12]}:{path}",
                        item.line,
                        item.column,
                        item.fingerprint,
                        item.message,
                    )
                )
    tags = _git(root, ["for-each-ref", "refs/tags", "--format=%(refname) %(objecttype)"])
    if tags.returncode == 0:
        for line in tags.stdout.decode(errors="replace").splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            tag_ref, kind = parts
            if kind != "tag":
                continue
            raw = _git(root, ["cat-file", "-p", tag_ref])
            if raw.returncode == 0:
                findings.extend(scan_text(raw.stdout.decode("utf-8", errors="replace"), f"tag:{tag_ref}", denylist))
    return findings


def _git(root: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)


def dedupe(findings: Iterable[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    result: list[Finding] = []
    for finding in findings:
        key = (finding.rule, finding.path, finding.line, finding.column, finding.fingerprint)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result


def run_scan(
    root: Path,
    *,
    scopes: Iterable[str],
    denylist_path: str | Path | None,
    require_denylist: bool = False,
) -> list[Finding]:
    denylist, present = load_denylist(denylist_path)
    findings: list[Finding] = []
    if require_denylist and not present:
        findings.append(
            Finding(
                "private_policy_missing",
                "error",
                "",
                message="the private supplemental denylist is missing or empty",
            )
        )
    scopes = set(scopes)
    if "tree" in scopes:
        findings.extend(scan_tree(root, denylist))
    if "staged" in scopes:
        findings.extend(scan_staged(root, denylist))
    if "history" in scopes:
        findings.extend(scan_history(root, denylist))
    return dedupe(findings)


def _render_text(findings: list[Finding], denylist: Iterable[str]) -> str:
    lines = []
    for finding in findings:
        path = redact_path(finding.path, denylist)
        location = f":{finding.line}:{finding.column}" if finding.line else ""
        detail = f" [{finding.message}]" if finding.message else ""
        lines.append(
            f"{finding.severity.upper()} {finding.rule} {path}{location} fingerprint={finding.fingerprint}{detail}"
        )
    errors = sum(1 for item in findings if item.severity == "error")
    warnings = len(findings) - errors
    lines.append(f"scanner {VERSION}: {errors} error(s), {warnings} warning(s)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan a repository for private information.")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument(
        "--scope",
        action="append",
        choices=["tree", "staged", "history", "all"],
        default=None,
        help="scope to scan (repeatable)",
    )
    parser.add_argument("--denylist", default=os.environ.get("CONDUCTOR_PRIVATE_DENYLIST"))
    parser.add_argument("--require-denylist", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--fail-on-warning", action="store_true")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(VERSION)
        return 0
    root = Path(args.root).resolve()
    scopes = args.scope or ["tree"]
    if "all" in scopes:
        scopes = ["tree", "staged", "history"]
    findings = run_scan(
        root,
        scopes=scopes,
        denylist_path=args.denylist,
        require_denylist=args.require_denylist,
    )
    denylist, _ = load_denylist(args.denylist)
    errors = [item for item in findings if item.severity == "error"]
    warnings = [item for item in findings if item.severity != "error"]
    if args.as_json:
        payload = []
        for finding in findings:
            item = asdict(finding)
            item["path"] = redact_path(finding.path, denylist)
            payload.append(item)
        print(json.dumps({"version": VERSION, "findings": payload}, ensure_ascii=False, indent=2))
    else:
        print(_render_text(findings, denylist))
    if errors or (warnings and args.fail_on_warning):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
