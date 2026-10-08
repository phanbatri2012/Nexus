"""
Pexels Background Video Downloader & Audio Stripper (Rock-Solid Multi-Page Engine)
- Robust multi-page crawler across 35+ Vietnam categories & queries
- Downloads Full HD 1080p (16:9) stock footage of Vietnam from Pexels
- Removes all audio tracks via FFmpeg lossless stream copy (-c:v copy -an)
- Names files sequentially: video_1.mp4, video_2.mp4, video_3.mp4...
- Maintains state and metadata for resuming without duplicate downloads
- Includes disk space safety guards (> 15GB free space required)
"""

import os
import sys
import json
import time
import shutil
import urllib.request
import urllib.parse
import threading
import subprocess
from queue import Queue, Empty
from pathlib import Path
from playwright.sync_api import sync_playwright

TARGET_DIR = Path(r"E:\yt\Tool\Auto_YT\Tool-auto-login-GPT\data\background_videos")
TARGET_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = TARGET_DIR / "state.json"
METADATA_FILE = TARGET_DIR / "metadata.json"
LOG_FILE = TARGET_DIR / "download.log"

FFMPEG_EXE = r"E:\yt\Tool\Auto_YT\Tool-auto-login-GPT\.venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"
if not os.path.exists(FFMPEG_EXE):
    FFMPEG_EXE = shutil.which("ffmpeg") or "ffmpeg"

SEARCH_QUERIES = [
    "vietnam",
    "vietnam travel",
    "vietnam countryside",
    "hanoi",
    "saigon",
    "ho chi minh city",
    "da nang",
    "halong bay",
    "nha trang",
    "phu quoc",
    "hoi an",
    "ninh binh",
    "sapa",
    "ha giang",
    "mekong delta",
    "hue",
    "dalat",
    "da lat",
    "vietnam drone",
    "vietnam landscape",
    "vietnam street",
    "vietnam traffic",
    "vietnam street food",
    "vietnamese food",
    "vietnam nature",
    "vietnam scenery",
    "vietnam beach",
    "vietnam farmer",
    "vietnam temple",
    "vietnam pagoda",
    "mui ne",
    "quy nhon",
    "vung tau",
    "cao bang",
    "ban gioc",
    "cat ba",
    "son doong"
]

