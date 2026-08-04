let currentUser = null;
let limitModalUserId = null;
let limitModalMode = 'set';
let increaseModalUserId = null;
let drawerUserId = null;
let drawerGranularity = 'day';

function fmt(n) {
    return Number(n || 0).toLocaleString();
}

function fmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function statusBadge(status) {
    const cls = status === 'active' ? 'badge-success' : 'badge-warning';
    const label = status === 'active' ? 'Active' : 'Pending limit';
    return `<span class="badge ${cls}">${label}</span>`;
}

async function apiFetch(url, options = {}) {
    const res = await fetch(url, { ...options, headers: authHeaders() });
    if (res.status === 401) {
        logout();
        throw new Error('Unauthorized');
    }
    if (res.status === 403) {
        window.location.href = '/chat';
        throw new Error('Forbidden');
    }
    return res;
}

async function loadUsers() {
    const tbody = document.getElementById('users-tbody');
    tbody.innerHTML = '<tr><td colspan="7" class="table-empty">Loading…</td></tr>';
    try {
        const res = await apiFetch('/api/admin/users');
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed to load users');
        renderUsersTable(data.items || []);
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" class="table-empty">${err.message}</td></tr>`;
    }
}

function renderUsersTable(users) {
    const tbody = document.getElementById('users-tbody');
    const rows = users.filter(u => u.role !== 'admin');
    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="table-empty">No users yet</td></tr>';
        return;
    }
    tbody.innerHTML = rows.map(u => {
        const usage = u.usage;
        const usedLimit = usage
            ? `${fmt(usage.used_tokens)} / ${fmt(usage.effective_limit)}`
            : '—';
        const inOut = usage
            ? `${fmt(usage.used_input_tokens)} / ${fmt(usage.used_output_tokens)}`
            : '—';
        const cycle = usage
            ? `${fmtDate(usage.cycle_start)} – ${fmtDate(usage.cycle_end)}`
            : '—';
        const queries = usage ? fmt(usage.query_count) : '0';
        const actions = [];
        if (u.status === 'pending_limit') {
            actions.push(`<button class="btn-sm btn-primary" onclick="openSetLimitModal(${u.id}, '${escapeAttr(u.email)}')">Set limit</button>`);
        } else if (usage) {
            actions.push(`<button class="btn-sm" onclick="openIncreaseModal(${u.id})">Increase</button>`);
        }
        actions.push(`<button class="btn-sm" onclick="openUserDrawer(${u.id}, '${escapeAttr(u.email)}')">Details</button>`);
        return `<tr>
            <td>${escapeHtml(u.email)}</td>
            <td>${statusBadge(u.status)}</td>
            <td>${usedLimit}</td>
            <td>${inOut}</td>
            <td>${cycle}</td>
            <td>${queries}</td>
            <td class="actions-cell">${actions.join(' ')}</td>
        </tr>`;
    }).join('');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}

