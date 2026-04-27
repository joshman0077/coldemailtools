#!/usr/bin/env python3
"""
Instantly API v2 Client

Shared API client for all Instantly tools. Handles authentication,
pagination, and rate limiting.

Not meant to be run directly — imported by other tools.
"""

import os
import time
import logging
import requests
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BASE_URL = "https://api.instantly.ai/api/v2"

logger = logging.getLogger("instantly")


def get_api_key() -> str:
    key = os.getenv("INSTANTLY_API_KEY")
    if not key or key == "your_instantly_api_key_here":
        raise RuntimeError(
            "INSTANTLY_API_KEY not set in .env. "
            "Generate a v2 API key at https://app.instantly.ai/app/settings/api "
            "with scopes: accounts:read, campaigns:read, analytics:read"
        )
    return key


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {get_api_key()}",
        "Content-Type": "application/json",
    }


def _request(method: str, endpoint: str, params: dict = None, json_body: dict = None, max_retries: int = 8) -> dict:
    """Make an API request with retry and rate-limit handling."""
    url = f"{BASE_URL}{endpoint}"

    for attempt in range(max_retries):
        try:
            resp = requests.request(
                method,
                url,
                headers=_headers(),
                params=params,
                json=json_body,
                timeout=60,
            )

            if resp.status_code == 429:
                wait = min(5 * (attempt + 1), 60)
                logger.warning(f"Rate limited. Waiting {wait}s before retry {attempt + 1}/{max_retries}")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.HTTPError as e:
            if attempt < max_retries - 1 and resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"API error {resp.status_code}: {resp.text}") from e
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Request failed: {e}") from e

    raise RuntimeError(f"Max retries ({max_retries}) exceeded for {method} {endpoint}")


def get(endpoint: str, params: dict = None) -> dict:
    return _request("GET", endpoint, params=params)


def post(endpoint: str, json_body: dict = None, params: dict = None) -> dict:
    return _request("POST", endpoint, params=params, json_body=json_body)


def patch(endpoint: str, json_body: dict = None) -> dict:
    return _request("PATCH", endpoint, json_body=json_body)


def get_paginated(endpoint: str, params: dict = None, limit: int = 100) -> list:
    """Fetch all pages from a cursor-based paginated GET endpoint."""
    all_items = []
    params = params or {}
    cursor = None

    while True:
        page_params = {**params, "limit": limit}
        if cursor:
            page_params["starting_after"] = cursor

        data = get(endpoint, params=page_params)

        if isinstance(data, list):
            items = data
            cursor = None
        elif isinstance(data, dict):
            items = data.get("items", data.get("data", []))
            cursor = data.get("next_starting_after")
        else:
            break

        if not items:
            break

        all_items.extend(items)
        logger.info(f"  Fetched {len(all_items)} items so far...")

        # Stop if no next cursor or fewer items than limit
        if not cursor or len(items) < limit:
            break

        time.sleep(0.1)

    return all_items


def ensure_tmp_dir() -> Path:
    tmp = Path(__file__).resolve().parent.parent / ".tmp"
    tmp.mkdir(exist_ok=True)
    return tmp
