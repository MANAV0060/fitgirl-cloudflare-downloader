import os
import re
import time
import threading
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, jsonify, send_from_directory
import primp
import requests
from bs4 import BeautifulSoup

# Detect Vercel environment
is_vercel = os.environ.get('VERCEL') == '1' or 'VERCEL' in os.environ

if is_vercel:
    app = Flask(__name__, static_folder='../web', template_folder='../web')
else:
    app = Flask(__name__, static_folder='web', template_folder='web')

# Default save location for local execution
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
        self.threads = 3
        self.save_dir = DEFAULT_SAVE_DIR
        self.cancel_requested = False
        self.executor = None
        self.lock = threading.Lock()

    def get_links_file_path(self):
        return os.path.join(self.save_dir, "links.txt")

    def load_saved_links(self):
        if is_vercel:
            return []  # No file access on Vercel
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
        if is_vercel:
            # On Vercel, just resolve links and return status
            parts_list = []
            for l in links:
                filename = l.split('#')[-1]
                parts_list.append({
                    'filename': filename,
                    'url': l,
                    'status': 'pending',
                    'downloaded_bytes': 0,
                    'total_bytes': 0,
                    'speed_mb': 0.0
                })
            return parts_list

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
        if is_vercel:
            return
        with self.lock:
            self.links = links
            self.threads = threads
            self.save_dir = directory
            self.cancel_requested = False
            self.status = "downloading"
            self.parts = self.get_parts_from_links(links, directory)

    def start_download_thread(self):
        if is_vercel:
            return
        t = threading.Thread(target=self._run_downloader, daemon=True)
        t.start()

    def _run_downloader(self):
        print(f"Starting ThreadPoolExecutor with {self.threads} threads...")
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
        
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            if self.cancel_requested:
                part['status'] = 'pending'
                part['speed_mb'] = 0.0
                return

            try:
                part['status'] = 'downloading'
                print(f"[{filename}] Attempt {attempt}: Resolving page link...")
                
                # Step 1: GET FuckingFast page
                response = primp.get(url, headers=headers, timeout=20)
                if response.status_code != 200:
                    raise Exception(f"Failed to get landing page: status {response.status_code}")
                
                soup = BeautifulSoup(response.text, 'html.parser')
                download_btn = soup.find('a', class_='link-button')
                if not download_btn:
                    raise Exception("Download button not found in page DOM")
                
                go_path = download_btn.get('hx-post')
                if not go_path:
                    raise Exception("hx-post attribute missing in download button")
                
                go_url = urljoin(response.url, go_path)

                # Step 2: POST to retrieve direct download link
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

                # Step 3: Stream download
                final_path = os.path.join(self.save_dir, filename)
                tmp_path = final_path + ".tmp"

                try:
                    head_res = requests.get(download_url, stream=True, headers=headers, timeout=15)
                    total_size = int(head_res.headers.get('content-length', 0))
                    head_res.close()
                except Exception as he:
                    print(f"[{filename}] Warning: Failed to fetch head content-length: {he}")
                    total_size = 0

                if total_size > 0:
                    part['total_bytes'] = total_size

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
                    print(f"[{filename}] Resuming download from position {current_size} bytes...")

                part_res = requests.get(download_url, stream=True, headers=download_headers, timeout=30)
                
                if current_size > 0 and part_res.status_code != 206:
                    print(f"[{filename}] Range request returned status {part_res.status_code}. Starting from scratch.")
                    open_mode = 'wb'
                    downloaded = 0

                os.makedirs(self.save_dir, exist_ok=True)

                last_time = time.time()
                last_bytes = downloaded

                with open(tmp_path, open_mode) as f:
                    for chunk in part_res.iter_content(chunk_size=65536):
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
                if attempt == max_retries:
                    part['status'] = 'failed'
                else:
                    time.sleep(2)

    def stop_downloads(self):
        if is_vercel:
            return
        with self.lock:
            self.cancel_requested = True

manager = DownloadManager()

