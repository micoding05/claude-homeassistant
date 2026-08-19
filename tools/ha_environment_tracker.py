#!/usr/bin/env python3
"""Home Assistant Environment Tracker.

Monitors and tracks HA version, integrations, updates, devices.
"""

# mypy: ignore-errors

import hashlib
import json
from datetime import datetime
from pathlib import Path

try:  # running as a module (tests, imports)
    from .paths import ha_config_dir, tracking_dir
except ImportError:  # running directly as a script
    from paths import ha_config_dir, tracking_dir


class HAEnvironmentTracker:
    """Track Home Assistant environment changes over time."""

    def __init__(self, config_dir: Path | None = None, tracking: Path | None = None):
        self.config_dir = Path(config_dir or ha_config_dir())
        self.storage_dir = self.config_dir / ".storage"
        self.tracker_dir = Path(tracking or tracking_dir())
        self.tracker_dir.mkdir(parents=True, exist_ok=True)

        self.current_data = {}
        self.previous_data = {}
        self.changes = {}

    def load_previous_snapshot(self) -> dict:
        """Load the last recorded snapshot."""
        snapshot_file = self.tracker_dir / "current_snapshot.json"
        if snapshot_file.exists():
            with open(snapshot_file) as f:
                return json.load(f)
        return {}

    def save_snapshot(self, data: dict):
        """Save current snapshot and archive previous one if changed."""
        snapshot_file = self.tracker_dir / "current_snapshot.json"
        data["timestamp"] = datetime.now().isoformat()

        # Check if anything changed
        prev_hash = None
        if snapshot_file.exists():
            with open(snapshot_file) as f:
                prev_data = json.load(f)
                prev_str = json.dumps(prev_data, indent=2)
                prev_hash = hashlib.md5(prev_str.encode()).hexdigest()

        curr_str = json.dumps(data, indent=2)
        curr_hash = hashlib.md5(curr_str.encode()).hexdigest()

        # Save current snapshot
        with open(snapshot_file, "w") as f:
            json.dump(data, f, indent=2)

        # If changed, archive with timestamp
        if prev_hash and prev_hash != curr_hash:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            snapshots_dir = self.tracker_dir / "snapshots"
            snapshots_dir.mkdir(exist_ok=True)
            archive_file = snapshots_dir / f"snapshot_{timestamp}.json"
            with open(archive_file, "w") as f:
                json.dump(data, f, indent=2)
            print(f"✅ Snapshot archived: {archive_file}")

    def get_ha_version(self) -> str:
        """Get Home Assistant version from storage metadata."""
        version_from_supervisor = None

        # Try to get version from supervisor
        supervisor_file = self.storage_dir / "hassio.json"
        if supervisor_file.exists():
            try:
                with open(supervisor_file) as f:
                    supervisor_data = json.load(f)
                    version_from_supervisor = (
                        supervisor_data.get("data", {}).get("core", {}).get("version")
                    )
            except json.JSONDecodeError:
                pass

        return version_from_supervisor or "unknown"

    def get_integrations(self) -> dict:
        """Parse integrations from config_entries."""
        config_entries_file = self.storage_dir / "core.config_entries"
        integrations = {}

        if not config_entries_file.exists():
            return integrations

        with open(config_entries_file) as f:
            data = json.load(f)
            entries = data.get("data", {}).get("entries", [])

            for entry in entries:
                domain = entry.get("domain")
                title = entry.get("title", "N/A")
                state = entry.get("state", "unknown")

                if domain not in integrations:
                    integrations[domain] = []

                integrations[domain].append(
                    {
                        "title": title,
                        "state": state,
                        "entry_id": entry.get("entry_id"),
                    }
                )

        return integrations

    def get_devices_summary(self) -> dict:
        """Get device summary from device registry."""
        device_registry_file = self.storage_dir / "core.device_registry"
        summary = {
            "total": 0,
            "by_integration": {},
            "by_manufacturer": {},
        }

        if not device_registry_file.exists():
            return summary

        with open(device_registry_file) as f:
            data = json.load(f)
            devices = data.get("data", {}).get("devices", [])
            summary["total"] = len(devices)

            for device in devices:
                # Count by integration
                for entry_id in device.get("config_entries", []):
                    if entry_id:
                        key = str(entry_id)
                        summary["by_integration"][key] = (
                            summary["by_integration"].get(key, 0) + 1
                        )

                # Count by manufacturer
                # An explicit null in the registry means "no manufacturer",
                # which a .get() default would not catch.
                mfg = device.get("manufacturer") or "Unknown"
                summary["by_manufacturer"][mfg] = (
                    summary["by_manufacturer"].get(mfg, 0) + 1
                )

        return summary

    def get_entities_summary(self) -> dict:
        """Get entity summary from entity registry."""
        entity_registry_file = self.storage_dir / "core.entity_registry"
        summary = {
            "total": 0,
            "by_domain": {},
            "by_area": {},
        }

        if not entity_registry_file.exists():
            return summary

        with open(entity_registry_file) as f:
            data = json.load(f)
            entities = data.get("data", {}).get("entities", [])
            summary["total"] = len(entities)

            for entity in entities:
                # Count by domain
                entity_id = entity.get("entity_id", "unknown")
                domain = entity_id.split(".")[0] if "." in entity_id else "unknown"
                summary["by_domain"][domain] = summary["by_domain"].get(domain, 0) + 1

                # Count by area
                area = entity.get("area_id", "unassigned")
                summary["by_area"][area] = summary["by_area"].get(area, 0) + 1

        return summary

    def get_areas(self) -> list:
        """Get areas from area registry."""
        area_registry_file = self.storage_dir / "core.area_registry"
        areas = []

        if not area_registry_file.exists():
            return areas

        with open(area_registry_file) as f:
            data = json.load(f)
            areas = data.get("data", {}).get("areas", [])

        return [{"id": a.get("id"), "name": a.get("name")} for a in areas]

    def get_recent_entities(self, limit: int = 15) -> list:
        """Get recently modified/created entities."""
        entity_registry_file = self.storage_dir / "core.entity_registry"
        recent = []

        if not entity_registry_file.exists():
            return recent

        with open(entity_registry_file) as f:
            data = json.load(f)
            entities = data.get("data", {}).get("entities", [])

            for entity in entities:
                timestamp = entity.get("modified_at") or entity.get("created_at")
                if timestamp:
                    recent.append(
                        {
                            "entity_id": entity.get("entity_id"),
                            "timestamp": timestamp,
                            "area_id": entity.get("area_id"),
                        }
                    )

        recent.sort(key=lambda x: x["timestamp"], reverse=True)
        return recent[:limit]

    def get_all_data(self) -> dict:
        """Collect all environment data."""
        return {
            "timestamp": datetime.now().isoformat(),
            "ha_version": self.get_ha_version(),
            "integrations": self.get_integrations(),
            "devices": self.get_devices_summary(),
            "entities": self.get_entities_summary(),
            "areas": self.get_areas(),
            "recent_entities": self.get_recent_entities(),
        }

    def compare_snapshots(self, current: dict, previous: dict) -> dict:
        """Compare two snapshots and report changes."""
        changes = {
            "version_change": None,
            "new_integrations": [],
            "removed_integrations": [],
            "integration_changes": {},
            "device_count_change": 0,
            "entity_count_change": 0,
            "new_devices": [],
            "new_areas": [],
        }

        # Version changes
        curr_version = current.get("ha_version", "unknown")
        prev_version = previous.get("ha_version", "unknown")
        if curr_version != prev_version:
            changes["version_change"] = {"from": prev_version, "to": curr_version}

        # Integration changes
        curr_integ = set(current.get("integrations", {}).keys())
        prev_integ = set(previous.get("integrations", {}).keys())
        changes["new_integrations"] = list(curr_integ - prev_integ)
        changes["removed_integrations"] = list(prev_integ - curr_integ)

        # Device changes
        curr_device_count = current.get("devices", {}).get("total", 0)
        prev_device_count = previous.get("devices", {}).get("total", 0)
        changes["device_count_change"] = curr_device_count - prev_device_count

        # Entity changes
        curr_entity_count = current.get("entities", {}).get("total", 0)
        prev_entity_count = previous.get("entities", {}).get("total", 0)
        changes["entity_count_change"] = curr_entity_count - prev_entity_count

        # Area changes
        curr_areas = {a["id"]: a["name"] for a in current.get("areas", [])}
        prev_areas = {a["id"]: a["name"] for a in previous.get("areas", [])}
        changes["new_areas"] = [
            name for aid, name in curr_areas.items() if aid not in prev_areas
        ]

        return changes

    def run(self, verbose: bool = True) -> dict:
        """Run the tracker and return results."""
        if not self.storage_dir.exists():
            print(f"❌ Storage directory not found: {self.storage_dir}")
            return {}

        # Get current data
        self.current_data = self.get_all_data()

        # Load previous data
        self.previous_data = self.load_previous_snapshot()

        # Compare
        if self.previous_data:
            self.changes = self.compare_snapshots(self.current_data, self.previous_data)

        # Save snapshot
        self.save_snapshot(self.current_data)

        # Print report
        if verbose:
            self.print_report()

        return self.current_data

    def print_report(self):
        """Print a detailed report of current state and changes."""
        print("\n" + "=" * 80)
        print("HOME ASSISTANT ENVIRONMENT REPORT")
        print("=" * 80)

        # Version
        print("\n🏠 HOME ASSISTANT VERSION:")
        print(f"   {self.current_data.get('ha_version', 'unknown')}")

        if self.changes.get("version_change"):
            change = self.changes["version_change"]
            print(f"   ⬆️  Updated from {change['from']} → {change['to']}")

        # Integrations
        integrations = self.current_data.get("integrations", {})
        print(f"\n🔌 INTEGRATIONS: {len(integrations)}")

        if self.changes.get("new_integrations"):
            new_integ = ", ".join(self.changes["new_integrations"])
            print(f"   ✨ New: {new_integ}")
        if self.changes.get("removed_integrations"):
            removed = ", ".join(self.changes["removed_integrations"])
            print(f"   ❌ Removed: {removed}")

        for domain in sorted(integrations.keys())[:15]:
            entries = integrations[domain]
            print(f"   - {domain:20} | {len(entries)} instance(s)")

        if len(integrations) > 15:
            print(f"   ... and {len(integrations) - 15} more")

        # Devices
        devices = self.current_data.get("devices", {})
        print(f"\n📱 DEVICES: {devices.get('total', 0)}")
        device_change = self.changes.get("device_count_change", 0)
        if device_change != 0:
            symbol = "+" if device_change > 0 else ""
            print(f"   {symbol}{device_change} devices")

        by_mfg = devices.get("by_manufacturer", {})
        for mfg in sorted(by_mfg.keys(), key=lambda x: by_mfg[x], reverse=True)[:10]:
            print(f"   - {mfg:30} | {by_mfg[mfg]:3} devices")

        # Entities
        entities = self.current_data.get("entities", {})
        print(f"\n🏠 ENTITIES: {entities.get('total', 0)}")
        entity_change = self.changes.get("entity_count_change", 0)
        if entity_change != 0:
            symbol = "+" if entity_change > 0 else ""
            print(f"   {symbol}{entity_change} entities")

        by_domain = entities.get("by_domain", {})
        for domain in sorted(
            by_domain.keys(), key=lambda x: by_domain[x], reverse=True
        )[:10]:
            print(f"   - {domain:20} | {by_domain[domain]:3} entities")

        # Areas
        areas = self.current_data.get("areas", [])
        print(f"\n🗺️  AREAS: {len(areas)}")
        for area in areas:
            print(f"   - {area['name']}")

        # Recent additions
        recent = self.current_data.get("recent_entities", [])
        if recent:
            print("\n🆕 RECENTLY MODIFIED ENTITIES:")
            for ent in recent[:5]:
                entity_id = ent.get("entity_id", "unknown")
                ts = ent.get("timestamp", "unknown")[:10]
                print(f"   - {entity_id:45} | {ts}")

        print("\n" + "=" * 80 + "\n")


def main():
    """Run the tracker."""
    tracker = HAEnvironmentTracker()
    tracker.run(verbose=True)


if __name__ == "__main__":
    main()
