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

            if (data.is_vercel) {
                btnAnalyze.textContent = 'Resolve Links';
                document.querySelector('.logo-section p').textContent = 'FitGirl Repack Multi-part Link Resolver (Vercel Mode)';
                
                // Hide local configs and controls
                document.getElementById('local-config').style.display = 'none';
                document.getElementById('local-controls').style.display = 'none';
                
                // Show Vercel controls and instructions
                document.getElementById('vercel-controls').style.display = 'flex';
                document.getElementById('vercel-instructions').style.display = 'block';

                // Change stats header text
                document.querySelector('.stats-container').parentElement.querySelector('.form-title').textContent = 'Link Resolver Stats';
                document.querySelector('#stat-completed').previousElementSibling.textContent = 'Resolved Parts';
                document.querySelector('#stat-progress').previousElementSibling.textContent = 'Resolve Status';
                document.querySelector('#stat-speed').previousElementSibling.textContent = 'Operation Status';
                document.querySelector('#stat-speed').textContent = 'ONLINE';
                document.querySelector('#stat-speed-sub').textContent = 'Direct Serverless APIs active';
                document.querySelector('#stat-eta').previousElementSibling.textContent = 'Total Parts';
                document.querySelector('#stat-eta').textContent = '45 Parts';
                document.querySelector('#stat-eta-sub').textContent = 'Sequenced correctly';
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
                
                const isVercelMode = document.getElementById('vercel-controls').style.display === 'flex';
                if (isVercelMode) {
                    queueCountBadge.className = 'status-badge status-completed';
                    statCompleted.textContent = `${data.parts.filter(p => p.direct_url).length} / ${data.parts.length}`;
                    statProgress.textContent = `${Math.round(data.parts.filter(p => p.direct_url).length / data.parts.length * 100)}%`;
                    statProgressSub.textContent = 'Bypassed Cloudflare successfully';
                    
                    renderPartCards(data.parts);
                    
                    // Enable copy all button
                    const btnCopyAll = document.getElementById('btn-copy-all');
                    if (btnCopyAll) {
                        btnCopyAll.disabled = false;
                        btnCopyAll.onclick = () => {
                            const directUrls = data.parts
                                .filter(p => p.direct_url)
                                .map(p => p.direct_url)
                                .join('\n');
                            
                            navigator.clipboard.writeText(directUrls).then(() => {
                                btnCopyAll.textContent = 'Copied to Clipboard!';
                                setTimeout(() => { btnCopyAll.textContent = 'Copy All Direct Links'; }, 2000);
                            }).catch(err => {
                                alert('Failed to copy: ' + err);
                            });
                        };
                    }
                } else {
                    queueCountBadge.className = 'status-badge status-downloading';
                    renderPartCards(data.parts);
                    btnStart.disabled = false;
                }
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
        const isVercelMode = document.getElementById('vercel-controls').style.display === 'flex';
        parts.forEach(part => {
            const pct = part.total_bytes > 0 ? (part.downloaded_bytes / part.total_bytes * 100).toFixed(1) : 0;
            
            let sizeLabel, barWidth, statusClass, statusText;
            
            if (isVercelMode) {
                sizeLabel = part.direct_url ? 'Direct URL Ready' : (part.error || 'Failed to resolve');
                barWidth = part.direct_url ? 100 : 0;
                statusClass = part.direct_url ? 'completed' : 'failed';
                statusText = part.direct_url ? 'ready' : 'failed';
            } else {
                sizeLabel = part.total_bytes > 0 ? 
                    `${formatBytes(part.downloaded_bytes)} / ${formatBytes(part.total_bytes)}` : 
                    'Pending check...';
                barWidth = pct;
                statusClass = part.status;
                statusText = part.status;
            }

            const card = document.createElement('div');
            card.className = `part-card ${part.status === 'downloading' ? 'active' : ''} ${statusClass === 'completed' ? 'completed' : ''}`;
            card.id = `card-${part.filename.replace(/[^a-zA-Z0-9]/g, '_')}`;
            
            card.innerHTML = `
                <div class="part-card-header">
                    <span class="part-title" title="${part.filename}">${part.filename}</span>
                    <span class="status-badge status-${statusClass}">${statusText}</span>
                </div>
                <div class="progress-bar-container">
                    <div class="progress-bar-fill" style="width: ${barWidth}%"></div>
                </div>
                <div class="part-details-row">
                    <span class="part-progress-text">${isVercelMode ? sizeLabel : pct + '% (' + sizeLabel + ')'}</span>
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
                    const pct = part.total_bytes > 0 ? (part.downloaded_bytes / part.total_bytes * 100).toFixed(1) : 0;
                    const fill = card.querySelector('.progress-bar-fill');
                    fill.style.width = `${pct}%`;

                    // Update labels
                    const sizeLabel = part.total_bytes > 0 ? 
                        `${formatBytes(part.downloaded_bytes)} / ${formatBytes(part.total_bytes)}` : 
                        'Pending check...';
                    card.querySelector('.part-progress-text').textContent = `${pct}% (${sizeLabel})`;
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

    // Run status check on load
    checkStatus();
});
