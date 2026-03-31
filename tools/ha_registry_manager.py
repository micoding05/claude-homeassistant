#!/usr/bin/env python3
"""
Home Assistant Registry Manager.

Applies entity renames, area assignments, and entity disabling via
the Home Assistant WebSocket API. Reads current registry state from local
.storage files and generates a plan that can be reviewed before execution.

Usage:
    python tools/ha_registry_manager.py --config config --plan        # Show plan only
    python tools/ha_registry_manager.py --config config --apply       # Apply changes
    python tools/ha_registry_manager.py --config config --apply --skip-confirm  # No prompts
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Registry loaders
# ---------------------------------------------------------------------------

def load_json(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def load_registries(config_path: Path) -> Tuple[Dict, Dict, Dict]:
    """Return (entities_by_id, devices_by_id, areas_by_id)."""
    entity_data = load_json(config_path / ".storage" / "core.entity_registry")
    device_data = load_json(config_path / ".storage" / "core.device_registry")
    area_data = load_json(config_path / ".storage" / "core.area_registry")

    entities = {}
    if entity_data:
        for e in entity_data.get("data", {}).get("entities", []):
            if not e.get("disabled_by") and not e.get("hidden_by"):
                entities[e["entity_id"]] = e

    devices = {}
    if device_data:
        for d in device_data.get("data", {}).get("devices", []):
            devices[d["id"]] = d

    areas = {}
    if area_data:
        for a in area_data.get("data", {}).get("areas", []):
            areas[a["id"]] = a["name"]

    return entities, devices, areas


def device_area(entity: Dict, devices: Dict) -> Optional[str]:
    """Resolve effective area_id for an entity (entity-level or device-level)."""
    area = entity.get("area_id")
    if area:
        return area
    dev_id = entity.get("device_id")
    if dev_id and dev_id in devices:
        return devices[dev_id].get("area_id")
    return None


# ---------------------------------------------------------------------------
# Naming convention: location_room_device_sensor
# ---------------------------------------------------------------------------


def _normalize(name: str) -> str:
    """Lowercase, strip non-alnum, collapse underscores."""
    import re
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


# ---------------------------------------------------------------------------
# Rules file loader
# ---------------------------------------------------------------------------

def load_rules(rules_path: Path) -> Dict:
    """Load cleanup rules from a JSON file.

    Expected structure:
    {
      "renames": [
        {"old_id": "light.old_name", "new_id": "light.home_room_device", "reason": "naming convention"}
      ],
      "disable": {
        "device_ids": [
          {"device_id": "abc123", "reason": "duplicate integration"}
        ],
        "by_model": [
          {"model": "Tracked device", "manufacturer": "Router Vendor",
           "keep_entities": ["device_tracker.my_phone"],
           "reason": "low value / noise"}
        ],
        "entity_ids": [
          {"entity_id": "sensor.redundant_sensor", "reason": "duplicate"}
        ]
      },
      "areas": {
        "create": [
          {"area_id": "network", "name": "Network", "reason": "infrastructure"}
        ],
        "device_moves": [
          {"device_id": "def456", "device_name": "Router", "new_area": "network",
           "reason": "infrastructure device"}
        ]
      }
    }
    """
    if not rules_path.exists():
        print(f"Error: Rules file not found: {rules_path}")
        print("Create a rules file or use --rules to specify the path.")
        print("See cleanup_rules.example.json for the expected format.")
        return {}
    with open(rules_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Plan generators (data-driven from rules file)
# ---------------------------------------------------------------------------

def plan_entity_renames(
    entities: Dict, devices: Dict, areas: Dict, rules: Dict
) -> List[Dict[str, str]]:
    """Generate entity rename proposals from rules file."""
    renames: List[Dict[str, str]] = []
    for rule in rules.get("renames", []):
        old_id = rule["old_id"]
        if old_id in entities:
            renames.append({
                "old_id": old_id,
                "new_id": rule["new_id"],
                "reason": rule.get("reason", "naming convention"),
            })
    return renames


def plan_entities_to_disable(
    entities: Dict, devices: Dict, areas: Dict, rules: Dict
) -> List[Dict[str, str]]:
    """Identify entities that should be disabled based on rules."""
    disable_list: List[Dict[str, str]] = []
    disable_rules = rules.get("disable", {})

    # Disable all entities belonging to specific devices
    for dev_rule in disable_rules.get("device_ids", []):
        target_device = dev_rule["device_id"]
        reason = dev_rule.get("reason", "disabled by rule")
        for eid, e in entities.items():
            if e.get("device_id") == target_device:
                disable_list.append({"entity_id": eid, "reason": reason})

    # Disable entities by device model/manufacturer pattern
    for model_rule in disable_rules.get("by_model", []):
        target_model = model_rule["model"]
        target_mfr = model_rule.get("manufacturer")
        keep_entities = set(model_rule.get("keep_entities", []))
        reason = model_rule.get("reason", "disabled by model rule")

        matched_devices = set()
        for did, d in devices.items():
            if d.get("model") != target_model:
                continue
            if target_mfr and d.get("manufacturer") != target_mfr:
                continue
            matched_devices.add(did)

        for eid, e in entities.items():
            if e.get("device_id") in matched_devices and eid not in keep_entities:
                disable_list.append({"entity_id": eid, "reason": reason})

    # Disable specific entities by entity_id
    for ent_rule in disable_rules.get("entity_ids", []):
        eid = ent_rule["entity_id"]
        if eid in entities:
            disable_list.append({
                "entity_id": eid,
                "reason": ent_rule.get("reason", "disabled by rule"),
            })

    return disable_list


def plan_area_changes(
    entities: Dict, devices: Dict, areas: Dict, rules: Dict
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Plan new areas and device area reassignments from rules."""
    new_areas: List[Dict[str, str]] = []
    device_moves: List[Dict[str, str]] = []
    area_rules = rules.get("areas", {})

    for area_rule in area_rules.get("create", []):
        area_id = area_rule["area_id"]
        if area_id not in areas:
            new_areas.append({
                "area_id": area_id,
                "name": area_rule["name"],
                "reason": area_rule.get("reason", "new area"),
            })

    for move_rule in area_rules.get("device_moves", []):
        dev_id = move_rule["device_id"]
        new_area = move_rule["new_area"]
        if dev_id in devices and devices[dev_id].get("area_id") != new_area:
            device_moves.append({
                "device_id": dev_id,
                "device_name": move_rule.get("device_name", devices[dev_id].get("name", dev_id)),
                "current_area": devices[dev_id].get("area_id", "none"),
                "new_area": new_area,
                "reason": move_rule.get("reason", "area reassignment"),
            })

    return new_areas, device_moves