function escapeAttr(text) {
    return (text || '').replace(/'/g, "\\'");
}

function openSetLimitModal(userId, email) {
    limitModalUserId = userId;
    limitModalMode = 'set';
    document.getElementById('limit-modal-title').textContent = 'Set monthly limit';
    document.getElementById('limit-modal-note').textContent = `Set the combined token limit for ${email}. The monthly cycle starts today.`;
    document.getElementById('limit-input').value = '100000';
    document.getElementById('limit-modal').hidden = false;
}

function closeLimitModal() {
    document.getElementById('limit-modal').hidden = true;
    limitModalUserId = null;
}

async function submitLimitModal() {
    const val = parseInt(document.getElementById('limit-input').value, 10);
    if (!limitModalUserId || !val || val <= 0) return;
    const res = await apiFetch(`/api/admin/users/${limitModalUserId}/limit`, {
        method: 'PATCH',
        body: JSON.stringify({ base_monthly_limit: val }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        alert(typeof data.detail === 'string' ? data.detail : 'Failed to set limit');
        return;
    }
    closeLimitModal();
    await loadUsers();
    if (drawerUserId === limitModalUserId) await refreshDrawerUsage();
}

function openIncreaseModal(userId) {
    increaseModalUserId = userId;
    document.getElementById('increase-input').value = '10000';
    document.getElementById('increase-modal').hidden = false;
}

function closeIncreaseModal() {
    document.getElementById('increase-modal').hidden = true;
    increaseModalUserId = null;
}

async function submitIncreaseModal() {
    const val = parseInt(document.getElementById('increase-input').value, 10);
    if (!increaseModalUserId || !val || val <= 0) return;
    const res = await apiFetch(`/api/admin/users/${increaseModalUserId}/limit/increase`, {
        method: 'POST',
        body: JSON.stringify({ bonus_tokens: val }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        alert(typeof data.detail === 'string' ? data.detail : 'Failed to increase limit');
        return;
    }
    closeIncreaseModal();
    await loadUsers();
    if (drawerUserId === increaseModalUserId) await refreshDrawerUsage();
}

function closeDrawer() {
    document.getElementById('user-drawer').hidden = true;
    drawerUserId = null;
}

async function openUserDrawer(userId, email) {
    drawerUserId = userId;
    drawerGranularity = 'day';
    document.getElementById('drawer-title').textContent = email;
    document.getElementById('user-drawer').hidden = false;
    await refreshDrawerContent();
}

async function refreshDrawerUsage() {
    if (!drawerUserId) return;
    await refreshDrawerContent();
}

async function refreshDrawerContent() {
    const body = document.getElementById('drawer-body');
    body.innerHTML = '<p class="table-empty">Loading…</p>';
    try {
        const [usageRes, historyRes] = await Promise.all([
            apiFetch(`/api/admin/users/${drawerUserId}/usage?granularity=${drawerGranularity}`),
            apiFetch(`/api/admin/users/${drawerUserId}/history`),
        ]);
        const usageData = await usageRes.json();
        const historyData = await historyRes.json();
        body.innerHTML = renderDrawerHtml(usageData, historyData.items || []);
    } catch (err) {
        body.innerHTML = `<p class="table-empty">${err.message}</p>`;
    }
}

function renderDrawerHtml(usageData, sessions) {
    const s = usageData.summary;
    let summaryHtml = '<p class="table-empty">No limit configured</p>';
    if (s) {
        summaryHtml = `
            <div class="usage-summary-grid">
                <div class="usage-stat"><span class="label">Used</span><span class="value">${fmt(s.used_tokens)} / ${fmt(s.effective_limit)}</span></div>
                <div class="usage-stat"><span class="label">Remaining</span><span class="value">${fmt(s.remaining_tokens)}</span></div>
                <div class="usage-stat"><span class="label">Input</span><span class="value">${fmt(s.used_input_tokens)}</span></div>
                <div class="usage-stat"><span class="label">Output</span><span class="value">${fmt(s.used_output_tokens)}</span></div>
                <div class="usage-stat"><span class="label">Cycle</span><span class="value">${fmtDate(s.cycle_start)} – ${fmtDate(s.cycle_end)}</span></div>
            </div>`;
    }

    const tabs = ['day', 'week', 'month', 'question'].map(g =>
        `<button class="usage-tab ${drawerGranularity === g ? 'active' : ''}" onclick="setDrawerGranularity('${g}')">${g}</button>`
    ).join('');

    const breakdown = (usageData.breakdown || []).map(row => `
        <tr>
            <td>${escapeHtml(row.label)}</td>
            <td>${fmt(row.input_tokens)}</td>
            <td>${fmt(row.output_tokens)}</td>
            <td>${fmt(row.total_tokens)}</td>
            <td>${fmt(row.query_count)}</td>
        </tr>`).join('') || '<tr><td colspan="5" class="table-empty">No usage in this cycle</td></tr>';

    const historyRows = sessions.slice(0, 20).map(sess => `
        <tr>
            <td>${escapeHtml(sess.title)}</td>
            <td>${fmt(sess.turn_count)}</td>
            <td><button class="btn-sm" onclick="viewThread('${escapeAttr(sess.session_id)}')">View</button></td>
        </tr>`).join('') || '<tr><td colspan="3" class="table-empty">No conversations</td></tr>';

    return `
        ${summaryHtml}
        <h4 class="drawer-subheading">Usage breakdown</h4>
        <div class="usage-tabs">${tabs}</div>
        <div class="table-wrap">
            <table class="data-table compact">
                <thead><tr><th>Label</th><th>In</th><th>Out</th><th>Total</th><th>Queries</th></tr></thead>
                <tbody>${breakdown}</tbody>
            </table>
        </div>
        <h4 class="drawer-subheading">Conversation history</h4>
        <div class="table-wrap">
            <table class="data-table compact">
                <thead><tr><th>Title</th><th>Turns</th><th></th></tr></thead>
                <tbody>${historyRows}</tbody>
            </table>
        </div>
        <div id="thread-panel"></div>`;
}

async function setDrawerGranularity(g) {
    drawerGranularity = g;
    await refreshDrawerContent();
}

async function viewThread(sessionId) {
    const panel = document.getElementById('thread-panel');
    panel.innerHTML = '<p class="table-empty">Loading thread…</p>';
    const res = await apiFetch(`/api/admin/users/${drawerUserId}/history/thread?session_id=${encodeURIComponent(sessionId)}`);
    const data = await res.json();
    if (!res.ok) {
        panel.innerHTML = '<p class="table-empty">Failed to load thread</p>';
        return;
    }
    const turns = (data.turns || []).map(t => {
        const usage = t.llm_usage;
        const usageLabel = usage ? `${fmt(usage.input_tokens)} in · ${fmt(usage.output_tokens)} out` : '';
        return `<div class="thread-turn">
            <div class="thread-q"><strong>Q:</strong> ${escapeHtml(t.original_question || t.question || '')}</div>
            <div class="thread-a"><strong>A:</strong> ${escapeHtml((t.answer || '').slice(0, 500))}${(t.answer || '').length > 500 ? '…' : ''}</div>
            ${usageLabel ? `<div class="thread-meta">${usageLabel}</div>` : ''}
        </div>`;
    }).join('');
    panel.innerHTML = `<h4 class="drawer-subheading">${escapeHtml(data.title)}</h4>${turns || '<p class="table-empty">Empty thread</p>'}`;
}

async function initDashboard() {
    currentUser = await verifySession();
    if (!currentUser) {
        window.location.href = '/';
        return;
    }
    if (currentUser.role !== 'admin') {
        window.location.href = '/chat';
        return;
    }
    await loadUsers();
}

initDashboard();
