import os
import re
import time
import sys
import threading
import webbrowser
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, jsonify, send_from_directory
import primp
import requests
from bs4 import BeautifulSoup

def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

app = Flask(__name__, 
            static_folder=get_resource_path('web'), 
            template_folder=get_resource_path('web'))

# Default save location
DEFAULT_SAVE_DIR = r"d:\detriot"

headers = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'accept-language': 'en-US,en;q=0.5',
    'referer': 'https://fitgirl-repacks.site/',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
}

class DownloadManager:
    def __init__(self):
        self.status = "idle"  # idle, downloading, stopped, completed
        self.parts = []       # list of part dicts
        self.links = []       # list of fuckingfast URLs
        self.threads = 5      # concurrent files (increased from 3)
        self.segments = 4     # parallel byte-range chunks per file (IDM-style)
        self.save_dir = DEFAULT_SAVE_DIR
        self.cancel_requested = False
        self.executor = None
        self.lock = threading.Lock()
        # Pre-resolved direct download URLs cache: fuckingfast_url -> direct_url
        self._resolved_cache = {}
        self._resolve_lock = threading.Lock()

    def get_links_file_path(self):
        return os.path.join(self.save_dir, "links.txt")

    def load_saved_links(self):
        links_file = self.get_links_file_path()
        if os.path.exists(links_file):
            try:
                with open(links_file, 'r', encoding='utf-8') as f:
                    links = [line.strip() for line in f if line.strip()]
                return links
            except Exception as e:
                print("Error reading links.txt:", e)
        return []

    def get_parts_from_links(self, links, directory=None):
        if directory:
            self.save_dir = directory
        
        parts_list = []
        for l in links:
            filename = l.split('#')[-1]
            if not filename or filename.startswith("https://"):
                filename = "unknown_part.rar"
            
            final_path = os.path.join(self.save_dir, filename)
            tmp_path = final_path + ".tmp"
            downloaded = 0
            status = 'pending'
            total_size = 0
            
            if os.path.exists(final_path):
                downloaded = os.path.getsize(final_path)
                total_size = downloaded
                status = 'completed'
            elif os.path.exists(tmp_path):
                downloaded = os.path.getsize(tmp_path)
                status = 'pending'

            parts_list.append({
                'filename': filename,
                'url': l,
                'status': status,
                'downloaded_bytes': downloaded,
                'total_bytes': total_size,
                'speed_mb': 0.0
            })
        return parts_list

    def reset_status_on_start(self, links, threads, directory):
        with self.lock:
            self.links = links
            self.threads = threads
            self.save_dir = directory
            self.cancel_requested = False
            self.status = "downloading"
            self.parts = self.get_parts_from_links(links, directory)

    def start_download_thread(self):
        t = threading.Thread(target=self._run_downloader, daemon=True)
        t.start()

    # ------------------------------------------------------------------ #
    #  Option 3: Pre-resolve ALL Cloudflare links upfront in background   #
    # ------------------------------------------------------------------ #
    def _preresolver_worker(self, part):
        """Resolves a fuckingfast URL to a direct CDN URL and caches it."""
        url = part['url']
        filename = part['filename']
        with self._resolve_lock:
            if url in self._resolved_cache:
                return  # already resolved
        try:
            print(f"[PRE-RESOLVE] {filename}")
            response = primp.get(url, headers=headers, timeout=20)
            if response.status_code != 200:
                return
            soup = BeautifulSoup(response.text, 'html.parser')
            download_btn = soup.find('a', class_='link-button')
            if not download_btn:
                return
            go_path = download_btn.get('hx-post')
            if not go_path:
                return
            go_url = urljoin(response.url, go_path)
            post_headers = {
                'accept': '*/*',
                'content-type': 'application/x-www-form-urlencoded',
                'hx-request': 'true',
                'hx-current-url': response.url,
                'origin': 'https://fuckingfast.co',
                'referer': response.url,
                'user-agent': headers['user-agent'],
            }
            go_response = primp.post(go_url, headers=post_headers, timeout=20)
            if go_response.status_code != 200:
                return
            direct_url = go_response.headers.get('HX-Redirect') or go_response.headers.get('hx-redirect')
            if direct_url:
                with self._resolve_lock:
                    self._resolved_cache[url] = direct_url
                print(f"[PRE-RESOLVE] ✓ {filename} cached")
        except Exception as e:
            print(f"[PRE-RESOLVE] Failed for {filename}: {e}")

    def _run_preresolver(self, pending_parts):
        """Launch background pre-resolution for all pending parts."""
        with ThreadPoolExecutor(max_workers=self.threads) as ex:
            for part in pending_parts:
                if part['status'] != 'completed':
                    ex.submit(self._preresolver_worker, part)

    def _run_downloader(self):
        print(f"Starting downloader: {self.threads} concurrent files, {self.segments} segments/file")
        
        pending = [p for p in self.parts if p['status'] != 'completed']
        
        # Option 3: Start pre-resolving all links immediately in background
        pre_t = threading.Thread(target=self._run_preresolver, args=(pending,), daemon=True)
        pre_t.start()

        self.executor = ThreadPoolExecutor(max_workers=self.threads)
        futures = []
        for part in self.parts:
            if part['status'] == 'completed':
                continue
            futures.append(self.executor.submit(self._download_worker, part))

        for fut in futures:
            try:
                fut.result()
            except Exception as e:
                print("Worker error:", e)

        self.executor.shutdown(wait=True)
        
        with self.lock:
            if self.cancel_requested:
                self.status = "stopped"
                print("Downloads stopped by user.")
            else:
                unfinished = [p for p in self.parts if p['status'] != 'completed']
                if not unfinished:
                    self.status = "completed"
                    print("All downloads finished!")
                else:
                    self.status = "idle"
                    print("Downloads finished but some parts are not completed.")

    # ------------------------------------------------------------------ #
    #  Option 1: Segmented (multi-chunk) download — IDM-style             #
    # ------------------------------------------------------------------ #
    def _download_segment(self, download_url, start_byte, end_byte, seg_path, seg_index, filename):
        """Download a single byte-range segment to a temp segment file."""
        seg_headers = dict(headers)
        seg_headers['Range'] = f'bytes={start_byte}-{end_byte}'
        try:
            r = requests.get(download_url, stream=True, headers=seg_headers, timeout=30)
            if r.status_code not in (200, 206):
                raise Exception(f"Segment {seg_index} got HTTP {r.status_code}")
            with open(seg_path, 'wb') as f:
                # Option 4: 1 MB write buffer (was 64 KB)
                for chunk in r.iter_content(chunk_size=1048576):
                    if self.cancel_requested:
                        return False
                    f.write(chunk)
            return True
        except Exception as e:
            print(f"[{filename}] Segment {seg_index} error: {e}")
            return False

    def _segmented_download(self, download_url, final_path, tmp_path, total_size, part, filename):
        """Split file into self.segments chunks, download each in parallel, then merge."""
        seg_size = total_size // self.segments
        segments_info = []
        for i in range(self.segments):
            start = i * seg_size
            end = (total_size - 1) if (i == self.segments - 1) else (start + seg_size - 1)
            seg_path = f"{tmp_path}.seg{i}"
            segments_info.append((i, start, end, seg_path))

        print(f"[{filename}] Segmented download: {self.segments} chunks × ~{seg_size // (1024*1024)} MB")

        # Track progress across all segments
        seg_progress = [0] * self.segments

        def download_and_track(seg_info):
            i, start, end, seg_path = seg_info
            seg_headers = dict(headers)
            seg_headers['Range'] = f'bytes={start}-{end}'
            try:
                r = requests.get(download_url, stream=True, headers=seg_headers, timeout=30)
                if r.status_code not in (200, 206):
                    raise Exception(f"Segment {i} got HTTP {r.status_code}")
                with open(seg_path, 'wb') as f:
                    # Option 4: 1 MB buffer
                    for chunk in r.iter_content(chunk_size=1048576):
                        if self.cancel_requested:
                            return False
                        f.write(chunk)
                        seg_progress[i] += len(chunk)
                        # Update overall progress
                        part['downloaded_bytes'] = sum(seg_progress)
                return True
            except Exception as e:
                print(f"[{filename}] Segment {i} failed: {e}")
                return False

        # Speed tracking thread
        def speed_tracker():
            last_bytes = part['downloaded_bytes']
            while not self.cancel_requested and part['status'] == 'downloading':
                time.sleep(1.0)
                now_bytes = part['downloaded_bytes']
                part['speed_mb'] = (now_bytes - last_bytes) / (1024 * 1024)
                last_bytes = now_bytes

        speed_t = threading.Thread(target=speed_tracker, daemon=True)
        speed_t.start()

        # Download all segments in parallel
        success = True
        with ThreadPoolExecutor(max_workers=self.segments) as seg_executor:
            results = list(seg_executor.map(download_and_track, segments_info))
            if not all(results):
                success = False

        part['speed_mb'] = 0.0

        if self.cancel_requested or not success:
            # Clean up segment files
            for _, _, _, seg_path in segments_info:
                if os.path.exists(seg_path):
                    os.remove(seg_path)
            return False

        # Merge all segments into the final file
        print(f"[{filename}] Merging {self.segments} segments...")
        with open(tmp_path, 'wb') as out:
            for i, start, end, seg_path in segments_info:
                with open(seg_path, 'rb') as seg_f:
                    # Option 4: 1 MB merge buffer
                    while True:
                        chunk = seg_f.read(1048576)
                        if not chunk:
                            break
                        out.write(chunk)
                os.remove(seg_path)

        return True

    def _download_worker(self, part):
        filename = part['filename']
        url = part['url']
        
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            if self.cancel_requested:
                part['status'] = 'pending'
                part['speed_mb'] = 0.0
                return

            try:
                part['status'] = 'downloading'
                
                # Check pre-resolved cache first (Option 3)
                with self._resolve_lock:
                    download_url = self._resolved_cache.get(url)
                
                if download_url:
                    print(f"[{filename}] Using pre-resolved URL (cache hit)")
                else:
                    print(f"[{filename}] Attempt {attempt}: Resolving page link...")
                    response = primp.get(url, headers=headers, timeout=20)
                    if response.status_code != 200:
                        raise Exception(f"Failed to get landing page: status {response.status_code}")
                    
                    soup = BeautifulSoup(response.text, 'html.parser')
                    download_btn = soup.find('a', class_='link-button')
                    if not download_btn:
                        raise Exception("Download button not found in page DOM")
                    
                    # Check meta title to resolve filename if it was initialized as unknown
                    if filename == "unknown_part.rar":
                        meta_title = soup.find('meta', attrs={'name': 'title'})
                        if meta_title and meta_title['content']:
                            filename = meta_title['content']
                            part['filename'] = filename

                    go_path = download_btn.get('hx-post')
                    if not go_path:
                        raise Exception("hx-post attribute missing in download button")
                    
                    go_url = urljoin(response.url, go_path)

                    post_headers = {
                        'accept': '*/*',
                        'content-type': 'application/x-www-form-urlencoded',
                        'hx-request': 'true',
                        'hx-current-url': response.url,
                        'origin': 'https://fuckingfast.co',
                        'referer': response.url,
                        'user-agent': headers['user-agent'],
                    }
                    go_response = primp.post(go_url, headers=post_headers, timeout=20)
                    if go_response.status_code != 200:
                        raise Exception(f"Failed to POST for direct link: status {go_response.status_code}")
                    
                    download_url = go_response.headers.get('HX-Redirect') or go_response.headers.get('hx-redirect')
                    if not download_url:
                        raise Exception("HX-Redirect header missing from POST response")
                    
                    # Cache for future use
                    with self._resolve_lock:
                        self._resolved_cache[url] = download_url

                final_path = os.path.join(self.save_dir, filename)
                tmp_path = final_path + ".tmp"

                # Get file size from server
                try:
                    head_res = requests.head(download_url, headers=headers, timeout=15, allow_redirects=True)
                    total_size = int(head_res.headers.get('content-length', 0))
                    accepts_ranges = head_res.headers.get('accept-ranges', '').lower() == 'bytes'
                except Exception as he:
                    print(f"[{filename}] Warning: HEAD request failed: {he}")
                    total_size = 0
                    accepts_ranges = False

                if total_size > 0:
                    part['total_bytes'] = total_size

                # Check if already complete
                if os.path.exists(final_path):
                    if total_size > 0 and os.path.getsize(final_path) == total_size:
                        print(f"[{filename}] Already downloaded, skipping.")
                        part['downloaded_bytes'] = total_size
                        part['status'] = 'completed'
                        part['speed_mb'] = 0.0
                        return
                    elif total_size == 0:
                        part['status'] = 'completed'
                        part['speed_mb'] = 0.0
                        return

                os.makedirs(self.save_dir, exist_ok=True)

                # -------------------------------------------------------- #
                # Option 1: Use segmented download if server supports ranges #
                # and we know the total size (minimum 10 MB to be worthwhile)#
                # -------------------------------------------------------- #
                if accepts_ranges and total_size >= 10 * 1024 * 1024:
                    # Clean any leftover .tmp before segmented download
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                    part['downloaded_bytes'] = 0
                    
                    success = self._segmented_download(
                        download_url, final_path, tmp_path, total_size, part, filename
                    )
                    
                    if not success:
                        if self.cancel_requested:
                            part['status'] = 'pending'
                            part['speed_mb'] = 0.0
                            return
                        raise Exception("Segmented download failed, will retry")
                else:
                    # Fallback: single-stream download with resume support
                    current_size = 0
                    if os.path.exists(tmp_path):
                        current_size = os.path.getsize(tmp_path)
                        if total_size > 0 and current_size >= total_size:
                            os.remove(tmp_path)
                            current_size = 0

                    download_headers = dict(headers)
                    open_mode = 'wb'
                    downloaded = 0

                    if current_size > 0:
                        download_headers['Range'] = f'bytes={current_size}-'
                        open_mode = 'ab'
                        downloaded = current_size
                        print(f"[{filename}] Resuming single-stream from {current_size} bytes...")

                    part_res = requests.get(download_url, stream=True, headers=download_headers, timeout=30)
                    
                    if current_size > 0 and part_res.status_code != 206:
                        open_mode = 'wb'
                        downloaded = 0

                    last_time = time.time()
                    last_bytes = downloaded

                    with open(tmp_path, open_mode) as f:
                        # Option 4: 1 MB buffer (was 64 KB)
                        for chunk in part_res.iter_content(chunk_size=1048576):
                            if self.cancel_requested:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            part['downloaded_bytes'] = downloaded

                            now = time.time()
                            if now - last_time >= 1.0:
                                elapsed = now - last_time
                                speed = (downloaded - last_bytes) / elapsed
                                part['speed_mb'] = speed / (1024 * 1024)
                                last_bytes = downloaded
                                last_time = now

                    part_res.close()

                    if self.cancel_requested:
                        part['status'] = 'pending'
                        part['speed_mb'] = 0.0
                        return

                if os.path.exists(tmp_path):
                    os.rename(tmp_path, final_path)
                part['status'] = 'completed'
                part['speed_mb'] = 0.0
                print(f"[{filename}] Finished downloading successfully!")
                return

            except Exception as e:
                print(f"[{filename}] Attempt {attempt} failed: {e}")
                part['speed_mb'] = 0.0
                # Invalidate cached URL on failure so next attempt re-resolves
                with self._resolve_lock:
                    self._resolved_cache.pop(url, None)
                if attempt == max_retries:
                    part['status'] = 'failed'
                else:
                    time.sleep(2)

    def stop_downloads(self):
        with self.lock:
            self.cancel_requested = True
        print("Cancel requested for all active threads...")


