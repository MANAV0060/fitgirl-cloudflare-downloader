import os
import re
import time
import sys
import threading
import subprocess
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

    @staticmethod
    def _infer_total_size_from_response(response, current_size=0):
        """Infer full file size from response headers when HEAD did not provide it."""
        content_range = response.headers.get('content-range', '')
        range_match = re.search(r'/(\d+)\s*$', content_range)
        if range_match:
            return int(range_match.group(1))

        content_length = response.headers.get('content-length')
        if content_length and content_length.isdigit():
            length = int(content_length)
            if response.status_code == 206:
                return current_size + length
            return length

        return 0

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
    #  JIT Direct Link Resolution & Robust Range-Resume Engine           #
    # ------------------------------------------------------------------ #
    def _resolve_direct_url(self, page_url):
        """Fetch fresh direct CDN download URL from fuckingfast landing page using primp."""
        client = primp.Client(impersonate="chrome_131")
        res = client.get(page_url, headers=headers, timeout=25)
        if res.status_code != 200:
            raise Exception(f"Landing page returned HTTP {res.status_code}")

        soup = BeautifulSoup(res.text, 'html.parser')
        btn = soup.find('a', class_='link-button')
        if not btn:
            raise Exception("Download button missing in DOM (Cloudflare check or rate-limited)")

        go_path = btn.get('hx-post')
        if not go_path:
            raise Exception("hx-post attribute missing in download button")

        go_url = urljoin(res.url, go_path)
        post_headers = {
            'accept': '*/*',
            'content-type': 'application/x-www-form-urlencoded',
            'hx-request': 'true',
            'hx-current-url': res.url,
            'origin': 'https://fuckingfast.co',
            'referer': res.url,
            'user-agent': headers['user-agent'],
        }
        go_res = client.post(go_url, headers=post_headers, timeout=25)
        if go_res.status_code != 200:
            raise Exception(f"POST /go failed with HTTP {go_res.status_code}")

        dl_url = go_res.headers.get('HX-Redirect') or go_res.headers.get('hx-redirect')
        if not dl_url:
            raise Exception("HX-Redirect header missing from POST response")

        resolved_name = None
        meta_title = soup.find('meta', attrs={'name': 'title'})
        if meta_title and meta_title.get('content'):
            resolved_name = meta_title['content'].strip()

        return dl_url, resolved_name

    def _run_downloader(self):
        print(f"Starting downloader: {self.threads} concurrent file downloads")
        
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

    def _download_worker(self, part):
        filename = part['filename']
        url = part['url']
        
        max_retries = 8
        for attempt in range(1, max_retries + 1):
            if self.cancel_requested:
                part['status'] = 'pending'
                part['speed_mb'] = 0.0
                return

            try:
                part['status'] = 'downloading'
                
                # Check cached direct URL first
                download_url = None
                with self._resolve_lock:
                    download_url = self._resolved_cache.get(url)
                
                if not download_url:
                    print(f"[{filename}] Attempt {attempt}/{max_retries}: Resolving direct link...")
                    download_url, resolved_name = self._resolve_direct_url(url)
                    if resolved_name and filename == "unknown_part.rar":
                        filename = resolved_name
                        part['filename'] = filename
                    with self._resolve_lock:
                        self._resolved_cache[url] = download_url
                    time.sleep(1.0)
                else:
                    print(f"[{filename}] Using cached direct URL")

                os.makedirs(self.save_dir, exist_ok=True)
                final_path = os.path.join(self.save_dir, filename)
                tmp_path = final_path + ".tmp"

                # Check if already fully downloaded
                if os.path.exists(final_path):
                    f_size = os.path.getsize(final_path)
                    part['downloaded_bytes'] = f_size
                    part['total_bytes'] = f_size
                    part['status'] = 'completed'
                    part['speed_mb'] = 0.0
                    return

                current_size = 0
                if os.path.exists(tmp_path):
                    current_size = os.path.getsize(tmp_path)

                download_headers = dict(headers)
                open_mode = 'wb'
                downloaded = 0

                if current_size > 0:
                    download_headers['Range'] = f'bytes={current_size}-'
                    open_mode = 'ab'
                    downloaded = current_size
                    part['downloaded_bytes'] = current_size
                    print(f"[{filename}] Resuming from {current_size / (1024*1024):.2f} MB...")

                part_res = requests.get(download_url, stream=True, headers=download_headers, timeout=60)
                
                # Handle expired CDN tokens or unauthorized
                if part_res.status_code in (401, 403, 404, 410):
                    with self._resolve_lock:
                        self._resolved_cache.pop(url, None)
                    raise Exception(f"CDN token expired (HTTP {part_res.status_code}), re-resolving...")

                if part_res.status_code == 416:
                    # Range not satisfiable - check if file size matches
                    total_s = part.get('total_bytes', 0)
                    if total_s > 0 and current_size >= total_s:
                        if os.path.exists(final_path):
                            os.remove(final_path)
                        os.rename(tmp_path, final_path)
                        part['status'] = 'completed'
                        part['speed_mb'] = 0.0
                        return
                    raise Exception("HTTP 416 Range Not Satisfiable")

                if part_res.status_code not in (200, 206):
                    raise Exception(f"Download stream returned HTTP {part_res.status_code}")

                if current_size > 0 and part_res.status_code == 200:
                    print(f"[{filename}] Server returned 200 (ignored Range), restarting from 0")
                    open_mode = 'wb'
                    downloaded = 0
                    part['downloaded_bytes'] = 0

                total_size = part.get('total_bytes', 0)
                if total_size <= 0:
                    inferred_total = self._infer_total_size_from_response(part_res, downloaded)
                    if inferred_total > 0:
                        total_size = inferred_total
                        part['total_bytes'] = total_size

                # Check if already completed
                if total_size > 0 and downloaded >= total_size:
                    if os.path.exists(tmp_path):
                        if os.path.exists(final_path):
                            os.remove(final_path)
                        os.rename(tmp_path, final_path)
                    part['status'] = 'completed'
                    part['speed_mb'] = 0.0
                    return

                last_time = time.time()
                last_bytes = downloaded

                with open(tmp_path, open_mode) as f:
                    for chunk in part_res.iter_content(chunk_size=1048576):
                        if self.cancel_requested:
                            break
                        if chunk:
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

                # Incomplete transfer check
                if total_size > 0 and downloaded < total_size:
                    raise Exception(f"Connection closed prematurely: got {downloaded}/{total_size} bytes")

                # Rename tmp to final completed file
                if os.path.exists(tmp_path):
                    if os.path.exists(final_path):
                        os.remove(final_path)
                    os.rename(tmp_path, final_path)

                part['downloaded_bytes'] = downloaded
                part['total_bytes'] = total_size if total_size > 0 else downloaded
                part['status'] = 'completed'
                part['speed_mb'] = 0.0
                print(f"[{filename}] Finished downloading successfully!")
                return

            except Exception as e:
                print(f"[{filename}] Attempt {attempt}/{max_retries} failed: {e}")
                part['speed_mb'] = 0.0
                with self._resolve_lock:
                    self._resolved_cache.pop(url, None)
                if attempt >= max_retries:
                    part['status'] = 'failed'
                else:
                    sleep_sec = min(20, 2 ** min(attempt, 4))
                    print(f"[{filename}] Waiting {sleep_sec}s before retry...")
                    time.sleep(sleep_sec)

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

        with manager._resolve_lock:
            manager._resolved_cache.pop(part['url'], None)
        
        if manager.status in ['idle', 'stopped', 'completed']:
            manager.status = 'downloading'
            manager.cancel_requested = False

    t = threading.Thread(target=manager._download_worker, args=(part,), daemon=True)
    t.start()

    return jsonify({'success': True})

@app.route('/api/browse_dir', methods=['POST'])
def browse_dir():
    data = request.json or {}
    current = data.get('current', '').strip()
    if not current or not os.path.isdir(current):
        current = manager.save_dir if os.path.isdir(manager.save_dir) else ""

    try:
        script = (
            "import sys, os, tkinter as tk\n"
            "from tkinter import filedialog\n"
            "root = tk.Tk()\n"
            "root.withdraw()\n"
            "root.wm_attributes('-topmost', 1)\n"
            "init_dir = sys.argv[1] if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]) else None\n"
            "chosen = filedialog.askdirectory(title='Select Save Directory', initialdir=init_dir)\n"
            "root.destroy()\n"
            "if chosen:\n"
            "    sys.stdout.write(os.path.normpath(chosen))\n"
        )
        res = subprocess.run([sys.executable, "-c", script, current], capture_output=True, text=True, timeout=120)
        selected = res.stdout.strip()
        if selected:
            norm = os.path.normpath(selected)
            manager.save_dir = norm
            return jsonify({'success': True, 'directory': norm})
        return jsonify({'success': False, 'cancelled': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

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
