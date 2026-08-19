#!/usr/bin/env python3
"""HA Environment Changelog - Display tracked environment changes.

Displays tracked changes to Home Assistant environment over time.
"""

# mypy: ignore-errors

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

try:  # running as a module (tests, imports)
    from .paths import tracking_dir
except ImportError:  # running directly as a script
    from paths import tracking_dir


class HAChangelogViewer:
    """View Home Assistant environment changelog and state changes."""

    def __init__(self, tracking: Path | None = None):
        self.tracking_dir = Path(tracking or tracking_dir())
        self.snapshots_dir = self.tracking_dir / "snapshots"
        self.current_file = self.tracking_dir / "current_snapshot.json"

    def load_snapshot(self, file_path: Path) -> dict:
        """Load a snapshot file."""
        try:
            with open(file_path) as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {file_path}: {e}")
            return {}

    def get_snapshots_sorted(self) -> List[Tuple[str, Path]]:
        """Get all snapshots sorted by timestamp (oldest first)."""
        if not self.snapshots_dir.exists():
            return []

        snapshots = []
        for file in self.snapshots_dir.glob("snapshot_*.json"):
            # Extract timestamp from filename
            parts = file.stem.replace("snapshot_", "").split("_")
            if len(parts) == 2:
                timestamp_str = (
                    f"{parts[0]} {parts[1][:2]}:{parts[1][2:4]}:{parts[1][4:]}"
                )
                snapshots.append((timestamp_str, file))

        snapshots.sort(key=lambda x: x[0])
        return snapshots

    def compare_snapshots(self, before: dict, after: dict) -> Dict:
        """Compare two snapshots."""
        changes = {
            "version": None,
            "integrations": {"added": [], "removed": []},
            "devices": 0,
            "entities": 0,
            "areas": {"added": [], "removed": []},
        }

        # Version
        before_version = before.get("ha_version", "unknown")
        after_version = after.get("ha_version", "unknown")
        if before_version != after_version:
            changes["version"] = (before_version, after_version)

        # Integrations
        before_integ = set(before.get("integrations", {}).keys())
        after_integ = set(after.get("integrations", {}).keys())
        changes["integrations"]["added"] = sorted(list(after_integ - before_integ))
        changes["integrations"]["removed"] = sorted(list(before_integ - after_integ))

        # Devices
        before_dev = before.get("devices", {}).get("total", 0)
        after_dev = after.get("devices", {}).get("total", 0)
        changes["devices"] = after_dev - before_dev

        # Entities
        before_ent = before.get("entities", {}).get("total", 0)
        after_ent = after.get("entities", {}).get("total", 0)
        changes["entities"] = after_ent - before_ent

        # Areas
        before_areas = {a["id"]: a["name"] for a in before.get("areas", [])}
        after_areas = {a["id"]: a["name"] for a in after.get("areas", [])}
        changes["areas"]["added"] = [
            name for aid, name in after_areas.items() if aid not in before_areas
        ]
        changes["areas"]["removed"] = [
            name for aid, name in before_areas.items() if aid not in after_areas
        ]

        return changes

    def print_entry(self, timestamp: str, before: dict, after: dict):
        """Print a changelog entry."""
        changes = self.compare_snapshots(before, after)

        print(f"\n📅 {timestamp}")
        print("   " + "=" * 76)

        # Version
        if changes["version"]:
            # pylint: disable=unpacking-non-sequence
            before_v, after_v = changes["version"]
            print(f"   🏠 HA Version: {before_v} → {after_v}")

        # Integrations
        if changes["integrations"]["added"] or changes["integrations"]["removed"]:
            if changes["integrations"]["added"]:
                items = ", ".join(changes["integrations"]["added"])
                print(f"   ✨ Integrations added: {items}")
            if changes["integrations"]["removed"]:
                items = ", ".join(changes["integrations"]["removed"])
                print(f"   ❌ Integrations removed: {items}")

        # Devices
        if changes["devices"] != 0:
            symbol = "+" if changes["devices"] > 0 else ""
            print(f"   📱 Devices: {symbol}{changes['devices']}")

        # Entities
        if changes["entities"] != 0:
            symbol = "+" if changes["entities"] > 0 else ""
            print(f"   🏠 Entities: {symbol}{changes['entities']}")

        # Areas
        if changes["areas"]["added"] or changes["areas"]["removed"]:
            if changes["areas"]["added"]:
                added = ", ".join(changes["areas"]["added"])
                print(f"   ✨ Areas added: {added}")
            if changes["areas"]["removed"]:
                removed = ", ".join(changes["areas"]["removed"])
                print(f"   ❌ Areas removed: {removed}")

    def show_changelog(self, limit: int = None):
        """Show changelog of all snapshots."""
        snapshots = self.get_snapshots_sorted()

        if not snapshots:
            msg = "📭 No snapshots found. Run 'make track' to start."
            print(msg)
            return

        print("\n" + "=" * 80)
        print("HOME ASSISTANT ENVIRONMENT CHANGELOG")
        print("=" * 80)

        # Load current snapshot for the last entry
        current = self.load_snapshot(self.current_file)

        prev_snapshot = None
        count = 0

        for timestamp, file_path in snapshots:
            if limit and count >= limit:
                break

            snapshot = self.load_snapshot(file_path)

            if prev_snapshot:
                self.print_entry(timestamp, prev_snapshot, snapshot)
                count += 1

            prev_snapshot = snapshot

        # Print final entry (comparing last snapshot to current)
        if prev_snapshot and limit is None:
            current_timestamp = current.get("timestamp", datetime.now().isoformat())[
                :16
            ]
            self.print_entry(current_timestamp, prev_snapshot, current)

        print("\n" + "=" * 80 + "\n")

    def show_latest(self):
        """Show latest changes."""
        self.show_changelog(limit=1)

    def show_current(self):
        """Show current environment state."""
        if not self.current_file.exists():
            print("📭 No snapshot found. Run 'make track' first.")
            return

        current = self.load_snapshot(self.current_file)

        print("\n" + "=" * 80)
        print("CURRENT HOME ASSISTANT ENVIRONMENT")
        print("=" * 80)

        print(f"\n🏠 VERSION: {current.get('ha_version', 'unknown')}")

        integrations = current.get("integrations", {})
        print(f"\n🔌 INTEGRATIONS: {len(integrations)}")
        for domain in sorted(integrations.keys())[:20]:
            print(f"   - {domain}")
        if len(integrations) > 20:
            print(f"   ... and {len(integrations) - 20} more")

        devices = current.get("devices", {})
        print(f"\n📱 DEVICES: {devices.get('total', 0)}")

        entities = current.get("entities", {})
        print(f"\n🏠 ENTITIES: {entities.get('total', 0)}")

        areas = current.get("areas", [])
        print(f"\n🗺️  AREAS: {len(areas)}")
        for area in areas:
            print(f"   - {area['name']}")

        print("\n" + "=" * 80 + "\n")


def main():
    """Run the changelog viewer."""
    viewer = HAChangelogViewer()

    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "latest":
            viewer.show_latest()
        elif command == "current":
            viewer.show_current()
        elif command == "changelog":
            viewer.show_changelog()
        else:
            print(f"Unknown command: {command}")
            print("Usage: ha_changelog.py [changelog|latest|current]")
    else:
        viewer.show_latest()


if __name__ == "__main__":
    main()
