// Mail Hunter - main application entry point

import { themeNames, applyTheme } from './themes.js';

// ── Helpers ─────────────────────────────────────────────

function esc(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

function formatDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString([], {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hour12: false,
    });
}

function formatSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
    return (bytes / 1073741824).toFixed(1) + ' GB';
}

// ── Confirm dialog ─────────────────────────────────────

function showConfirm(message, { title = 'Confirm', okLabel = 'Delete', okClass = 'btn-danger', cancelLabel = 'Cancel' } = {}) {
    return new Promise((resolve) => {
        const modal = document.getElementById('confirm-modal');
        document.getElementById('confirm-title').textContent = title;
        document.getElementById('confirm-message').textContent = message;
        const okBtn = document.getElementById('confirm-ok');
        const cancelBtn = document.getElementById('confirm-cancel');
        okBtn.textContent = okLabel;
        okBtn.className = `btn btn-sm ${okClass}`;
        cancelBtn.textContent = cancelLabel;
        modal.classList.remove('hidden');

        function cleanup(result) {
            modal.classList.add('hidden');
            okBtn.removeEventListener('click', onOk);
            cancelBtn.removeEventListener('click', onCancel);
            modal.removeEventListener('click', onBackdrop);
            resolve(result);
        }
        function onOk() { cleanup(true); }
        function onCancel() { cleanup(false); }
        function onBackdrop(e) { if (e.target === modal) cleanup(null); }

        okBtn.addEventListener('click', onOk);
        cancelBtn.addEventListener('click', onCancel);
        modal.addEventListener('click', onBackdrop);
    });
}

// ── Message preview dialog ─────────────────────────────

function showMessagePreview(mail, bodyText, bodyHtml, rawSource) {
    const modal = document.getElementById('preview-modal');
    const previewBody = document.getElementById('preview-body');
    const previewRaw = document.getElementById('preview-raw');
    const previewIframe = document.getElementById('preview-iframe');
    const toggle = document.getElementById('preview-toggle');
    const btnHtml = document.getElementById('preview-btn-html');
    const btnText = document.getElementById('preview-btn-text');
    const btnRaw = document.getElementById('preview-btn-raw');
    const headers = document.getElementById('preview-headers');

    document.getElementById('preview-title').textContent = mail.subject || '(no subject)';

    let headerHtml = `<span class="label">From</span><span class="value">${esc(mail.from_name ? `${mail.from_name} <${mail.from_addr}>` : mail.from_addr || '')}</span>`;
    headerHtml += `<span class="label">To</span><span class="value">${esc(mail.to_addr || '')}</span>`;
    headerHtml += `<span class="label">CC</span><span class="value">${esc(mail.cc_addr || '')}</span>`;
    headerHtml += `<span class="label">Date</span><span class="value">${formatDate(mail.date)}</span>`;
    headers.innerHTML = headerHtml;

    previewBody.textContent = bodyText || '';
    previewRaw.textContent = rawSource || '';

    const allBtns = [btnHtml, btnText, btnRaw];
    const allViews = [previewIframe, previewBody, previewRaw];

    function activate(btn, view) {
        allBtns.forEach(b => b.classList.remove('active'));
        allViews.forEach(v => v.classList.add('hidden'));
        btn.classList.add('active');
        view.classList.remove('hidden');
        if (view === previewIframe) previewIframe.srcdoc = '<style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}</style>' + bodyHtml;
    }

    toggle.classList.remove('hidden');
    if (bodyHtml) {
        btnHtml.classList.remove('hidden');
        activate(btnHtml, previewIframe);
    } else {
        btnHtml.classList.add('hidden');
        activate(btnText, previewBody);
    }

    btnHtml.onclick = () => activate(btnHtml, previewIframe);
    btnText.onclick = () => activate(btnText, previewBody);
    btnRaw.onclick = () => activate(btnRaw, previewRaw);

    modal.classList.remove('hidden');

    function close() {
        modal.classList.add('hidden');
        previewIframe.srcdoc = '';
        btnHtml.onclick = null;
        btnText.onclick = null;
        btnRaw.onclick = null;
        document.getElementById('preview-close').removeEventListener('click', close);
        modal.removeEventListener('click', onBackdrop);
    }
    function onBackdrop(e) { if (e.target === modal) close(); }

    document.getElementById('preview-close').addEventListener('click', close);
    modal.addEventListener('click', onBackdrop);
}

// ── Resize handles ──────────────────────────────────────

function initResize(handleId, leftPanel, rightPanel) {
    const handle = document.getElementById(handleId);
    if (!handle) return;
    let startX, startLeftW, startRightW;

    handle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        startX = e.clientX;
        startLeftW = leftPanel.offsetWidth;
        startRightW = rightPanel.offsetWidth;
        handle.classList.add('dragging');
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });

    function onMove(e) {
        const dx = e.clientX - startX;
        const minL = parseInt(getComputedStyle(leftPanel).minWidth) || 100;
        const minR = parseInt(getComputedStyle(rightPanel).minWidth) || 100;
        const newL = Math.max(minL, startLeftW + dx);
        const newR = Math.max(minR, startRightW - dx);
        leftPanel.style.width = newL + 'px';
        if (rightPanel.style.flex !== '1') {
            rightPanel.style.width = newR + 'px';
        }
    }

    function onUp() {
        handle.classList.remove('dragging');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        savePanelWidths();
    }
}

const serverPanel = document.getElementById('server-panel');
const mailPanel = document.getElementById('mail-panel');
const detailPanel = document.getElementById('detail-panel');

function savePanelWidths() {
    localStorage.setItem('mh-panel-widths', JSON.stringify({
        server: serverPanel.style.width,
        detail: detailPanel.style.width,
    }));
}

function restorePanelWidths() {
    try {
        const saved = JSON.parse(localStorage.getItem('mh-panel-widths'));
        if (!saved) return;
        if (saved.server) serverPanel.style.width = saved.server;
        if (saved.detail) detailPanel.style.width = saved.detail;
    } catch (e) { /* ignore */ }
}

restorePanelWidths();

initResize('resize-left', serverPanel, mailPanel);
initResize('resize-right', mailPanel, detailPanel);

// ── Column resize ───────────────────────────────────────

const COL_WIDTH_KEY = 'mh-col-widths';
const COL_KEYS = ['from', 'to', 'subject', 'date', 'size'];
const COL_MIN = 40;

function restoreColWidths() {
    try {
        return JSON.parse(localStorage.getItem(COL_WIDTH_KEY)) || {};
    } catch (e) { return {}; }
}

function saveColWidths(table) {
    const cols = table.querySelectorAll('colgroup col');
    const widths = {};
    // cols: 0=check, 1=from, 2=to, 3=subject, 4=date, 5=size, 6=att
    for (let i = 0; i < COL_KEYS.length; i++) {
        const col = cols[i + 1]; // skip checkbox col
        const w = col.style.width;
        if (w) widths[COL_KEYS[i]] = parseInt(w);
    }
    localStorage.setItem(COL_WIDTH_KEY, JSON.stringify(widths));
}

function initColumnResize(table) {
    if (!table) return;
    const cols = table.querySelectorAll('colgroup col');
    table.querySelectorAll('.col-resize-handle').forEach(handle => {
        const th = handle.parentElement;
        // Map th to col index: th index within the header row
        const thIndex = Array.from(th.parentElement.children).indexOf(th);
        const col = cols[thIndex];
        if (!col) return;

        handle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const startX = e.clientX;
            const startW = th.offsetWidth;
            handle.classList.add('dragging');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';

            function onMove(ev) {
                const w = Math.max(COL_MIN, startW + (ev.clientX - startX));
                col.style.width = w + 'px';
            }

            function onUp() {
                handle.classList.remove('dragging');
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
                saveColWidths(table);
            }

            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        });
    });
}

// ── Activity log ────────────────────────────────────────

const activityToggle = document.getElementById('activity-log-toggle');
const activityLog = document.getElementById('activity-log');
const activityList = document.getElementById('activity-log-list');

activityToggle.addEventListener('click', () => {
    activityLog.classList.toggle('collapsed');
    activityToggle.innerHTML = activityLog.classList.contains('collapsed')
        ? 'Activity &#x25B8;'
        : 'Activity &#x25BE;';
});

const ActivityLog = {
    add(message) {
        const entry = document.createElement('div');
        entry.className = 'activity-entry';
        const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        entry.textContent = `[${time}] ${message}`;
        activityList.appendChild(entry);
        activityList.scrollTop = activityList.scrollHeight;
    },
};

ActivityLog.add('Mail Hunter started');

// ── WebSocket ──────────────────────────────────────────

const statusDot = document.querySelector('#status-connection .status-dot');
const statusLabel = document.querySelector('#status-connection span:last-child');

let ws = null;
let wsRetryDelay = 1000;

function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.addEventListener('open', () => {
        wsRetryDelay = 1000;
        statusDot.classList.add('connected');
        statusLabel.textContent = 'Connected';
    });

    ws.addEventListener('close', () => {
        statusDot.classList.remove('connected');
        statusLabel.textContent = 'Reconnecting...';
        setTimeout(connectWS, wsRetryDelay);
        wsRetryDelay = Math.min(wsRetryDelay * 2, 30000);
    });

    ws.addEventListener('message', (e) => {
        try {
            const msg = JSON.parse(e.data);
            handleWSMessage(msg);
        } catch (err) {
            console.error('WS parse error:', err);
        }
    });
}

