"""Service for researching and fetching visual reference media (Wikimedia Commons, DuckDuckGo, Local Vault).

Provides generic, zero-cost reference image retrieval and preprocessing for Auto_YT pipeline stages.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from auto_yt.paths import DATA_DIR, LOCAL_VAULT_DIR, REFERENCES_DIR

logger = logging.getLogger(__name__)

USER_AGENT = "AutoYT-VisualResearch/1.0 (https://github.com/auto-yt; contact@auto-yt.local)"
REQUEST_TIMEOUT_SECONDS = 7
MIN_IMAGE_DIMENSION = 300
MAX_IMAGE_DIMENSION = 1024


def extract_queries_from_response(response_text: str) -> list[str]:
    """Extract clean search queries from LLM output (JSON or structured text)."""
    if not response_text or not response_text.strip():
        return []

    text = response_text.strip()

    # 1. Try to find and parse JSON block
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate_json = json_match.group(1) if json_match else text

    # Try direct or substring JSON extraction
    try:
        # Find outer braces if not in code block
        if "{" in candidate_json and "}" in candidate_json:
            start = candidate_json.find("{")
            end = candidate_json.rfind("}") + 1
            candidate_json = candidate_json[start:end]
        data = json.loads(candidate_json)
        if isinstance(data, dict):
            queries = data.get("queries") or data.get("search_queries") or data.get("keywords")
            if isinstance(queries, list):
                cleaned = [str(q).strip() for q in queries if str(q).strip()]
                if cleaned:
                    return cleaned
    except Exception:
        pass

    # 2. Fallback: Parse line-by-line bullet points or numbered lists
    lines = text.splitlines()
    fallback_queries = []
    for line in lines:
        cleaned_line = re.sub(r"^[\s*\-•\d.]+\s*", "", line).strip().strip('"').strip("'")
        if cleaned_line and len(cleaned_line) > 3 and not cleaned_line.startswith(("{", "}", "[", "]")):
            fallback_queries.append(cleaned_line)

    return fallback_queries[:4]


def search_wikimedia_commons(query: str, limit: int = 2) -> list[dict[str, Any]]:
    """Search Wikimedia Commons API for historical/factual reference images."""
    if not query or not query.strip():
        return []

    endpoint = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrnamespace": "6",  # File namespace
        "gsrsearch": query.strip(),
        "gsrlimit": str(max(1, limit * 2)),
        "prop": "imageinfo",
        "iiprop": "url|size|mime",
        "iiurlwidth": str(MAX_IMAGE_DIMENSION),
        "format": "json",
    }
    headers = {"User-Agent": USER_AGENT}

    try:
        resp = requests.get(endpoint, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            logger.warning("Wikimedia search returned status %d for query: %s", resp.status_code, query)
            return []

        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        results: list[dict[str, Any]] = []

        for _, page in pages.items():
            imageinfo = page.get("imageinfo")
            if not imageinfo or not isinstance(imageinfo, list):
                continue
            info = imageinfo[0]
            mime = info.get("mime", "").lower()
            if mime not in ("image/jpeg", "image/png", "image/webp"):
                continue

            width = info.get("width", 0)
            height = info.get("height", 0)
            if width < MIN_IMAGE_DIMENSION and height < MIN_IMAGE_DIMENSION:
                continue

            # Prefer thumburl (pre-scaled) or fallback to full url
            img_url = info.get("thumburl") or info.get("url")
            if img_url:
                results.append({
                    "title": page.get("title", ""),
                    "url": img_url,
                    "width": width,
                    "height": height,
                    "source": "wikimedia",
                })
                if len(results) >= limit:
                    break

        return results
    except Exception as exc:
        logger.warning("Wikimedia search failed for query '%s': %s", query, exc)
        return []


def search_duckduckgo_images(query: str, limit: int = 2) -> list[dict[str, Any]]:
    """Search DuckDuckGo images as a zero-key fallback."""
    if not query or not query.strip():
        return []

    try:
        # Step 1: Get vqd token from DuckDuckGo
        token_url = "https://duckduckgo.com/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        token_resp = requests.get(token_url, params={"q": query.strip()}, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        vqd_match = re.search(r"vqd=['\"]?([0-9-]+)['\"]?", token_resp.text)
        if not vqd_match:
            return []
        vqd = vqd_match.group(1)

        # Step 2: Query image JSON endpoint
        img_endpoint = "https://duckduckgo.com/i.js"
        params = {
            "l": "us-en",
            "o": "json",
            "q": query.strip(),
            "vqd": vqd,
            "f": ",,,",
            "p": "1",
        }
        img_resp = requests.get(img_endpoint, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        if img_resp.status_code != 200:
            return []

        data = img_resp.json()
        results_raw = data.get("results", [])
        results: list[dict[str, Any]] = []

        for item in results_raw:
            img_url = item.get("image")
            if not img_url or not img_url.startswith("http"):
                continue
            width = item.get("width", 0)
            height = item.get("height", 0)
            if width and width < MIN_IMAGE_DIMENSION and height and height < MIN_IMAGE_DIMENSION:
                continue

            results.append({
                "title": item.get("title", ""),
                "url": img_url,
                "width": width,
                "height": height,
                "source": "duckduckgo",
            })
            if len(results) >= limit:
                break

        return results
    except Exception as exc:
        logger.warning("DuckDuckGo image search failed for query '%s': %s", query, exc)
        return []


def search_local_vault(query: str, limit: int = 2) -> list[dict[str, Any]]:
    """Search for matching image files in the local curated vault."""
    if not LOCAL_VAULT_DIR.exists() or not query.strip():
        return []

    results: list[dict[str, Any]] = []
    tokens = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2]
    if not tokens:
        return []

    for file_path in LOCAL_VAULT_DIR.rglob("*"):
        if file_path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            filename_lower = file_path.stem.lower()
            matches = sum(1 for token in tokens if token in filename_lower)
            if matches > 0:
                results.append({
                    "title": file_path.stem,
                    "url": str(file_path.resolve()),
                    "local_path": file_path,
                    "source": "local_vault",
                    "score": matches,
                })

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results[:limit]


def download_and_sanitize_image(
    image_source: str,
    output_path: Path,
    min_dimension: int = MIN_IMAGE_DIMENSION,
    max_dimension: int = MAX_IMAGE_DIMENSION,
) -> str | None:
    """Download, validate and sanitize an image into a standard RGB JPEG file.

    Handles remote URLs as well as local file paths.
    """
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Read image bytes
        if image_source.startswith(("http://", "https://")):
            headers = {"User-Agent": USER_AGENT}
            resp = requests.get(image_source, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
            if resp.status_code != 200:
                logger.warning("Failed to download image from %s (status %d)", image_source, resp.status_code)
                return None
            raw_bytes = resp.content
        else:
            local_file = Path(image_source)
            if not local_file.exists():
                return None
            raw_bytes = local_file.read_bytes()

        if len(raw_bytes) < 1024:
            return None

        # 2. Open and validate with PIL
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img_format = (img.format or "").upper()
            if img_format not in ("JPEG", "PNG", "WEBP", "BMP", "TIFF"):
                logger.info("Skipping unsupported image format: %s", img_format)
                return None

            width, height = img.size
            if width < min_dimension and height < min_dimension:
                logger.info("Skipping image below minimum dimensions (%dx%d)", width, height)
                return None

            # 3. Convert mode to RGB
            if img.mode in ("RGBA", "LA", "P"):
                # Composite over white background
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                if img.mode == "RGBA":
                    background.paste(img, mask=img.split()[3])
                else:
                    background.paste(img, mask=img.split()[1])
                processed_img = background
            elif img.mode != "RGB":
                processed_img = img.convert("RGB")
            else:
                processed_img = img.copy()

            # 4. Resize if exceeding max_dimension
            p_width, p_height = processed_img.size
            if max(p_width, p_height) > max_dimension:
                scale = max_dimension / max(p_width, p_height)
                new_size = (max(1, int(p_width * scale)), max(1, int(p_height * scale)))
                processed_img = processed_img.resize(new_size, Image.Resampling.LANCZOS)

            # 5. Save as optimized JPEG
            processed_img.save(str(output_path), format="JPEG", quality=90, optimize=True)

        if output_path.exists() and output_path.stat().st_size > 0:
            return str(output_path.resolve())
        return None
    except Exception as exc:
        logger.warning("Error processing image from %s: %s", image_source, exc)
        return None


def image_to_base64(image_path: Path | str) -> str | None:
    """Read a local image file and return its standard base64 data string."""
    try:
        path = Path(image_path)
        if not path.exists() or not path.is_file():
            return None
        data = path.read_bytes()
        return base64.b64encode(data).decode("utf-8")
    except Exception as exc:
        logger.warning("Error converting image %s to base64: %s", image_path, exc)
        return None


def fetch_reference_images_for_queries(
    queries: list[str],
    output_dir: Path,
    max_images: int = 2,
) -> list[str]:
    """Execute multi-source visual research and save sanitized reference images.

    Returns a list of absolute paths to downloaded reference image files.
    """
    if not queries:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded_paths: list[str] = []
    seen_urls: set[str] = set()

    for query_idx, query in enumerate(queries):
        if len(downloaded_paths) >= max_images:
            break

        candidates: list[dict[str, Any]] = []

        # 1. Check local curated vault
        vault_hits = search_local_vault(query, limit=1)
        if vault_hits:
            candidates.extend(vault_hits)

        # 2. Search Wikimedia Commons
        if len(candidates) < 2:
            wiki_hits = search_wikimedia_commons(query, limit=2)
            candidates.extend(wiki_hits)

        # 3. Fallback: DuckDuckGo
        if len(candidates) < 1:
            ddg_hits = search_duckduckgo_images(query, limit=2)
            candidates.extend(ddg_hits)

        # Download best candidate
        for candidate in candidates:
            url = candidate.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            dest_file = output_dir / f"ref_{len(downloaded_paths) + 1}.jpg"
            saved_path = download_and_sanitize_image(url, dest_file)
            if saved_path:
                downloaded_paths.append(saved_path)
                logger.info(
                    "Saved visual reference #%d for query '%s' from %s: %s",
                    len(downloaded_paths),
                    query,
                    candidate.get("source", "unknown"),
                    saved_path,
                )
                break

    return downloaded_paths
