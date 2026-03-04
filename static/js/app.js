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
    return (bytes / 1048576).toFixed(1) + ' MB';
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

function showMessagePreview(subject, body) {
    const modal = document.getElementById('preview-modal');
    document.getElementById('preview-title').textContent = subject || '(no subject)';
    document.getElementById('preview-body').textContent = body || '';
    modal.classList.remove('hidden');

    function close() {
        modal.classList.add('hidden');
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
    }
}

const serverPanel = document.getElementById('server-panel');
const mailPanel = document.getElementById('mail-panel');
const detailPanel = document.getElementById('detail-panel');

initResize('resize-left', serverPanel, mailPanel);
initResize('resize-right', mailPanel, detailPanel);

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
            break;
        case 'import_progress':
            ActivityLog.add(`Imported ${msg.count} messages${msg.total ? ' of ' + msg.total : ''}...`);
            break;
        case 'import_completed':
            ActivityLog.add(`Import complete: ${msg.count} messages imported`);
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
            syncingServerId = msg.server_id;
            renderSyncStatus(`Syncing ${msg.server_name || 'server'}...`);
            loadServers();
            if (msg.server_id === selectedServerId) {
                currentPage = 0;
                if (fullSyncServerId !== msg.server_id) loadMails();
                renderServerDetail();
            }
            break;
        case 'sync_progress': {
            const detail = msg.total
                ? `${msg.folder} — ${msg.count} of ${msg.total}`
                : `${msg.folder}...`;
            renderSyncStatus(`Syncing: ${detail}`);
            if (msg.count && (msg.count === 1 || msg.count % 50 === 0 || msg.count === msg.total)) {
                ActivityLog.add(`Sync: ${detail}`);
            }
            if (msg.folder_count != null) {
                const folderEl = document.querySelector(`.folder-item[data-server="${msg.server_id}"][data-folder="${CSS.escape(msg.folder)}"]`);
                if (folderEl) {
                    const countSpan = folderEl.querySelector('.folder-count');
                    if (countSpan) countSpan.textContent = msg.folder_count;
                }
            }
            break;
        }
        case 'sync_completed':
            ActivityLog.add(`Sync complete: ${msg.imported} imported, ${msg.skipped} skipped${msg.errors ? ', ' + msg.errors + ' errors' : ''}`);
            syncingServerId = null;
            if (fullSyncServerId === msg.server_id) fullSyncServerId = null;
            renderSyncStatus(null);
            loadServers();
            if (msg.server_id === selectedServerId) {
                loadMails();
                renderServerDetail();
            }
            break;
        case 'sync_cancelled':
            ActivityLog.add(`Sync cancelled (${msg.imported} imported before cancel)`);
            syncingServerId = null;
            if (fullSyncServerId === msg.server_id) fullSyncServerId = null;
            renderSyncStatus(null);
            loadServers();
            if (msg.server_id === selectedServerId) {
                loadMails();
                renderServerDetail();
            }
            break;
        case 'sync_error':
            ActivityLog.add(`Sync error: ${msg.error}`);
            syncingServerId = null;
            renderSyncStatus(null);
            renderServers(allServers);
            if (msg.server_id === selectedServerId) renderServerDetail();
            break;
        default:
            if (msg.message) ActivityLog.add(msg.message);
            break;
    }
}

connectWS();

// ── Sync status ─────────────────────────────────────────

let syncingServerId = null;
let fullSyncServerId = null;
const statusActivity = document.getElementById('status-activity');

function renderSyncStatus(detail) {
    if (!detail) {
        statusActivity.innerHTML = '';
        return;
    }
    statusActivity.innerHTML = `
        <span class="status-sync-text">${esc(detail)}</span>
        <button class="btn btn-sm status-cancel-btn" id="status-cancel-sync">Cancel</button>
    `;
    document.getElementById('status-cancel-sync')?.addEventListener('click', async () => {
        if (syncingServerId) {
            try {
                await fetch(`/api/servers/${syncingServerId}/sync/cancel`, { method: 'POST' });
            } catch (err) {
                console.error('Cancel failed:', err);
            }
        }
    });
}

