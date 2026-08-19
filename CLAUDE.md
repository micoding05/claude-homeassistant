# Home Assistant Configuration Management

This repository manages Home Assistant configuration files with automated validation, testing, and deployment.

## Before Making Changes

**Always consult the latest Home Assistant documentation** at https://www.home-assistant.io/docs/ before suggesting configurations, automations, or integrations. HA updates frequently and syntax/features change between versions.

## Code and Data Are Separate Repositories

This repository holds **only the tooling**. It is a public GitHub fork, so no
Home Assistant data may ever be committed here.

The HA data lives in a **separate private repository**, located via `HA_DATA_DIR`
(default: `../claude-homeassistant-data`):

| Location | Contents | Repository |
|----------|----------|------------|
| `$HA_DATA_DIR/ha/` | HA configuration and `.storage/`, mirror of the server's `/config/` | private data repo |
| `$HA_DATA_DIR/tracking/` | Environment snapshots written by the tracking tools | private data repo |

`tools/paths.py` is the single source of truth for these locations. Every tool
takes its default from there - never hardcode a data path, and never reintroduce
a `config/` directory in this repository. `.gitignore` blocks `config/`, `ha/`,
`data/`, `.storage/`, `.ha_tracking/` and `secrets.yaml` as a backstop.

### This Repository (code and tooling config)

- `tools/` - Validation, discovery and safety scripts
- `etc/` - Tooling configuration (rsync excludes, yamllint rules)
- `templates/` - Example files (`secrets.yaml.example`)
- `tests/` - Test suite
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
| `etc/rsync-excludes-pull` | `make pull` | Less restrictive |
| `etc/rsync-excludes-push` | `make push` | More restrictive |

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

### Entity/Device Changes (Manual Only)

Do not change entities or devices programmatically from this repo. If changes are
needed, make them manually in the Home Assistant UI:
- Settings → Devices & Services → Entities → Edit

### Reloading After YAML Changes
- Automations: `POST /api/services/automation/reload`
- Scenes: `POST /api/services/scene/reload`
- Scripts: `POST /api/services/script/reload`

## Workflow Rules

### Before Making Changes
1. Run `make pull` to ensure local files are current
2. Identify if the change affects YAML files or `.storage/` files
3. YAML files → edit in the data repo, then `make push`
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

### Safety Agents

Two agents guard the two riskiest moments in this workflow. Both are backed by
deterministic tools, so they can also be run by hand.

**`ha-sync-check` - run at the start of a session, before editing any YAML.**
Compares the HA server with this checkout: SSH reachability, HA core version vs.
the local `homeassistant` package, config drift (rsync dry run), pending updates
and git state. A `SessionStart` hook runs the underlying tool automatically and
only speaks up when something is off.

- `make sync-check` - run the comparison manually
- `python tools/ha_sync_check.py --json` - machine-readable output

The version check matters most: when the server's HA core is newer than the venv
package, the official validator fails with *"Storage file ... has version N which
is newer than the max supported version"*. That is a local tooling problem, not a
broken config. Fix it with `make pin-ha`, which reads the server's version and
installs exactly that. `make setup` runs it automatically, so a freshly created
venv always matches the server.

**`privacy-guard` - run before every push or pull request.**
This repo is a **public** GitHub fork; anything pushed is world-readable and
stays in history forever. This is a purely private setup, so the agent checks
for household data: coordinates, names, hardware inventory and presence
patterns.

- `make privacy-scan` - scan staged changes, or unpushed commits if nothing is staged
- `python tools/privacy_scan.py --range origin/main..HEAD` - scan a PR range

A `PreToolUse` hook runs the scanner before `git push`, `gh pr create` and
`glab mr create`, and **denies the command** when it finds blocking issues. The
scanner reads the real private values from `.env` and from the data repository
(`ha/secrets.yaml`, `ha/.storage/`) and matches those literals against the diff,
so hits are
rarely false positives. It never echoes a secret's value.

If a secret was ever committed, removing it in a later commit is not enough - it
remains in history and must be rotated.

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

- **Post-Edit Hook**: Runs validation after editing any HA YAML files
- **Pre-Push Hook**: Validates configuration before pushing to Home Assistant
- **Blocks invalid pushes**: Prevents uploading broken configurations

## Home Assistant Instance Details

- **Host**: Configure in Makefile `HA_HOST` variable
- **User**: Configure SSH access as needed
- **SSH Key**: Configure SSH key authentication
- **Config Path**: /config/ (standard HA path)
- **Version**: Compatible with Home Assistant Core 2024.x+

## Entity Registry

The system tracks entities across these domains:
- alarm_control_panel, binary_sensor, button, camera, climate
- device_tracker, event, image, light, lock, media_player
- number, person, scene, select, sensor, siren, switch
- time, tts, update, vacuum, water_heater, weather, zone

## Development Workflow

1. **Pull Latest**: `make pull` to sync from HA
2. **Edit Locally**: Modify files in `$HA_DATA_DIR/ha/` (the data repository)
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
binary_sensor.home_basement_motion_battery
binary_sensor.home_basement_motion_tamper
media_player.home_kitchen_sonos
media_player.office_main_bedroom_sonos
climate.home_living_room_heatpump
climate.office_living_room_thermostat
lock.home_front_door_august
sensor.office_driveway_camera_battery
vacuum.home_roborock
vacuum.office_roborock
```

### **Benefits:**
- **Clear location identification** - no ambiguity between properties
- **Consistent structure** - easy to predict entity names
- **Automation-friendly** - simple to target location-specific devices
- **Scalable** - supports additional locations or rooms

### **Implementation:**
- All location-based entities follow this convention
- Legacy entities have been systematically renamed
- New entities should follow this pattern
- Vendor prefixes (aquanta_, august_, etc.) are replaced with descriptive device names

### **Claude Code Integration:**
- When creating automations, always ask the user for input if there are multiple choices for sensors or devices
- Use the entity explorer tools to discover available entities before writing automations
- Follow the naming convention when suggesting entity names in automations
