"""
image_search_tool.py
--------------------
Agent-compatible image search using Pixabay + Unsplash.
Both APIs are genuinely free, require no credit card, and have no aggressive
rate limits (Pixabay: 100 req/min, Unsplash: 50 req/hr).

--- Pixabay ---
One key only.
  1. Sign up at https://pixabay.com
  2. Visit https://pixabay.com/api/docs/ — your key is shown at the top.

--- Unsplash ---
Unsplash issues TWO keys per app: an Access Key and a Secret Key.
  * Access Key — used as a public client ID for search/read requests. THIS is what we need.
  * Secret Key — used only for OAuth2 user-authentication flows (acting on behalf
                 of a user: uploads, likes, etc.). NOT needed here.

  1. Go to https://unsplash.com/oauth/applications
  2. Click "New Application", accept terms.
  3. Copy the "Access Key" (NOT the Secret Key).

Set environment variables:
  PIXABAY_API_KEY=your_pixabay_key
  UNSPLASH_ACCESS_KEY=your_unsplash_ACCESS_key   # the public one, not the secret

Or pass them directly to image_search(...).

Dependencies:
    pip install requests

Quick usage:
    results = image_search("golden retriever", max_results=5)
    # agent inspects result.summary() and picks one
    save_image_to_disk(results[0], directory="./images")
"""

import base64
import logging
import mimetypes
import os
import time
from dataclasses import dataclass, field
from typing import Optional
from langchain.tools import tool
from tools import WATCHED_FOLDER

import requests
import dotenv

dotenv.load_dotenv(r"../.env")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ImageSearchTool/1.0)"}

_MIME_TO_EXT = {
    "image/jpeg":   ".jpg",
    "image/png":    ".png",
    "image/gif":    ".gif",
    "image/webp":   ".webp",
    "image/bmp":    ".bmp",
    "image/svg+xml":".svg",
}


def _guess_ext(url: str, content_type: str) -> str:
    ct = content_type.split(";")[0].strip()
    ext = _MIME_TO_EXT.get(ct) or mimetypes.guess_extension(ct) or ""
    if ext in ("", ".jpe"):
        _, url_ext = os.path.splitext(url.split("?")[0])
        ext = url_ext.lower() or ".bin"
    return ext


