// ══════════ GLOBAL JS ══════════

document.addEventListener('DOMContentLoaded', () => {
    checkYouTubeStatus();
});

// ──── YouTube Status (Sidebar) ────
function checkYouTubeStatus() {
    fetch('/api/youtube/status')
    .then(r => r.json())
    .then(data => {
        const el = document.getElementById('ytConnectionStatus');
        if (!el) return;
        if (data.connected && data.channel) {
            el.innerHTML = `
                <span class="status-dot connected"></span>
                <span class="status-text">YT: ${data.channel.name}</span>
            `;
        } else {
            el.innerHTML = `
                <span class="status-dot disconnected"></span>
                <span class="status-text">YouTube: Not Connected</span>
            `;
        }
    })
    .catch(() => {});
}

// ──── Sidebar Toggle (Mobile) ────
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const main = document.getElementById('mainContent');
    sidebar.classList.toggle('collapsed');
    main.classList.toggle('expanded');
}

// ──── Toast Notifications ────
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    const icons = {
        success: 'fa-check-circle',
        error: 'fa-times-circle',
        warning: 'fa-exclamation-triangle',
        info: 'fa-info-circle'
    };

    toast.innerHTML = `
        <i class="fas ${icons[type] || icons.info}"></i>
        <span>${message}</span>
        <button class="toast-close" onclick="this.parentElement.remove()">×</button>
    `;

    container.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => toast.classList.add('show'));

    // Auto remove after 5s
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}

// ──── Real-Time Log Viewer (SSE) ────
function startLogStream(taskId, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return null;

    const source = new EventSource(`/api/logs/stream/${taskId}`);

    source.onmessage = function(event) {
        const data = JSON.parse(event.data);
        if (data.type === 'heartbeat') return;

        const line = document.createElement('div');
        line.className = `log-line log-${data.level || 'info'}`;
        line.innerHTML = `
            <span class="log-ts">${data.time || ''}</span>
            <span class="log-msg">${escapeHtml(data.message || '')}</span>
        `;
        container.appendChild(line);
        container.scrollTop = container.scrollHeight;
    };

    source.onerror = function() {
        // Connection closed — task probably finished
        source.close();
    };

    return source;
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