# ---------------------------------------------------------------------------
# HA WebSocket API helpers
# ---------------------------------------------------------------------------

def load_env():
    """Load .env file into os.environ."""
    for env_path in [Path(".env"), Path(__file__).parent.parent / ".env"]:
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        os.environ.setdefault(
                            key.strip(), value.strip().strip('"').strip("'")
                        )
            break


class HAWebSocket:
    """Manages an authenticated WebSocket connection to Home Assistant."""

    def __init__(self):
        self._ws = None
        self._msg_id = 0

    async def connect(self) -> bool:
        """Connect and authenticate."""
        if websockets is None:
            print("Error: 'websockets' package not installed")
            return False

        ha_url = os.environ.get("HA_URL", "http://homeassistant.local:8123")
        token = os.environ.get("HA_TOKEN", "")
        if not token:
            print("Error: HA_TOKEN not set")
            return False

        ws_url = ha_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url += "/api/websocket"

        try:
            self._ws = await websockets.connect(ws_url)
            # Wait for auth_required
            auth_req = json.loads(await self._ws.recv())
            if auth_req.get("type") != "auth_required":
                print(f"Unexpected message: {auth_req}")
                return False
            # Send auth
            await self._ws.send(json.dumps({
                "type": "auth",
                "access_token": token,
            }))
            auth_resp = json.loads(await self._ws.recv())
            if auth_resp.get("type") != "auth_ok":
                print(f"Authentication failed: {auth_resp}")
                return False
            return True
        except Exception as exc:
            print(f"Connection error: {exc}")
            return False

    async def send_command(self, msg_type: str, **kwargs) -> Dict[str, Any]:
        """Send a WebSocket command and wait for the response."""
        self._msg_id += 1
        payload = {"id": self._msg_id, "type": msg_type, **kwargs}
        await self._ws.send(json.dumps(payload))
        resp = json.loads(await self._ws.recv())
        return resp

    async def close(self):
        if self._ws:
            await self._ws.close()

    async def create_area(self, name: str) -> Optional[str]:
        """Create a new area. Returns area_id or None."""
        resp = await self.send_command("config/area_registry/create", name=name)
        if resp.get("success"):
            return resp["result"].get("area_id")
        print(f"    Error: {resp.get('error', {}).get('message', resp)}")
        return None

    async def update_device(self, device_id: str, area_id: str) -> bool:
        """Assign a device to an area."""
        resp = await self.send_command(
            "config/device_registry/update",
            device_id=device_id,
            area_id=area_id,
        )
        if resp.get("success"):
            return True
        print(f"    Error: {resp.get('error', {}).get('message', resp)}")
        return False

    async def rename_entity(self, entity_id: str, new_entity_id: str) -> bool:
        """Rename an entity."""
        resp = await self.send_command(
            "config/entity_registry/update",
            entity_id=entity_id,
            new_entity_id=new_entity_id,
        )
        if resp.get("success"):
            return True
        print(f"    Error: {resp.get('error', {}).get('message', resp)}")
        return False

    async def disable_entity(self, entity_id: str) -> bool:
        """Disable an entity."""
        resp = await self.send_command(
            "config/entity_registry/update",
            entity_id=entity_id,
            disabled_by="user",
        )
        if resp.get("success"):
            return True
        print(f"    Error: {resp.get('error', {}).get('message', resp)}")
        return False


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def print_plan(
    renames: List[Dict],
    disables: List[Dict],
    new_areas: List[Dict],
    device_moves: List[Dict],
):
    """Pretty-print the full cleanup plan."""
    print("=" * 80)
    print("HOME ASSISTANT CLEANUP PLAN")
    print("=" * 80)

    # --- Renames ---
    print(f"\n📝 ENTITY RENAMES ({len(renames)} changes):\n")
    for r in sorted(renames, key=lambda x: x["old_id"]):
        print(f"  {r['old_id']}")
        print(f"    → {r['new_id']}")

    # --- Disables ---
    print(f"\n🚫 ENTITIES TO DISABLE ({len(disables)} entities):\n")
    by_reason = {}
    for d in disables:
        by_reason.setdefault(d["reason"], []).append(d["entity_id"])
    for reason, eids in sorted(by_reason.items()):
        print(f"  {reason} ({len(eids)}):")
        for eid in sorted(eids):
            print(f"    • {eid}")
        print()

    # --- New Areas ---
    print(f"\n🏠 NEW AREAS ({len(new_areas)}):\n")
    for a in new_areas:
        print(f"  + {a['name']} ({a['reason']})")

    # --- Device Moves ---
    print(f"\n🔄 DEVICE AREA REASSIGNMENTS ({len(device_moves)}):\n")
    for m in device_moves:
        print(f"  {m['device_name']}")
        print(f"    {m['current_area']} → {m['new_area']} ({m['reason']})")

    total = len(renames) + len(disables) + len(new_areas) + len(device_moves)
    print(f"\n{'=' * 80}")
    print(f"TOTAL: {total} changes")
    print(f"  • {len(renames)} entity renames")
    print(f"  • {len(disables)} entities to disable")
    print(f"  • {len(new_areas)} new areas")
    print(f"  • {len(device_moves)} device area moves")
    print(f"{'=' * 80}")