function handleWSMessage(msg) {
    switch (msg.type) {
        case 'import_started':
            ActivityLog.add(`Import started: ${msg.filename || 'file'}`);
            document.getElementById('status-activity').textContent = `Importing ${msg.filename || 'file'}...`;
            loadServers();
            break;
        case 'import_progress':
            document.getElementById('status-activity').textContent =
                `Importing: ${msg.count}${msg.total ? '/' + msg.total : ''} messages`;
            break;
        case 'import_completed':
            ActivityLog.add(`Import complete: ${msg.count} messages imported, ${msg.skipped} skipped`);
            document.getElementById('status-activity').textContent = '';
            loadStats();
            if (msg.server_id) {
                selectedServerId = msg.server_id;
                selectedFolder = null;
                selectedMailId = null;
                loadServers().then(() => {
                    loadMails().then(() => {
                        if (msg.mail_id) selectMail(msg.mail_id);
                    });
                    renderServerDetail();
                });
            } else {
                loadServers();
                if (selectedServerId) loadMails();
            }
            break;
        case 'import_error':
            ActivityLog.add(`Import error: ${msg.error}`);
            break;
        case 'sync_started':
            ActivityLog.add(`Sync started: ${msg.server_name || 'server'}`);
            syncingServerIds.add(msg.server_id);
            queuedServerIds.delete(msg.server_id);
            renderSyncStatus(`Syncing ${msg.server_name || 'server'}...`, msg.server_id);
            loadServers();
            if (msg.server_id === selectedServerId) {
                currentPage = 0;
                if (fullSyncServerId !== msg.server_id) loadMails();
                renderServerDetail();
            }
            break;
        case 'sync_folders':
            ActivityLog.add(`Found ${msg.folders.length} folders`);
            loadServers();
            if (msg.server_id === selectedServerId) renderServerDetail();
            break;
        case 'sync_progress': {
            if (msg.server_id && !syncingServerIds.has(msg.server_id)) {
                syncingServerIds.add(msg.server_id);
                renderServers(allServers);
            }
            let statusText = `Syncing: ${msg.folder}`;
            let logDetail = statusText;
            if (msg.total) {
                const existing = msg.existing_count || 0;
                statusText += ` — ${msg.count} of ${msg.total} [new] ${existing} [existing]`;
                logDetail = `Sync: ${msg.folder} — ${msg.count} of ${msg.total} new`;
            }
            renderSyncStatus(statusText, msg.server_id);
            if (msg.count && (msg.count === 1 || msg.count % 50 === 0 || msg.count === msg.total)) {
                ActivityLog.add(logDetail);
            }
            if (msg.folder_count != null) {
                // Update in-memory folder count
                const srv = allServers.find(x => x.id === msg.server_id);
                if (srv && srv.folders) {
                    const fo = srv.folders.find(f => f.name === msg.folder);
                    if (fo) fo.count = msg.folder_count;
                    else srv.folders.push({ name: msg.folder, count: msg.folder_count });
                }
                // Re-render tree so parent roll-ups recalculate
                renderServers(allServers);
            }
            // Update global stats in real-time
            if (msg.mail && _lastStats) {
                _lastStats.messages++;
                updateStatsBar();
                if (!selectedServerId) renderGlobalStats();
            }
            break;
        }
        case 'sync_completed':
            ActivityLog.add(`Sync complete: ${msg.imported} imported, ${msg.skipped} skipped${msg.errors ? ', ' + msg.errors + ' errors' : ''}`);
            syncingServerIds.delete(msg.server_id);
            cancellingServerIds.delete(msg.server_id);
            if (fullSyncServerId === msg.server_id) fullSyncServerId = null;
            if (_statusBarSyncId === msg.server_id) renderSyncStatus(null);
            loadStats().then(() => { if (!selectedServerId) renderGlobalStats(); });
            loadServers().then(() => {
                if (msg.server_id === selectedServerId) {
                    loadMails();
                    renderServerDetail();
                }
            });
            break;
        case 'sync_cancelled':
            ActivityLog.add(`Sync cancelled (${msg.imported} imported before cancel)`);
            syncingServerIds.delete(msg.server_id);
            cancellingServerIds.delete(msg.server_id);
            if (fullSyncServerId === msg.server_id) fullSyncServerId = null;
            if (_statusBarSyncId === msg.server_id) renderSyncStatus(null);
            loadServers();
            if (msg.server_id === selectedServerId) {
                loadMails();
                renderServerDetail();
            } else if (!selectedServerId) {
                renderGlobalStats();
            }
            break;
        case 'sync_error':
            ActivityLog.add(`Sync error: ${msg.error}`);
            syncingServerIds.delete(msg.server_id);
            cancellingServerIds.delete(msg.server_id);
            if (_statusBarSyncId === msg.server_id) renderSyncStatus(null);
            renderServers(allServers);
            if (msg.server_id === selectedServerId) renderServerDetail();
            else if (!selectedServerId) renderGlobalStats();
            break;
        case 'backfill_started':
            ActivityLog.add(`Label backfill started: ${msg.server_name || 'server'}`);
            backfillingServerId = msg.server_id;
            renderBackfillStatus(`Backfill: starting...`);
            break;
        case 'backfill_progress':
            renderBackfillStatus(`Backfill: ${msg.count}/${msg.total} (${msg.tagged} tagged)`);
            break;
        case 'backfill_completed':
            ActivityLog.add(`Label backfill complete: ${msg.tagged} messages tagged`);
            backfillingServerId = null;
            renderBackfillStatus(null);
            loadStats();
            break;
        case 'backfill_cancelled':
            ActivityLog.add(`Label backfill cancelled (${msg.tagged} tagged before cancel)`);
            backfillingServerId = null;
            renderBackfillStatus(null);
            break;
        case 'backfill_error':
            ActivityLog.add(`Backfill error: ${msg.error}`);
            backfillingServerId = null;
            renderBackfillStatus(null);
            break;
        case 'delete_started':
            ActivityLog.add(`Deleting server: ${msg.server_name}`);
            statusActivity.innerHTML = `<span class="status-sync-text">${esc(`Deleting ${msg.server_name}...`)}</span>`;
            break;
        case 'delete_progress':
            statusActivity.innerHTML = `<span class="status-sync-text">${esc(`Deleting: ${msg.count} / ${msg.total} files removed`)}</span>`;
            if (msg.count % 500 === 0) {
                ActivityLog.add(`Delete progress: ${msg.count} / ${msg.total} files`);
            }
            break;
        case 'delete_completed':
            ActivityLog.add(`Server deleted: ${msg.deleted} messages removed`);
            renderSyncStatus(null);
            loadStats();
            break;
        case 'delete_error':
            ActivityLog.add(`Delete error: ${msg.error}`);
            renderSyncStatus(null);
            loadServers();
            break;
        case 'sync_queued':
            ActivityLog.add(`Sync queued: ${msg.server_name || 'server'}`);
            queuedServerIds.add(msg.server_id);
            renderServers(allServers);
            if (msg.server_id === selectedServerId) renderServerDetail();
            break;
        case 'sync_dequeued':
            queuedServerIds.delete(msg.server_id);
            renderServers(allServers);
            if (msg.server_id === selectedServerId) renderServerDetail();
            break;
        default:
            if (msg.message) ActivityLog.add(msg.message);
            break;
    }
}

connectWS();

// ── Global stats ────────────────────────────────────────

let _lastStats = null;

function updateStatsBar() {
    const s = _lastStats;
    if (!s) return;
    const el = document.getElementById('status-stats');
    el.innerHTML =
        `Servers: <strong>${s.servers.toLocaleString()}</strong> &nbsp; ` +
        `Messages: <strong>${s.messages.toLocaleString()}</strong> &nbsp; ` +
        `Duplicates: <strong>${s.duplicates.toLocaleString()}</strong> &nbsp; ` +
        `Archive: <strong>${formatSize(s.archive_size)}</strong>`;
}

async function loadStats() {
    try {
        const resp = await fetch('/api/stats');
        if (!resp.ok) return;
        _lastStats = await resp.json();
        updateStatsBar();
    } catch (err) {
        console.error('Failed to load stats:', err);
    }
}

function renderGlobalStats() {
    const container = document.getElementById('detail-content');
    const s = _lastStats;
    if (!s) {
        container.innerHTML = '<div class="empty-state"><span>Loading...</span></div>';
        loadStats().then(renderGlobalStats);
        return;
    }

    // Determine system status
    let statusDot, statusText;
    if (syncingServerIds.size > 0) {
        const sid = _statusBarSyncId || [...syncingServerIds][0];
        const srv = allServers.find(x => x.id === sid);
        const name = srv ? srv.name : `Server ${sid}`;
        statusDot = 'syncing';
        statusText = `Syncing ${esc(name)}`;
        if (queuedServerIds.size > 0) {
            statusText += ` (${queuedServerIds.size} queued)`;
        }
    } else if (backfillingServerId) {
        const srv = allServers.find(x => x.id === backfillingServerId);
        const name = srv ? srv.name : `Server ${backfillingServerId}`;
        statusDot = 'syncing';
        statusText = `Backfilling ${esc(name)}`;
    } else {
        statusDot = 'idle';
        statusText = 'Idle';
    }

    container.innerHTML = `
        <div class="detail-section">
            <div class="detail-subject">Mail Hunter</div>
        </div>
        <div class="detail-section">
            <h3>Status</h3>
            <div class="system-status">
                <span class="status-dot ${statusDot}"></span>
                <span>${statusText}</span>
            </div>
        </div>
        <div class="detail-section">
            <h3>Overview</h3>
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-value">${s.messages.toLocaleString()}</div>
                    <div class="stat-label">Messages</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${s.servers.toLocaleString()}</div>
                    <div class="stat-label">Servers</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${s.duplicates.toLocaleString()}</div>
                    <div class="stat-label">Duplicates</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${s.held.toLocaleString()}</div>
                    <div class="stat-label">Held</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${formatSize(s.archive_size)}</div>
                    <div class="stat-label">Archive</div>
                </div>
            </div>
        </div>
    `;
}

// ── Sync status ─────────────────────────────────────────

const syncingServerIds = new Set();
const cancellingServerIds = new Set();
let _statusBarSyncId = null;
let fullSyncServerId = null;
let backfillingServerId = null;
const queuedServerIds = new Set();
const statusActivity = document.getElementById('status-activity');

function renderSyncStatus(detail, serverId) {
    if (!detail) {
        _statusBarSyncId = null;
        if (!backfillingServerId) {
            statusActivity.innerHTML = '<span class="status-idle">Idle</span>';
        }
        return;
    }
    if (serverId != null) _statusBarSyncId = serverId;
    statusActivity.innerHTML = `
        <span class="status-sync-text">${esc(detail)}</span>
        <button class="btn btn-sm status-cancel-btn" id="status-cancel-sync">Cancel</button>
    `;
    document.getElementById('status-cancel-sync')?.addEventListener('click', async () => {
        if (_statusBarSyncId) {
            cancellingServerIds.add(_statusBarSyncId);
            statusActivity.innerHTML = '<span class="status-sync-text">Cancelling...</span>';
            renderServers(allServers);
            try {
                await fetch(`/api/servers/${_statusBarSyncId}/sync/cancel`, { method: 'POST' });
            } catch (err) {
                console.error('Cancel failed:', err);
            }
        }
    });
}

function renderBackfillStatus(detail) {
    if (!detail) {
        if (syncingServerIds.size === 0) {
            statusActivity.innerHTML = '<span class="status-idle">Idle</span>';
        }
        return;
    }
    statusActivity.innerHTML = `
        <span class="status-sync-text">${esc(detail)}</span>
        <button class="btn btn-sm status-cancel-btn" id="status-cancel-backfill">Cancel</button>
    `;
    document.getElementById('status-cancel-backfill')?.addEventListener('click', async () => {
        if (backfillingServerId) {
            try {
                await fetch(`/api/servers/${backfillingServerId}/backfill/cancel`, { method: 'POST' });
            } catch (err) {
                console.error('Cancel failed:', err);
            }
        }
    });
}

// ── Server filter ───────────────────────────────────────

const serverFilter = document.getElementById('server-filter');
let allServers = [];

document.querySelector('#server-panel .panel-title').addEventListener('click', () => {
    clearSelection();
    renderServers(allServers);
});
document.querySelector('#server-panel .panel-title').style.cursor = 'pointer';

serverFilter.addEventListener('input', () => renderServers(allServers));

serverFilter.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        serverFilter.value = '';
        renderServers(allServers);
    }
});

// ── Search panel ────────────────────────────────────────

const searchPanel = document.getElementById('search-panel');
const searchToggleBtn = document.getElementById('btn-search');
const searchFields = ['search-from', 'search-to', 'search-subject', 'search-body', 'search-date-from', 'search-date-to', 'search-attachment', 'search-tag', 'search-held', 'search-has-dups', 'search-server'];

searchToggleBtn.addEventListener('click', () => {
    const visible = !searchPanel.classList.contains('hidden');
    searchPanel.classList.toggle('hidden', visible);
    searchToggleBtn.classList.toggle('btn-active', !visible);
    if (!visible) document.getElementById('search-from').focus();
});

function getSearchParams() {
    const params = {};
    const from = document.getElementById('search-from').value.trim();
    const to = document.getElementById('search-to').value.trim();
    const subject = document.getElementById('search-subject').value.trim();
    const body = document.getElementById('search-body').value.trim();
    const dateFrom = document.getElementById('search-date-from').value;
    const dateTo = document.getElementById('search-date-to').value;
    if (from) params.from = from;
    if (to) params.to = to;
    if (subject) params.subject = subject;
    if (body) params.body = body;
    const attachment = document.getElementById('search-attachment').value.trim();
    const tag = document.getElementById('search-tag').value.trim();
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    if (attachment) params.attachment = attachment;
    if (tag) params.tag = tag;
    const held = document.getElementById('search-held').checked;
    if (held) params.held = '1';
    const hasDups = document.getElementById('search-has-dups').checked;
    if (hasDups) params.has_dups = '1';
    const searchServer = document.getElementById('search-server').value;
    if (searchServer) params.server_id = searchServer;
    else if (selectedServerId) params.server_id = selectedServerId;
    return params;
}

function hasSearchParams() {
    const p = getSearchParams();
    return p.from || p.to || p.subject || p.body || p.date_from || p.date_to || p.attachment || p.tag || p.held || p.has_dups;
}

document.getElementById('search-go').addEventListener('click', () => {
    if (hasSearchParams()) { currentPage = 0; doSearch(); }
});

document.getElementById('search-clear').addEventListener('click', () => {
    searchFields.forEach(id => {
        const el = document.getElementById(id);
        if (el.type === 'checkbox') el.checked = false;
        else el.value = '';
    });
    document.getElementById('mail-filter').value = '';
    currentPage = 0;
    if (selectedServerId) loadMails();
});

// Enter in any search field triggers search
searchFields.forEach(id => {
    document.getElementById(id).addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && hasSearchParams()) { currentPage = 0; doSearch(); }
    });
});

