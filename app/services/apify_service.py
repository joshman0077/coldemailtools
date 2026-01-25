import requests
from typing import Optional
import time


class ApifyService:
    """Service for interacting with the Apify API."""

    BASE_URL = "https://api.apify.com/v2"

    def __init__(self, api_token: str):
        self.api_token = api_token
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }

    def start_run(self, actor_id: str, input_data: dict) -> dict:
        """
        Start an async Actor run.

        Returns the run data including id, status, defaultDatasetId, etc.
        """
        url = f"{self.BASE_URL}/acts/{actor_id}/runs"
        response = requests.post(url, headers=self.headers, json=input_data)
        response.raise_for_status()
        return response.json()["data"]

    def get_run_status(self, run_id: str) -> dict:
        """
        Get the current status of an Actor run.

        Status can be: READY, RUNNING, SUCCEEDED, FAILED, TIMED_OUT, ABORTED
        """
        url = f"{self.BASE_URL}/actor-runs/{run_id}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()["data"]

    def get_dataset_items(self, dataset_id: str, format: str = "json") -> bytes:
        """
        Download dataset items in the specified format.

        Supported formats: json, csv, xml, excel, html, rss
        """
        url = f"{self.BASE_URL}/datasets/{dataset_id}/items"
        params = {"format": format}
        response = requests.get(url, headers=self.headers, params=params)
        response.raise_for_status()
        return response.content

    def run_sync(self, actor_id: str, input_data: dict, timeout: int = 300) -> Optional[dict]:
        """
        Run an Actor synchronously and wait for results.

        This is suitable for quick jobs (< 5 minutes).
        Returns the dataset items directly, or None if timeout.
        """
        url = f"{self.BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items"
        params = {"timeout": timeout}

        try:
            response = requests.post(
                url,
                headers=self.headers,
                json=input_data,
                params=params,
                timeout=timeout + 10  # Add buffer for network latency
            )
            response.raise_for_status()
            return response.json()
        except requests.Timeout:
            return None

    def wait_for_run(self, run_id: str, max_wait: int = 600, poll_interval: int = 5) -> dict:
        """
        Poll for run completion with exponential backoff.

        Returns the final run status data.
        """
        start_time = time.time()
        current_interval = poll_interval

        while time.time() - start_time < max_wait:
            run_data = self.get_run_status(run_id)
            status = run_data.get("status")

            if status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
                return run_data

            time.sleep(current_interval)
            # Exponential backoff, capped at 30 seconds
            current_interval = min(current_interval * 1.5, 30)

        return self.get_run_status(run_id)
