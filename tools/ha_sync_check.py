#!/usr/bin/env python3
"""Compare the Home Assistant server against the local working copy.

Answers the question "is what I have here still what the server has?" before any
work starts. Checks, in order of how often they bite:

* SSH reachability of the HA host (and whether the configured name resolves)
* HA core version on the server vs. the ``homeassistant`` package in the venv -
  a mismatch makes the official validator fail on newer storage schemas
* Config drift between ``config/`` and the server, via an rsync dry run
* Pending HA updates reported by the update entities
* Git state: uncommitted files and divergence from the remotes

Exit codes: 0 = in sync, 1 = drift found, 2 = server unreachable.
"""

# mypy: ignore-errors

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:  # running as a module (tests, imports)
    from .paths import data_dir, ha_config_dir, storage_dir
except ImportError:  # running directly as a script
    from paths import data_dir, ha_config_dir, storage_dir

SSH_OPTS = ["-o", "ConnectTimeout=8", "-o", "BatchMode=yes"]


class CheckResult:
    """One check outcome, rendered as a single report line."""

    def __init__(self, name, status, detail, action=None):
        self.name = name
        self.status = status  # "ok" | "drift" | "error" | "skip"
        self.detail = detail
        self.action = action

    @property
    def icon(self):
        return {"ok": "✅", "drift": "⚠️ ", "error": "❌", "skip": "⏭️ "}[self.status]

    def __str__(self):
        line = f"{self.icon} {self.name}: {self.detail}"
        if self.action:
            line += f"\n     → {self.action}"
        return line


def run(cmd, cwd=None, timeout=60):
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def load_env(root: Path) -> dict:
    """Read simple KEY=VALUE pairs from the project .env file."""
    env = {}
    env_file = root / ".env"
    if not env_file.exists():
        return env
    for raw in env_file.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("\"'")
    return env


def check_ssh(host):
    """Verify the HA host resolves and accepts a key-based SSH session.

    Returns (result, working_host). ``working_host`` is None when unreachable,
    and may differ from ``host`` when the .local fallback is what actually
    connects - the remaining checks must use that name, not the broken one.
    """
    code, out, err = run(["ssh", *SSH_OPTS, host, "echo ok"], timeout=15)
    if code == 0 and "ok" in out:
        return CheckResult("SSH", "ok", f"{host} reachable"), host

    if "could not resolve hostname" in err.lower():
        # A .local variant often works when the bare name does not (VPN/DNS).
        alt = f"{host}.local" if not host.endswith(".local") else None
        if alt:
            code_alt, out_alt, _ = run(["ssh", *SSH_OPTS, alt, "echo ok"], timeout=15)
            if code_alt == 0 and "ok" in out_alt:
                return (
                    CheckResult(
                        "SSH",
                        "drift",
                        f"{host} does not resolve, but {alt} does - using it for this run",
                        f"Set HA_HOST={alt} in .env, or add a Host alias in ~/.ssh/config",
                    ),
                    alt,
                )
        return (
            CheckResult(
                "SSH",
                "error",
                f"{host} does not resolve (DNS/VPN?)",
                "Check the VPN and HA_HOST in .env",
            ),
            None,
        )

    last_line = err.strip().splitlines()[-1] if err.strip() else "unknown error"
    return (
        CheckResult(
            "SSH",
            "error",
            f"cannot connect to {host}: {last_line}",
            "Verify SSH key auth and that HA is running",
        ),
        None,
    )


def server_ha_version(host):
    """Read the running HA core version from the server."""
    code, out, _ = run(["ssh", *SSH_OPTS, host, "cat /config/.HA_VERSION"], timeout=20)
    if code == 0 and out.strip():
        return out.strip()

    code, out, _ = run(["ssh", *SSH_OPTS, host, "ha core info --raw-json"], timeout=30)
    if code == 0 and out.strip():
        try:
            return json.loads(out).get("data", {}).get("version")
        except json.JSONDecodeError:
            pass
    return None


