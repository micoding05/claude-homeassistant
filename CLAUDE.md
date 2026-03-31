# Home Assistant Configuration Management

This repository manages Home Assistant configuration files with automated validation, testing, and deployment.

## Before Making Changes

**Always consult the latest Home Assistant documentation** at https://www.home-assistant.io/docs/ before suggesting configurations, automations, or integrations. HA updates frequently and syntax/features change between versions.

## Project Structure

- `config/` - Contains all Home Assistant configuration files (synced from HA instance)
- `tools/` - Validation and testing scripts
- `venv/` - Python virtual environment with dependencies
- `temp/` - Temporary directory for Claude to write and test code before moving to final locations
- `Makefile` - Commands for pulling/pushing configuration
- `.claude-code/` - Project-specific Claude Code settings and hooks
  - `hooks/` - Validation hooks that run automatically
  - `settings.json` - Project configuration

## Rsync Architecture

This project uses **two separate exclude files** for different sync operations:

| File | Used By | Purpose |
|------|---------|---------|
| `.rsync-excludes-pull` | `make pull` | Less restrictive |
| `.rsync-excludes-push` | `make push` | More restrictive |

**Why separate files?**
- `make pull` downloads most files including `.storage/` (excluding sensitive auth files) for local reference
- `make push` writes never overwrites HA's runtime state (`.storage/`)

## What This Repo Can and Cannot Manage

### SAFE TO MANAGE (YAML files)
- `automations.yaml` - Automation definitions
- `scenes.yaml` - Scene definitions
- `scripts.yaml` - Script definitions
- `configuration.yaml` - Main configuration
- `secrets.yaml` - Secret values

### NEVER MODIFY LOCALLY (Runtime State)
These files in `.storage/` are managed by Home Assistant at runtime. Local modifications will be **overwritten** by HA on restart or ignored entirely.

### Entity/Device Changes (WebSocket API)

Entity renames, disabling, area creation, and device area assignments **require the WebSocket API**. The HA REST API does NOT expose registry mutation endpoints (they return 404).

Use `tools/ha_registry_manager.py` for batch operations, or connect directly:
```
ws://<HA_URL>/api/websocket
```

Key WebSocket commands:
| Command | Purpose |
|---------|--------|
| `config/entity_registry/list` | List all entities |
| `config/entity_registry/update` | Rename (`new_entity_id`) or disable (`disabled_by: "user"`) |
| `config/device_registry/list` | List all devices |
| `config/device_registry/update` | Move device to area (`area_id`) |
| `config/area_registry/list` | List all areas |
| `config/area_registry/create` | Create new area (`name`) |
| `config/area_registry/delete` | Delete area (`area_id`) |

Authentication: send `{"type": "auth", "access_token": "<token>"}` after connecting.

### Reloading After YAML Changes
- Automations: `POST /api/services/automation/reload`
- Scenes: `POST /api/services/scene/reload`
- Scripts: `POST /api/services/script/reload`

## Workflow Rules

### Before Making Changes
1. Run `make pull` to ensure local files are current
2. Identify if the change affects YAML files or `.storage/` files
3. YAML files → edit locally, then `make push`
4. `.storage/` files → use the HA UI only (manual changes)

### Before Running `make push`
1. Validation runs automatically - do not push if validation fails
2. Only YAML configuration files will be synced (`.storage/` is protected)

### After `make push`
1. Reload the relevant HA components (automations, scenes, scripts)
2. Verify changes took effect in HA

## Available Commands

### Configuration Management
- `make pull` - Pull latest config from Home Assistant instance
- `make push` - Push local config to Home Assistant (with validation)
- `make backup` - Create backup of current config
- `make validate` - Run all validation tests

### Validation Tools
- `python tools/run_tests.py` - Run complete validation suite
- `python tools/yaml_validator.py` - YAML syntax validation only
- `python tools/reference_validator.py` - Entity/device reference validation
- `python tools/ha_official_validator.py` - Official HA configuration validation

### Entity Discovery Tools
- `make entities` - Explore available Home Assistant entities
- `python tools/entity_explorer.py` - Entity registry parser and explorer
  - `--search TERM` - Search entities by name, ID, or device class
  - `--domain DOMAIN` - Show entities from specific domain (e.g., climate, sensor)
  - `--area AREA` - Show entities from specific area
  - `--full` - Show complete detailed output

### Registry Manager
- `python tools/ha_registry_manager.py --config config --rules cleanup_rules.json --plan` - Show cleanup plan
- `python tools/ha_registry_manager.py --config config --rules cleanup_rules.json --apply` - Apply via WebSocket API
  - `--skip-confirm` - Skip interactive confirmation
  - `--renames-only` / `--disable-only` / `--areas-only` - Filter operations
  - Requires `websockets` package and `HA_TOKEN`/`HA_URL` in `.env`
  - Rules are defined in a JSON file (see `cleanup_rules.example.json` for format)

## Validation System

This project includes comprehensive validation to prevent invalid configurations:

### Core Goal

- Verify all agent-produced configuration and automation changes before saving YAML files to Home Assistant
- Never generate, save, or push YAML changes that fail validation
- Use a layered validation suite that combines Home Assistant's own validation with repository-specific validators