def apply_plan(
    renames: List[Dict],
    disables: List[Dict],
    new_areas: List[Dict],
    device_moves: List[Dict],
    skip_confirm: bool = False,
):
    """Apply the cleanup plan via the HA WebSocket API."""
    load_env()

    if not skip_confirm:
        total = len(renames) + len(disables) + len(new_areas) + len(device_moves)
        answer = input(f"Apply {total} changes? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return False

    return asyncio.run(_apply_plan_async(renames, disables, new_areas, device_moves))


async def _apply_plan_async(
    renames: List[Dict],
    disables: List[Dict],
    new_areas: List[Dict],
    device_moves: List[Dict],
) -> bool:
    """Async implementation of plan application."""
    ws = HAWebSocket()
    if not await ws.connect():
        print("\n❌ Cannot connect to Home Assistant WebSocket API.")
        print("   Check HA_URL and HA_TOKEN in your .env file.")
        return False

    print("\n✅ Connected to Home Assistant WebSocket API\n")

    success = 0
    errors = 0

    try:
        # 1. Create new areas first
        area_id_map = {}
        for a in new_areas:
            print(f"  Creating area: {a['name']}...", end=" ")
            aid = await ws.create_area(a["name"])
            if aid:
                area_id_map[a["area_id"]] = aid
                print(f"✅ (id: {aid})")
                success += 1
            else:
                print("❌")
                errors += 1

        # 2. Device area reassignments
        for m in device_moves:
            new_area = area_id_map.get(m["new_area"], m["new_area"])
            print(f"  Moving {m['device_name']} → {m['new_area']}...", end=" ")
            if await ws.update_device(m["device_id"], new_area):
                print("✅")
                success += 1
            else:
                print("❌")
                errors += 1

        # 3. Entity renames
        for r in renames:
            print(f"  Renaming {r['old_id']} → {r['new_id']}...", end=" ")
            if await ws.rename_entity(r["old_id"], r["new_id"]):
                print("✅")
                success += 1
            else:
                print("❌")
                errors += 1

        # 4. Entity disables
        for d in disables:
            print(f"  Disabling {d['entity_id']}...", end=" ")
            if await ws.disable_entity(d["entity_id"]):
                print("✅")
                success += 1
            else:
                print("❌")
                errors += 1
    finally:
        await ws.close()

    print(f"\nDone: {success} succeeded, {errors} failed.")
    return errors == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Home Assistant Setup Cleanup Tool"
    )
    parser.add_argument(
        "--config", "-c", default="config",
        help="Path to HA config directory with .storage/",
    )
    parser.add_argument(
        "--rules", "-r", default="cleanup_rules.json",
        help="Path to JSON rules file (default: cleanup_rules.json)",
    )
    parser.add_argument(
        "--plan", action="store_true",
        help="Show cleanup plan without applying",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply cleanup plan via HA WebSocket API",
    )
    parser.add_argument(
        "--skip-confirm", action="store_true",
        help="Skip confirmation prompt when applying",
    )
    parser.add_argument(
        "--renames-only", action="store_true",
        help="Only show/apply entity renames",
    )
    parser.add_argument(
        "--disable-only", action="store_true",
        help="Only show/apply entity disabling",
    )
    parser.add_argument(
        "--areas-only", action="store_true",
        help="Only show/apply area changes",
    )

    args = parser.parse_args()
    config_path = Path(args.config)
    rules_path = Path(args.rules)

    if not (config_path / ".storage").exists():
        print(f"Error: .storage directory not found in {config_path}")
        print("Run 'make pull' first to sync registry files.")
        return 1

    # Load rules
    rules = load_rules(rules_path)
    if not rules:
        return 1

    # Load registries
    entities, devices, areas = load_registries(config_path)
    if not entities:
        print("Error: No entities found in registry")
        return 1

    # Generate plans
    renames = plan_entity_renames(entities, devices, areas, rules)
    disables = plan_entities_to_disable(entities, devices, areas, rules)
    new_areas, device_moves = plan_area_changes(entities, devices, areas, rules)

    # Filter by flags
    if args.renames_only:
        disables, new_areas, device_moves = [], [], []
    elif args.disable_only:
        renames, new_areas, device_moves = [], [], []
    elif args.areas_only:
        renames, disables = [], []

    if not args.apply:
        # Default: show plan
        print_plan(renames, disables, new_areas, device_moves)
        if not args.plan:
            print("\nUse --apply to execute these changes via the HA API.")
        return 0
    else:
        print_plan(renames, disables, new_areas, device_moves)
        ok = apply_plan(
            renames, disables, new_areas, device_moves,
            skip_confirm=args.skip_confirm,
        )
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