// ── Saved searches ──────────────────────────────────────

async function loadSavedSearches() {
    try {
        const resp = await fetch('/api/searches');
        if (!resp.ok) return;
        const searches = await resp.json();
        renderSavedSearches(searches);
    } catch (err) {
        console.error('Failed to load saved searches:', err);
    }
}

function renderSavedSearches(searches) {
    const container = document.getElementById('saved-searches');
    if (!searches.length) {
        container.innerHTML = '';
        return;
    }
    container.innerHTML = searches.map(s =>
        `<span class="saved-search-item" data-id="${s.id}" data-params="${esc(s.params)}">` +
        `<span class="saved-search-name">${esc(s.name)}</span>` +
        `<span class="saved-search-delete" data-id="${s.id}">&times;</span>` +
        `</span>`
    ).join('');

    container.querySelectorAll('.saved-search-name').forEach(el => {
        el.addEventListener('click', () => {
            const item = el.closest('.saved-search-item');
            const params = JSON.parse(item.dataset.params);
            // Populate search fields from saved params
            searchFields.forEach(id => {
                const field = document.getElementById(id);
                if (field.type === 'checkbox') field.checked = false;
                else field.value = '';
            });
            if (params.from) document.getElementById('search-from').value = params.from;
            if (params.to) document.getElementById('search-to').value = params.to;
            if (params.subject) document.getElementById('search-subject').value = params.subject;
            if (params.body) document.getElementById('search-body').value = params.body;
            if (params.date_from) document.getElementById('search-date-from').value = params.date_from;
            if (params.date_to) document.getElementById('search-date-to').value = params.date_to;
            if (params.attachment) document.getElementById('search-attachment').value = params.attachment;
            if (params.tag) document.getElementById('search-tag').value = params.tag;
            if (params.held) document.getElementById('search-held').checked = true;
            if (params.has_dups) document.getElementById('search-has-dups').checked = true;
            if (params.server_id) document.getElementById('search-server').value = params.server_id;
            // Show search panel and run
            searchPanel.classList.remove('hidden');
            searchToggleBtn.classList.add('btn-active');
            currentPage = 0;
            doSearch();
        });
    });

    container.querySelectorAll('.saved-search-delete').forEach(el => {
        el.addEventListener('click', async (e) => {
            e.stopPropagation();
            try {
                await fetch(`/api/searches/${el.dataset.id}`, { method: 'DELETE' });
                loadSavedSearches();
            } catch (err) {
                console.error('Failed to delete saved search:', err);
            }
        });
    });
}

document.getElementById('search-save').addEventListener('click', async () => {
    const params = getSearchParams();
    if (!Object.keys(params).length) return;
    const name = prompt('Save search as:');
    if (!name) return;
    try {
        await fetch('/api/searches', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, params }),
        });
        loadSavedSearches();
    } catch (err) {
        console.error('Failed to save search:', err);
    }
});

loadSavedSearches();

async function doSearch() {
    selectedMailIds.clear();
    _anchorIdx = -1;
    const params = getSearchParams();
    params.sort = sortKey;
    params.sortDir = sortDirParam();
    params.page = currentPage;
    const container = document.getElementById('mail-content');
    const countEl = document.getElementById('mail-count');
    const titleEl = document.getElementById('mail-panel-title');
    container.innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';
    try {
        const qs = new URLSearchParams(params).toString();
        const resp = await fetch(`/api/mails/search?${qs}`);
        if (!resp.ok) return;
        const data = await resp.json();
        titleEl.textContent = 'Search Results';
        totalMails = data.total;
        currentPage = data.page;
        countEl.textContent = data.total ? `(${data.total})` : '';
        currentMails = data.items;
        applyFilterAndRender();
    } catch (err) {
        console.error('Search failed:', err);
    }
}

// ── Mail panel filter ───────────────────────────────────

const mailFilter = document.getElementById('mail-filter');
let currentMails = [];
let sortKey = 'date';
let sortDir = -1; // 1=asc, -1=desc
let currentPage = 0;
let totalMails = 0;
const PAGE_SIZE = 100;

function sortDirParam() { return sortDir === 1 ? 'asc' : 'desc'; }

function toggleSort(key) {
    if (sortKey === key) {
        sortDir = -sortDir;
    } else {
        sortKey = key;
        sortDir = 1;
    }
    currentPage = 0;
    selectedMailIds.clear();
    _anchorIdx = -1;
    if (hasSearchParams()) {
        doSearch();
    } else {
        loadMails();
    }
}

mailFilter.addEventListener('input', () => applyFilterAndRender());

mailFilter.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        mailFilter.value = '';
        applyFilterAndRender();
    }
});

function applyFilterAndRender() {
    selectedMailIds.clear();
    _anchorIdx = -1;
    const q = mailFilter.value.trim().toLowerCase();
    if (!q) {
        renderMails(currentMails);
        return;
    }
    const filtered = currentMails.filter(m =>
        (m.from_name || '').toLowerCase().includes(q) ||
        (m.from_addr || '').toLowerCase().includes(q) ||
        (m.to_addr || '').toLowerCase().includes(q) ||
        (m.subject || '').toLowerCase().includes(q)
    );
    renderMails(filtered);
    const countEl = document.getElementById('mail-count');
    if (filtered.length !== currentMails.length) {
        countEl.textContent = `(${filtered.length} of ${totalMails})`;
    }
}

// ── Settings modal ──────────────────────────────────────

const settingsModal = document.getElementById('settings-modal');
const settingsContent = document.getElementById('settings-content');

function openSettings() {
    settingsModal.classList.remove('hidden');
    renderSettings();
}

function closeSettings() {
    settingsModal.classList.add('hidden');
}

document.getElementById('btn-settings').addEventListener('click', openSettings);
document.getElementById('settings-close').addEventListener('click', closeSettings);
settingsModal.addEventListener('click', (e) => {
    if (e.target === settingsModal) closeSettings();
});

async function renderSettings() {
    let servers = [];
    try {
        const resp = await fetch('/api/servers');
        if (resp.ok) servers = await resp.json();
    } catch (err) { /* empty */ }

    // Theme dropdown options
    const sorted = ['default', ...themeNames.filter(n => n !== 'default').sort()];
    const currentTheme = localStorage.getItem('mh-theme') || 'default';
    const themeOpts = sorted.map(n =>
        `<option value="${n}"${n === currentTheme ? ' selected' : ''}>${n.replace(/\b\w/g, c => c.toUpperCase())}</option>`
    ).join('');

    // Server rows
    let serverRows = '';
    if (servers.length) {
        for (const s of servers) {
            serverRows += `<tr>
                <td>${esc(s.name)}</td>
                <td>${esc(s.host)}:${s.port}</td>
                <td>${esc(s.username)}</td>
                <td class="settings-server-actions">
                    <button class="btn btn-sm" data-edit="${s.id}">Edit</button>
                    <button class="btn btn-sm btn-danger" data-delete="${s.id}">Delete</button>
                </td>
            </tr>`;
        }
    }

    settingsContent.innerHTML = `
        <div class="settings-section">
            <div class="settings-section-title">Appearance</div>
            <div class="settings-row">
                <div class="settings-inline">
                    <label class="modal-label">Theme</label>
                    <select id="settings-theme">${themeOpts}</select>
                </div>
            </div>
        </div>
        <div class="settings-section">
            <div class="settings-section-title">Mail Servers</div>
            <div class="settings-row">
                ${servers.length ? `<table class="settings-servers-table">
                    <colgroup><col style="width:22%"><col style="width:22%"><col style="width:28%"><col style="width:28%"></colgroup>
                    <thead><tr><th>Name</th><th>Server</th><th>User</th><th></th></tr></thead>
                    <tbody>${serverRows}</tbody>
                </table>` : '<span class="text-muted">No servers configured</span>'}
                <div style="margin-top: 0.5rem;">
                    <button class="btn btn-sm btn-primary" id="settings-add-server">+ Add Server</button>
                </div>
                <div id="settings-server-form-container"></div>
            </div>
        </div>
    `;

    // Theme change
    document.getElementById('settings-theme').addEventListener('change', (e) => {
        applyTheme(e.target.value);
    });

    // Add server
    document.getElementById('settings-add-server').addEventListener('click', () => {
        showServerForm();
    });

    // Edit buttons
    settingsContent.querySelectorAll('[data-edit]').forEach(btn => {
        btn.addEventListener('click', () => {
            const id = parseInt(btn.dataset.edit);
            const server = servers.find(s => s.id === id);
            if (server) showServerForm(server);
        });
    });

    // Delete buttons
    settingsContent.querySelectorAll('[data-delete]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const id = parseInt(btn.dataset.delete);
            const server = servers.find(s => s.id === id);
            if (!await showConfirm(`Delete server "${server?.name}" and all its messages?`)) return;
            // Remove from tree immediately
            allServers = allServers.filter(s => s.id !== id);
            renderServers(allServers);
            if (selectedServerId === id) clearSelection();
            renderSettings();
            fetch(`/api/servers/${id}`, { method: 'DELETE' }).catch(err => {
                console.error('Delete failed:', err);
            });
        });
    });
}