1. **YAML Syntax Validation** - Ensures proper YAML syntax with HA-specific tags
2. **Entity Reference Validation** - Checks that all referenced entities/devices exist
3. **Official HA Validation** - Uses Home Assistant's own validation tools

### Automated Validation Hooks

- **Post-Edit Hook**: Runs validation after editing any YAML files in `config/`
- **Pre-Push Hook**: Validates configuration before pushing to Home Assistant
- **Blocks invalid pushes**: Prevents uploading broken configurations

## Home Assistant Instance Details

- **Host**: Configure in Makefile `HA_HOST` variable
- **User**: Configure SSH access as needed
- **SSH Key**: Configure SSH key authentication
- **Config Path**: /config/ (standard HA path)
- **Version**: Compatible with Home Assistant Core 2026.3.4+

## Entity Registry

The system tracks entities across these domains:
- alarm_control_panel, binary_sensor, button, camera, climate
- device_tracker, event, image, light, lock, media_player
- number, person, scene, select, sensor, siren, switch
- time, tts, update, vacuum, water_heater, weather, zone

### Area Resolution

**Important**: Most entities have `area_id: null` in `core.entity_registry`. Area assignments are stored at the **device level** in `core.device_registry`. To resolve an entity's area:
1. Look up the entity's `device_id` in the entity registry
2. Find that device in the device registry
3. Read the device's `area_id`

The `entity_explorer.py` tool handles this automatically.

### Areas

Areas are defined in `core.area_registry` inside `.storage/`. Use the entity explorer to list current areas:
```
python tools/entity_explorer.py --config config
```

## Development Workflow

1. **Pull Latest**: `make pull` to sync from HA
2. **Edit Locally**: Modify files in `config/` directory
3. **Auto-Validation**: Hooks automatically validate on edits
4. **Test Changes**: `make validate` for full test suite
5. **Deploy**: `make push` to upload (blocked if validation fails)

## Key Features

- ✅ **Safe Deployments**: Pre-push validation prevents broken configs
- ✅ **Entity Validation**: Ensures all references point to real entities
- ✅ **Entity Discovery**: Advanced tools to explore and search available entities
- ✅ **Official HA Tools**: Uses Home Assistant's own validation
- ✅ **YAML Support**: Handles HA-specific tags (!include, !secret, !input)
- ✅ **Comprehensive Testing**: Multiple validation layers
- ✅ **Automated Hooks**: Validation runs automatically on file changes

## Important Notes

- **Never push without validation**: The hooks prevent this, but be aware
- **Blueprint files** use `!input` tags which are normal and expected
- **Secrets are skipped** during validation for security
- **SSH access required** for pull/push operations
- **Python venv required** for validation tools
- All python tools need to be run with `source venv/bin/activate && python <tool_path>`
- **REST API vs WebSocket API**: The REST API (`/api/services/`, `/api/states/`) works for state queries and service calls. Registry mutations (entity rename, disable, area/device management) **only work via WebSocket**.

## Troubleshooting

### Validation Fails
1. Check YAML syntax errors first
2. Verify entity references exist in `.storage/` files
3. Run individual validators to isolate issues
4. Check HA logs if official validation fails

### SSH Issues
1. Verify SSH key permissions: `chmod 600 ~/.ssh/your_key`
2. Test connection: `ssh your_homeassistant_host`
3. Check SSH config in `~/.ssh/config`

### Missing Dependencies
1. Activate venv: `source venv/bin/activate`
2. Install requirements: `pip install homeassistant voluptuous pyyaml`

## Security

- **SSH keys** are used for secure access
- **Secrets.yaml** is excluded from validation (contains sensitive data)
- **No credentials** are stored in this repository
- **Access tokens** in config are for authorized integrations

This system ensures you can confidently manage Home Assistant configurations with Claude while maintaining safety and reliability.

## Entity Naming Convention

This Home Assistant setup uses a **standardized entity naming convention** for multi-location deployments:

### **Format: `location_room_device_sensor`**

**Structure:**
- **location**: `home`, `office`, `cabin`, etc.
- **room**: `basement`, `kitchen`, `living_room`, `main_bedroom`, `guest_bedroom`, `driveway`, etc.
- **device**: `motion`, `heatpump`, `sonos`, `lock`, `vacuum`, `water_heater`, `alarm`, etc.
- **sensor**: `battery`, `tamper`, `status`, `temperature`, `humidity`, `door`, `running`, etc.

### **Examples:**
```
domain.location_room_device
domain.location_room_device_sensor

binary_sensor.home_kitchen_motion_battery
media_player.home_living_room_sonos
climate.home_bedroom_thermostat
sensor.home_laundry_washer_status
light.home_kitchen_led
```

### **Benefits:**
- **Clear location identification** - no ambiguity between properties
- **Consistent structure** - easy to predict entity names
- **Automation-friendly** - simple to target location-specific devices
- **Scalable** - supports additional locations or rooms

### **Implementation:**
- All location-based entities should follow this convention
- Use `tools/ha_registry_manager.py` with a rules file to batch-rename entities to match the convention
- New entities should follow this pattern
- Vendor prefixes and hardware identifiers should be replaced with descriptive device names

### **Agent Integration:**
- When creating automations, always ask the user for input if there are multiple choices for sensors or devices
- Use the entity explorer tools to discover available entities before writing automations
- Follow the naming convention when suggesting entity names in automations
