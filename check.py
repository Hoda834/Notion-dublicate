#!/usr/bin/env python3
"""Mark Notion records where both name and email are duplicated."""

import json
import os
import time
import unicodedata
from collections import defaultdict
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


# The database from the link you sent.
DATABASE_ID = "3bbf7394-9b79-8041-8896-ebdbbbbbf2b1"
API_VERSION = "2025-09-03"
TOKEN = os.environ["NOTION_TOKEN"]

# Explicit fields from your Candidate Tracker.
MATCH_NAME_FIELD = "DiA Staff Name"
MATCH_EMAIL_FIELD = "Email"
DUPLICATE_FIELD = "Duplicate"
DUPLICATE_OPTION = "Duplicate"


def call(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode() if body else None
    request = Request(
        f"https://api.notion.com/v1{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": API_VERSION, "Content-Type": "application/json"},
    )
    for attempt in range(4):
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except HTTPError as error:
            if error.code == 429 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(error.read().decode()) from error
    raise RuntimeError("Notion API rate limit was reached.")


def text(prop: dict[str, Any]) -> str:
    kind = prop["type"]
    if kind == "email":
        return prop.get("email") or ""
    return "".join(item.get("plain_text", "") for item in prop.get(kind, []))


def clean(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().strip().split())


def resolve_property(properties: dict[str, Any], wanted: str) -> str:
    """Find a property even if Notion's stored name has invisible spacing."""
    wanted_key = "".join(char for char in clean(wanted) if char.isalnum())
    for actual_name in properties:
        actual_key = "".join(char for char in clean(actual_name) if char.isalnum())
        if actual_key == wanted_key:
            return actual_name
    available = ", ".join(repr(name) for name in properties)
    raise RuntimeError(f"Cannot find {wanted!r}. Available properties: {available}")


def main() -> None:
    database = call("GET", f"/databases/{DATABASE_ID}")
    data_sources = database.get("data_sources", [])
    if len(data_sources) != 1:
        raise RuntimeError("This database has more than one data source. Set its ID in the code.")
    source_id = data_sources[0]["id"]

    pages, cursor = [], None
    while True:
        result = call("POST", f"/data_sources/{source_id}/query", {"page_size": 100, **({"start_cursor": cursor} if cursor else {})})
        pages.extend(result["results"])
        if not result.get("has_more"):
            break
        cursor = result["next_cursor"]

    if not pages:
        print("Database is empty.")
        return

    sample = pages[0]["properties"]
    match_name_field = resolve_property(sample, MATCH_NAME_FIELD)
    match_email_field = resolve_property(sample, MATCH_EMAIL_FIELD)
    duplicate_field = resolve_property(sample, DUPLICATE_FIELD)
    if sample[duplicate_field]["type"] != "select":
        raise RuntimeError(f"{DUPLICATE_FIELD} must be a Select property.")

    grouped = defaultdict(list)
    for page in pages:
        key = (
            clean(text(page["properties"][match_name_field])),
            clean(text(page["properties"][match_email_field])),
        )
        if key[0] and key[1]:
            grouped[key].append(page)
    duplicate_ids = {page["id"] for group in grouped.values() if len(group) > 1 for page in group}

    changed = 0
    for page in pages:
        target_option = DUPLICATE_OPTION if page["id"] in duplicate_ids else None
        current_option = page["properties"][duplicate_field].get("select")
        current_name = current_option.get("name") if current_option else None
        if current_name == target_option:
            continue
        call("PATCH", f"/pages/{page['id']}", {
            "properties": {duplicate_field: {"select": {"name": target_option} if target_option else None}}
        })
        changed += 1
        time.sleep(0.35)

    groups = sum(len(group) > 1 for group in grouped.values())
    print(f"Checked {len(pages)} records. Marked {len(duplicate_ids)} records across {groups} duplicate groups. Updated {changed} records.")


if __name__ == "__main__":
    main()
