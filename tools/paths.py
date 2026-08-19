#!/usr/bin/env python3
"""Single source of truth for where the Home Assistant data lives.

Code and data are separate repositories. This one holds the tooling; the HA
configuration, runtime state and environment snapshots live in a private data
repository outside this working tree.

Layout of the data repository::

    <HA_DATA_DIR>/
    ├── ha/          Home Assistant config and .storage (mirror of /config/)
    └── tracking/    environment snapshots written by ha_environment_tracker

Resolution order for ``HA_DATA_DIR``: process environment, then the project
``.env`` file, then the default sibling directory. Every tool takes its default
from here, so the location is changed in exactly one place.
"""

# mypy: ignore-errors

import os
from pathlib import Path

#: Sibling of the tooling repo, used when nothing else is configured.
DEFAULT_DATA_DIR = "../claude-homeassistant-data"

_ENV_KEY = "HA_DATA_DIR"


def project_root() -> Path:
    """Absolute path of the tooling repository (the parent of tools/)."""
    return Path(__file__).resolve().parent.parent


def _from_env_file(root: Path) -> str | None:
    """Read HA_DATA_DIR from the project .env, if it is set there."""
    env_file = root / ".env"
    if not env_file.exists():
        return None
    try:
        content = env_file.read_text(errors="replace")
    except OSError:
        return None
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == _ENV_KEY:
            value = value.strip().strip("\"'")
            if value:
                return value
    return None


def data_dir() -> Path:
    """Root of the data repository, resolved against the project root."""
    root = project_root()
    configured = os.environ.get(_ENV_KEY) or _from_env_file(root) or DEFAULT_DATA_DIR
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = (root / path).resolve()
    return path


def ha_config_dir() -> Path:
    """Home Assistant configuration directory (mirror of the server's /config/)."""
    return data_dir() / "ha"


def storage_dir() -> Path:
    """Home Assistant runtime state (.storage) inside the data repository."""
    return ha_config_dir() / ".storage"


def tracking_dir() -> Path:
    """Environment snapshots written by the tracking tools."""
    return data_dir() / "tracking"


def describe() -> str:
    """Human-readable summary of the resolved locations."""
    return (
        f"data repository: {data_dir()}\n"
        f"  HA config:     {ha_config_dir()}\n"
        f"  tracking:      {tracking_dir()}"
    )


if __name__ == "__main__":
    print(describe())
