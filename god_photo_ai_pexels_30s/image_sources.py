"""Image providers: Wikimedia Commons (no API key) and optional Pexels API."""
import logging
import html
import re
from pathlib import Path
from urllib.parse import urlparse
import httpx

from pexels_source import PexelsSource

log = logging.getLogger("god-photo-ai.sources")
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
def _license_is_reusable(name):
    normalized = name.lower().replace("_", " ").strip()
    # Exclude NonCommercial and NoDerivatives variants; only accept broadly reusable licenses.
    if "noncommercial" in normalized or "non-commercial" in normalized or " no derivatives" in normalized or "-nd" in normalized:
        return False
    compact = normalized.replace(" ", "-")
    return (
        compact == "cc0" or compact.startswith("cc0-")
        or normalized.startswith("public domain")
        or compact.startswith("cc-by-") or compact.startswith("cc-by ")
        or compact.startswith("cc-by-sa-") or compact.startswith("cc-by-sa ")
    )


class ImageSources:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pexels = PexelsSource(self.data_dir / "pexels")
        self._commons_seen = set()

    @property
    def configured(self):
        # Commons works without a private key; Pexels is an optional extra source.
        return True

    async def fetch(self, queries, limit=5):
        results = []
        per_query = max(1, min(3, limit))
        for query in queries[:3]:
            try:
                results.extend(await self._commons_search(query, per_query))
            except Exception:
                log.exception("Wikimedia Commons search failed for %r", query)
            if len(results) >= limit:
                break
        if len(results) < limit and self.pexels.configured:
            try:
                results.extend(await self.pexels.fetch_and_download(limit=limit-len(results)))
            except Exception:
                log.exception("Pexels fallback failed")
        # Stable de-duplication across providers.
        unique, output = set(), []
        for item in results:
            if item["unique_id"] not in unique:
                unique.add(item["unique_id"])
                output.append(item)
        return output[:limit]

    async def _commons_search(self, query, limit):
        headers = {"User-Agent": "GodPhotoAI/1.0 (Telegram photo discovery bot; contact admin via bot)"}
        params = {
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f'filetype:bitmap {query}', "gsrnamespace": 6,
            "gsrlimit": max(5, limit * 3), "prop": "imageinfo",
            "iiprop": "url|extmetadata", "iiurlwidth": 1400,
        }
        output = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
            response = await client.get(COMMONS_API, params=params)
            response.raise_for_status()
            pages = (response.json().get("query") or {}).get("pages") or {}
            for page in pages.values():
                info_list = page.get("imageinfo") or []
                if not info_list:
                    continue
                info = info_list[0]
                meta = info.get("extmetadata") or {}
                license_name = _meta_value(meta, "LicenseShortName")
                license_url = _meta_value(meta, "LicenseUrl")
                if not _license_is_reusable(license_name):
                    continue
                image_url = info.get("thumburl") or info.get("url")
                page_url = info.get("descriptionurl")
                if not image_url or not page_url:
                    continue
                title = page.get("title", "image")
                safe_id = str(page.get("pageid") or abs(hash(title)))
                unique_id = f"commons_{safe_id}"
                if unique_id in self._commons_seen:
                    continue
                try:
                    image_response = await client.get(image_url)
                    image_response.raise_for_status()
                    ctype = image_response.headers.get("content-type", "").lower()
                    if not ctype.startswith("image/"):
                        continue
                    ext = Path(urlparse(image_url).path).suffix.lower()
                    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                        ext = ".jpg"
                    target = self.data_dir / f"{unique_id}{ext}"
                    if not target.exists():
                        target.write_bytes(image_response.content)
                    self._commons_seen.add(unique_id)
                    output.append({
                        "unique_id": unique_id, "path": str(target),
                        "source_url": page_url,
                        "photographer": _meta_value(meta, "Artist") or "Wikimedia Commons contributor",
                        "license": license_name, "license_url": license_url,
                    })
                    if len(output) >= limit:
                        break
                except Exception:
                    log.exception("Commons image download failed: %s", title)
        return output


def _meta_value(metadata, key):
    value = metadata.get(key) or {}
    if isinstance(value, dict):
        raw = str(value.get("value") or "").strip()
    return html.unescape(re.sub(r"<[^>]+>", " ", raw)).strip()
    return str(value).strip()
