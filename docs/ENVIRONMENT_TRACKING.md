# Home Assistant Environment Tracking

This system automatically monitors and tracks changes to your Home Assistant environment over time, including version upgrades, integration changes, device additions, and entity modifications.

## Quick Start

Track your current environment:
```bash
make track
```

View changelog:
```bash
python3 tools/ha_changelog.py changelog    # Show all changes
python3 tools/ha_changelog.py latest       # Show latest changes
python3 tools/ha_changelog.py current      # Show current state
```

## How It Works

### Automatic Snapshots

Every time you run `make track`, the system:

1. **Collects current state** from Home Assistant `.storage/` files:
   - Home Assistant version
   - Installed integrations (MQTT, Zigbee, FRITZ!Box, etc.)
   - Connected devices (by manufacturer, integration)
   - Available entities (by domain, area)
   - Configured areas

2. **Compares with previous snapshot** and detects:
   - Version upgrades
   - New integrations added
   - Integrations removed
   - Device count changes
   - Entity count changes
   - New areas created

3. **Archives snapshots** in `config/.ha_tracking/snapshots/`
   - Timestamped files for historical comparison
   - JSON format for easy parsing

4. **Maintains current snapshot** at `config/.ha_tracking/current_snapshot.json`
   - Latest state for quick reference

## What Gets Tracked

### 📍 Core Information
- **HA Version** - Home Assistant Core version
- **Integrations** - All configured integrations with counts
- **Devices** - Total count by manufacturer/integration
- **Entities** - Total count by domain/area
- **Areas** - Configured room/zone areas

### 🔄 Changes Detected
- Version changes (updates)
- New integrations
- Removed integrations
- Device additions/removals
- Entity additions/removals
- Area changes

### 📊 Examples of Tracked Events

```
📅 2026-06-23 15:35
🏠 HA Version: 2026.6.3 → 2026.6.4
✨ Integrations added: mqtt
📱 Devices: +3
🏠 Entities: +15
✨ Areas added: Guest Bedroom
```

## Directory Structure

```
config/
└── .ha_tracking/
    ├── current_snapshot.json          # Latest environment state
    └── snapshots/
        ├── snapshot_20260620_210101.json
        ├── snapshot_20260621_092535.json
        └── snapshot_20260623_163450.json
```

## Usage Patterns

### Daily Tracking

Add to your routine:
```bash
# Check what changed since last tracking
make track
python3 tools/ha_changelog.py latest
```

### After Updates

Track before and after Home Assistant updates:
```bash
# Before update
make track

# Do the update...

# After update - check what changed
python3 tools/ha_changelog.py latest
```

### After Major Changes

Track when adding new integrations, devices, or automations:
```bash
# Before: baseline
make track

# Make changes in HA UI...

# After: see what was added
python3 tools/ha_changelog.py latest
```

### Historical Analysis

Review all changes over time:
```bash
python3 tools/ha_changelog.py changelog
```

## Integration with Workflows

### In CI/CD or Automation Scripts

```bash
#!/bin/bash
# Track HA changes daily
cd ~/claude-homeassistant
make track

# Optional: Store in log for review
python3 tools/ha_changelog.py latest >> ha_tracking.log
```

### Scheduled Tracking

You could add to crontab:
```bash
# Track every day at 09:00
0 9 * * * cd ~/claude-homeassistant && make track
```

## Files

- `tools/ha_environment_tracker.py` - Main tracking script
  - Collects data from `.storage/` files
  - Detects changes
  - Manages snapshots
  
- `tools/ha_changelog.py` - Changelog viewer
  - Displays environment changes
  - Compares snapshots
  - Shows current state

- `Makefile` - Integration
  - `make track` - Run tracking

- `.gitignore` - Configuration
  - `config/.ha_tracking/` excluded (not in git)

## Notes

- ⚠️ `.ha_tracking/` is **not** committed to git (local snapshots only)
- ✅ Snapshots are quick to generate (< 1 second)
- 📂 Storage size: ~5-50KB per snapshot (compressed JSON)
- 🔄 No performance impact on Home Assistant
- 🔐 No sensitive data - only metadata from registries

## Troubleshooting

### Version showing "unknown"

This is normal if `hassio.json` or manifest files aren't available in the pulled config.
The system will try multiple sources.

### No snapshots found

Run `make track` first to create the initial snapshot.

### Changelog shows no changes

Changes are only detected if something actually changed between snapshots.
Run `make track` multiple times with actual changes to Home Assistant to see differences.

## Future Enhancements

Potential additions:
- Email notifications on major changes
- Integration with Home Assistant backup system
- Comparison with upstream HA releases
- Entity state tracking (enabled/disabled)
- Automation/scene versioning
- Device registry snapshots
