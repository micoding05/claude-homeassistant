#!/usr/bin/env python3
"""HA Device Discovery Tool - Show devices not yet in tracking history."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def show_new_devices():
    """Show devices that exist but weren't tracked before."""
    storage_dir = Path("config/.storage")
    tracker_dir = Path("config/.ha_tracking")

    # Get first snapshot (baseline for comparison)
    snapshots_dir = tracker_dir / "snapshots"
    if not snapshots_dir.exists():
        print("❌ Keine Snapshots gefunden. Führe 'make track' aus.")
        return

    snapshots = sorted(snapshots_dir.glob("snapshot_*.json"))
    if not snapshots:
        print("❌ Keine Snapshots gefunden.")
        return

    first_snapshot = json.load(open(snapshots[0]))
    current_snapshot = json.load(open(snapshots[-1]))

    # Get current real entities
    with open(storage_dir / "core.entity_registry") as f:
        all_entities = json.load(f).get("data", {}).get("entities", [])

    with open(storage_dir / "core.device_registry") as f:
        all_devices = json.load(f).get("data", {}).get("devices", [])

    # Analyze what's new
    first_integrations = set(first_snapshot.get("integrations", {}).keys())
    curr_integrations = set(current_snapshot.get("integrations", {}).keys())

    first_devices = first_snapshot.get("devices", {}).get("total", 0)
    curr_devices = current_snapshot.get("devices", {}).get("total", 0)

    print("\n" + "=" * 80)
    print("NEW DEVICES DISCOVERY")
    print("=" * 80)

    # New integrations
    new_integrations = curr_integrations - first_integrations
    if new_integrations:
        print(f"\n✨ NEW INTEGRATIONS ({len(new_integrations)}):")
        for integ in sorted(new_integrations):
            print(f"  - {integ}")

    # Device count change
    if curr_devices > first_devices:
        print(f"\n📱 DEVICE COUNT CHANGE: {first_devices} → {curr_devices} (+{curr_devices - first_devices})")

        # Group new devices by manufacturer
        manufacturers = {}
        for device in all_devices:
            mfg = device.get("manufacturer", "Unknown")
            if mfg not in manufacturers:
                manufacturers[mfg] = []
            manufacturers[mfg].append(device)

        print("\n  By Manufacturer:")
        for mfg in sorted(manufacturers.keys()):
            devices = manufacturers[mfg]
            print(f"    - {mfg}: {len(devices)} devices")
            # Show devices added recently (last 14 days)
            recent = []
            for dev in devices:
                created = dev.get("created_at", "")
                if created:
                    try:
                        created_dt = datetime.fromisoformat(created)
                        if created_dt > datetime.now(timezone.utc) - timedelta(days=14):
                            recent.append((dev.get("name", "Unknown"), created[:10]))
                    except Exception:
                        pass

            if recent:
                for name, date in sorted(recent):
                    print(f"      🆕 {name} ({date})")

    # New entities by domain
    entities_by_domain = {}
    for entity in all_entities:
        entity_id = entity.get("entity_id", "")
        if "." in entity_id:
            domain = entity_id.split(".")[0]
            if domain not in entities_by_domain:
                entities_by_domain[domain] = []
            entities_by_domain[domain].append(entity)

    print(f"\n🏠 ENTITIES BY DOMAIN:")
    for domain in sorted(entities_by_domain.keys(), key=lambda x: len(entities_by_domain[x]), reverse=True)[:15]:
        count = len(entities_by_domain[domain])
        print(f"  {domain:20} | {count:3} entities")

    # Find all entities created in last 30 days
    all_new = []
    for entity in all_entities:
        created = entity.get("created_at", "")
        if created:
            try:
                # Parse ISO format datetime with timezone info
                created_dt = datetime.fromisoformat(created)
                if created_dt > datetime.now(timezone.utc) - timedelta(days=30):
                    all_new.append({
                        "entity_id": entity.get("entity_id", ""),
                        "platform": entity.get("platform", ""),
                        "date": created[:10],
                    })
            except Exception:
                pass

    if all_new:
        print(f"\n🆕 ENTITIES CREATED IN LAST 30 DAYS ({len(all_new)}):")
        by_date = {}
        for item in all_new:
            date = item["date"]
            if date not in by_date:
                by_date[date] = []
            by_date[date].append(item)

        for date in sorted(by_date.keys(), reverse=True):
            items = by_date[date]
            print(f"\n  {date}: {len(items)} entities")
            by_platform = {}
            for item in items:
                platform = item["platform"]
                if platform not in by_platform:
                    by_platform[platform] = []
                by_platform[platform].append(item)

            for platform in sorted(by_platform.keys()):
                entities = by_platform[platform]
                print(f"    - {platform}: {len(entities)} entities")
                for e in entities[:3]:
                    print(f"        • {e['entity_id']}")
                if len(entities) > 3:
                    print(f"        ... and {len(entities) - 3} more")

    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    show_new_devices()