// ── Server filter ───────────────────────────────────────

const serverFilter = document.getElementById('server-filter');
let allServers = [];

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
const searchFields = ['search-from', 'search-to', 'search-subject', 'search-body', 'search-date-from', 'search-date-to'];

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
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    if (selectedServerId) params.server_id = selectedServerId;
    return params;
}

function hasSearchParams() {
    const p = getSearchParams();
    return p.from || p.to || p.subject || p.body || p.date_from || p.date_to;
}

document.getElementById('search-go').addEventListener('click', () => {
    if (hasSearchParams()) { currentPage = 0; doSearch(); }
});

document.getElementById('search-clear').addEventListener('click', () => {
    searchFields.forEach(id => document.getElementById(id).value = '');
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

async function doSearch() {
    const params = getSearchParams();
    params.sort = sortKey;
    params.sortDir = sortDirParam();
    params.page = currentPage;
    const countEl = document.getElementById('mail-count');
    const titleEl = document.getElementById('mail-panel-title');
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
    const q = mailFilter.value.trim().toLowerCase();
    if (!q) {
        renderMails(currentMails);
        return;
    }
    const filtered = currentMails.filter(m =>
        (m.from_name || '').toLowerCase().includes(q) ||
        (m.from_addr || '').toLowerCase().includes(q) ||
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
            try {
                const resp = await fetch(`/api/servers/${id}`, { method: 'DELETE' });
                if (resp.ok) {
                    if (selectedServerId === id) clearSelection();
                    renderSettings();
                    loadServers();
                }
            } catch (err) {
                console.error('Delete failed:', err);
            }
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
        const data = {
            name: document.getElementById('sf-name').value.trim(),
            host: document.getElementById('sf-host').value.trim(),
            port: parseInt(document.getElementById('sf-port').value) || 993,
            username: document.getElementById('sf-username').value.trim(),
            password: document.getElementById('sf-password').value,
        };
        if (!data.host || !data.username) return;
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

function openImport() {
    importModal.classList.remove('hidden');
    importFiles = [];
    renderImportFiles();
    document.getElementById('import-status').textContent = '';
    document.getElementById('import-go').disabled = true;
    document.getElementById('import-resolve').classList.add('hidden');
    document.getElementById('import-resolve').innerHTML = '';
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

function getImportFormat() {
    return document.querySelector('input[name="import-format"]:checked')?.value || 'eml';
}

document.querySelectorAll('input[name="import-format"]').forEach(radio => {
    radio.addEventListener('change', () => {
        importFiles = [];
        renderImportFiles();
        updateImportButton();
    });
});

function updateImportButton() {
    document.getElementById('import-go').disabled = !importFiles.length;
}

// Dropzone
const dropzone = document.getElementById('import-dropzone');
const fileInput = document.getElementById('import-file-input');

dropzone.addEventListener('click', () => {
    const fmt = getImportFormat();
    fileInput.accept = fmt === 'mbox' ? '.mbox' : '.eml';
    fileInput.multiple = fmt === 'eml';
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
    const fmt = getImportFormat();
    for (const f of fileList) {
        if (fmt === 'mbox') {
            importFiles = [f];
            break;
        } else {
            importFiles.push(f);
        }
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

    const fmt = getImportFormat();
    const statusEl = document.getElementById('import-status');
    const btn = document.getElementById('import-go');
    btn.disabled = true;
    statusEl.textContent = 'Uploading...';

    try {
        const formData = new FormData();
        formData.append('format', fmt);
        for (const f of importFiles) {
            formData.append('files', f);
        }

        const resp = await fetch('/api/import', { method: 'POST', body: formData });
        const result = await resp.json();

        if (!resp.ok) {
            statusEl.textContent = `Error: ${result.error || 'upload failed'}`;
            btn.disabled = false;
            return;
        }

        if (result.ok) {
            // All messages matched existing servers — import started
            closeImport();
            return;
        }

        if (result.unmatched) {
            closeImport();
            showAddressResolver(result.import_id, result.unmatched);
        }
    } catch (err) {
        statusEl.textContent = `Error: ${err.message}`;
        btn.disabled = false;
    }
});

function showAddressResolver(importId, unmatched) {
    const modal = document.getElementById('confirm-modal');
    document.getElementById('confirm-title').textContent = 'Select Address';

    const msgEl = document.getElementById('confirm-message');
    msgEl.innerHTML = `
        <span>Which address should these messages be filed under?</span>
        <div class="import-address-list" style="margin-top: 0.5rem;">
            ${unmatched.map((a, i) => `
                <label class="import-address-item">
                    <input type="radio" name="import-address" value="${esc(a.address)}"${i === 0 ? ' checked' : ''}>
                    <span>${esc(a.address)}</span>
                </label>
            `).join('')}
        </div>
    `;

    const okBtn = document.getElementById('confirm-ok');
    okBtn.textContent = 'Import';
    okBtn.className = 'btn btn-sm btn-primary';
    modal.classList.remove('hidden');

    function cleanup() {
        modal.classList.add('hidden');
        okBtn.removeEventListener('click', onOk);
        document.getElementById('confirm-cancel').removeEventListener('click', onCancel);
        modal.removeEventListener('click', onBackdrop);
    }
    async function onOk() {
        const selected = msgEl.querySelector('input[name="import-address"]:checked')?.value;
        cleanup();
        if (selected) {
            try {
                await fetch('/api/import/resolve', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ import_id: importId, create_servers: [selected] }),
                });
            } catch (err) {
                ActivityLog.add(`Import error: ${err.message}`);
            }
        }
    }
    function onCancel() { cleanup(); }
    function onBackdrop(e) { if (e.target === modal) cleanup(); }

    okBtn.addEventListener('click', onOk);
    document.getElementById('confirm-cancel').addEventListener('click', onCancel);
    modal.addEventListener('click', onBackdrop);
}

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
    // Auto-detect format from extension
    const hasEml = files.some(f => f.name.toLowerCase().endsWith('.eml'));
    const hasMbox = files.some(f => f.name.toLowerCase().endsWith('.mbox'));
    const fmt = hasMbox && !hasEml ? 'mbox' : 'eml';

    ActivityLog.add(`Uploading ${files.length} file(s)...`);

    const formData = new FormData();
    formData.append('format', fmt);
    for (const f of files) {
        formData.append('files', f);
    }

    try {
        const resp = await fetch('/api/import', { method: 'POST', body: formData });
        const result = await resp.json();

        if (!resp.ok) {
            ActivityLog.add(`Import error: ${result.error || 'upload failed'}`);
            return;
        }

        if (result.ok) {
            // All matched — import running
            return;
        }

        if (result.unmatched) {
            showAddressResolver(result.import_id, result.unmatched);
        }
    } catch (err) {
        ActivityLog.add(`Import error: ${err.message}`);
    }
}

// ── Server list ─────────────────────────────────────────

let selectedServerId = null;
let selectedFolder = null;
let selectedMailId = null;

async function loadServers() {
    try {
        const resp = await fetch('/api/servers');
        if (!resp.ok) return;
        allServers = await resp.json();
        renderServers(allServers);
    } catch (err) {
        console.error('Failed to load servers:', err);
    }
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

    let html = '';
    for (const s of filtered) {
        const sel = s.id === selectedServerId ? ' selected' : '';
        const syncBadge = s.id === syncingServerId ? '<span class="sync-badge">syncing</span>' : '';
        html += `<div class="server-item${sel}" data-id="${s.id}">
            <span class="server-label">${esc(s.name)}</span>
            ${syncBadge}
        </div>`;
        if (s.folders) {
            for (const f of s.folders) {
                const fsel = s.id === selectedServerId && f.name === selectedFolder ? ' selected' : '';
                html += `<div class="folder-item${fsel}" data-server="${s.id}" data-folder="${esc(f.name)}">
                    <span>${esc(f.name)}</span>
                    <span class="folder-count">${f.count ?? ''}</span>
                </div>`;
            }
        }
    }
    container.innerHTML = html;

    container.querySelectorAll('.server-item').forEach(el => {
        el.addEventListener('click', () => selectServer(parseInt(el.dataset.id)));
    });
    container.querySelectorAll('.folder-item').forEach(el => {
        el.addEventListener('click', () => {
            selectServer(parseInt(el.dataset.server));
            selectFolder(el.dataset.folder);
        });
    });
}

function clearSelection() {
    selectedServerId = null;
    selectedFolder = null;
    selectedMailId = null;
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
    currentPage = 0;
    loadServers();
    loadMails();
    renderServerDetail();
}

function selectFolder(name) {
    selectedFolder = name;
    selectedMailId = null;
    currentPage = 0;
    loadMails();
    renderServerDetail();
}

async function renderServerDetail() {
    const container = document.getElementById('detail-content');
    const server = allServers.find(s => s.id === selectedServerId);
    if (!server) {
        container.innerHTML = '<div class="empty-state"><span>Select a message</span></div>';
        return;
    }

    const totalMails = (server.folders || []).reduce((sum, f) => sum + (f.count || 0), 0);
    const folderCount = (server.folders || []).length;
    const isImportOnly = !server.host;

    // Check sync status for non-import servers
    let syncing = false;
    if (!isImportOnly) {
        try {
            const resp = await fetch(`/api/servers/${server.id}/sync`);
            if (resp.ok) {
                const status = await resp.json();
                syncing = status.syncing;
            }
        } catch (err) { /* ignore */ }
    }

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
                ${!isImportOnly ? `<button class="btn" id="btn-test-connection">Test Connection</button>` : ''}
                <button class="btn btn-danger" id="btn-delete-server">Delete Server</button>
            </div>
        </div>
    `;

    // Sync (incremental)
    document.getElementById('btn-sync-server')?.addEventListener('click', async () => {
        try {
            const resp = await fetch(`/api/servers/${server.id}/sync`, { method: 'POST' });
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
        try {
            await fetch(`/api/servers/${server.id}/sync/cancel`, { method: 'POST' });
        } catch (err) {
            console.error('Cancel failed:', err);
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
        if (!await showConfirm(`Delete server "${server.name}" and all its messages?`)) return;
        try {
            const resp = await fetch(`/api/servers/${server.id}`, { method: 'DELETE' });
            if (resp.ok) {
                clearSelection();
                loadServers();
            }
        } catch (err) {
            console.error('Delete failed:', err);
        }
    });
}

// ── Mail list ───────────────────────────────────────────

async function loadMails() {
    if (!selectedServerId) return;
    const container = document.getElementById('mail-content');
    const countEl = document.getElementById('mail-count');
    const titleEl = document.getElementById('mail-panel-title');
    titleEl.textContent = 'Messages';
    mailFilter.value = '';
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

function sortHeader(label, key, cls) {
    const active = sortKey === key;
    const activeClass = active ? ' sort-active' : '';
    const arrow = active ? sortArrow(sortDir) : '';
    return `<th class="${cls}${activeClass}" data-sort="${key}">${label}${arrow}</th>`;
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

function renderMails(mails) {
    const container = document.getElementById('mail-content');
    if (!mails || !mails.length) {
        container.innerHTML = '<div class="empty-state"><span>No messages</span></div>';
        renderPagingBar();
        return;
    }

    let html = `<table class="mail-table"><thead><tr>
        ${sortHeader('From', 'from', 'col-from')}
        ${sortHeader('Subject', 'subject', 'col-subject')}
        ${sortHeader('Date', 'date', 'col-date')}
        ${sortHeader('Size', 'size', 'col-size')}
        <th class="col-attachments">Att.</th>
    </tr></thead><tbody>`;

    for (const m of mails) {
        const sel = m.id === selectedMailId ? ' selected' : '';
        const unread = m.unread ? ' unread' : '';
        html += `<tr class="${sel}${unread}" data-id="${m.id}">
            <td class="col-from">${esc(m.from_name || m.from_addr || '')}</td>
            <td class="col-subject">${esc(m.subject || '(no subject)')}</td>
            <td class="col-date">${formatDate(m.date)}</td>
            <td class="col-size">${formatSize(m.size)}</td>
            <td class="col-attachments">${m.attachment_count || ''}</td>
        </tr>`;
    }

    html += '</tbody></table>';
    container.innerHTML = html;

    container.querySelectorAll('th[data-sort]').forEach(el => {
        el.addEventListener('click', () => toggleSort(el.dataset.sort));
    });
    container.querySelectorAll('tr[data-id]').forEach(el => {
        el.addEventListener('click', () => selectMail(parseInt(el.dataset.id)));
    });

    renderPagingBar();
}

async function selectMail(id) {
    selectedMailId = id;
    document.querySelectorAll('.mail-table tr.selected').forEach(el => el.classList.remove('selected'));
    document.querySelector(`.mail-table tr[data-id="${id}"]`)?.classList.add('selected');
    try {
        const resp = await fetch(`/api/mails/${id}`);
        if (!resp.ok) return;
        const mail = await resp.json();

        // Update server/folder selection to match this mail
        if (mail.server_id && mail.server_id !== selectedServerId) {
            selectedServerId = mail.server_id;
            selectedFolder = mail.folder_name || null;
            renderServers(allServers);
        } else if (mail.folder_name && mail.folder_name !== selectedFolder) {
            selectedFolder = mail.folder_name;
            renderServers(allServers);
        }

        renderDetail(mail);
    } catch (err) {
        console.error('Failed to load mail:', err);
    }
}

// ── Detail panel ────────────────────────────────────────

function renderDetail(mail) {
    const container = document.getElementById('detail-content');
    if (!mail) {
        container.innerHTML = '<div class="empty-state"><span>Select a message</span></div>';
        return;
    }

    const tags = (mail.tags || []).map(t =>
        `<span class="tag">${esc(t)}<span class="tag-remove" data-tag="${esc(t)}">&times;</span></span>`
    ).join('');

    container.innerHTML = `
        <div class="detail-section">
            <div class="detail-subject">${esc(mail.subject || '(no subject)')}</div>
        </div>
        <div class="detail-section">
            <h3>Headers</h3>
            <div class="detail-field"><span class="label">From</span><span class="value">${esc(mail.from_addr || '')}</span></div>
            <div class="detail-field"><span class="label">To</span><span class="value">${esc(mail.to_addr || '')}</span></div>
            <div class="detail-field"><span class="label">Date</span><span class="value">${formatDate(mail.date)}</span></div>
            <div class="detail-field"><span class="label">Size</span><span class="value">${formatSize(mail.size)}</span></div>
            ${mail.cc_addr ? `<div class="detail-field"><span class="label">CC</span><span class="value">${esc(mail.cc_addr)}</span></div>` : ''}
        </div>
        ${mail.attachments?.length ? `<div class="detail-section">
            <h3>Attachments</h3>
            <div class="attachment-list">
                ${mail.attachments.map((a, i) => `<div class="attachment-item">
                    <a href="/api/mails/${mail.id}/attachments/${i}" class="attachment-link">${esc(a.filename)}</a>
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
                ${(mail.body_text || mail.body_preview) ? `<button class="btn" id="btn-view-message">View Message</button>` : ''}
                ${mail.raw_path ? `<button class="btn" id="btn-download-eml">Download EML</button>` : ''}
                <button class="btn btn-danger" id="btn-delete-mail">Delete Message</button>
            </div>
        </div>
    `;

    // View message
    document.getElementById('btn-view-message')?.addEventListener('click', () => {
        showMessagePreview(mail.subject, mail.body_text || mail.body_preview);
    });

    // Download EML
    document.getElementById('btn-download-eml')?.addEventListener('click', () => {
        window.location.href = `/api/mails/${mail.id}/raw`;
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

// ── Init ────────────────────────────────────────────────

loadServers();
