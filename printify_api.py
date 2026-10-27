"""Everything that talks to the Printify API lives in this file.

Nothing here talks to Etsy: Printify publishes to your Etsy shop for us.
API reference: https://developers.printify.com/

Your token is read from the .env file and is only ever placed in the
"Authorization" header. It is never printed or written to log.csv.
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

API_BASE = "https://api.printify.com/v1"
USER_AGENT = "mug-lister/1.0 (Python)"   # Printify requires every request to name the app
NOT_A_REAL_TOKEN = {"", "paste-your-token-here"}

MAX_ATTEMPTS = 5          # 1 try + 4 retries
FIRST_WAIT_SECONDS = 2    # then 4, 8, 16 ...
MAX_WAIT_SECONDS = 300    # never wait more than 5 minutes for one retry

# What Printify should push to Etsy when publishing (from the publish endpoint docs).
PUBLISH_FIELDS = {
    "title": True,
    "description": True,
    "images": True,
    "variants": True,
    "tags": True,
    "keyFeatures": True,
    "shipping_template": True,
}


class PrintifyError(Exception):
    """A Printify request failed. The message is safe to show (it never contains the token)."""


class StopRun(PrintifyError):
    """A failure that should stop the whole run, e.g. a bad token or Printify being down."""


def load_token(project_dir: Path) -> str | None:
    load_dotenv(project_dir / ".env")
    token = os.environ.get("PRINTIFY_API_TOKEN", "").strip()
    return None if token in NOT_A_REAL_TOKEN else token


class PrintifyClient:
    def __init__(self, token: str, sleep=time.sleep):
        self._token = token
        self._sleep = sleep
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        })

    # --- Read-only calls (used by setup) ---------------------------------------

    def list_shops(self):
        return self._request("GET", "/shops.json")

    def list_blueprints(self):
        return self._request("GET", "/catalog/blueprints.json")

    def get_blueprint(self, blueprint_id):
        return self._request("GET", f"/catalog/blueprints/{blueprint_id}.json")

    def list_print_providers(self, blueprint_id):
        return self._request("GET", f"/catalog/blueprints/{blueprint_id}/print_providers.json")

    def get_print_provider(self, provider_id):
        return self._request("GET", f"/catalog/print_providers/{provider_id}.json")

    def list_variants(self, blueprint_id, provider_id):
        return self._request(
            "GET", f"/catalog/blueprints/{blueprint_id}/print_providers/{provider_id}/variants.json")

    def get_shipping(self, blueprint_id, provider_id):
        return self._request(
            "GET", f"/catalog/blueprints/{blueprint_id}/print_providers/{provider_id}/shipping.json")

    def find_product_by_title(self, shop_id, title):
        """Look through the shop's products for one with exactly this title (one try only)."""
        page = self._request("GET", f"/shops/{shop_id}/products.json?limit=50", attempts=1)
        for product in page.get("data", []):
            if product.get("title") == title:
                return product
        return None

    # --- Calls that change your Printify account ---------------------------------

    def upload_image(self, path: Path):
        """Upload a PNG to your Printify media library. Returns the image info (with its id)."""
        contents = base64.b64encode(path.read_bytes()).decode("ascii")
        return self._request("POST", "/uploads/images.json",
                             body={"file_name": path.name, "contents": contents}, timeout=300)

    def create_product(self, shop_id, product: dict):
        """Create an (unpublished) product. Returns the new product, including its id."""

        def already_created():
            # A server error or timeout doesn't always mean the product wasn't made.
            # Before trying again, check, so a retry never creates a duplicate.
            try:
                return self.find_product_by_title(shop_id, product["title"])
            except PrintifyError:
                return None

        return self._request("POST", f"/shops/{shop_id}/products.json", body=product,
                             timeout=120, before_retry=already_created)

    def publish_product(self, shop_id, product_id):
        """Ask Printify to publish the product to the connected (Etsy) shop."""
        return self._request("POST", f"/shops/{shop_id}/products/{product_id}/publish.json",
                             body=PUBLISH_FIELDS)

    # --- The one place requests are actually sent ----------------------------------

    def _safe(self, text) -> str:
        """Belt and braces: make sure the token can never appear in a message."""
        return str(text).replace(self._token, "***")

    def _request(self, method, path, *, body=None, timeout=60, attempts=MAX_ATTEMPTS,
                 before_retry=None):
        url = API_BASE + path
        problem = "unknown problem"
        for attempt in range(1, attempts + 1):
            wait = None
            try:
                response = self._session.request(method, url, json=body, timeout=timeout)
            except requests.RequestException as exc:
                problem = self._safe(f"network problem ({type(exc).__name__}: {exc})")[:300]
            else:
                status = response.status_code
                if status < 400:
                    try:
                        return response.json() if response.content else {}
                    except ValueError:
                        return {}
                if status == 401:
                    raise StopRun("Printify rejected your token (HTTP 401). Check PRINTIFY_API_TOKEN in "
                                  "your .env file. It may be mistyped, expired, or revoked.")
                if status == 403:
                    raise StopRun("Printify says your token isn't allowed to do this (HTTP 403). Create a "
                                  "new token with all access scopes (see README).")
                if status != 429 and status < 500:
                    raise PrintifyError(self._safe(f"Printify refused the request (HTTP {status}): "
                                                   f"{_explain(response)}"))
                if status == 429:
                    problem = "Printify's rate limit was reached (HTTP 429)"
                else:
                    problem = f"Printify server error (HTTP {status})"
                wait = _retry_after_seconds(response)

            if attempt == attempts:
                break
            wait = min(wait or FIRST_WAIT_SECONDS * 2 ** (attempt - 1), MAX_WAIT_SECONDS)
            print(f"    ... {problem}. Waiting {wait:g}s, then trying again "
                  f"(attempt {attempt + 1} of {attempts}).")
            self._sleep(wait)
            if before_retry:
                found = before_retry()
                if found is not None:
                    print("    ... it turns out the request did go through, so no retry is needed.")
                    return found

        raise StopRun(f"Gave up after {attempts} tries. Last problem: {problem}.")


def _retry_after_seconds(response):
    try:
        return max(1.0, float(response.headers.get("Retry-After", "")))
    except ValueError:
        return None


def _explain(response) -> str:
    """Pull the useful part out of a Printify error response."""
    try:
        data = response.json()
    except ValueError:
        return (response.text or "no details given").strip()[:300]
    if not isinstance(data, dict):
        return str(data)[:500]
    message = data.get("message") or data.get("error") or ""
    errors = data.get("errors")
    detail = f"{message} {errors}" if errors else message
    return (detail or str(data)).strip()[:500]