function showServerForm(existing) {
    const container = document.getElementById('settings-server-form-container');
    container.innerHTML = `
        <div class="settings-server-form">
            <div class="settings-server-form-fields">
                <input class="modal-input modal-input-full" id="sf-name" placeholder="Name" value="${esc(existing?.name || '')}">
                <input class="modal-input" id="sf-host" placeholder="imap.example.com" value="${esc(existing?.host || '')}">
                <input class="modal-input" id="sf-port" type="number" placeholder="993" value="${existing?.port || 993}">
                <input class="modal-input" id="sf-username" placeholder="user@example.com" value="${esc(existing?.username || '')}">
                <input class="modal-input" id="sf-password" type="password" placeholder="${existing ? '(unchanged)' : 'Password'}">
                ${!existing || existing.host ? `<div class="settings-auto-sync-row">
                    <label class="settings-checkbox"><input type="checkbox" id="sf-sync-enabled" ${!existing || existing.sync_enabled !== false ? 'checked' : ''}> Auto Sync</label>
                    <label class="settings-interval">every <input class="modal-input modal-input-narrow" id="sf-sync-interval" type="number" min="0" value="${existing?.sync_interval ?? 15}"> min</label>
                </div>` : ''}
            </div>
            <div class="settings-server-form-actions">
                <button class="btn btn-sm" id="sf-cancel">Cancel</button>
                <button class="btn btn-sm btn-primary" id="sf-save">${existing ? 'Update' : 'Save'}</button>
            </div>
        </div>
    `;

    document.getElementById('sf-cancel').addEventListener('click', () => {
        container.innerHTML = '';
    });

    document.getElementById('sf-save').addEventListener('click', async () => {
        const syncEl = document.getElementById('sf-sync-enabled');
        const data = {
            name: document.getElementById('sf-name').value.trim(),
            host: document.getElementById('sf-host').value.trim(),
            port: parseInt(document.getElementById('sf-port').value) || 993,
            username: document.getElementById('sf-username').value.trim(),
            password: document.getElementById('sf-password').value,
            sync_enabled: syncEl ? syncEl.checked : true,
            sync_interval: parseInt(document.getElementById('sf-sync-interval')?.value) || 15,
        };
        if (!existing && (!data.host || !data.username)) return;
        if (!data.name) data.name = data.host;

        try {
            let resp;
            if (existing) {
                resp = await fetch(`/api/servers/${existing.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data),
                });
            } else {
                resp = await fetch('/api/servers', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data),
                });
            }
            if (resp.ok) {
                renderSettings();
                loadServers();
            }
        } catch (err) {
            console.error('Save failed:', err);
        }
    });

    document.getElementById('sf-name').focus();
}

// ── Import modal ────────────────────────────────────────

const importModal = document.getElementById('import-modal');
let importFiles = [];

function importServerName(files) {
    const ts = formatDate(new Date().toISOString());
    if (files.length === 1) {
        const stem = files[0].name.replace(/\.[^.]+$/, '');
        return `Import: ${stem} \u2014 ${ts}`;
    }
    return `Import: ${ts}`;
}

function openImport() {
    importModal.classList.remove('hidden');
    importFiles = [];
    renderImportFiles();
    document.getElementById('import-status').textContent = '';
    document.getElementById('import-go').disabled = true;
}

function closeImport() {
    importModal.classList.add('hidden');
    importFiles = [];
}

document.getElementById('btn-import').addEventListener('click', openImport);
document.getElementById('import-close').addEventListener('click', closeImport);
importModal.addEventListener('click', (e) => {
    if (e.target === importModal) closeImport();
});


function updateImportButton() {
    document.getElementById('import-go').disabled = !importFiles.length;
}

// Dropzone
const dropzone = document.getElementById('import-dropzone');
const fileInput = document.getElementById('import-file-input');

dropzone.addEventListener('click', () => {
    fileInput.accept = '.eml,.mbox';
    fileInput.multiple = true;
    fileInput.click();
});

dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('drag-over');
});

dropzone.addEventListener('dragleave', () => {
    dropzone.classList.remove('drag-over');
});

dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('drag-over');
    addImportFiles(e.dataTransfer.files);
});

fileInput.addEventListener('change', () => {
    addImportFiles(fileInput.files);
    fileInput.value = '';
});

function addImportFiles(fileList) {
    for (const f of fileList) {
        importFiles.push(f);
    }
    renderImportFiles();
    updateImportButton();
}

function renderImportFiles() {
    const container = document.getElementById('import-file-list');
    if (!importFiles.length) {
        container.innerHTML = '';
        return;
    }
    container.innerHTML = importFiles.map((f, i) =>
        `<div class="import-file-item">
            <span>${esc(f.name)} (${formatSize(f.size)})</span>
            <span class="remove" data-idx="${i}">&times;</span>
        </div>`
    ).join('');
    container.querySelectorAll('.remove').forEach(el => {
        el.addEventListener('click', () => {
            importFiles.splice(parseInt(el.dataset.idx), 1);
            renderImportFiles();
            updateImportButton();
        });
    });
}

document.getElementById('import-go').addEventListener('click', async () => {
    if (!importFiles.length) return;

    const statusEl = document.getElementById('import-status');
    const btn = document.getElementById('import-go');
    btn.disabled = true;
    statusEl.textContent = 'Uploading...';

    try {
        const formData = new FormData();
        for (const f of importFiles) {
            formData.append('files', f);
        }
        formData.append('server_name', importServerName(importFiles));

        const resp = await fetch('/api/import', { method: 'POST', body: formData });
        const result = await resp.json();

        if (!resp.ok) {
            statusEl.textContent = `Error: ${result.error || 'upload failed'}`;
            btn.disabled = false;
            return;
        }

        if (result.ok) {
            closeImport();
        }
    } catch (err) {
        statusEl.textContent = `Error: ${err.message}`;
        btn.disabled = false;
    }
});

// ── Global drag-drop import ─────────────────────────────

const dropOverlay = document.getElementById('drop-overlay');
let dragCounter = 0;

document.addEventListener('dragenter', (e) => {
    e.preventDefault();
    dragCounter++;
    if (dragCounter === 1) dropOverlay.classList.remove('hidden');
});

document.addEventListener('dragleave', (e) => {
    e.preventDefault();
    dragCounter--;
    if (dragCounter <= 0) {
        dragCounter = 0;
        dropOverlay.classList.add('hidden');
    }
});

document.addEventListener('dragover', (e) => {
    e.preventDefault();
});

document.addEventListener('drop', (e) => {
    e.preventDefault();
    dragCounter = 0;
    dropOverlay.classList.add('hidden');

    // Ignore drops inside the import modal dropzone (handled separately)
    if (e.target.closest('#import-dropzone')) return;

    const files = [...e.dataTransfer.files];
    if (!files.length) return;

    startDirectImport(files);
});

async function startDirectImport(files) {
    ActivityLog.add(`Uploading ${files.length} file(s)...`);

    const formData = new FormData();
    for (const f of files) {
        formData.append('files', f);
    }
    formData.append('server_name', importServerName(files));

    try {
        const resp = await fetch('/api/import', { method: 'POST', body: formData });
        const result = await resp.json();

        if (!resp.ok) {
            ActivityLog.add(`Import error: ${result.error || 'upload failed'}`);
            return;
        }

        if (!result.ok) {
            ActivityLog.add(`Import error: ${result.error || 'unknown'}`);
        }
    } catch (err) {
        ActivityLog.add(`Import error: ${err.message}`);
    }
}

// ── Server list ─────────────────────────────────────────

let selectedServerId = null;
let selectedFolder = null;
let selectedMailId = null;
const selectedMailIds = new Set();
let _anchorIdx = -1;

async function loadServers() {
    const container = document.getElementById('server-content');
    if (!allServers.length) {
        container.innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';
    }
    try {
        const resp = await fetch('/api/servers');
        if (!resp.ok) return;
        allServers = await resp.json();
        // Sync state comes exclusively from WebSocket replay (_sync_state).
        // No API hydration — avoids dual-source-of-truth stale badges.
        renderServers(allServers);
        updateSearchServerOptions();
    } catch (err) {
        console.error('Failed to load servers:', err);
    }
}

function updateSearchServerOptions() {
    const sel = document.getElementById('search-server');
    const cur = sel.value;
    sel.innerHTML = '<option value="">All servers</option>';
    for (const s of allServers) {
        const opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = s.name;
        sel.appendChild(opt);
    }
    sel.value = cur;
}

function getCollapsedNodes() {
    try {
        return new Set(JSON.parse(localStorage.getItem('mh-collapsed-nodes') || '[]'));
    } catch (e) { return new Set(); }
}

function saveCollapsedNodes(nodes) {
    localStorage.setItem('mh-collapsed-nodes', JSON.stringify([...nodes]));
}

function toggleCollapse(key) {
    const nodes = getCollapsedNodes();
    if (nodes.has(key)) {
        nodes.delete(key);
    } else {
        nodes.add(key);
    }
    saveCollapsedNodes(nodes);
    renderServers(allServers);
}

function buildFolderTree(folders) {
    // Sort by name so parents come before children
    const sorted = [...folders].sort((a, b) => a.name.localeCompare(b.name));
    const root = [];
    const map = {};

    function findOrCreateParent(fullName) {
        // Find longest existing prefix separated by '/' or '.'
        let bestKey = null;
        let bestLen = 0;
        for (const key of Object.keys(map)) {
            const sep = fullName[key.length];
            if (fullName.startsWith(key) && (sep === '/' || sep === '.') && key.length > bestLen) {
                bestKey = key;
                bestLen = key.length;
            }
        }
        if (bestKey) return { parent: map[bestKey], prefixLen: bestLen };

        // No existing parent — check if there's an implicit one (e.g. "[Google Mail]" from "[Google Mail]/All Mail")
        for (const sep of ['/', '.']) {
            const idx = fullName.indexOf(sep);
            if (idx > 0) {
                const prefix = fullName.slice(0, idx);
                if (!map[prefix]) {
                    const virtual = { name: prefix, fullName: prefix, children: [], count: 0 };
                    map[prefix] = virtual;
                    root.push(virtual);
                }
                return { parent: map[prefix], prefixLen: idx };
            }
        }
        return null;
    }

    for (const f of sorted) {
        const node = { name: f.name, fullName: f.name, children: [], count: f.count ?? 0 };
        const result = findOrCreateParent(f.name);

        if (result) {
            node.name = f.name.slice(result.prefixLen + 1);
            result.parent.children.push(node);
        } else {
            root.push(node);
        }
        map[f.name] = node;
    }

    // Roll up child counts into parents
    function sumCounts(nodes) {
        let total = 0;
        for (const node of nodes) {
            const childTotal = sumCounts(node.children);
            node.count += childTotal;
            total += node.count;
        }
        return total;
    }
    sumCounts(root);
    return root;
}

function renderFolderTree(nodes, serverId, collapsed, depth) {
    let html = '';
    for (const node of nodes) {
        const fsel = serverId === selectedServerId && node.fullName === selectedFolder ? ' selected' : '';
        const indent = 1.5 + depth * 0.75;
        const key = `s${serverId}:${node.fullName}`;
        const hasChildren = node.children.length > 0;
        const isCollapsed = collapsed.has(key);
        let chevron = '';
        if (hasChildren) {
            chevron = `<span class="folder-toggle" data-toggle-key="${esc(key)}">${isCollapsed ? '&#x25B8;' : '&#x25BE;'}</span>`;
        } else {
            chevron = '<span class="folder-toggle">&nbsp;</span>';
        }
        html += `<div class="folder-item${fsel}" data-server="${serverId}" data-folder="${esc(node.fullName)}" style="padding-left:${indent}rem">
            ${chevron}<span>${esc(node.name)}</span>
            <span class="folder-count">${node.count || ''}</span>
        </div>`;
        if (hasChildren && !isCollapsed) {
            html += renderFolderTree(node.children, serverId, collapsed, depth + 1);
        }
    }
    return html;
}

function renderServers(servers) {
    const container = document.getElementById('server-content');
    if (!servers.length) {
        container.innerHTML = `<div class="empty-state">
            <span>No servers configured</span>
            <span class="text-muted">Add one in Settings</span>
        </div>`;
        return;
    }

    const fq = serverFilter.value.trim().toLowerCase();
    const filtered = fq
        ? servers.filter(s =>
            s.name.toLowerCase().includes(fq) ||
            s.host.toLowerCase().includes(fq) ||
            (s.folders || []).some(f => f.name.toLowerCase().includes(fq))
          )
        : servers;

    if (!filtered.length) {
        container.innerHTML = '<div class="empty-state"><span>No matches</span></div>';
        return;
    }

    const collapsed = getCollapsedNodes();
    let html = '';
    for (const s of filtered) {
        const sel = s.id === selectedServerId ? ' selected' : '';
        const isSyncing = syncingServerIds.has(s.id);
        const isCancelling = cancellingServerIds.has(s.id);
        const isQueued = queuedServerIds.has(s.id);
        let syncBadge = '';
        if (isCancelling) {
            syncBadge = '<span class="sync-badge cancelling-badge">cancelling</span>';
        } else if (isSyncing && isQueued) {
            syncBadge = '<span class="sync-badge">syncing</span> <span class="sync-badge queued-badge" data-dequeue="' + s.id + '">queued</span>';
        } else if (isSyncing) {
            syncBadge = '<span class="sync-badge">syncing</span>';
        } else if (isQueued) {
            syncBadge = '<span class="sync-badge queued-badge" data-dequeue="' + s.id + '">queued</span>';
        }
        const serverKey = `srv:${s.id}`;
        const isCollapsed = collapsed.has(serverKey);
        const hasFolders = s.folders && s.folders.length > 0;
        const chevron = hasFolders
            ? `<span class="server-toggle" data-toggle-key="${serverKey}">${isCollapsed ? '&#x25B8;' : '&#x25BE;'}</span>`
            : '<span class="server-toggle-spacer"></span>';
        const totalMsgs = (s.folders || []).reduce((sum, f) => sum + (f.count || 0), 0);
        const folderCnt = (s.folders || []).length;
        const summary = folderCnt > 0
            ? `<span class="server-summary">${totalMsgs.toLocaleString()} messages, ${folderCnt} folders</span>`
            : '';
        html += `<div class="server-item${sel}" data-id="${s.id}">
            ${chevron}
            <span class="server-label">${esc(s.name)}${summary}</span>
            ${syncBadge}
        </div>`;
        if (hasFolders && !isCollapsed) {
            const tree = buildFolderTree(s.folders);
            html += renderFolderTree(tree, s.id, collapsed, 0);
        }
    }
    container.innerHTML = html;

    container.querySelectorAll('[data-toggle-key]').forEach(el => {
        el.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleCollapse(el.dataset.toggleKey);
        });
    });
    container.querySelectorAll('.server-item').forEach(el => {
        el.addEventListener('click', () => selectServer(parseInt(el.dataset.id)));
    });
    container.querySelectorAll('.folder-item').forEach(el => {
        el.addEventListener('click', () => {
            const serverId = parseInt(el.dataset.server);
            if (serverId !== selectedServerId) {
                selectedServerId = serverId;
                loadServers();
            }
            selectFolder(el.dataset.folder);
        });
    });
    container.querySelectorAll('[data-dequeue]').forEach(el => {
        el.addEventListener('click', async (e) => {
            e.stopPropagation();
            const sid = parseInt(el.dataset.dequeue);
            const srv = allServers.find(x => x.id === sid);
            const name = srv ? srv.name : `Server ${sid}`;
            const ok = await showConfirm(
                `Cancel the queued sync for "${name}"?`,
                { title: 'Cancel Queued Sync', okLabel: 'Cancel Sync', okClass: 'btn-danger' }
            );
            if (!ok) return;
            try {
                await fetch(`/api/servers/${sid}/sync/queue`, { method: 'DELETE' });
            } catch (err) {
                console.error('Dequeue failed:', err);
            }
        });
    });
}

function clearSelection() {
    selectedServerId = null;
    selectedFolder = null;
    selectedMailId = null;
    selectedMailIds.clear();
    _anchorIdx = -1;
    currentMails = [];
    totalMails = 0;
    currentPage = 0;
    document.getElementById('mail-content').innerHTML = '<div class="empty-state"><span>Select a server or folder</span></div>';
    const pagingBar = document.getElementById('mail-panel').querySelector('.paging-bar');
    if (pagingBar) pagingBar.remove();
    document.getElementById('mail-count').textContent = '';
    document.getElementById('mail-panel-title').textContent = 'Messages';
    document.getElementById('mail-filter').value = '';
    renderDetail(null);
}

function selectServer(id) {
    selectedServerId = id;
    selectedFolder = null;
    selectedMailId = null;
    selectedMailIds.clear();
    _anchorIdx = -1;
    currentPage = 0;
    searchPanel.classList.add('hidden');
    searchToggleBtn.classList.remove('btn-active');
    renderServers(allServers);
    loadMails();
    renderServerDetail();
}

function selectFolder(name) {
    selectedFolder = name;
    selectedMailId = null;
    selectedMailIds.clear();
    _anchorIdx = -1;
    currentPage = 0;
    searchPanel.classList.add('hidden');
    searchToggleBtn.classList.remove('btn-active');
    renderServers(allServers);
    loadMails();
    renderServerDetail();
}

async function renderServerDetail() {
    const container = document.getElementById('detail-content');
    const server = allServers.find(s => s.id === selectedServerId);
    if (!server) {
        renderGlobalStats();
        return;
    }

    if (selectedFolder) {
        const leafName = selectedFolder.includes('/') ? selectedFolder.split('/').pop()
            : selectedFolder.includes('.') ? selectedFolder.split('.').pop()
            : selectedFolder;
        const folderObj = (server.folders || []).find(f => f.name === selectedFolder);
        const msgCount = folderObj ? (folderObj.count || 0) : 0;
        const isImportOnly = !server.host;
        const syncing = syncingServerIds.has(server.id);
        container.innerHTML = `
            <div class="detail-section">
                <div class="detail-subject">${esc(leafName)}</div>
            </div>
            <div class="detail-section">
                <h3>Folder Info</h3>
                <div class="detail-field"><span class="label">Path</span><span class="value">${esc(selectedFolder)}</span></div>
                <div class="detail-field"><span class="label">Messages</span><span class="value">${msgCount.toLocaleString()}</span></div>
            </div>
            <div class="detail-section">
                <div class="detail-btn-group">
                    ${!isImportOnly && !syncing ? `<button class="btn" id="btn-sync-folder">Sync Folder</button>` : ''}
                    ${msgCount > 0 ? `<button class="btn btn-danger" id="btn-delete-folder-msgs">Delete Messages</button>` : ''}
                </div>
            </div>
        `;

        document.getElementById('btn-sync-folder')?.addEventListener('click', async () => {
            try {
                const resp = await fetch(`/api/servers/${server.id}/sync?folder=${encodeURIComponent(selectedFolder)}`, { method: 'POST' });
                if (resp.ok) {
                    renderServerDetail();
                } else {
                    const err = await resp.json();
                    ActivityLog.add(`Sync failed: ${err.error || 'unknown error'}`);
                }
            } catch (err) {
                ActivityLog.add(`Sync failed: ${err.message}`);
            }
        });

        document.getElementById('btn-delete-folder-msgs')?.addEventListener('click', async () => {
            if (!await showConfirm(`Delete all ${msgCount} messages in "${leafName}"?`)) return;
            try {
                const resp = await fetch(`/api/servers/${server.id}/folders?folder=${encodeURIComponent(selectedFolder)}`, { method: 'DELETE' });
                if (resp.ok) {
                    selectedFolder = null;
                    loadServers();
                    loadMails();
                    renderServerDetail();
                } else {
                    const err = await resp.json();
                    ActivityLog.add(`Delete failed: ${err.error || 'unknown error'}`);
                }
            } catch (err) {
                ActivityLog.add(`Delete failed: ${err.message}`);
            }
        });

        return;
    }

    const totalMails = (server.folders || []).reduce((sum, f) => sum + (f.count || 0), 0);
    const folderCount = (server.folders || []).length;
    const isImportOnly = !server.host;

    // Use WebSocket-driven state for sync status (avoids race with API during transitions)
    const syncing = !isImportOnly && syncingServerIds.has(server.id);

    container.innerHTML = `
        <div class="detail-section">
            <div class="detail-subject">${esc(server.name)}</div>
        </div>
        <div class="detail-section">
            <h3>Server Info</h3>
            ${server.host ? `<div class="detail-field"><span class="label">Host</span><span class="value">${esc(server.host)}:${server.port}</span></div>` : ''}
            <div class="detail-field"><span class="label">User</span><span class="value">${esc(server.username)}</span></div>
            ${server.last_sync ? `<div class="detail-field"><span class="label">Last Sync</span><span class="value">${formatDate(server.last_sync)}</span></div>` : ''}
        </div>
        <div class="detail-section">
            <h3>Contents</h3>
            <div class="detail-field"><span class="label">Messages</span><span class="value">${totalMails}</span></div>
            <div class="detail-field"><span class="label">Folders</span><span class="value">${folderCount}</span></div>
        </div>
        <div class="detail-section">
            <div class="detail-btn-group">
                ${!isImportOnly && !syncing ? `<button class="btn" id="btn-sync-server">Sync</button>` : ''}
                ${!isImportOnly && !syncing && server.last_sync ? `<button class="btn" id="btn-full-sync">Full Sync</button>` : ''}
                ${!isImportOnly && !syncing && server.last_sync ? `<button class="btn" id="btn-purge-sync">Delete &amp; Re-sync</button>` : ''}
                ${!isImportOnly && syncing ? `<button class="btn" id="btn-cancel-sync">Cancel Sync</button>` : ''}
                ${!isImportOnly && !syncing && server.is_gmail ? `<button class="btn" id="btn-backfill-labels">Backfill Labels</button>` : ''}
                ${!isImportOnly ? `<button class="btn" id="btn-test-connection">Test Connection</button>` : ''}
                <button class="btn btn-danger" id="btn-delete-server">${isImportOnly ? 'Delete Import' : 'Delete Server'}</button>
            </div>
        </div>
    `;

    // Sync (incremental)
    document.getElementById('btn-sync-server')?.addEventListener('click', async () => {
        try {
            const folderParam = selectedFolder ? `?folder=${encodeURIComponent(selectedFolder)}` : '';
            const resp = await fetch(`/api/servers/${server.id}/sync${folderParam}`, { method: 'POST' });
            if (resp.ok) {
                renderServerDetail();
            } else {
                const err = await resp.json();
                ActivityLog.add(`Sync failed: ${err.error || 'unknown error'}`);
            }
        } catch (err) {
            ActivityLog.add(`Sync failed: ${err.message}`);
        }
    });

    // Full sync (reset sync state, re-walk UIDs, skip existing)
    document.getElementById('btn-full-sync')?.addEventListener('click', async () => {
        try {
            const resp = await fetch(`/api/servers/${server.id}/sync?full=1`, { method: 'POST' });
            if (resp.ok) {
                renderServerDetail();
            } else {
                const err = await resp.json();
                ActivityLog.add(`Sync failed: ${err.error || 'unknown error'}`);
            }
        } catch (err) {
            ActivityLog.add(`Sync failed: ${err.message}`);
        }
    });

    // Delete & re-sync (purge all mails, reset sync state, re-download)
    document.getElementById('btn-purge-sync')?.addEventListener('click', async () => {
        if (!await showConfirm(
            `Delete all messages for "${server.name}" and re-download from server?`,
            { title: 'Delete & Re-sync', okLabel: 'Delete & Re-sync', okClass: 'btn-danger' }
        )) return;
        if (server.id === selectedServerId) {
            fullSyncServerId = server.id;
            document.getElementById('mail-content').innerHTML = '<div class="empty-state"><span>Synchronising...</span></div>';
            const pagingBar = document.getElementById('mail-panel').querySelector('.paging-bar');
            if (pagingBar) pagingBar.remove();
            document.getElementById('mail-count').textContent = '';
        }
        try {
            const resp = await fetch(`/api/servers/${server.id}/sync?purge=1`, { method: 'POST' });
            if (resp.ok) {
                loadServers();
                renderServerDetail();
            } else {
                const err = await resp.json();
                ActivityLog.add(`Sync failed: ${err.error || 'unknown error'}`);
                fullSyncServerId = null;
            }
        } catch (err) {
            ActivityLog.add(`Sync failed: ${err.message}`);
            fullSyncServerId = null;
        }
    });

    // Cancel sync
    document.getElementById('btn-cancel-sync')?.addEventListener('click', async () => {
        cancellingServerIds.add(server.id);
        const btn = document.getElementById('btn-cancel-sync');
        if (btn) { btn.textContent = 'Cancelling...'; btn.disabled = true; }
        renderServers(allServers);
        try {
            await fetch(`/api/servers/${server.id}/sync/cancel`, { method: 'POST' });
        } catch (err) {
            console.error('Cancel failed:', err);
        }
    });

    // Backfill labels
    document.getElementById('btn-backfill-labels')?.addEventListener('click', async () => {
        try {
            const resp = await fetch(`/api/servers/${server.id}/backfill`, { method: 'POST' });
            if (resp.ok) {
                ActivityLog.add('Label backfill started');
            } else {
                const err = await resp.json();
                ActivityLog.add(`Backfill failed: ${err.error || 'unknown error'}`);
            }
        } catch (err) {
            ActivityLog.add(`Backfill failed: ${err.message}`);
        }
    });

    // Test connection
    document.getElementById('btn-test-connection')?.addEventListener('click', async () => {
        const btn = document.getElementById('btn-test-connection');
        btn.disabled = true;
        btn.textContent = 'Testing...';
        try {
            const resp = await fetch(`/api/servers/${server.id}/test`, { method: 'POST' });
            const result = await resp.json();
            if (result.ok) {
                ActivityLog.add(`Connection OK — ${result.folders.length} folders: ${result.folders.join(', ')}`);
                await loadServers();
                renderServerDetail();
            } else {
                ActivityLog.add(`Connection failed: ${result.error}`);
            }
        } catch (err) {
            ActivityLog.add(`Connection test failed: ${err.message}`);
        }
        btn.disabled = false;
        btn.textContent = 'Test Connection';
    });

    // Delete
    document.getElementById('btn-delete-server').addEventListener('click', async () => {
        if (!await showConfirm(`Delete ${isImportOnly ? 'import' : 'server'} "${server.name}" and all its messages?`)) return;
        // Remove from tree immediately
        allServers = allServers.filter(s => s.id !== server.id);
        renderServers(allServers);
        clearSelection();
        fetch(`/api/servers/${server.id}`, { method: 'DELETE' }).catch(err => {
            console.error('Delete failed:', err);
        });
    });
}

// ── Mail list ───────────────────────────────────────────

async function loadMails() {
    if (!selectedServerId) return;
    selectedMailIds.clear();
    _anchorIdx = -1;
    const container = document.getElementById('mail-content');
    const countEl = document.getElementById('mail-count');
    const titleEl = document.getElementById('mail-panel-title');
    titleEl.textContent = 'Messages';
    mailFilter.value = '';
    container.innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';
    try {
        const params = new URLSearchParams({
            sort: sortKey,
            sortDir: sortDirParam(),
            page: currentPage,
        });
        if (selectedFolder) params.set('folder', selectedFolder);
        const resp = await fetch(`/api/servers/${selectedServerId}/mails?${params}`);
        if (!resp.ok) return;
        const data = await resp.json();
        totalMails = data.total;
        currentPage = data.page;
        countEl.textContent = data.total ? `(${data.total})` : '';
        currentMails = data.items;
        renderMails(currentMails);
    } catch (err) {
        console.error('Failed to load mails:', err);
        container.innerHTML = '<div class="empty-state"><span>Failed to load messages</span></div>';
    }
}

function sortArrow(dir) {
    if (dir === 1) {
        return '<span class="sort-indicator"><svg width="10" height="10" viewBox="0 0 10 10"><path d="M5 2L9 8H1Z" fill="currentColor"/></svg></span>';
    }
    return '<span class="sort-indicator"><svg width="10" height="10" viewBox="0 0 10 10"><path d="M5 8L1 2H9Z" fill="currentColor"/></svg></span>';
}

function sortHeader(label, key, cls, resizable = true) {
    const active = sortKey === key;
    const activeClass = active ? ' sort-active' : '';
    const arrow = active ? sortArrow(sortDir) : '';
    const handle = resizable ? '<div class="col-resize-handle"></div>' : '';
    return `<th class="${cls}${activeClass}" data-sort="${key}">${label}${arrow}${handle}</th>`;
}

function renderPagingBar() {
    const container = document.getElementById('mail-panel');
    const existing = container.querySelector('.paging-bar');
    if (existing) existing.remove();

    if (totalMails <= PAGE_SIZE) return;

    const totalPages = Math.ceil(totalMails / PAGE_SIZE);
    const pageNum = currentPage + 1;
    const bar = document.createElement('div');
    bar.className = 'paging-bar';
    bar.innerHTML = `
        <button class="btn btn-sm" id="page-prev"${currentPage === 0 ? ' disabled' : ''}>&#x2039;</button>
        <span class="paging-info">Page ${pageNum} of ${totalPages}</span>
        <span class="paging-total">(${totalMails} messages)</span>
        <button class="btn btn-sm" id="page-next"${currentPage >= totalPages - 1 ? ' disabled' : ''}>&#x203A;</button>
    `;
    container.appendChild(bar);

    document.getElementById('page-prev')?.addEventListener('click', () => {
        if (currentPage > 0) {
            currentPage--;
            if (hasSearchParams()) doSearch(); else loadMails();
        }
    });
    document.getElementById('page-next')?.addEventListener('click', () => {
        if (currentPage < totalPages - 1) {
            currentPage++;
            if (hasSearchParams()) doSearch(); else loadMails();
        }
    });
}

function _getVisibleMails() {
    // Returns the mails array currently rendered (filtered or not)
    const q = mailFilter.value.trim().toLowerCase();
    if (!q) return currentMails;
    return currentMails.filter(m =>
        (m.from_name || '').toLowerCase().includes(q) ||
        (m.from_addr || '').toLowerCase().includes(q) ||
        (m.to_addr || '').toLowerCase().includes(q) ||
        (m.subject || '').toLowerCase().includes(q)
    );
}

function _updateHeaderCheckbox() {
    const cb = document.getElementById('header-check');
    if (!cb) return;
    const visible = _getVisibleMails();
    const visibleIds = visible.map(m => m.id);
    const selectedCount = visibleIds.filter(id => selectedMailIds.has(id)).length;
    cb.checked = selectedCount > 0 && selectedCount === visibleIds.length;
    cb.indeterminate = selectedCount > 0 && selectedCount < visibleIds.length;
}

function _updateRowCheckboxes() {
    document.querySelectorAll('.mail-table tbody tr[data-id]').forEach(el => {
        const id = parseInt(el.dataset.id);
        const cb = el.querySelector('.row-check');
        if (cb) cb.checked = selectedMailIds.has(id);
        el.classList.toggle('selected', selectedMailIds.has(id) || id === selectedMailId);
    });
}

function _syncDetailPanel() {
    if (selectedMailIds.size > 1) {
        renderMultiSelect();
    } else if (selectedMailIds.size === 1) {
        const id = [...selectedMailIds][0];
        selectedMailId = id;
        selectMail(id);
    } else if (selectedMailId) {
        // single select still active, leave detail as-is
    }
}

function renderMails(mails) {
    const container = document.getElementById('mail-content');
    if (!mails || !mails.length) {
        container.innerHTML = '<div class="empty-state"><span>No messages</span></div>';
        renderPagingBar();
        return;
    }

    const colWidths = restoreColWidths();
    const colFrom = colWidths.from ? ` style="width:${colWidths.from}px"` : ' style="width:120px"';
    const colTo = colWidths.to ? ` style="width:${colWidths.to}px"` : ' style="width:120px"';
    const colSubject = colWidths.subject ? ` style="width:${colWidths.subject}px"` : '';
    const colDate = colWidths.date ? ` style="width:${colWidths.date}px"` : ' style="width:175px"';
    const colSize = colWidths.size ? ` style="width:${colWidths.size}px"` : ' style="width:80px"';

    let html = `<table class="mail-table"><colgroup>
        <col style="width:30px">
        <col${colFrom}>
        <col${colTo}>
        <col${colSubject}>
        <col${colDate}>
        <col${colSize}>
        <col style="width:40px">
    </colgroup><thead><tr>
        <th class="col-check"><input type="checkbox" id="header-check"></th>
        ${sortHeader('From', 'from', 'col-from')}
        ${sortHeader('To', 'to', 'col-to')}
        ${sortHeader('Subject', 'subject', 'col-subject')}
        ${sortHeader('Date', 'date', 'col-date')}
        ${sortHeader('Size', 'size', 'col-size')}
        <th class="col-attachments">Att.</th>
    </tr></thead><tbody>`;

    for (const m of mails) {
        const sel = (selectedMailIds.has(m.id) || m.id === selectedMailId) ? ' selected' : '';
        const unread = m.unread ? ' unread' : '';
        const held = m.legal_hold ? ' held' : '';
        const checked = selectedMailIds.has(m.id) ? ' checked' : '';
        const fromDisplay = m.from_name || m.from_addr || '';
        const toDisplay = m.to_addr || '';
        const subjectDisplay = m.subject || '(no subject)';
        const holdIcon = m.legal_hold ? '<span class="hold-indicator" title="Legal hold">&#128274;</span>' : '';
        const dupIcon = m.dup_count ? `<span class="dup-indicator" title="${m.dup_count} duplicate${m.dup_count > 1 ? 's' : ''}">&#x2733;</span>` : '';
        html += `<tr class="${sel}${unread}${held}" data-id="${m.id}">
            <td class="col-check"><input type="checkbox" class="row-check"${checked}></td>
            <td class="col-from" title="${esc(fromDisplay)}">${esc(fromDisplay)}</td>
            <td class="col-to" title="${esc(toDisplay)}">${esc(toDisplay)}</td>
            <td class="col-subject" title="${esc(subjectDisplay)}">${dupIcon}${holdIcon}${esc(subjectDisplay)}</td>
            <td class="col-date">${formatDate(m.date)}</td>
            <td class="col-size">${formatSize(m.size)}</td>
            <td class="col-attachments">${m.attachment_count || ''}</td>
        </tr>`;
    }

    html += '</tbody></table>';
    container.innerHTML = html;

    // Sort headers (ignore clicks on resize handle)
    container.querySelectorAll('th[data-sort]').forEach(el => {
        el.addEventListener('click', (e) => {
            if (e.target.classList.contains('col-resize-handle')) return;
            toggleSort(el.dataset.sort);
        });
    });

    // Column resize
    initColumnResize(container.querySelector('.mail-table'));

    // Header checkbox — toggle all visible
    document.getElementById('header-check')?.addEventListener('change', (e) => {
        const visible = _getVisibleMails();
        if (e.target.checked) {
            visible.forEach(m => selectedMailIds.add(m.id));
        } else {
            visible.forEach(m => selectedMailIds.delete(m.id));
        }
        _updateRowCheckboxes();
        _updateHeaderCheckbox();
        _syncDetailPanel();
    });

    // Row clicks
    container.querySelectorAll('tr[data-id]').forEach(el => {
        const id = parseInt(el.dataset.id);
        const visibleMails = mails; // capture for closure

        // Checkbox click — toggle without changing anchor
        el.querySelector('.row-check')?.addEventListener('click', (e) => {
            e.stopPropagation();
            if (selectedMailIds.has(id)) {
                selectedMailIds.delete(id);
            } else {
                selectedMailIds.add(id);
            }
            if (selectedMailIds.size === 0) {
                selectedMailId = null;
                renderDetail(null);
            }
            _updateRowCheckboxes();
            _updateHeaderCheckbox();
            _syncDetailPanel();
        });

        // Row click
        el.addEventListener('click', (e) => {
            // Ignore if click was on the checkbox
            if (e.target.classList.contains('row-check')) return;

            const isMeta = e.ctrlKey || e.metaKey;
            const isShift = e.shiftKey;
            const idx = visibleMails.findIndex(m => m.id === id);

            if (isShift && _anchorIdx >= 0) {
                // Range select
                const start = Math.min(_anchorIdx, idx);
                const end = Math.max(_anchorIdx, idx);
                if (!isMeta) selectedMailIds.clear();
                for (let i = start; i <= end; i++) {
                    selectedMailIds.add(visibleMails[i].id);
                }
                selectedMailId = id;
                _updateRowCheckboxes();
                _updateHeaderCheckbox();
                _syncDetailPanel();
            } else if (isMeta) {
                // Toggle in set
                if (selectedMailIds.has(id)) {
                    selectedMailIds.delete(id);
                } else {
                    selectedMailIds.add(id);
                }
                _anchorIdx = idx;
                selectedMailId = id;
                _updateRowCheckboxes();
                _updateHeaderCheckbox();
                _syncDetailPanel();
            } else {
                // Plain click — single select
                selectedMailIds.clear();
                selectedMailIds.add(id);
                _anchorIdx = idx;
                selectMail(id);
            }
        });
    });

    _updateHeaderCheckbox();
    renderPagingBar();
}

async function selectMail(id) {
    selectedMailId = id;
    // Update row highlight — include multi-selected rows
    document.querySelectorAll('.mail-table tr[data-id]').forEach(el => {
        const rowId = parseInt(el.dataset.id);
        el.classList.toggle('selected', rowId === id || selectedMailIds.has(rowId));
    });
    _updateRowCheckboxes();
    _updateHeaderCheckbox();
    document.getElementById('detail-content').innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';
    try {
        const resp = await fetch(`/api/mails/${id}`);
        if (!resp.ok) return;
        const mail = await resp.json();

        // Update server/folder selection to match this mail
        if (mail.server_id) {
            selectedServerId = mail.server_id;
            selectedFolder = mail.folder_name || null;
            // Ensure the server and parent folders are expanded
            const collapsed = getCollapsedNodes();
            collapsed.delete(`srv:${mail.server_id}`);
            if (mail.folder_name) {
                for (const sep of ['/', '.']) {
                    let idx = 0;
                    while ((idx = mail.folder_name.indexOf(sep, idx)) > 0) {
                        collapsed.delete(`s${mail.server_id}:${mail.folder_name.slice(0, idx)}`);
                        idx++;
                    }
                }
            }
            saveCollapsedNodes(collapsed);
            renderServers(allServers);
            const sel = document.querySelector('.folder-item.selected') || document.querySelector('.server-item.selected');
            if (sel) sel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }

        renderDetail(mail);
    } catch (err) {
        console.error('Failed to load mail:', err);
    }
}

// ── Detail panel ────────────────────────────────────────

function renderDetail(mail) {
    if (!mail) {
        renderGlobalStats();
        return;
    }
    const container = document.getElementById('detail-content');

    const tags = (mail.tags || []).map(t =>
        `<span class="tag">${esc(t)}<span class="tag-remove" data-tag="${esc(t)}">&times;</span></span>`
    ).join('');

    container.innerHTML = `
        <div class="detail-section">
            <div class="detail-subject">${esc(mail.subject || '(no subject)')}${mail.legal_hold ? ' <span class="hold-badge">LEGAL HOLD</span>' : ''}${mail.dup_count ? ` <span class="dup-badge">${mail.dup_count} duplicate${mail.dup_count > 1 ? 's' : ''}</span>` : ''}</div>
            <div class="detail-location">${(() => {
                const crumbs = [];
                if (mail.server_name) {
                    crumbs.push(`<a class="breadcrumb-link" data-bc-server="${mail.server_id}">${esc(mail.server_name)}</a>`);
                }
                if (mail.folder_name) {
                    const sep = mail.folder_name.includes('/') ? '/' : '.';
                    const segments = mail.folder_name.split(sep);
                    for (let i = 0; i < segments.length; i++) {
                        const path = segments.slice(0, i + 1).join(sep);
                        crumbs.push(`<a class="breadcrumb-link" data-bc-folder="${esc(path)}" data-bc-server="${mail.server_id}">${esc(segments[i])}</a>`);
                    }
                }
                return crumbs.join(' / ');
            })()}</div>
        </div>
        <div class="detail-section">
            <h3>Headers</h3>
            <div class="detail-field"><span class="label">From</span><span class="value">${esc(mail.from_addr || '')}</span></div>
            <div class="detail-field"><span class="label">To</span><span class="value">${esc(mail.to_addr || '')}</span></div>
            <div class="detail-field"><span class="label">CC</span><span class="value">${esc(mail.cc_addr || '')}</span></div>
            <div class="detail-field"><span class="label">Date</span><span class="value">${formatDate(mail.date)}</span></div>
            <div class="detail-field"><span class="label">Size</span><span class="value">${formatSize(mail.size)}</span></div>
        </div>
        ${mail.attachments?.length ? `<div class="detail-section">
            <h3>Attachments</h3>
            <div class="attachment-list">
                ${mail.attachments.map((a, i) => `<div class="attachment-item">
                    <button class="btn btn-sm attachment-name" data-att-idx="${i}">${esc(a.filename)}</button>
                    <span class="attachment-size">${formatSize(a.size)}</span>
                </div>`).join('')}
            </div>
        </div>` : ''}
        <div class="detail-section">
            <h3>Tags</h3>
            <div class="tag-list">${tags}</div>
            <div class="tag-input-row">
                <input class="tag-input" id="tag-input" placeholder="Add tag...">
                <button class="btn btn-sm" id="btn-add-tag">Add</button>
            </div>
        </div>
        <div class="detail-section">
            <div class="detail-btn-group">
                ${mail.raw_path ? `<button class="btn" id="btn-view-message">View Message</button>` : ''}
                ${mail.raw_path ? `<button class="btn" id="btn-download-eml">Download EML</button>` : ''}
                ${mail.raw_path ? `<button class="btn" id="btn-export-zip">Export</button>` : ''}
                ${mail.dup_count > 0 ? `<button class="btn" id="btn-show-dups">Show Duplicates</button>` : ''}
                <button class="btn" id="btn-toggle-hold">${mail.legal_hold ? 'Release Hold' : 'Legal Hold'}</button>
                <button class="btn btn-danger" id="btn-delete-mail"${mail.legal_hold ? ' disabled title="Message is on legal hold"' : ''}>Delete Message</button>
            </div>
        </div>
    `;

    // Breadcrumb navigation
    container.querySelectorAll('.breadcrumb-link').forEach(el => {
        el.addEventListener('click', () => {
            const serverId = parseInt(el.dataset.bcServer);
            if (el.dataset.bcFolder) {
                selectedServerId = serverId;
                selectFolder(el.dataset.bcFolder);
                renderServers(allServers);
            } else {
                selectServer(serverId);
            }
        });
    });

    // Show duplicates
    document.getElementById('btn-show-dups')?.addEventListener('click', async () => {
        try {
            const resp = await fetch(`/api/mails/${mail.id}/duplicates?sort=${sortKey}&sortDir=${sortDirParam()}`);
            if (!resp.ok) return;
            const data = await resp.json();
            currentMails = data.items;
            totalMails = data.total;
            currentPage = data.page;
            document.getElementById('mail-panel-title').textContent = `Duplicates of: ${mail.subject || '(no subject)'}`;
            document.getElementById('mail-count').textContent = data.total ? `(${data.total})` : '';
            applyFilterAndRender();
        } catch (err) {
            console.error('Failed to load duplicates:', err);
        }
    });

    // View message
    document.getElementById('btn-view-message')?.addEventListener('click', async () => {
        try {
            const res = await fetch(`/api/mails/${mail.id}/preview`);
            if (!res.ok) return;
            const data = await res.json();
            showMessagePreview(mail, data.body_text, data.body_html, data.raw_source);
        } catch (e) {
            // ignore
        }
    });

    // Download EML
    document.getElementById('btn-download-eml')?.addEventListener('click', () => {
        window.location.href = `/api/mails/${mail.id}/raw`;
    });

    // Export as zip
    document.getElementById('btn-export-zip')?.addEventListener('click', async () => {
        try {
            const resp = await fetch('/api/mails/batch/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mail_ids: [mail.id] }),
            });
            if (!resp.ok) return;
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'mail-hunter-export.zip';
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        } catch (err) {
            console.error('Export failed:', err);
        }
    });

    // Attachment downloads
    container.querySelectorAll('[data-att-idx]').forEach(btn => {
        btn.addEventListener('click', () => {
            window.location.href = `/api/mails/${mail.id}/attachments/${btn.dataset.attIdx}`;
        });
    });

    // Toggle legal hold
    document.getElementById('btn-toggle-hold')?.addEventListener('click', async () => {
        try {
            const resp = await fetch(`/api/mails/${mail.id}/hold`, { method: 'PUT' });
            if (!resp.ok) return;
            const data = await resp.json();
            mail.legal_hold = data.legal_hold;
            // Update list row state
            const listItem = currentMails.find(m => m.id === mail.id);
            if (listItem) listItem.legal_hold = data.legal_hold;
            applyFilterAndRender();
            renderDetail(mail);
        } catch (err) {
            console.error('Toggle hold failed:', err);
        }
    });

    // Delete mail
    document.getElementById('btn-delete-mail')?.addEventListener('click', async () => {
        if (!await showConfirm('Delete this message?')) return;
        try {
            const resp = await fetch(`/api/mails/${mail.id}`, { method: 'DELETE' });
            if (resp.ok) {
                selectedMailId = null;
                currentMails = currentMails.filter(m => m.id !== mail.id);
                applyFilterAndRender();
                renderServerDetail();
                loadServers();
                loadStats();
            } else {
                const err = await resp.json();
                ActivityLog.add(`Delete failed: ${err.error || 'unknown error'}`);
            }
        } catch (err) {
            console.error('Delete failed:', err);
        }
    });

    // Tag add
    const tagInput = document.getElementById('tag-input');
    const addTag = async () => {
        const tag = tagInput.value.trim();
        if (!tag) return;
        try {
            await fetch(`/api/mails/${mail.id}/tags`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tag }),
            });
            tagInput.value = '';
            selectMail(mail.id);
        } catch (err) {
            console.error('Failed to add tag:', err);
        }
    };

    document.getElementById('btn-add-tag')?.addEventListener('click', addTag);
    tagInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') addTag(); });

    // Tag remove
    container.querySelectorAll('.tag-remove').forEach(el => {
        el.addEventListener('click', async () => {
            try {
                await fetch(`/api/mails/${mail.id}/tags/${encodeURIComponent(el.dataset.tag)}`, {
                    method: 'DELETE',
                });
                selectMail(mail.id);
            } catch (err) {
                console.error('Failed to remove tag:', err);
            }
        });
    });
}

