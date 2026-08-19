#!/usr/bin/env python3
"""Audit Home Assistant config entries for suspicious duplicates."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

try:  # running as a module (tests, imports)
    from .paths import ha_config_dir
except ImportError:  # running directly as a script
    from paths import ha_config_dir


def normalize_title(value: str) -> str:
    """Normalize a title so cosmetic differences still compare equal."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def load_config_entries(storage_dir: Path) -> list[dict[str, Any]]:
    """Load config entries from Home Assistant storage."""
    config_entries_file = storage_dir / "core.config_entries"
    with open(config_entries_file) as file_handle:
        data = json.load(file_handle)
    return data.get("data", {}).get("entries", [])


def summarize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Return only the fields useful for duplicate diagnosis."""
    entry_data = entry.get("data") or {}
    return {
        "entry_id": entry.get("entry_id"),
        "title": entry.get("title"),
        "source": entry.get("source"),
        "disabled_by": entry.get("disabled_by"),
        "created_at": entry.get("created_at"),
        "modified_at": entry.get("modified_at"),
        "url": entry_data.get("url"),
        "host": entry_data.get("host"),
        "port": entry_data.get("port"),
        "unique_id": entry.get("unique_id"),
    }


def find_duplicates(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find suspicious duplicate config entries.

    Suspicious means same domain and one of:
    - same normalized title
    - same URL
    - same unique_id
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for entry in entries:
        domain = entry.get("domain", "unknown")
        title = entry.get("title") or ""
        url = (entry.get("data") or {}).get("url") or ""
        unique_id = entry.get("unique_id") or ""

        grouped[(domain, f"title:{normalize_title(title)}")].append(entry)
        if url:
            grouped[(domain, f"url:{url}")].append(entry)
        if unique_id:
            grouped[(domain, f"unique_id:{unique_id}")].append(entry)

    seen_ids: set[tuple[str, ...]] = set()
    duplicates: list[dict[str, Any]] = []

    for (domain, match_key), match_entries in grouped.items():
        if len(match_entries) < 2:
            continue

        entry_ids = tuple(sorted(str(item.get("entry_id")) for item in match_entries))
        if entry_ids in seen_ids:
            continue
        seen_ids.add(entry_ids)

        duplicates.append(
            {
                "domain": domain,
                "match_key": match_key,
                "entries": [summarize_entry(item) for item in match_entries],
            }
        )

    duplicates.sort(key=lambda item: (item["domain"], item["match_key"]))
    return duplicates


def print_duplicates(
    duplicates: list[dict[str, Any]], domain_filter: str | None
) -> int:
    """Print duplicate report and return exit code."""
    print("\n" + "=" * 80)
    print("HOME ASSISTANT CONFIG ENTRY DUPLICATE AUDIT")
    print("=" * 80)

    filtered = duplicates
    if domain_filter:
        filtered = [item for item in duplicates if item["domain"] == domain_filter]

    if not filtered:
        if domain_filter:
            print(f"\n✅ No suspicious duplicates found for domain: {domain_filter}")
        else:
            print("\n✅ No suspicious duplicate config entries found.")
        print("\n" + "=" * 80 + "\n")
        return 0

    print(f"\n⚠️  Found {len(filtered)} suspicious duplicate group(s).")
    for item in filtered:
        print(f"\nDomain: {item['domain']}")
        print(f"Match:  {item['match_key']}")
        for entry in item["entries"]:
            print("  ---")
            print(f"  title:       {entry['title']}")
            print(f"  source:      {entry['source']}")
            print(f"  disabled_by: {entry['disabled_by']}")
            print(f"  url:         {entry['url']}")
            print(f"  entry_id:    {entry['entry_id']}")
            print(f"  created_at:  {entry['created_at']}")
            print(f"  unique_id:   {entry['unique_id']}")

    print("\n" + "=" * 80 + "\n")
    return 1


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Audit Home Assistant config entries for suspicious duplicates."
    )
    parser.add_argument(
        "--domain",
        help="Only show suspicious duplicates for a single domain, for example otbr.",
    )
    parser.add_argument(
        "--config-dir",
        default=None,
        help="Path to the Home Assistant config directory. "
        "Default: the ha/ directory of the data repository.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the duplicate audit."""
    args = parse_args()
    storage_dir = Path(args.config_dir or ha_config_dir()) / ".storage"
    entries = load_config_entries(storage_dir)
    duplicates = find_duplicates(entries)
    return print_duplicates(duplicates, args.domain)


if __name__ == "__main__":
    raise SystemExit(main())
