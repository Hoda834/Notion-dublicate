#!/usr/bin/env python3
"""Colour Notion names red where both name and email are duplicated."""

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
MATCH_NAME_FIELD = "Original Given Name"
MATCH_EMAIL_FIELD = "Email"
COLOUR_NAME_FIELD = "DiA Staff Name"


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
    required = {MATCH_NAME_FIELD, MATCH_EMAIL_FIELD, COLOUR_NAME_FIELD}
    missing = required - set(sample)
    if missing:
        raise RuntimeError("Missing Notion property/properties: " + ", ".join(sorted(missing)))
    colour_type = sample[COLOUR_NAME_FIELD]["type"]
    if colour_type not in {"title", "rich_text"}:
        raise RuntimeError(f"{COLOUR_NAME_FIELD} must be a Title or Text property.")

    grouped = defaultdict(list)
    for page in pages:
        key = (
            clean(text(page["properties"][MATCH_NAME_FIELD])),
            clean(text(page["properties"][MATCH_EMAIL_FIELD])),
        )
        if key[0] and key[1]:
            grouped[key].append(page)
    duplicate_ids = {page["id"] for group in grouped.values() if len(group) > 1 for page in group}

    changed = 0
    for page in pages:
        name = text(page["properties"][COLOUR_NAME_FIELD])
        target_colour = "red" if page["id"] in duplicate_ids else "default"
        name_parts = page["properties"][COLOUR_NAME_FIELD][colour_type]
        current_colours = [part.get("annotations", {}).get("color", "default") for part in name_parts]
        if name_parts and all(colour == target_colour for colour in current_colours):
            continue
        call("PATCH", f"/pages/{page['id']}", {"properties": {COLOUR_NAME_FIELD: {colour_type: [{
            "type": "text", "text": {"content": name},
            "annotations": {"bold": False, "italic": False, "strikethrough": False, "underline": False, "code": False, "color": target_colour},
        }]}}})
        changed += 1
        time.sleep(0.35)

    groups = sum(len(group) > 1 for group in grouped.values())
    print(f"Checked {len(pages)} records. {len(duplicate_ids)} names are red across {groups} duplicate groups. Updated {changed} names.")


if __name__ == "__main__":
    main()
