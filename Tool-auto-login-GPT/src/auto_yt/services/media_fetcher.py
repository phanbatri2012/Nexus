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


def clean_query_to_core_entity(query: str) -> list[str]:
    """Extract clean entity candidate names from overly verbose query strings."""
    if not query or not query.strip():
        return []

    text = query.strip()

    # 1. Remove common filler phrases
    fillers = [
        r"chân\s+dung\s+ảnh\s+tư\s+liệu\s+lịch\s+sử",
        r"ảnh\s+tư\s+liệu\s+lịch\s+sử",
        r"chân\s+dung\s+tư\s+liệu",
        r"tư\s+liệu\s+lịch\s+sử",
        r"ảnh\s+lịch\s+sử",
        r"ảnh\s+tư\s+liệu",
        r"archival\s+photo(?:graph)?",
        r"historical\s+photo(?:graph)?",
        r"chân\s+dung",
        r"chiến\s+dịch\s+[^,0-9]+(?:\s+\d{4})?",
        r"năm\s+\d{4}",
        r"\b\d{4}\b",
    ]
    cleaned = text
    for f in fillers:
        cleaned = re.sub(f, " ", cleaned, flags=re.IGNORECASE)

    # 2. Remove military and political titles for core name isolation
    titles = [
        r"\bĐại\s+tướng\b",
        r"\bTrung\s+tướng\b",
        r"\bThiếu\s+tướng\b",
        r"\bThượng\s+tướng\b",
        r"\bĐại\s+tá\b",
        r"\bTướng\b",
        r"\bGeneral\b",
        r"\bAdmiral\b",
        r"\bChủ\s+tịch\b",
        r"\bTổng\s+thống\b",
        r"\bThủ\s+tướng\b",
    ]
    entity_only = cleaned
    for t in titles:
        entity_only = re.sub(t, " ", entity_only, flags=re.IGNORECASE)

    entity_only = re.sub(r"\s+", " ", entity_only).strip(" ,.-'\"")
    cleaned_with_title = re.sub(r"\s+", " ", cleaned).strip(" ,.-'\"")
    raw_cleaned = re.sub(r"\s+", " ", text).strip(" ,.-'\"")

    candidates: list[str] = []
    if entity_only and len(entity_only) >= 2:
        candidates.append(entity_only)
    if cleaned_with_title and cleaned_with_title != entity_only and len(cleaned_with_title) >= 2:
        candidates.append(cleaned_with_title)
    if raw_cleaned and raw_cleaned not in candidates:
        candidates.append(raw_cleaned)

    return candidates