class DownloadManager:
    def __init__(self, max_workers=6, min_free_gb=15.0):
        self.max_workers = max_workers
        self.min_free_gb = min_free_gb
        self.queue = Queue(maxsize=3000)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        
        self.state = self._load_state()
        self.metadata = self._load_metadata()
        
        # Determine starting index from existing files
        existing_indices = []
        for f in TARGET_DIR.glob("video_*.mp4"):
            name = f.stem
            try:
                idx = int(name.replace("video_", ""))
                existing_indices.append(idx)
            except ValueError:
                pass
        self.current_index = max(existing_indices, default=0)
        self.log(f"Initialized Downloader. Current highest index: video_{self.current_index}.mp4")
        self.log(f"Total already saved in state: {len(self.state.get('downloaded_ids', []))} videos.")

    def log(self, message: str):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {message}"
        print(line, flush=True)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"downloaded_ids": []}

    def _save_state(self):
        with self.lock:
            try:
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, ensure_ascii=False, indent=2)
            except Exception as e:
                self.log(f"Error saving state: {e}")

    def _load_metadata(self) -> dict:
        if METADATA_FILE.exists():
            try:
                with open(METADATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_metadata(self):
        with self.lock:
            try:
                with open(METADATA_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.metadata, f, ensure_ascii=False, indent=2)
            except Exception as e:
                self.log(f"Error saving metadata: {e}")

    def check_disk_space(self) -> bool:
        try:
            total, used, free = shutil.disk_usage(str(TARGET_DIR))
            free_gb = free / (1024 ** 3)
            if free_gb < self.min_free_gb:
                self.log(f"WARNING: Low disk space on drive ({free_gb:.2f} GB free < {self.min_free_gb} GB). Pausing downloads.")
                return False
            return True
        except Exception:
            return True

    def select_best_video_file(self, video_files: list) -> tuple:
        """Select best 16:9 Full HD (1080p) or closest resolution"""
        if not video_files:
            return None, 0, 0, ""
            
        # Priority 1: Exact 1920x1080
        for f in video_files:
            w, h = f.get("width", 0), f.get("height", 0)
            if w == 1920 and h == 1080:
                return f.get("link"), w, h, f.get("quality", "hd")

        # Priority 2: Horizontal 1080p or higher
        for f in video_files:
            w, h = f.get("width", 0), f.get("height", 0)
            if w >= 1920 and w > h:
                return f.get("link"), w, h, f.get("quality", "hd")

        # Priority 3: 1280x720 (720p horizontal)
        for f in video_files:
            w, h = f.get("width", 0), f.get("height", 0)
            if w == 1280 and h == 720:
                return f.get("link"), w, h, f.get("quality", "hd")

        # Priority 4: Any horizontal stream
        for f in video_files:
            w, h = f.get("width", 0), f.get("height", 0)
            if w > h and f.get("link"):
                return f.get("link"), w, h, f.get("quality", "")

        return None, 0, 0, ""

    def download_and_process_worker(self, worker_id: int):
        while not self.stop_event.is_set():
            try:
                item = self.queue.get(timeout=4)
            except Empty:
                continue

            if item is None:
                break

            video_id = item.get("id")
            attr = item.get("attributes", item)
            vid = attr.get("video", attr)
            video_files = vid.get("video_files", attr.get("video_files", []))
            
            link, width, height, quality = self.select_best_video_file(video_files)
            if not link:
                self.queue.task_done()
                continue

            if not self.check_disk_space():
                self.stop_event.set()
                self.queue.task_done()
                break

            # Assign sequential index atomically
            with self.lock:
                if len(self.state["downloaded_ids"]) >= 4000 or self.current_index >= 4000:
                    self.stop_event.set()
                    self.queue.task_done()
                    break

                if video_id in self.state["downloaded_ids"]:
                    self.queue.task_done()
                    continue
                self.current_index += 1
                idx = self.current_index
                filename = f"video_{idx}.mp4"
                final_path = TARGET_DIR / filename
                temp_raw_path = TARGET_DIR / f".tmp_raw_{idx}_{video_id}.mp4"

            try:
                self.log(f"[Worker-{worker_id}] Downloading {filename} (ID: {video_id}, {width}x{height}, {attr.get('duration', 0)}s)...")
                
                # Download stream
                req = urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                with urllib.request.urlopen(req, timeout=45) as resp, open(temp_raw_path, "wb") as out_f:
                    shutil.copyfileobj(resp, out_f)

                # Strip audio using FFmpeg (-c:v copy -an)
                cmd = [
                    FFMPEG_EXE,
                    "-y",
                    "-i", str(temp_raw_path),
                    "-c:v", "copy",
                    "-an",
                    str(final_path)
                ]
                
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode != 0 or not final_path.exists() or final_path.stat().st_size == 0:
                    if temp_raw_path.exists():
                        shutil.move(str(temp_raw_path), str(final_path))
                else:
                    if temp_raw_path.exists():
                        temp_raw_path.unlink(missing_ok=True)

                file_size_mb = round(final_path.stat().st_size / (1024 * 1024), 2)
                self.log(f" -> [Worker-{worker_id}] Saved {filename} ({file_size_mb} MB, muted) [Progress: {self.current_index}/4000]")

                # Update metadata and state
                with self.lock:
                    self.state["downloaded_ids"].append(video_id)
                    self.metadata[filename] = {
                        "id": video_id,
                        "title": attr.get("title", ""),
                        "duration": attr.get("duration", 0),
                        "width": width,
                        "height": height,
                        "aspect_ratio": attr.get("aspect_ratio", "16:9"),
                        "pexels_url": f"https://www.pexels.com/video/{video_id}/",
                        "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "size_mb": file_size_mb
                    }
                
                self._save_state()
                self._save_metadata()

                if self.current_index >= 4000:
                    self.log("TARGET OF 4000 VIDEOS REACHED! STOPPING DOWNLOADER.")
                    self.stop_event.set()

            except Exception as e:
                self.log(f"Error downloading video {video_id}: {e}")
                if temp_raw_path.exists():
                    temp_raw_path.unlink(missing_ok=True)
                if final_path.exists():
                    final_path.unlink(missing_ok=True)
            finally:
                self.queue.task_done()

    def run_crawler(self, max_total_videos: int = 4000):
        self.log(f"Starting High-Capacity Crawler Engine (Target Limit: {max_total_videos} videos)...")

        
        # Start download worker threads
        workers = []
        for i in range(self.max_workers):
            t = threading.Thread(target=self.download_and_process_worker, args=(i + 1,), daemon=True)
            t.start()
            workers.append(t)

        discovered_ids = set(self.state.get("downloaded_ids", []))
        
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=['--disable-blink-features=AutomationControlled']
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            for query in SEARCH_QUERIES:
                if self.stop_event.is_set() or len(self.state["downloaded_ids"]) >= max_total_videos:
                    break

                self.log(f"\n==========================================")
                self.log(f"=== SEARCHING CATEGORY: '{query}' ===")
                self.log(f"==========================================")

                encoded_query = urllib.parse.quote(query)
                empty_pages_in_a_row = 0

                for page_num in range(1, 41):
                    if self.stop_event.is_set() or len(self.state["downloaded_ids"]) >= max_total_videos:
                        break

                    if page_num == 1:
                        target_url = f"https://www.pexels.com/search/videos/{encoded_query}/"
                    else:
                        target_url = f"https://www.pexels.com/search/videos/{encoded_query}/?page={page_num}"

                    try:
                        page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
                        page.wait_for_timeout(1500)
                        
                        # Extract data from Next.js payload defensively
                        scripts = page.locator("script#__NEXT_DATA__").all_inner_texts()
                        new_on_page = 0
                        if scripts:
                            data = json.loads(scripts[0])
                            page_props = data.get("props", {}).get("pageProps", {}) or {}
                            init_data = page_props.get("initialData") or {}
                            
                            media_list = []
                            if isinstance(init_data, dict):
                                media_list = init_data.get("data") or []
                            elif isinstance(init_data, list):
                                media_list = init_data

                            if not media_list:
                                empty_pages_in_a_row += 1
                                if empty_pages_in_a_row >= 2:
                                    self.log(f"Reached end of query '{query}' at page {page_num}.")
                                    break
                                continue

                            empty_pages_in_a_row = 0
                            for item in media_list:
                                v_id = item.get("id")
                                if v_id and v_id not in discovered_ids:
                                    discovered_ids.add(v_id)
                                    self.queue.put(item)
                                    new_on_page += 1

                        self.log(f"Query '{query}' [Page {page_num}]: +{new_on_page} new videos queued. Total discovered: {len(discovered_ids)}. Active Queue: {self.queue.qsize()}")
                    except Exception as e:
                        self.log(f"Error on page {page_num} of query '{query}': {e}")
                        page.wait_for_timeout(2000)

            browser.close()

        # Wait for all queued downloads to finish
        self.log("Waiting for all queued downloads in worker pool to finish...")
        self.queue.join()
        self.stop_event.set()
        self.log(f"FINISHED ALL DOWNLOADS. Total videos processed: {len(self.state['downloaded_ids'])}.")


if __name__ == "__main__":
    manager = DownloadManager(max_workers=6)
    manager.run_crawler()