# Helper function to resolve a single link concurrently for Vercel mode
def resolve_single_link(link):
    filename = link.split('#')[-1]
    if not filename or filename.startswith("https://"):
        filename = "unknown_part.rar"
    try:
        # Step 1: GET FuckingFast page
        response = primp.get(link, headers=headers, timeout=10)
        if response.status_code != 200:
            raise Exception(f"Landing page status {response.status_code}")
            
        soup = BeautifulSoup(response.text, 'html.parser')
        download_btn = soup.find('a', class_='link-button')
        if not download_btn:
            raise Exception("Download button missing")
            
        go_path = download_btn.get('hx-post')
        if not go_path:
            raise Exception("hx-post missing")
            
        go_url = urljoin(response.url, go_path)

        # Step 2: POST for direct link
        post_headers = {
            'accept': '*/*',
            'content-type': 'application/x-www-form-urlencoded',
            'hx-request': 'true',
            'hx-current-url': response.url,
            'origin': 'https://fuckingfast.co',
            'referer': response.url,
            'user-agent': headers['user-agent'],
        }
        go_response = primp.post(go_url, headers=post_headers, timeout=10)
        if go_response.status_code != 200:
            raise Exception(f"POST status {go_response.status_code}")
            
        download_url = go_response.headers.get('HX-Redirect') or go_response.headers.get('hx-redirect')
        if not download_url:
            raise Exception("HX-Redirect header missing")
            
        return {
            'filename': filename,
            'url': link,
            'direct_url': download_url,
            'status': 'completed',
            'downloaded_bytes': 100,  # Show mock 100% complete resolved state
            'total_bytes': 100,
            'speed_mb': 0.0
        }
    except Exception as e:
        print(f"Failed to resolve {link}: {e}")
        return {
            'filename': filename,
            'url': link,
            'direct_url': None,
            'status': 'failed',
            'downloaded_bytes': 0,
            'total_bytes': 0,
            'speed_mb': 0.0,
            'error': str(e)
        }

# Routes
@app.route('/')
def index():
    folder = '../web' if is_vercel else 'web'
    return send_from_directory(folder, 'index.html')

@app.route('/static/<path:path>')
def serve_static(path):
    folder = '../web' if is_vercel else 'web'
    return send_from_directory(folder, path)

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
                'speed_mb': p['speed_mb'],
                'direct_url': p.get('direct_url')
            })
            if p['status'] == 'downloading':
                overall_speed += p['speed_mb']
            if p['status'] == 'completed':
                completed_count += 1
            
            overall_downloaded += p['downloaded_bytes']
            overall_total += p['total_bytes']
        
        current_save_dir = manager.save_dir

    total_count = len(parts_data)
    progress_percent = 0
    if overall_total > 0:
        progress_percent = round((overall_downloaded / overall_total) * 100, 1)

    eta_seconds = None
    if overall_speed > 0:
        bytes_left = overall_total - overall_downloaded
        if bytes_left > 0:
            eta_seconds = bytes_left / (overall_speed * 1024 * 1024)

    return jsonify({
        'status': manager.status,
        'is_vercel': is_vercel,
        'overall_speed_mb': overall_speed,
        'overall_progress_percent': progress_percent,
        'overall_downloaded_gb': overall_downloaded / (1024 * 1024 * 1024),
        'overall_total_gb': overall_total / (1024 * 1024 * 1024),
        'eta_seconds': eta_seconds,
        'completed_count': completed_count,
        'total_count': total_count,
        'parts': parts_data,
        'saved_links': [] if is_vercel else manager.load_saved_links(),
        'save_dir': current_save_dir
    })

@app.route('/api/analyze', methods=['POST'])
def analyze_links():
    data = request.json or {}
    text = data.get('text', '')
    directory = data.get('directory', manager.save_dir).strip()
    
    # Parse URLs matching fuckingfast.co
    pattern = r'https://fuckingfast\.co/[a-zA-Z0-9]+#Detroit_Become_Human_[^\s"\'>]+'
    links = re.findall(pattern, text)
    
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
        match = re.search(r'\.part(\d+)\.rar', link)
        return int(match.group(1)) if match else 999

    unique_links.sort(key=get_part_num)
    valid_links = [l for l in unique_links if 1 <= get_part_num(l) <= 45]

    # If Vercel: Resolve all links concurrently in parallel
    if is_vercel:
        print("Vercel Mode: Resolving all links in parallel...")
        with ThreadPoolExecutor(max_workers=20) as exec:
            resolved_parts = list(exec.map(resolve_single_link, valid_links))
        
        with manager.lock:
            manager.links = valid_links
            manager.parts = resolved_parts
            manager.status = "completed"

        return jsonify({
            'success': True,
            'links': valid_links,
            'parts': resolved_parts
        })

    # Local Mode
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
    if is_vercel:
        return jsonify({'success': False, 'error': 'Background downloads not supported on Vercel. Please copy resolved direct links to IDM.'})

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
    if is_vercel:
         return jsonify({'success': True})
    manager.stop_downloads()
    return jsonify({'success': True})

if __name__ == '__main__':
    # Local fallback startup
    os.makedirs(DEFAULT_SAVE_DIR, exist_ok=True)
    saved = manager.load_saved_links()
    if saved:
        manager.links = saved
        manager.parts = manager.get_parts_from_links(saved)
    app.run(host='0.0.0.0', port=5000, debug=False)