def search_wikipedia_pageimages(query: str, limit: int = 2) -> list[dict[str, Any]]:
    """Search Wikipedia (Vietnamese & English) pageimages API for official portraits/historical images."""
    if not query or not query.strip():
        return []

    results: list[dict[str, Any]] = []
    headers = {"User-Agent": USER_AGENT}

    for lang in ("vi", "en"):
        if len(results) >= limit:
            break

        # 1. Direct title query (handles exact person/topic matches with automatic redirects)
        direct_url = f"https://{lang}.wikipedia.org/w/api.php"
        direct_params = {
            "action": "query",
            "titles": query.strip(),
            "redirects": "1",
            "prop": "pageimages",
            "pithumbsize": str(MAX_IMAGE_DIMENSION),
            "format": "json",
        }
        try:
            resp = requests.get(direct_url, params=direct_params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
            if resp.status_code == 200:
                data = resp.json()
                pages = data.get("query", {}).get("pages", {})
                for _, page in pages.items():
                    thumb = page.get("thumbnail")
                    if thumb and thumb.get("source"):
                        source_url = thumb["source"]
                        results.append({
                            "title": page.get("title", ""),
                            "url": source_url,
                            "width": thumb.get("width", 0),
                            "height": thumb.get("height", 0),
                            "source": f"wikipedia_{lang}",
                        })
                        if len(results) >= limit:
                            break
        except Exception as exc:
            logger.debug("Wikipedia %s direct lookup failed for '%s': %s", lang, query, exc)

        # 2. Generator search if direct title produced no thumbnail
        if len(results) < limit:
            search_params = {
                "action": "query",
                "generator": "search",
                "gsrsearch": query.strip(),
                "gsrlimit": str(limit),
                "prop": "pageimages",
                "pithumbsize": str(MAX_IMAGE_DIMENSION),
                "format": "json",
            }
            try:
                resp = requests.get(direct_url, params=search_params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
                if resp.status_code == 200:
                    data = resp.json()
                    pages = data.get("query", {}).get("pages", {})
                    for _, page in pages.items():
                        thumb = page.get("thumbnail")
                        if thumb and thumb.get("source"):
                            source_url = thumb["source"]
                            if not any(r["url"] == source_url for r in results):
                                results.append({
                                    "title": page.get("title", ""),
                                    "url": source_url,
                                    "width": thumb.get("width", 0),
                                    "height": thumb.get("height", 0),
                                    "source": f"wikipedia_{lang}_search",
                                })
                                if len(results) >= limit:
                                    break
            except Exception as exc:
                logger.debug("Wikipedia %s search failed for '%s': %s", lang, query, exc)

    return results


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
        "gsrlimit": str(max(2, limit * 2)),
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


def search_bing_images(query: str, limit: int = 2) -> list[dict[str, Any]]:
    """Search Bing Images as a zero-key web fallback."""
    if not query or not query.strip():
        return []

    try:
        encoded_query = urllib.parse.quote(query.strip())
        bing_url = f"https://www.bing.com/images/search?q={encoded_query}&form=HDRSC2&first=1"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        resp = requests.get(bing_url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            return []

        # Bing encodes direct source URLs in 'murl&quot;:&quot;URL&quot;' attributes
        raw_urls = re.findall(r'murl&quot;:&quot;(https?://[^&]+)&quot;', resp.text)
        results: list[dict[str, Any]] = []

        for img_url in raw_urls:
            # Skip unworkable formats or tracking URLs
            clean_url = img_url.replace(r"\/", "/")
            if not clean_url.startswith("http"):
                continue
            lower_url = clean_url.lower()
            if any(lower_url.endswith(ext) for ext in (".svg", ".gif", ".ico")):
                continue

            results.append({
                "title": query.strip(),
                "url": clean_url,
                "width": 800,
                "height": 600,
                "source": "bing",
            })
            if len(results) >= limit:
                break

        return results
    except Exception as exc:
        logger.warning("Bing image search failed for query '%s': %s", query, exc)
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

    Applies query cleaning and searches sequentially across Local Vault,
    Wikipedia Pageimages (vi/en), Wikimedia Commons, Bing Images, and DuckDuckGo.

    Returns a list of absolute paths to downloaded reference image files.
    """
    if not queries:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded_paths: list[str] = []
    seen_urls: set[str] = set()

    for query_idx, raw_query in enumerate(queries):
        if len(downloaded_paths) >= max_images:
            break

        # Generate cleaned query variations (e.g. core entity first, then full query)
        search_variations = clean_query_to_core_entity(raw_query)
        if not search_variations:
            search_variations = [raw_query]

        found_image = False

        for q_var in search_variations:
            if found_image or len(downloaded_paths) >= max_images:
                break

            candidates: list[dict[str, Any]] = []

            # 1. Check local curated vault
            vault_hits = search_local_vault(q_var, limit=1)
            if vault_hits:
                candidates.extend(vault_hits)

            # 2. Search Wikipedia Pageimages (official historical portraits/equipment)
            if len(candidates) < 2:
                wiki_page_hits = search_wikipedia_pageimages(q_var, limit=2)
                candidates.extend(wiki_page_hits)

            # 3. Search Wikimedia Commons
            if len(candidates) < 2:
                wiki_hits = search_wikimedia_commons(q_var, limit=2)
                candidates.extend(wiki_hits)

            # 4. Search Bing Images as robust zero-key web fallback
            if len(candidates) < 2:
                bing_hits = search_bing_images(q_var, limit=2)
                candidates.extend(bing_hits)

            # 5. Fallback: DuckDuckGo
            if len(candidates) < 1:
                ddg_hits = search_duckduckgo_images(q_var, limit=2)
                candidates.extend(ddg_hits)

            # Attempt to download best candidate
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
                        "Saved visual reference #%d for query '%s' (via '%s') from %s: %s",
                        len(downloaded_paths),
                        raw_query,
                        q_var,
                        candidate.get("source", "unknown"),
                        saved_path,
                    )
                    found_image = True
                    break

    return downloaded_paths
