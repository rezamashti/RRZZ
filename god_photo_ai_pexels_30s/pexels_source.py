import os
import logging
from pathlib import Path
from urllib.parse import urlparse
import httpx

log = logging.getLogger("god-photo-ai.pexels")
API_URL = "https://api.pexels.com/v1/search"
SEARCH_TERMS = [
    "aesthetic profile photography",
    "nature aesthetic photography",
    "minimal aesthetic photography",
    "portrait photography",
    "sky sunset photography",
    "abstract aesthetic art",
]


class PexelsSource:
    """Download photos using the official Pexels API."""
    def __init__(self, download_dir: Path):
        self.api_key = os.getenv("PEXELS_API_KEY", "").strip()
        self.query = os.getenv("PEXELS_SEARCH_QUERY", "").strip()
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._query_index = 0

    @property
    def configured(self):
        return bool(self.api_key)

    def next_query(self):
        if self.query:
            return self.query
        value = SEARCH_TERMS[self._query_index % len(SEARCH_TERMS)]
        self._query_index += 1
        return value

    async def fetch_and_download(self, limit=5):
        if not self.api_key:
            log.warning("Pexels API key is not configured.")
            return []

        headers = {"Authorization": self.api_key}
        params = {
            "query": self.next_query(),
            "per_page": min(max(int(limit), 1), 80),
            "page": 1,
            "orientation": "square",
        }
        results = []
        async with httpx.AsyncClient(timeout=35, follow_redirects=True) as client:
            response = await client.get(API_URL, headers=headers, params=params)
            if response.status_code in (401, 403):
                log.error("Pexels API access denied (%s): %s", response.status_code, response.text[:300])
                return []
            response.raise_for_status()
            for photo in response.json().get("photos", []):
                photo_id = str(photo.get("id", "")).strip()
                src = photo.get("src") or {}
                image_url = src.get("large2x") or src.get("large") or src.get("medium")
                if not photo_id or not image_url:
                    continue
                try:
                    img = await client.get(image_url)
                    img.raise_for_status()
                    ctype = img.headers.get("content-type", "").lower()
                    if not ctype.startswith("image/"):
                        continue
                    ext = Path(urlparse(image_url).path).suffix.lower()
                    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                        ext = ".png" if "png" in ctype else ".webp" if "webp" in ctype else ".jpg"
                    path = self.download_dir / f"pexels_{photo_id}{ext}"
                    if not path.exists():
                        path.write_bytes(img.content)
                    photographer = str(photo.get("photographer", "Pexels contributor")).strip()
                    results.append({
                        "unique_id": f"pexels_{photo_id}",
                        "path": str(path),
                        "source_url": photo.get("url") or f"https://www.pexels.com/photo/{photo_id}/",
                        "photographer": photographer,
                    })
                except Exception:
                    log.exception("Could not download Pexels photo %s", photo_id)
        return results
