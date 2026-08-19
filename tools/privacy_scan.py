#!/usr/bin/env python3
"""Privacy scanner for outgoing changes.

Checks a git diff for private data that must not reach the public repository.

Two layers of detection:

1. **Known-secret matching** - reads the real private values from the project
   ``.env`` and from the separate data repository (``ha/secrets.yaml``,
   ``ha/.storage/``) and searches the diff for those literals. This catches
   leaks that no generic pattern would, and produces almost no false positives.
2. **Pattern matching** - generic regexes for tokens, coordinates, MAC
   addresses, IP addresses, e-mail addresses and personal hostnames.

Findings are reported as BLOCK (must be fixed) or WARN (needs a human decision).
Secret values are never echoed - only a masked fingerprint is shown.
"""

# mypy: ignore-errors

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:  # running as a module (tests, imports)
    from .paths import ha_config_dir, storage_dir
except ImportError:  # running directly as a script
    from paths import ha_config_dir, storage_dir

# Words that look like identifiers but are far too generic to match on.
GENERIC_TOKENS = {
    "home",
    "house",
    "office",
    "cabin",
    "user",
    "admin",
    "guest",
    "test",
    "demo",
    "example",
    "default",
    "local",
    "main",
    "true",
    "false",
    "none",
    "null",
    "kitchen",
    "bedroom",
    "bathroom",
    "laundry",
    "floor",
    "garage",
    "garden",
    "living",
    "room",
    "assistant",
    "supervisor",
    "core",
    "system",
}

# Files whose content is expected to contain placeholder-looking secrets.
ALLOWLISTED_PATHS = (
    ".example",
    "secrets.yaml.example",
    ".env.example",
)

# .env keys whose values are genuinely secret, vs. keys that merely hold
# configuration (paths, directory names) and would flood the scan with noise.
SECRET_KEY_RE = re.compile(
    r"(?i)(token|key|secret|password|passwd|pass|credential|auth|webhook|salt)"
)
IDENTITY_KEY_RE = re.compile(r"(?i)(host|user|email|mail|domain|url|ssid)")

PATTERNS = [
    # (severity, name, regex, hint)
    (
        "BLOCK",
        "Home Assistant long-lived token (JWT)",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        "Revoke the token in HA (Profile -> Security) and move it to .env",
    ),
    (
        "BLOCK",
        "Assigned secret value",
        re.compile(
            r"(?i)\b(token|api[_-]?key|apikey|password|passwd|secret|client[_-]?secret"
            r"|access[_-]?key|private[_-]?key|webhook)\b\s*[:=]\s*"
            r"['\"]?(?!!secret\b)(?!\$\{)(?!<)(?!your[_-])(?!xxx)(?!\.\.\.)"
            r"[A-Za-z0-9_\-./+=]{12,}"
        ),
        "Replace with !secret <name> (YAML) or an env var",
    ),
    (
        "BLOCK",
        "Private SSH/TLS key material",
        re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
        "Remove the key and rotate it",
    ),
    (
        "BLOCK",
        "Geographic coordinates",
        re.compile(r"(?i)\b(latitude|longitude)\b\s*[:=]\s*['\"]?-?\d{1,3}\.\d{4,}"),
        "Home coordinates identify where you live",
    ),
    (
        "WARN",
        "E-mail address",
        re.compile(
            r"\b[A-Za-z0-9._%+-]+@(?!example\.|test\.)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
        ),
        "Use a placeholder unless this is a public contact address",
    ),
    (
        "WARN",
        "MAC address",
        re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"),
        "MAC addresses identify your physical hardware",
    ),
    (
        "WARN",
        "Private IP address",
        re.compile(
            r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"
        ),
        "Reveals your internal network layout",
    ),
    (
        "WARN",
        "Personal hostname",
        re.compile(r"(?i)\b[\w-]+\.(?:local|fritz\.box|home|lan)\b"),
        "Use a configurable placeholder instead",
    ),
]

# Paths that must never be committed to the public tooling repository.
# HA data lives in a separate private repository, so any of it appearing here
# means it leaked back across the boundary.
FORBIDDEN_PATHS = [
    (re.compile(r"^(?:config|ha|data)/.*\.yaml$"), "HA configuration data"),
    (re.compile(r"(?:^|/)\.storage/"), "HA runtime state"),
    (re.compile(r"(?:^|/)\.ha_tracking/"), "HA environment snapshots"),
    (re.compile(r"(?:^|/)secrets\.yaml$"), "secrets file"),
    (re.compile(r"^\.env$"), "environment file with credentials"),
    (re.compile(r"\.(?:log|db|db-shm|db-wal)$"), "log or database file"),
    (re.compile(r"^backups?/"), "backup directory"),
    (re.compile(r"(?:^|/)known_devices\.yaml$"), "device tracker history"),
]


