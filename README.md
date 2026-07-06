# Universal Downloader Fit

A premium, local web-based download manager designed to bypass Cloudflare protection and stream high-speed multi-part downloads from `fuckingfast.co` for any FitGirl game repack.

## 🚀 Features
- **Universal Game Repack Resolver:** Paste plain text filenames (e.g. from IDM or FitGirl spoilers), and the app will automatically query the FitGirl Repack search engine, find the correct game page, and map the corresponding FuckingFast download links.
- **Failed Part Retry:** Individual card controls that allow you to retry only the failed parts on the fly without interrupting the rest of the active downloads.
- **HTTP Range Resume:** Support for resuming partial downloads. If you stop the download or lose connection, it resumes exactly where it left off on the `.tmp` files.
- **Custom Save Directories:** Choose exactly where to download the files on your computer. The server will automatically create the folders and stream files directly to disk.
- **Concurrent Download Threads:** Dynamically configure how many parts download in parallel (default is 3, recommended maximum of 8-10 to prevent IP temporary bans).
- **Clean Space-Dark Glassmorphic UI:** A premium, real-time updated dashboard using Outfit typography, HSL tailored color schemes, and active pulse indicators.

---

## 🛠️ How to Run Locally

### Prerequisites
Make sure you have [Python 3.x](https://www.python.org/downloads/) installed on your system.

### Step 1: Clone the Repository
```bash
git clone https://github.com/MANAV0060/fitgirl-cloudflare-downloader.git
cd fitgirl-cloudflare-downloader
```

### Step 2: Install Dependencies
Open your command terminal in the project directory and install the required Python libraries:
```bash
pip install flask primp requests beautifulsoup4
```
*(Note: `primp` is a Rust-based HTTP client used to bypass Cloudflare security filters).*

### Step 3: Launch the Server
Start the Flask application:
```bash
python app.py
```

### Step 4: Open in Browser
Navigate to the local dashboard address in your web browser:
👉 **[http://localhost:5000](http://localhost:5000)**

---

## 📖 How to Use
1. Paste either the **FuckingFast URLs** or the **Plain Text Filenames** of the game parts into the input box on the left.
2. Type in your desired output folder in the **Save Directory** box (e.g., `D:\Games\GTA V`).
3. Click **Analyze Links**. The resolver will automatically scrape and find all active links online.
4. Set your desired **Concurrent Downloads** thread count.
5. Click **Start Download**.
6. If any individual part reports a `FAILED` status, click the orange **Retry** button on its card to restart that specific part immediately.