// ── Multi-select detail panel ────────────────────────────

function renderMultiSelect() {
    const container = document.getElementById('detail-content');
    const visible = _getVisibleMails();
    const selected = visible.filter(m => selectedMailIds.has(m.id));
    const totalSize = selected.reduce((sum, m) => sum + (m.size || 0), 0);

    let listHtml = selected.map(m => {
        const from = m.from_name || m.from_addr || '';
        const subject = m.subject || '(no subject)';
        return `<div class="multi-select-item">
            <div class="ms-subject" title="${esc(subject)}">${esc(subject)}</div>
            <div class="ms-from">${esc(from)}</div>
        </div>`;
    }).join('');

    container.innerHTML = `
        <div class="detail-section">
            <div class="multi-select-header">${selectedMailIds.size} Messages Selected</div>
            <div class="multi-select-size">${formatSize(totalSize)} total</div>
        </div>
        <div class="detail-section">
            <h3>Batch Actions</h3>
            <div class="detail-btn-group">
                <button class="btn" id="btn-batch-export">Export</button>
                <button class="btn" id="btn-batch-hold">Legal Hold</button>
                <button class="btn" id="btn-batch-release">Release Hold</button>
                <button class="btn btn-danger" id="btn-batch-delete">Delete</button>
            </div>
        </div>
        <div class="detail-section">
            <h3>Tags</h3>
            <div class="tag-input-row">
                <input class="tag-input" id="batch-tag-input" placeholder="Add tag to all...">
                <button class="btn btn-sm" id="btn-batch-add-tag">Add</button>
            </div>
        </div>
        <div class="detail-section">
            <h3>Selected Items</h3>
            <div class="multi-select-list">${listHtml}</div>
        </div>
    `;

    // Batch hold / release
    const batchHoldHandler = async (hold) => {
        const label = hold ? 'hold' : 'release';
        try {
            const resp = await fetch('/api/mails/batch/hold', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mail_ids: [...selectedMailIds], hold }),
            });
            if (!resp.ok) return;
            const result = await resp.json();
            ActivityLog.add(`Batch ${label}: ${result.updated} messages updated`);
            for (const m of currentMails) {
                if (selectedMailIds.has(m.id)) m.legal_hold = hold;
            }
            applyFilterAndRender();
            renderMultiSelect();
        } catch (err) {
            console.error(`Batch ${label} failed:`, err);
        }
    };
    document.getElementById('btn-batch-hold')?.addEventListener('click', () => batchHoldHandler(1));
    document.getElementById('btn-batch-release')?.addEventListener('click', () => batchHoldHandler(0));

    // Batch export
    document.getElementById('btn-batch-export')?.addEventListener('click', async () => {
        try {
            const resp = await fetch('/api/mails/batch/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mail_ids: [...selectedMailIds] }),
            });
            if (!resp.ok) return;
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'mail-hunter-export.zip';
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
            ActivityLog.add(`Exported ${selectedMailIds.size} messages as zip`);
        } catch (err) {
            console.error('Batch export failed:', err);
        }
    });

    // Batch delete
    document.getElementById('btn-batch-delete')?.addEventListener('click', async () => {
        if (!await showConfirm(`Delete ${selectedMailIds.size} messages?`)) return;
        try {
            const resp = await fetch('/api/mails/batch/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mail_ids: [...selectedMailIds] }),
            });
            if (!resp.ok) return;
            const result = await resp.json();
            ActivityLog.add(`Batch delete: ${result.deleted} deleted${result.held ? ', ' + result.held + ' on legal hold' : ''}`);
            selectedMailIds.clear();
            _anchorIdx = -1;
            selectedMailId = null;
            if (hasSearchParams()) {
                doSearch();
            } else {
                loadMails();
            }
            loadServers();
            loadStats();
            renderDetail(null);
        } catch (err) {
            console.error('Batch delete failed:', err);
        }
    });

    // Batch add tag
    const batchTagInput = document.getElementById('batch-tag-input');
    const addBatchTag = async () => {
        const tag = batchTagInput.value.trim();
        if (!tag) return;
        try {
            const resp = await fetch('/api/mails/batch/tags', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mail_ids: [...selectedMailIds], add_tags: [tag] }),
            });
            if (!resp.ok) return;
            const result = await resp.json();
            ActivityLog.add(`Batch tag: added "${tag}" to ${result.updated} items`);
            batchTagInput.value = '';
        } catch (err) {
            console.error('Batch tag failed:', err);
        }
    };

    document.getElementById('btn-batch-add-tag')?.addEventListener('click', addBatchTag);
    batchTagInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') addBatchTag(); });
}