def _fetch_image(url: str, timeout: int = 15) -> tuple:
    """Download a direct image URL. Returns (raw_bytes, mime_type)."""
    r = requests.get(url, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    ct = r.headers.get("Content-Type", "application/octet-stream")
    if not ct.startswith("image/"):
        raise ValueError(f"Non-image content-type: {ct!r}")
    return r.content, ct


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ImageResult:
    index:           int
    source:          str           # "pixabay" | "unsplash"
    title:           str
    source_page_url: str
    image_url:       str
    width:           Optional[int]
    height:          Optional[int]
    tags:            list
    photographer:    str
    license:         str
    mime_type:       str
    extension:       str
    size_bytes:      int
    base64_data:     str
    fetch_error:     Optional[str] = field(default=None)

    def summary(self) -> str:
        if self.fetch_error:
            return (
                f"[{self.index}] FAILED ({self.source}) -- {self.fetch_error}\n"
                f"    Title : {self.title}\n"
                f"    URL   : {self.image_url}"
            )
        return (
            f"[{self.index}] {self.title}  [{self.source}]\n"
            f"    Photographer : {self.photographer}\n"
            f"    License      : {self.license}\n"
            f"    Dimensions   : {self.width}x{self.height}px\n"
            f"    Size         : {self.size_bytes:,} bytes ({self.mime_type})\n"
            f"    Tags         : {', '.join(self.tags[:8])}\n"
            f"    Page         : {self.source_page_url}"
        )


# ---------------------------------------------------------------------------
# Pixabay provider
# ---------------------------------------------------------------------------

_PIXABAY_URL = "https://pixabay.com/api/"


def _pixabay_search(query: str, n: int, key: str) -> list:
    # Pixabay keys look like "12345678-abc123def456abc123def456ab12" (~32 chars after the dash).
    # A 400 almost always means the key is wrong or was copied truncated.
    # Pixabay requires per_page to be between 3 and 200.
    # When splitting results across providers the requested count can fall below 3,
    # so we clamp upward and trim the returned list to n afterward.
    per_page = max(3, min(n, 200))
    params = {
        "key": key, "q": query,
        "image_type": "photo", "per_page": per_page,
        "safesearch": "true", "lang": "en",
    }
    r = requests.get(_PIXABAY_URL, params=params, headers=_HEADERS, timeout=15)
    if r.status_code == 400:
        raise ValueError(
            f"Pixabay 400 Bad Request — API key is likely invalid or truncated.\n"
            f"  Key used : {key}\n"
            f"  Expected : format like \'12345678-abc123...\' (~39 chars total)\n"
            f"  Fix      : copy a fresh key from https://pixabay.com/api/docs/"
        )
    r.raise_for_status()
    return r.json().get("hits", [])


def _pixabay_hit_to_result(hit: dict, index: int) -> ImageResult:
    tags = [t.strip() for t in hit.get("tags", "").split(",") if t.strip()]
    return ImageResult(
        index=index, source="pixabay",
        title=tags[0].title() if tags else "Pixabay image",
        source_page_url=hit.get("pageURL", ""),
        image_url=hit.get("largeImageURL") or hit.get("webformatURL", ""),
        width=hit.get("imageWidth"), height=hit.get("imageHeight"),
        tags=tags, photographer=hit.get("user", "Unknown"),
        license="Pixabay License — free commercial use, no attribution required",
        mime_type="", extension="", size_bytes=0, base64_data="",
    )


# ---------------------------------------------------------------------------
# Unsplash provider
# ---------------------------------------------------------------------------

_UNSPLASH_URL = "https://api.unsplash.com/search/photos"


def _unsplash_search(query: str, n: int, key: str) -> list:
    params = {"query": query, "per_page": min(n, 30), "page": 1}
    headers = {**_HEADERS, "Authorization": f"Client-ID {key}"}
    r = requests.get(_UNSPLASH_URL, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json().get("results", [])


def _unsplash_item_to_result(item: dict, index: int) -> ImageResult:
    urls = item.get("urls", {})
    user = item.get("user", {})
    # NOTE: Unsplash search results don't include tags (only the /photos/:id endpoint does).
    # We build a best-effort tag list from topic_submissions keys + description words instead.
    topic_tags = list(item.get("topic_submissions", {}).keys())
    desc = item.get("alt_description") or item.get("description") or ""
    desc_words = [w.strip(".,") for w in desc.split() if len(w) > 3][:6]
    tags = topic_tags + [w for w in desc_words if w not in topic_tags]
    return ImageResult(
        index=index, source="unsplash",
        title=item.get("alt_description") or item.get("description") or "Unsplash photo",
        source_page_url=item.get("links", {}).get("html", ""),
        image_url=urls.get("regular") or urls.get("full", ""),
        width=item.get("width"), height=item.get("height"),
        tags=tags, photographer=user.get("name", "Unknown"),
        license="Unsplash License — free for commercial and non-commercial use",
        mime_type="", extension="", size_bytes=0, base64_data="",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def image_search(
    query: str,
    max_results: int = 5,
    provider: str = "both",          # "pixabay" | "unsplash" | "both"
    fetch_images: bool = True,        # False = metadata only (much faster)
    fetch_timeout: int = 15,
    delay_between_fetches: float = 0.2,
    pixabay_api_key: Optional[str] = None,
    unsplash_access_key: Optional[str] = None,
) -> list:
    """
    Search Pixabay and/or Unsplash for images matching *query*.

    Parameters
    ----------
    query               Natural-language search query.
    max_results         Total images to return (split evenly across providers).
    provider            Which service(s) to use: "pixabay", "unsplash", or "both".
    fetch_images        Download images + embed base64. Set False for metadata only.
    fetch_timeout       Per-image HTTP timeout (seconds).
    delay_between_fetches  Pause between downloads (seconds).
    pixabay_api_key     Overrides PIXABAY_API_KEY env var.
    unsplash_access_key Overrides UNSPLASH_ACCESS_KEY env var.

    Returns
    -------
    list[ImageResult]
        Sorted by provider then relevance rank. Failed downloads still appear
        with fetch_error set and base64_data == "".
    """
    pb_key = os.getenv("PIXABAY_API_KEY") 
    us_key = os.getenv("UNSPLASH_ACCESS_KEY")

    use_pb = provider in ("pixabay", "both")  and bool(pb_key)
    use_us = provider in ("unsplash", "both") and bool(us_key)

    if not use_pb and not use_us:
        missing = []
        if provider in ("pixabay",  "both"): missing.append("PIXABAY_API_KEY")
        if provider in ("unsplash", "both"): missing.append("UNSPLASH_ACCESS_KEY")
        raise ValueError(
            f"No API keys found. Set: {', '.join(missing)}\n"
            "Free keys:\n"
            "  Pixabay  -> https://pixabay.com/api/docs/\n"
            "  Unsplash -> https://unsplash.com/oauth/applications\n"
            "             (use the ACCESS KEY, not the Secret Key)"
        )

    # Split quota between providers
    if use_pb and use_us:
        pb_n = max_results // 2 + max_results % 2
        us_n = max_results // 2
    else:
        pb_n = max_results if use_pb else 0
        us_n = max_results if use_us else 0

    results: list = []

    if use_pb and pb_n > 0:
        try:
            hits = _pixabay_search(query, pb_n, pb_key)
            for i, h in enumerate(hits[:pb_n], start=1):  # slice trims any over-fetch
                results.append(_pixabay_hit_to_result(h, i))
        except Exception as e:
            logger.warning("Pixabay search failed: %s", e)

    if use_us and us_n > 0:
        try:
            offset = len(results)
            items = _unsplash_search(query, us_n, us_key)
            for i, item in enumerate(items[:us_n], start=offset + 1):
                results.append(_unsplash_item_to_result(item, i))
        except Exception as e:
            logger.warning("Unsplash search failed: %s", e)

    if not results:
        return []

    # Re-index sequentially after merging
    for i, r in enumerate(results, start=1):
        r.index = i

    if not fetch_images:
        return results

    # Download each image
    for i, result in enumerate(results):
        if not result.image_url:
            result.fetch_error = "No image URL available"
            continue
        try:
            raw, mime = _fetch_image(result.image_url, timeout=fetch_timeout)
            result.mime_type   = mime
            result.extension   = _guess_ext(result.image_url, mime)
            result.size_bytes  = len(raw)
            result.base64_data = base64.b64encode(raw).decode("utf-8")
        except Exception as e:
            result.fetch_error = str(e)
        if i < len(results) - 1:
            time.sleep(delay_between_fetches)

    return results


def save_image_to_disk(
    result: ImageResult,
    filename: str = "",
) -> str:
    """
    Write a fetched ImageResult to disk.

    Parameters
    ----------
    result     ImageResult with populated base64_data.
    directory  Target folder (created if absent).
    filename   Base name without extension. Defaults to a sanitised image title.

    Returns
    -------
    str  Absolute path of the saved file.
    """
    if not result.base64_data:
        raise ValueError(
            f"ImageResult [{result.index}] has no data "
            f"(fetch_error={result.fetch_error!r})."
        )
    directory = rf"{WATCHED_FOLDER}/images"
    os.makedirs(directory, exist_ok=True)

    if not filename:
        safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in result.title)
        filename = safe.strip()[:80] or f"image_{result.index}"

    ext  = result.extension or ".bin"
    path = os.path.join(directory, filename + ext)

    counter = 1
    base_path = path
    while os.path.exists(path):
        stem, _ = os.path.splitext(base_path)
        path = f"{stem}_{counter}{ext}"
        counter += 1

    with open(path, "wb") as fh:
        fh.write(base64.b64decode(result.base64_data))

    logger.info("Saved '%s' -> %s", result.title, path)
    return os.path.abspath(path)


# ---------------------------------------------------------------------------
# Optional LangChain @tool wrapper
# ---------------------------------------------------------------------------


@tool(
    "image_search",
    description=(
        "Search Pixabay and Unsplash for images matching a query. "
        "Returns metadata + base64 image content for each result. "
        "Inspect the summaries, choose the best image, then call "
        "save_image_to_disk() with your chosen ImageResult."
    ),
)
def image_search_tool(query: str, max_results: int = 5) -> str:
    """Args: query (str), max_results (int, 1-20)."""
    results = image_search(query, max_results=max_results)
    if not results:
        return f"No images found for: '{query}'"
    lines = [f"Image results for '{query}':\n"]
    for r in results:
        lines.append(r.summary())
        lines.append("")
    lines.append("Call save_image_to_disk(results[i-1], directory='./images') to save.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Smoke test:  python image_search_tool.py "your query here"
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    q = " ".join(sys.argv[1:]) or "mountain lake at sunset"
    print(f"Searching for: {q!r}\n")

    try:
        results = image_search(q, max_results=4, fetch_images=True)
    except ValueError as e:
        print(f"Configuration error:\n{e}")
        sys.exit(1)

    for r in results:
        print(r.summary())
        print()

    good = [r for r in results if not r.fetch_error]
    if good:
        path = save_image_to_disk(good[0])
        print(f"Saved [{good[0].source}] '{good[0].title}' -> {path}")
    else:
        print("All downloads failed.")