manager = DownloadManager()



# Helper functions for scraping
def get_slug_from_filename(filename):
    name = re.sub(r'\.part\d+\.rar$', '', filename, flags=re.IGNORECASE)
    name = re.sub(r'fitgirl-repacks\.site', '', name, flags=re.IGNORECASE)
    name = name.replace('–', ' ').replace('-', ' ').replace('_', ' ').replace('.', ' ')
    name = ' '.join(name.split())
    slug = name.lower().replace(' ', '-')
    return slug

def search_fitgirl_links(query):
    # 1. Try slug directly
    slug = query.lower().replace(' ', '-').replace('_', '-')
    direct_url = f"https://fitgirl-repacks.site/{slug}/"
    print(f"Trying direct FitGirl URL: {direct_url}")
    try:
        r = primp.get(direct_url, headers=headers, timeout=15)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print("Direct slug fetch failed:", e)

    # 2. Search on FitGirl
    search_url = f"https://fitgirl-repacks.site/?s={query.replace(' ', '+')}"
    print(f"Searching FitGirl: {search_url}")
    try:
        r = primp.get(search_url, headers=headers, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            first_article = soup.find('article')
            if first_article:
                title_el = first_article.find(class_='entry-title')
                if title_el:
                    first_link = title_el.find('a', href=True)
                    if first_link:
                        target_url = first_link['href']
                        print(f"Found post URL from search: {target_url}")
                        r_target = primp.get(target_url, headers=headers, timeout=15)
                        if r_target.status_code == 200:
                            return r_target.text
    except Exception as e:
        print("FitGirl search failed:", e)
    return None

# Routes
@app.route('/')
def index():
    return send_from_directory('web', 'index.html')

@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('web', path)

@app.route('/api/status', methods=['GET'])
def get_status():
    parts_data = []
    overall_speed = 0.0
    overall_downloaded = 0
    overall_total = 0
    completed_count = 0

    with manager.lock:
        for p in manager.parts:
            parts_data.append({
                'filename': p['filename'],
                'status': p['status'],
                'downloaded_bytes': p['downloaded_bytes'],
                'total_bytes': p['total_bytes'],
                'speed_mb': p['speed_mb']
            })
            if p['status'] == 'downloading':
                overall_speed += p['speed_mb']
            if p['status'] == 'completed':
                completed_count += 1

            # Accumulate bytes for all files with a known size
            if p['total_bytes'] > 0:
                overall_downloaded += p['downloaded_bytes']
                overall_total += p['total_bytes']
            elif p['status'] == 'completed':
                overall_downloaded += p['downloaded_bytes']
                overall_total += p['downloaded_bytes']

        current_save_dir = manager.save_dir

    total_count = len(parts_data)

    # ------------------------------------------------------------------ #
    # Progress %: use file-count ratio — always correct regardless of     #
    # whether individual file sizes are known yet                         #
    # ------------------------------------------------------------------ #
    progress_percent = 0.0
    if total_count > 0:
        progress_percent = min(100.0, round((completed_count / total_count) * 100, 1))

    # ------------------------------------------------------------------ #
    # ETA: estimate remaining bytes for incomplete files                  #
    # Use known total_bytes where available; fall back to average size    #
    # ------------------------------------------------------------------ #
    eta_seconds = None
    if overall_speed > 0 and total_count > 0:
        # Average file size from files we know about
        known_count = sum(1 for p in parts_data if p['total_bytes'] > 0)
        avg_size = (overall_total / known_count) if known_count > 0 else 500 * 1024 * 1024

        remaining_bytes = 0
        for p in parts_data:
            if p['status'] == 'completed':
                continue
            if p['total_bytes'] > 0:
                remaining_bytes += max(0, p['total_bytes'] - p['downloaded_bytes'])
            else:
                # Unknown size: use average estimate
                remaining_bytes += avg_size

        if remaining_bytes > 0:
            eta_seconds = remaining_bytes / (overall_speed * 1024 * 1024)

    return jsonify({
        'status': manager.status,
        'overall_speed_mb': overall_speed,
        'overall_progress_percent': progress_percent,
        'overall_downloaded_gb': overall_downloaded / (1024 * 1024 * 1024),
        'overall_total_gb': overall_total / (1024 * 1024 * 1024),
        'eta_seconds': eta_seconds,
        'completed_count': completed_count,
        'total_count': total_count,
        'parts': parts_data,
        'saved_links': manager.load_saved_links(),
        'save_dir': current_save_dir
    })


@app.route('/api/analyze', methods=['POST'])
def analyze_links():
    data = request.json or {}
    text = data.get('text', '')
    directory = data.get('directory', manager.save_dir).strip()
    
    # Parse URLs matching fuckingfast.co (universal pattern matching any game name/fragment)
    pattern_url = r'https://fuckingfast\.co/[a-zA-Z0-9]+(?:#[^\s"\'>]*)?'
    links = re.findall(pattern_url, text)
    
    # If no FuckingFast links, check if there are plain filenames
    if not links:
        filename_pattern = r'[a-zA-Z0-9_–\.\-]+?\.part\d+\.rar'
        filenames = re.findall(filename_pattern, text)
        if filenames:
            print(f"Detected plain filenames. Resolving FuckingFast links online...")
            first_filename = filenames[0]
            
            # Extract game name from filename
            raw_game_name = re.sub(r'[\.–_]+fitgirl-repacks.*', '', first_filename, flags=re.IGNORECASE)
            raw_game_name = raw_game_name.replace('_', ' ').replace('-', ' ').replace('–', ' ').strip()
            print(f"Extracted game name query: '{raw_game_name}'")
            
            slug = get_slug_from_filename(first_filename)
            print(f"Extracted slug: '{slug}'")

            # Try loading/searching FitGirl repack page
            page_html = search_fitgirl_links(slug)
            if not page_html and raw_game_name:
                page_html = search_fitgirl_links(raw_game_name)

            if page_html:
                soup = BeautifulSoup(page_html, 'html.parser')
                page_links = []
                for dlinks_div in soup.find_all("div", class_="dlinks"):
                    for a in dlinks_div.find_all("a", href=True):
                        href = a["href"]
                        if href.startswith("https://fuckingfast.co/"):
                            page_links.append((href, a.get_text()))
                
                # Match links to the filenames by checking if part number matches
                matched_links = []
                for fname in filenames:
                    part_match = re.search(r'\.part(\d+)\.rar', fname, flags=re.IGNORECASE)
                    if part_match:
                        part_num = part_match.group(1)
                        part_num_int = int(part_num)
                        
                        # Match by integer value of part number to handle diff digit lengths (e.g. 7 and 007)
                        for href, anchor_text in page_links:
                            href_match = re.search(r'\.part(\d+)\.rar', href, flags=re.IGNORECASE)
                            anchor_match = re.search(r'\.part(\d+)\.rar', anchor_text, flags=re.IGNORECASE)
                            
                            href_num = int(href_match.group(1)) if href_match else -1
                            anchor_num = int(anchor_match.group(1)) if anchor_match else -1
                            
                            if part_num_int == href_num or part_num_int == anchor_num:
                                matched_links.append(href)
                                break
                
                links = matched_links
                print(f"Resolved {len(links)} links online!")

    # Decode formatting and remove duplicates preserving order
    seen = set()
    unique_links = []
    for link in links:
        clean = link.replace("&#8211;", "--")
        if clean not in seen:
            seen.add(clean)
            unique_links.append(clean)

    # Sort links by part number
    def get_part_num(link):
        match = re.search(r'\.part(\d+)\.rar', link, flags=re.IGNORECASE)
        return int(match.group(1)) if match else 99999

    unique_links.sort(key=get_part_num)
    valid_links = unique_links

    with manager.lock:
        manager.save_dir = directory
        manager.links = valid_links

    # Save to links.txt on disk under the save directory
    os.makedirs(directory, exist_ok=True)
    links_file = manager.get_links_file_path()
    try:
        with open(links_file, 'w', encoding='utf-8') as f:
            for l in valid_links:
                f.write(l + "\n")
    except Exception as e:
        print("Failed to save links.txt to disk:", e)

    # Get parsed parts status list
    parts = manager.get_parts_from_links(valid_links, directory)
    
    with manager.lock:
        manager.parts = parts

    return jsonify({
        'success': True,
        'links': valid_links,
        'parts': parts
    })

@app.route('/api/start', methods=['POST'])
def start_downloads():
    data = request.json or {}
    links = data.get('links', [])
    threads = int(data.get('threads', 3))
    directory = data.get('directory', manager.save_dir).strip()
    
    if not links:
        return jsonify({'success': False, 'error': 'No links provided'})

    if manager.status == 'downloading':
        return jsonify({'success': False, 'error': 'Download already in progress'})

    manager.reset_status_on_start(links, threads, directory)
    manager.start_download_thread()

    return jsonify({'success': True})

@app.route('/api/stop', methods=['POST'])
def stop_downloads():
    manager.stop_downloads()
    return jsonify({'success': True})

@app.route('/api/retry_part', methods=['POST'])
def retry_part():
    data = request.json or {}
    filename = data.get('filename', '')
    if not filename:
        return jsonify({'success': False, 'error': 'No filename provided'})

    with manager.lock:
        part = next((p for p in manager.parts if p['filename'] == filename), None)
        if not part:
            return jsonify({'success': False, 'error': 'Part not found'})
        
        if part['status'] == 'downloading':
            return jsonify({'success': False, 'error': 'Part is already downloading'})

        part['status'] = 'pending'
        part['speed_mb'] = 0.0
        
        if manager.status in ['idle', 'stopped', 'completed']:
            manager.status = 'downloading'
            manager.cancel_requested = False

    t = threading.Thread(target=manager._download_worker, args=(part,), daemon=True)
    t.start()

    return jsonify({'success': True})

def open_browser():
    time.sleep(1.5)
    try:
        webbrowser.open("http://127.0.0.1:5000")
    except Exception as e:
        print("Failed to auto-open browser:", e)

if __name__ == '__main__':
    os.makedirs(DEFAULT_SAVE_DIR, exist_ok=True)
    
    saved = manager.load_saved_links()
    if saved:
        manager.links = saved
        manager.parts = manager.get_parts_from_links(saved)
        
    # Start auto-open browser thread
    threading.Thread(target=open_browser, daemon=True).start()
    
    app.run(host='0.0.0.0', port=5000, debug=False)