// ── About dialog ────────────────────────────────────────

const aboutModal = document.getElementById('about-modal');
const aboutContent = document.getElementById('about-content');

async function openAbout() {
    let version = '\u2026';
    try {
        const res = await fetch('/api/version');
        if (res.ok) {
            const data = await res.json();
            version = data.version;
        }
    } catch (_) { /* ignore */ }

    aboutContent.innerHTML = `
        <div class="about-info">
            <div class="about-version">Mail Hunter v${esc(version)}</div>
            <p class="about-desc">Email archiving and search tool for managing remote mailboxes and local mail archives.</p>
            <p class="about-links">
                <a href="https://github.com/zen-logic/mail-hunter" target="_blank" rel="noopener">GitHub</a>
            </p>
            <p class="about-copyright">&copy; 2026 <a href="https://zenlogic.co.uk" target="_blank" rel="noopener">Zen Logic Ltd.</a></p>
        </div>
    `;
    aboutModal.classList.remove('hidden');
}

document.querySelector('#toolbar h1').addEventListener('click', () => openAbout());
document.getElementById('about-close').addEventListener('click', () => aboutModal.classList.add('hidden'));
aboutModal.addEventListener('click', (e) => {
    if (e.target === aboutModal) aboutModal.classList.add('hidden');
});