def venv_ha_version(root: Path):
    """Read the homeassistant package version installed in the local venv."""
    for candidate in ("venv", ".venv"):
        python = root / candidate / "bin" / "python3"
        if not python.exists():
            continue
        code, out, _ = run(
            [
                str(python),
                "-c",
                "import importlib.metadata as m; print(m.version('homeassistant'))",
            ],
            timeout=30,
        )
        if code == 0 and out.strip():
            return out.strip()
    return None


def check_versions(host, root):
    """Compare server core version against the local validator package."""
    server = server_ha_version(host)
    local = venv_ha_version(root)

    if server is None:
        return CheckResult(
            "HA version", "drift", "could not read the version from the server"
        )
    if local is None:
        return CheckResult(
            "HA version",
            "drift",
            f"server runs {server}, no homeassistant package found in the venv",
            "Run: make setup",
        )
    if server == local:
        return CheckResult("HA version", "ok", f"server and venv both on {server}")

    return CheckResult(
        "HA version",
        "drift",
        f"server runs {server}, local validator is {local}",
        f"Run: source venv/bin/activate && pip install --upgrade "
        f"'homeassistant=={server}'  (a stale package makes the official "
        f"validator reject newer .storage schemas)",
    )


def check_config_drift(host, root, remote_path, local_path):
    """Use an rsync dry run to list files that differ from the server."""
    excludes = root / "etc" / "rsync-excludes-pull"
    cmd = ["rsync", "-rin", "--delete"]
    if excludes.exists():
        cmd.append(f"--exclude-from={excludes}")
    cmd += [f"{host}:{remote_path}", f"{str(local_path).rstrip('/')}/"]

    code, out, err = run(cmd, cwd=root, timeout=180)
    if code != 0:
        return CheckResult(
            "Config drift", "error", f"rsync dry run failed: {err.strip()[:200]}"
        )

    changes = [
        line
        for line in out.splitlines()
        if line.strip() and not line.startswith(("sending", "total", "sent "))
    ]
    if not changes:
        return CheckResult("Config drift", "ok", "local config matches the server")

    preview = ", ".join(line.split()[-1] for line in changes[:5])
    more = f" (+{len(changes) - 5} more)" if len(changes) > 5 else ""
    return CheckResult(
        "Config drift",
        "drift",
        f"{len(changes)} file(s) differ: {preview}{more}",
        "Run: make pull",
    )


def check_pending_updates(root: Path):
    """Report HA components that advertise an available update."""
    restore = storage_dir() / "core.restore_state"
    if not restore.exists():
        return CheckResult(
            "Pending updates", "skip", "no local .storage snapshot (run make pull)"
        )

    try:
        data = json.loads(restore.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        return CheckResult(
            "Pending updates", "skip", "could not parse core.restore_state"
        )

    # core.restore_state stores {"data": [{"state": {...}}, ...]}
    entries = data.get("data", [])
    if not isinstance(entries, list):
        return CheckResult(
            "Pending updates", "skip", "unexpected core.restore_state layout"
        )

    pending = []
    for entry in entries:
        state = entry.get("state", {})
        entity_id = state.get("entity_id", "")
        if not entity_id.startswith("update."):
            continue
        if state.get("state") != "on":
            continue
        attrs = state.get("attributes", {})
        installed = attrs.get("installed_version")
        latest = attrs.get("latest_version")
        name = attrs.get("friendly_name") or entity_id
        if installed and latest and installed != latest:
            pending.append(f"{name}: {installed} → {latest}")

    if not pending:
        return CheckResult("Pending updates", "ok", "nothing waiting to be updated")

    preview = "; ".join(pending[:4])
    more = f" (+{len(pending) - 4} more)" if len(pending) > 4 else ""
    return CheckResult(
        "Pending updates", "drift", f"{len(pending)} available: {preview}{more}"
    )


def check_git(root: Path):
    """Report uncommitted work and divergence from the tracked remote."""
    code, out, _ = run(["git", "status", "--porcelain"], cwd=root)
    if code != 0:
        return [CheckResult("Git", "skip", "not a git repository")]

    results = []
    dirty = [line for line in out.splitlines() if line.strip()]
    if dirty:
        tracked = [line for line in dirty if not line.startswith("??")]
        untracked = len(dirty) - len(tracked)
        detail = f"{len(tracked)} modified, {untracked} untracked"
        results.append(CheckResult("Git working tree", "drift", detail))
    else:
        results.append(CheckResult("Git working tree", "ok", "clean"))

    _, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    branch = branch.strip()
    _, upstream, _ = run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd=root,
    )
    upstream = upstream.strip()
    if not upstream:
        results.append(CheckResult("Git remote", "skip", f"{branch} has no upstream"))
        return results

    _, counts, _ = run(
        ["git", "rev-list", "--left-right", "--count", f"{upstream}...HEAD"], cwd=root
    )
    match = re.match(r"(\d+)\s+(\d+)", counts.strip())
    if not match:
        results.append(
            CheckResult("Git remote", "skip", "could not compare to upstream")
        )
        return results

    behind, ahead = int(match.group(1)), int(match.group(2))
    if behind == 0 and ahead == 0:
        results.append(
            CheckResult("Git remote", "ok", f"{branch} in sync with {upstream}")
        )
    else:
        results.append(
            CheckResult(
                "Git remote",
                "drift",
                f"{branch} is {ahead} ahead / {behind} behind {upstream}",
            )
        )
    return results


