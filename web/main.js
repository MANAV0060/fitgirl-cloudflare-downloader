document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const linksInput = document.getElementById('links-input');
    const threadsInput = document.getElementById('threads-input');
    const directoryInput = document.getElementById('directory-input');
    const btnAnalyze = document.getElementById('btn-analyze');
    const btnStart = document.getElementById('btn-start');
    const btnStop = document.getElementById('btn-stop');
    const partsGrid = document.getElementById('parts-grid');
    const queueCountBadge = document.getElementById('queue-count-badge');
    const pulseIndicator = document.getElementById('pulse-indicator');
    const headerStatusText = document.getElementById('header-status-text');

    // Stats elements
    const statSpeed = document.getElementById('stat-speed');
    const statProgress = document.getElementById('stat-progress');
    const statProgressSub = document.getElementById('stat-progress-sub');
    const statEta = document.getElementById('stat-eta');
    const statCompleted = document.getElementById('stat-completed');
    const statCompletedSub = document.getElementById('stat-completed-sub');

    let pollInterval = null;
    let currentLinks = [];

    // Helper: format bytes to human readable format
    function formatBytes(bytes, decimals = 2) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    }

    // Helper: format seconds to HH:MM:SS
    function formatSeconds(seconds) {
        if (seconds === null || isNaN(seconds) || seconds === Infinity || seconds < 0) return '--:--:--';
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        return [
            h.toString().padStart(2, '0'),
            m.toString().padStart(2, '0'),
            s.toString().padStart(2, '0')
        ].join(':');
    }

    // Initialize: Check status and pre-load links
    async function checkStatus() {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();
            
            // If server is actively downloading, disable controls and start polling
            if (data.status === 'downloading') {
                btnStart.disabled = true;
                btnStop.disabled = false;
                btnAnalyze.disabled = true;
                linksInput.disabled = true;
                threadsInput.disabled = true;
                
                pulseIndicator.classList.add('active');
                headerStatusText.textContent = 'Status: Downloading...';
                
                startPolling();
            } else {
                pulseIndicator.classList.remove('active');
                headerStatusText.textContent = `Status: ${data.status.toUpperCase()}`;
            }

            // If server has custom save directory, populate it
            if (data.save_dir) {
                directoryInput.value = data.save_dir;
            }

            // Pre-load links in textarea if backend has saved links
            if (data.saved_links && data.saved_links.length > 0 && linksInput.value.trim() === '') {
                linksInput.value = data.saved_links.join('\n');
                analyzeLinks();
            }
        } catch (err) {
            console.error('Error checking status:', err);
        }
    }

    // Analyze pasted links
    async function analyzeLinks() {
        const text = linksInput.value.trim();
        if (!text) return;

        btnAnalyze.disabled = true;
        btnAnalyze.textContent = 'Analyzing...';

        try {
            const res = await fetch('/api/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text, directory: directoryInput.value.trim() })
            });
            const data = await res.json();
            
            if (data.success) {
                currentLinks = data.links;
                queueCountBadge.textContent = `${data.parts.length} Parts`;
                queueCountBadge.className = 'status-badge status-downloading';
                
                renderPartCards(data.parts);
                btnStart.disabled = false;
            } else {
                alert('Analysis failed: ' + data.error);
            }
        } catch (err) {
            console.error('Error analyzing links:', err);
        } finally {
            btnAnalyze.disabled = false;
            btnAnalyze.textContent = 'Analyze Links';
        }
    }

    // Render cards for each part
    function renderPartCards(parts) {
        partsGrid.innerHTML = '';
        parts.forEach(part => {
            const pct = part.total_bytes > 0 ? (part.downloaded_bytes / part.total_bytes * 100).toFixed(1) : 0;
            const sizeLabel = part.total_bytes > 0 ? 
                `${formatBytes(part.downloaded_bytes)} / ${formatBytes(part.total_bytes)}` : 
                'Pending check...';

            const card = document.createElement('div');
            card.className = `part-card ${part.status === 'downloading' ? 'active' : ''} ${part.status === 'completed' ? 'completed' : ''}`;
            card.id = `card-${part.filename.replace(/[^a-zA-Z0-9]/g, '_')}`;
            
            card.innerHTML = `
                <div class="part-card-header">
                    <span class="part-title" title="${part.filename}">${part.filename}</span>
                    <div class="part-header-actions" style="display: flex; align-items: center; gap: 8px;">
                        <button class="btn-retry-card" data-filename="${part.filename}" style="display: ${part.status === 'failed' ? 'inline-flex' : 'none'};">Retry</button>
                        <span class="status-badge status-${part.status}">${part.status}</span>
                    </div>
                </div>
                <div class="progress-bar-container">
                    <div class="progress-bar-fill" style="width: ${pct}%"></div>
                </div>
                <div class="part-details-row">
                    <span class="part-progress-text">${pct}% (${sizeLabel})</span>
                    <span class="part-speed">${part.speed_mb > 0 ? part.speed_mb.toFixed(1) + ' MB/s' : ''}</span>
                </div>
            `;
            partsGrid.appendChild(card);
        });
    }

    // Start Download
    async function startDownload() {
        const threads = parseInt(threadsInput.value, 10) || 3;
        
        btnStart.disabled = true;
        btnAnalyze.disabled = true;
        linksInput.disabled = true;
        threadsInput.disabled = true;
        btnStop.disabled = false;

        pulseIndicator.classList.add('active');
        headerStatusText.textContent = 'Status: Downloading...';

        try {
            const res = await fetch('/api/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ links: currentLinks, threads, directory: directoryInput.value.trim() })
            });
            const data = await res.json();
            if (!data.success) {
                alert('Failed to start download: ' + data.error);
                stopPolling();
                resetControls();
            } else {
                startPolling();
            }
        } catch (err) {
            console.error('Error starting download:', err);
        }
    }

    // Stop Download
    async function stopDownload() {
        btnStop.disabled = true;
        try {
            await fetch('/api/stop', { method: 'POST' });
        } catch (err) {
            console.error('Error stopping download:', err);
        }
        stopPolling();
        resetControls();
        checkStatus();
    }

    function resetControls() {
        btnStart.disabled = false;
        btnAnalyze.disabled = false;
        linksInput.disabled = false;
        threadsInput.disabled = false;
        btnStop.disabled = true;
        pulseIndicator.classList.remove('active');
        headerStatusText.textContent = 'Status: Stopped';
    }

    // Polling logic
    function startPolling() {
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(updateProgress, 1000);
    }

    function stopPolling() {
        if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
        }
    }

    // Update progress stats and individual cards
    async function updateProgress() {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();

            // Update stats
            statSpeed.textContent = `${data.overall_speed_mb.toFixed(1)} MB/s`;
            statProgress.textContent = `${data.overall_progress_percent}%`;
            statProgressSub.textContent = `${data.overall_downloaded_gb.toFixed(2)} GB of ${data.overall_total_gb.toFixed(2)} GB`;
            statEta.textContent = formatSeconds(data.eta_seconds);
            statCompleted.textContent = `${data.completed_count} / ${data.total_count}`;

            // If it finishes naturally
            if (data.status === 'completed') {
                stopPolling();
                resetControls();
                pulseIndicator.classList.remove('active');
                headerStatusText.textContent = 'Status: Finished!';
                statSpeed.textContent = '0.0 MB/s';
                statEta.textContent = '--:--:--';
                alert('All parts downloaded successfully!');
            } else if (data.status === 'stopped' || data.status === 'idle') {
                stopPolling();
                resetControls();
            }

            // Update cards
            data.parts.forEach(part => {
                const cardId = `card-${part.filename.replace(/[^a-zA-Z0-9]/g, '_')}`;
                const card = document.getElementById(cardId);
                if (card) {
                    // Update badge
                    const badge = card.querySelector('.status-badge');
                    badge.textContent = part.status;
                    badge.className = `status-badge status-${part.status}`;

                    // Update retry button visibility
                    const retryBtn = card.querySelector('.btn-retry-card');
                    if (retryBtn) {
                        retryBtn.style.display = part.status === 'failed' ? 'inline-flex' : 'none';
                    }

                    // Update active animations
                    if (part.status === 'downloading') {
                        card.classList.add('active');
                        card.classList.remove('completed');
                    } else if (part.status === 'completed') {
                        card.classList.remove('active');
                        card.classList.add('completed');
                    } else {
                        card.classList.remove('active', 'completed');
                    }

                    // Update bar fill
                    const fill = card.querySelector('.progress-bar-fill');
                    if (part.status === 'downloading' && part.total_bytes === 0) {
                        // Segmented download: size not yet known — show indeterminate pulse
                        fill.style.width = '100%';
                        fill.classList.add('indeterminate');
                        card.querySelector('.part-progress-text').textContent = 'Connecting...';
                    } else {
                        fill.classList.remove('indeterminate');
                        const pct = part.total_bytes > 0 ? (part.downloaded_bytes / part.total_bytes * 100).toFixed(1) : (part.status === 'completed' ? 100 : 0);
                        fill.style.width = `${pct}%`;
                        const sizeLabel = part.total_bytes > 0
                            ? `${formatBytes(part.downloaded_bytes)} / ${formatBytes(part.total_bytes)}`
                            : (part.status === 'completed' ? formatBytes(part.downloaded_bytes) : 'Pending...');
                        card.querySelector('.part-progress-text').textContent = `${pct}% (${sizeLabel})`;
                    }
                    card.querySelector('.part-speed').textContent = part.speed_mb > 0 ? part.speed_mb.toFixed(1) + ' MB/s' : '';
                }
            });
        } catch (err) {
            console.error('Error fetching progress:', err);
        }
    }

    // Event Listeners
    btnAnalyze.addEventListener('click', analyzeLinks);
    btnStart.addEventListener('click', startDownload);
    btnStop.addEventListener('click', stopDownload);

    partsGrid.addEventListener('click', async (e) => {
        if (e.target.classList.contains('btn-retry-card')) {
            const filename = e.target.getAttribute('data-filename');
            e.target.disabled = true;
            const originalText = e.target.textContent;
            e.target.textContent = 'Retrying...';
            try {
                const res = await fetch('/api/retry_part', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename })
                });
                const data = await res.json();
                if (data.success) {
                    // Reset UI card state to downloading/pending immediately
                    const cardId = `card-${filename.replace(/[^a-zA-Z0-9]/g, '_')}`;
                    const card = document.getElementById(cardId);
                    if (card) {
                        card.classList.add('active');
                        card.classList.remove('completed');
                        const badge = card.querySelector('.status-badge');
                        if (badge) {
                            badge.textContent = 'pending';
                            badge.className = 'status-badge status-pending';
                        }
                        e.target.style.display = 'none';
                    }
                    
                    // Start polling
                    const statusRes = await fetch('/api/status');
                    const statusData = await statusRes.json();
                    if (statusData.status === 'downloading') {
                        btnStart.disabled = true;
                        btnStop.disabled = false;
                        btnAnalyze.disabled = true;
                        linksInput.disabled = true;
                        threadsInput.disabled = true;
                        pulseIndicator.classList.add('active');
                        headerStatusText.textContent = 'Status: Downloading...';
                        startPolling();
                    }
                } else {
                    alert('Failed to retry: ' + data.error);
                    e.target.disabled = false;
                    e.target.textContent = originalText;
                }
            } catch (err) {
                console.error('Error retrying:', err);
                e.target.disabled = false;
                e.target.textContent = originalText;
            }
        }
    });

    // Run status check on load
    checkStatus();
});