// ── Keyboard navigation ─────────────────────────────────

let activePanel = 'server';
const panelOrder = ['server', 'mail', 'detail'];
const panelElements = {
    server: serverPanel,
    mail: mailPanel,
    detail: detailPanel,
};

function setActivePanel(name) {
    activePanel = name;
    for (const [key, el] of Object.entries(panelElements)) {
        el.classList.toggle('panel-focused', key === name);
    }
}

// Set active panel on click
for (const [key, el] of Object.entries(panelElements)) {
    el.addEventListener('mousedown', () => setActivePanel(key));
}

// ── Modal helpers ───────────────────────────────────────

const modalIds = ['preview-modal', 'confirm-modal', 'settings-modal', 'import-modal', 'about-modal'];

function getTopmostModal() {
    for (const id of modalIds) {
        const el = document.getElementById(id);
        if (el && !el.classList.contains('hidden')) return el;
    }
    return null;
}

function closeModal(modal) {
    if (!modal) return;
    const id = modal.id;
    if (id === 'preview-modal') {
        document.getElementById('preview-close')?.click();
    } else if (id === 'confirm-modal') {
        document.getElementById('confirm-cancel')?.click();
    } else if (id === 'settings-modal') {
        closeSettings();
    } else if (id === 'import-modal') {
        closeImport();
    } else if (id === 'about-modal') {
        modal.classList.add('hidden');
    }
}