def check_data_repo():
    """Report the state of the separate HA data repository."""
    data = data_dir()
    if not data.exists():
        return CheckResult(
            "Data repo",
            "error",
            f"{data} does not exist",
            "Clone the private data repository, or set HA_DATA_DIR in .env",
        )

    if not (data / ".git").exists():
        return CheckResult(
            "Data repo", "drift", f"{data} exists but is not a git repository"
        )

    code, out, _ = run(["git", "status", "--porcelain"], cwd=data)
    if code != 0:
        return CheckResult("Data repo", "skip", "could not read the data repo status")

    dirty = [line for line in out.splitlines() if line.strip()]
    if not dirty:
        return CheckResult("Data repo", "ok", f"{data.name} clean")

    # Uncommitted HA data means the last pull is not recorded anywhere yet.
    return CheckResult(
        "Data repo",
        "drift",
        f"{len(dirty)} uncommitted change(s) in {data.name}",
        f"Review and commit: git -C {data} status",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Compare the HA server against the local working copy"
    )
    parser.add_argument("--root", default=".", help="Project root")
    parser.add_argument("--host", help="Override HA_HOST from .env")
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    parser.add_argument(
        "--skip-remote",
        action="store_true",
        help="Only run the local checks (no SSH or rsync)",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    env = load_env(root)
    host = args.host or env.get("HA_HOST", "homeassistant")
    remote_path = env.get("HA_REMOTE_PATH", "/config/")
    local_path = ha_config_dir()

    results = []
    working_host = None

    if args.skip_remote:
        results.append(CheckResult("SSH", "skip", "skipped via --skip-remote"))
    else:
        ssh_result, working_host = check_ssh(host)
        results.append(ssh_result)

    if working_host:
        results.append(check_versions(working_host, root))
        results.append(check_config_drift(working_host, root, remote_path, local_path))

    results.append(check_pending_updates(root))
    results.extend(check_git(root))
    results.append(check_data_repo())

    drift = [r for r in results if r.status == "drift"]
    errors = [r for r in results if r.status == "error"]

    if args.json:
        print(
            json.dumps(
                {
                    "host": working_host or host,
                    "reachable": bool(working_host),
                    "in_sync": not drift and not errors,
                    "checks": [
                        {
                            "name": r.name,
                            "status": r.status,
                            "detail": r.detail,
                            "action": r.action,
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
        )
    else:
        print("🔄 Home Assistant Sync Check")
        print("=" * 60)
        print(f"Host: {host}")
        print()
        for result in results:
            print(result)
        print()
        print("-" * 60)
        if errors:
            print(f"❌ {len(errors)} error(s), {len(drift)} drift item(s)")
        elif drift:
            print(f"⚠️  {len(drift)} item(s) need attention before you start working")
        else:
            print("✅ Local setup is in sync with the server")

    if errors:
        return 2
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