class Finding:
    """A single privacy issue found in the diff."""

    def __init__(self, severity, category, path, line_no, hint, excerpt):
        self.severity = severity
        self.category = category
        self.path = path
        self.line_no = line_no
        self.hint = hint
        self.excerpt = excerpt

    def __str__(self):
        icon = "🚫" if self.severity == "BLOCK" else "⚠️ "
        where = f"{self.path}:{self.line_no}" if self.line_no else self.path
        return (
            f"{icon} [{self.severity}] {self.category}\n"
            f"     {where}\n"
            f"     {self.excerpt}\n"
            f"     → {self.hint}"
        )


def mask(value: str) -> str:
    """Return a non-reversible hint of a secret so logs stay safe to share."""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def run_git(args, cwd):
    """Run a git command, returning stdout or an empty string on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return result.stdout if result.returncode == 0 else ""


class SecretInventory:
    """Collects the real private values from files kept out of git."""

    def __init__(self, root: Path):
        self.root = root
        self.literals: dict[str, str] = {}  # literal -> where it came from

    def _add(self, value, source, min_len=6):
        if not value:
            return
        value = str(value).strip().strip("\"'")
        if len(value) < min_len or value.lower() in GENERIC_TOKENS:
            return
        # Skip HA indirections and obvious placeholders.
        if value.startswith(("!secret", "!env_var", "${", "<", "your_", "xxx")):
            return
        # Skip plain relative paths (config/, tools/, ./venv) - not secrets.
        if re.fullmatch(r"\.{0,2}/?[\w.-]+/[\w./-]*", value):
            return
        self.literals.setdefault(value, source)

    def collect(self):
        """Gather secrets from .env, secrets.yaml and the HA storage registry."""
        self._collect_env()
        self._collect_secrets_yaml()
        self._collect_ha_storage()
        return self

    def _collect_env(self):
        env_file = self.root / ".env"
        if not env_file.exists():
            return
        for raw in env_file.read_text(errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            # Only real credentials and identifying values are worth matching;
            # path settings like LOCAL_CONFIG_PATH=config/ are not secrets.
            if SECRET_KEY_RE.search(key):
                self._add(value, f".env ({key})")
            elif IDENTITY_KEY_RE.search(key):
                self._add(value, f".env ({key})", min_len=5)

    def _collect_secrets_yaml(self):
        secrets_file = ha_config_dir() / "secrets.yaml"
        if not secrets_file.exists():
            return
        for raw in secrets_file.read_text(errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            self._add(value, f"secrets.yaml ({key.strip()})")

    def _collect_ha_storage(self):
        """Pull identifying values out of the HA registries."""
        storage = storage_dir()
        if not storage.is_dir():
            return

        core_config = self._load_json(storage / "core.config")
        data = core_config.get("data", {})
        for key in ("latitude", "longitude"):
            if data.get(key) is not None:
                self._add(f"{data[key]}", f"core.config ({key})", min_len=4)

        # Person names are the strongest identity signal in a HA setup.
        person = self._load_json(storage / "person")
        for entry in person.get("data", {}).get("persons", []):
            for token in re.split(r"[\s_-]+", str(entry.get("name", ""))):
                if len(token) >= 4 and token.lower() not in GENERIC_TOKENS:
                    self._add(token, "person registry (name)", min_len=4)

        # MAC addresses and serials tie the repo to physical hardware.
        devices = self._load_json(storage / "core.device_registry")
        for device in devices.get("data", {}).get("devices", [])[:2000]:
            for _, identifier in device.get("connections", []) or []:
                if re.fullmatch(
                    r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", str(identifier)
                ):
                    self._add(identifier, "device registry (MAC)", min_len=17)
            serial = device.get("serial_number")
            if serial:
                self._add(serial, "device registry (serial)", min_len=8)

    @staticmethod
    def _load_json(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(errors="replace"))
        except (json.JSONDecodeError, OSError):
            return {}


def parse_diff(diff_text):
    """Yield (path, line_number, added_line) for every added line in a diff."""
    current_path = None
    new_line_no = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_path = line[6:]
            continue
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            new_line_no = int(match.group(1)) if match else 0
            continue
        if not current_path:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            yield current_path, new_line_no, line[1:]
            new_line_no += 1
        elif not line.startswith("-"):
            new_line_no += 1


def is_allowlisted(path):
    """True for files that are meant to hold placeholder credentials."""
    return any(path.endswith(suffix) for suffix in ALLOWLISTED_PATHS)


def scan_paths(paths):
    """Flag files that must never be committed at all."""
    findings = []
    for path in paths:
        for pattern, description in FORBIDDEN_PATHS:
            if pattern.search(path):
                findings.append(
                    Finding(
                        "BLOCK",
                        f"Forbidden file ({description})",
                        path,
                        None,
                        "Remove from the commit: git rm --cached "
                        f"'{path}' - it is gitignored for a reason",
                        "entire file",
                    )
                )
                break
    return findings


def scan_diff(diff_text, inventory):
    """Run both detection layers over the added lines of a diff."""
    findings = []
    for path, line_no, content in parse_diff(diff_text):
        if is_allowlisted(path):
            continue
        stripped = content.strip()
        if not stripped:
            continue

        # Layer 1: literal match against values we know are private.
        for literal, source in inventory.literals.items():
            if literal in content:
                findings.append(
                    Finding(
                        "BLOCK",
                        f"Known private value from {source}",
                        path,
                        line_no,
                        "This exact value is private - replace it with a "
                        "placeholder or !secret reference",
                        f"matched {mask(literal)}",
                    )
                )

        # Layer 2: generic patterns.
        for severity, category, pattern, hint in PATTERNS:
            match = pattern.search(content)
            if match:
                excerpt = stripped[:100] + ("..." if len(stripped) > 100 else "")
                findings.append(
                    Finding(severity, category, path, line_no, hint, excerpt)
                )
    return findings


def resolve_range(root, explicit_range):
    """Decide which changes to scan: explicit range, staged, or unpushed."""
    if explicit_range:
        return explicit_range, f"range {explicit_range}"

    if run_git(["diff", "--cached", "--name-only"], root).strip():
        return "--cached", "staged changes"

    upstream = run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], root
    ).strip()
    if upstream:
        return f"{upstream}..HEAD", f"unpushed commits ({upstream}..HEAD)"

    return "HEAD", "working tree vs HEAD"


def main():
    parser = argparse.ArgumentParser(
        description="Scan outgoing changes for private data"
    )
    parser.add_argument(
        "--range",
        dest="rev_range",
        help="Git range to scan (e.g. origin/main..HEAD). "
        "Defaults to staged changes, else unpushed commits.",
    )
    parser.add_argument(
        "--root", default=".", help="Repository root (default: current directory)"
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on WARN findings too, not just BLOCK",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    rev_range, description = resolve_range(root, args.rev_range)

    diff_args = ["diff", "-U0"]
    if rev_range == "--cached":
        diff_args.append("--cached")
    else:
        diff_args.append(rev_range)

    diff_text = run_git(diff_args, root)

    # --name-status so deletions can be skipped: removing a private file from
    # the repo is exactly what we want, not something to block on.
    status_args = ["diff", "--name-status"]
    status_args += ["--cached"] if rev_range == "--cached" else [rev_range]
    changed_paths = []
    for line in run_git(status_args, root).splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or parts[0].startswith("D"):
            continue
        changed_paths.append(parts[-1])

    inventory = SecretInventory(root).collect()
    findings = scan_paths(changed_paths) + scan_diff(diff_text, inventory)

    blocks = [f for f in findings if f.severity == "BLOCK"]
    warns = [f for f in findings if f.severity == "WARN"]

    if args.json:
        print(
            json.dumps(
                {
                    "scope": description,
                    "files_changed": len(changed_paths),
                    "known_secrets_loaded": len(inventory.literals),
                    "blocking": len(blocks),
                    "warnings": len(warns),
                    "findings": [
                        {
                            "severity": f.severity,
                            "category": f.category,
                            "path": f.path,
                            "line": f.line_no,
                            "hint": f.hint,
                        }
                        for f in findings
                    ],
                },
                indent=2,
            )
        )
    else:
        print("🔍 Privacy Scan")
        print("=" * 60)
        print(f"Scope: {description}")
        print(f"Files changed: {len(changed_paths)}")
        print(f"Known private values loaded: {len(inventory.literals)}")
        print()

        if not findings:
            print("✅ No private data detected in the outgoing changes.")
        else:
            for finding in blocks + warns:
                print(finding)
                print()
            print("-" * 60)
            print(f"🚫 Blocking: {len(blocks)}   ⚠️  Warnings: {len(warns)}")

    if blocks or (args.strict and warns):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