// ── Server panel navigation ─────────────────────────────

function getServerNavigableItems() {
    return [...document.querySelectorAll('#server-content .server-item, #server-content .folder-item')];
}

function getSelectedServerItem() {
    return document.querySelector('#server-content .server-item.selected, #server-content .folder-item.selected');
}

function handleServerKeys(e) {
    const items = getServerNavigableItems();
    if (!items.length) return;

    const current = getSelectedServerItem();
    const idx = current ? items.indexOf(current) : -1;

    switch (e.key) {
        case 'ArrowDown': {
            e.preventDefault();
            const next = idx < items.length - 1 ? idx + 1 : 0;
            activateServerItem(items[next]);
            break;
        }
        case 'ArrowUp': {
            e.preventDefault();
            const prev = idx > 0 ? idx - 1 : items.length - 1;
            activateServerItem(items[prev]);
            break;
        }
        case 'ArrowRight': {
            e.preventDefault();
            if (!current) break;
            // Expand collapsed server
            const toggleKey = current.querySelector('[data-toggle-key]');
            if (toggleKey) {
                const key = toggleKey.dataset.toggleKey;
                const collapsed = getCollapsedNodes();
                if (collapsed.has(key)) {
                    toggleCollapse(key);
                }
            }
            break;
        }
        case 'ArrowLeft': {
            e.preventDefault();
            if (!current) break;
            if (current.classList.contains('folder-item')) {
                // If folder has children and is expanded, collapse it
                const serverId = current.dataset.server;
                const folderName = current.dataset.folder;
                const key = `s${serverId}:${folderName}`;
                const collapsed = getCollapsedNodes();
                if (!collapsed.has(key) && document.querySelector(`.folder-item[data-folder^="${CSS.escape(folderName)}"]`) !== current) {
                    // Check if this folder actually has children by looking for a toggle
                    const toggle = current.querySelector('[data-toggle-key]');
                    if (toggle && !collapsed.has(toggle.dataset.toggleKey)) {
                        toggleCollapse(toggle.dataset.toggleKey);
                        break;
                    }
                }
                // Jump to parent server
                const serverItem = document.querySelector(`.server-item[data-id="${serverId}"]`);
                if (serverItem) activateServerItem(serverItem);
            } else if (current.classList.contains('server-item')) {
                // Collapse expanded server
                const serverKey = `srv:${current.dataset.id}`;
                const collapsed = getCollapsedNodes();
                if (!collapsed.has(serverKey)) {
                    toggleCollapse(serverKey);
                }
            }
            break;
        }
        case 'Enter': {
            e.preventDefault();
            if (!current) break;
            if (current.classList.contains('server-item')) {
                selectServer(parseInt(current.dataset.id));
            } else if (current.classList.contains('folder-item')) {
                const serverId = parseInt(current.dataset.server);
                if (serverId !== selectedServerId) {
                    selectedServerId = serverId;
                    loadServers();
                }
                selectFolder(current.dataset.folder);
            }
            break;
        }
        case '/': {
            e.preventDefault();
            serverFilter.focus();
            break;
        }
    }
}

function activateServerItem(el) {
    if (!el) return;
    // Remove existing selection
    document.querySelectorAll('#server-content .server-item.selected, #server-content .folder-item.selected')
        .forEach(s => s.classList.remove('selected'));
    el.classList.add('selected');
    el.scrollIntoView({ block: 'nearest', behavior: 'instant' });
}

// ── Mail panel navigation ───────────────────────────────

function getMailRows() {
    return [...document.querySelectorAll('.mail-table tbody tr[data-id]')];
}

function handleMailKeys(e) {
    const rows = getMailRows();
    const visible = _getVisibleMails();

    // Ctrl+A / Cmd+A — select all visible
    if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
        e.preventDefault();
        visible.forEach(m => selectedMailIds.add(m.id));
        _updateRowCheckboxes();
        _updateHeaderCheckbox();
        _syncDetailPanel();
        return;
    }

    switch (e.key) {
        case 'ArrowDown': {
            e.preventDefault();
            if (!rows.length) break;
            const sel = document.querySelector('.mail-table tr.selected');
            const idx = sel ? rows.indexOf(sel) : -1;
            if (e.shiftKey) {
                // Extend selection downward
                if (idx < rows.length - 1) {
                    const next = rows[idx + 1];
                    const nextId = parseInt(next.dataset.id);
                    selectedMailIds.add(nextId);
                    // Also keep current in set
                    if (idx >= 0) selectedMailIds.add(parseInt(rows[idx].dataset.id));
                    selectedMailId = nextId;
                    _updateRowCheckboxes();
                    _updateHeaderCheckbox();
                    _syncDetailPanel();
                    next.scrollIntoView({ block: 'nearest', behavior: 'instant' });
                }
            } else {
                if (idx < rows.length - 1) {
                    const next = rows[idx + 1];
                    const nextId = parseInt(next.dataset.id);
                    selectedMailIds.clear();
                    selectedMailIds.add(nextId);
                    _anchorIdx = visible.findIndex(m => m.id === nextId);
                    selectMail(nextId);
                    next.scrollIntoView({ block: 'nearest', behavior: 'instant' });
                } else if (idx === rows.length - 1) {
                    const nextBtn = document.getElementById('page-next');
                    if (nextBtn && !nextBtn.disabled) nextBtn.click();
                }
            }
            break;
        }
        case 'ArrowUp': {
            e.preventDefault();
            if (!rows.length) break;
            const sel = document.querySelector('.mail-table tr.selected');
            const idx = sel ? rows.indexOf(sel) : -1;
            if (e.shiftKey) {
                // Extend selection upward
                if (idx > 0) {
                    const prev = rows[idx - 1];
                    const prevId = parseInt(prev.dataset.id);
                    selectedMailIds.add(prevId);
                    if (idx >= 0) selectedMailIds.add(parseInt(rows[idx].dataset.id));
                    selectedMailId = prevId;
                    _updateRowCheckboxes();
                    _updateHeaderCheckbox();
                    _syncDetailPanel();
                    prev.scrollIntoView({ block: 'nearest', behavior: 'instant' });
                }
            } else {
                if (idx > 0) {
                    const prev = rows[idx - 1];
                    const prevId = parseInt(prev.dataset.id);
                    selectedMailIds.clear();
                    selectedMailIds.add(prevId);
                    _anchorIdx = visible.findIndex(m => m.id === prevId);
                    selectMail(prevId);
                    prev.scrollIntoView({ block: 'nearest', behavior: 'instant' });
                } else if (idx === 0) {
                    const prevBtn = document.getElementById('page-prev');
                    if (prevBtn && !prevBtn.disabled) prevBtn.click();
                }
            }
            break;
        }
        case 'Enter': {
            e.preventDefault();
            if (!selectedMailId) break;
            document.getElementById('btn-view-message')?.click();
            break;
        }
        case 'Home': {
            e.preventDefault();
            if (rows.length) {
                const first = rows[0];
                const firstId = parseInt(first.dataset.id);
                selectedMailIds.clear();
                selectedMailIds.add(firstId);
                _anchorIdx = 0;
                selectMail(firstId);
                first.scrollIntoView({ block: 'nearest', behavior: 'instant' });
            }
            break;
        }
        case 'End': {
            e.preventDefault();
            if (rows.length) {
                const last = rows[rows.length - 1];
                const lastId = parseInt(last.dataset.id);
                selectedMailIds.clear();
                selectedMailIds.add(lastId);
                _anchorIdx = visible.length - 1;
                selectMail(lastId);
                last.scrollIntoView({ block: 'nearest', behavior: 'instant' });
            }
            break;
        }
        case 'PageDown': {
            e.preventDefault();
            const nextBtn = document.getElementById('page-next');
            if (nextBtn && !nextBtn.disabled) nextBtn.click();
            break;
        }
        case 'PageUp': {
            e.preventDefault();
            const prevBtn = document.getElementById('page-prev');
            if (prevBtn && !prevBtn.disabled) prevBtn.click();
            break;
        }
        case '/': {
            e.preventDefault();
            mailFilter.focus();
            break;
        }
    }
}

// ── Global keydown handler ──────────────────────────────

document.addEventListener('keydown', (e) => {
    const ae = document.activeElement;
    const tag = ae?.tagName;
    const inInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';

    // Modal keys — always handled first
    const modal = getTopmostModal();
    if (modal) {
        if (e.key === 'Escape') {
            e.preventDefault();
            closeModal(modal);
            return;
        }
        if (e.key === 'Enter' && modal.id === 'confirm-modal' && !inInput) {
            e.preventDefault();
            document.getElementById('confirm-ok')?.click();
            return;
        }
        return; // Don't process panel keys when modal is open
    }

    // Escape in filter input — clear and blur
    if (e.key === 'Escape' && inInput) {
        e.preventDefault();
        if (ae.classList.contains('panel-filter') || ae.classList.contains('search-input')) {
            ae.value = '';
            ae.dispatchEvent(new Event('input'));
            ae.blur();
        } else {
            ae.blur();
        }
        return;
    }

    // Escape with no modal, no input — deselect
    if (e.key === 'Escape') {
        e.preventDefault();
        clearSelection();
        return;
    }

    // Don't handle navigation keys when typing in inputs
    if (inInput) return;

    // Tab / Shift+Tab — cycle active panel
    if (e.key === 'Tab') {
        e.preventDefault();
        const idx = panelOrder.indexOf(activePanel);
        if (e.shiftKey) {
            setActivePanel(panelOrder[(idx - 1 + panelOrder.length) % panelOrder.length]);
        } else {
            setActivePanel(panelOrder[(idx + 1) % panelOrder.length]);
        }
        return;
    }

    // Route to active panel handler
    if (activePanel === 'server') {
        handleServerKeys(e);
    } else if (activePanel === 'mail') {
        handleMailKeys(e);
    }
});

// Set initial active panel
setActivePanel('server');

// ── Init ────────────────────────────────────────────────

loadServers();
loadStats().then(renderGlobalStats);
renderSyncStatus(null);
