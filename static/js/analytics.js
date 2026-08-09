let sessionId = 'analytics_' + Date.now().toString(36);
let isLoading = false;
let pendingRepId = null;
let pendingTitleEdit = null;
let pendingDelete = null;

function updateSessionInfo() {
    const el = document.getElementById('session-info');
    if (el) el.textContent = 'Session: ' + sessionId.slice(0, 18);
}

updateSessionInfo();

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
}

/** Format a Date or ISO string for message timestamps (local time). */
function formatMessageTime(value) {
    const d = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
    });
}

function truncateText(text, maxLen) {
    const s = (text || '').trim();
    if (s.length <= maxLen) return s;
    return s.slice(0, maxLen - 1).trimEnd() + '…';
}

function formatAnswer(text) {
    if (!text) return '';
    let html = escapeHtml(text);
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\n/g, '<br>');
    return html;
}

function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        submitMessage();
    }
}

function scrollToBottom() {
    const container = document.getElementById('chat-container');
    setTimeout(() => { container.scrollTop = container.scrollHeight; }, 50);
}

function toggleHistorySidebar() {
    const page = document.body;
    const collapsed = page.classList.toggle('history-collapsed');
    const expandBtn = document.getElementById('history-expand-btn');
    if (expandBtn) expandBtn.hidden = !collapsed;
}

function welcomeHtml() {
    return `
        <div class="welcome" id="welcome">
            <h2>Describe the analysis you need</h2>
            <p>
                I’ll clarify your analytical intent, resolve platform details, and ask you to
                confirm the plan — without generating SQL yet.
            </p>
            <div class="suggestions">
                <div class="suggestion" onclick="askSuggestion(this)">
                    Temperature forecast for Houston next week
                </div>
                <div class="suggestion" onclick="askSuggestion(this)">
                    Show P50 load forecast for all ERCOT load zones for the next 7 days
                </div>
                <div class="suggestion" onclick="askSuggestion(this)">
                    What locations are available in ERCOT?
                </div>
            </div>
        </div>`;
}

function hideWelcome() {
    const welcome = document.getElementById('welcome');
    if (welcome) welcome.remove();
}

function phaseChipFromAep(aep) {
    if (!aep) return '';
    const status = (aep.status || '').toLowerCase();
    const intent = ((aep.query || {}).intent || '').toLowerCase();
    if (status === 'clarification_required') return 'clarify';
    if (intent === 'metadata' || intent === 'awareness') return 'answered';
    if (status === 'resolved') return 'confirm';
    return '';
}

function appendMessage(role, html, phaseChip, sentAt = new Date()) {
    hideWelcome();
    const container = document.getElementById('chat-container');
    const div = document.createElement('div');
    div.className = 'message ' + role;
    const timeLabel = formatMessageTime(sentAt);
    const avatar = role === 'user' ? 'You' : 'AI';
    let chip = '';
    if (phaseChip) {
        chip = `<div class="phase-chip">${escapeHtml(phaseChip)}</div>`;
    }
    const meta = timeLabel
        ? `<div class="message-meta"><span class="message-time">${escapeHtml(timeLabel)}</span></div>`
        : '';
    div.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-body">
            ${meta}
            ${chip}
            <div class="message-content">${html}</div>
        </div>`;
    container.appendChild(div);
    scrollToBottom();
    return div;
}

function appendSystemNote(text, sentAt = new Date()) {
    hideWelcome();
    const container = document.getElementById('chat-container');
    const div = document.createElement('div');
    div.className = 'message system-note';
    const timeLabel = formatMessageTime(sentAt);
    div.innerHTML = timeLabel
        ? `<div class="message-meta"><span class="message-time">${escapeHtml(timeLabel)}</span></div>${escapeHtml(text)}`
        : escapeHtml(text);
    container.appendChild(div);
    scrollToBottom();
}

function setLoading(loading) {
    isLoading = loading;
    const btn = document.getElementById('btn-send');
    const input = document.getElementById('question-input');
    if (btn) btn.disabled = loading;
    if (input) input.disabled = loading;
}

function clearConfirmDock() {
    const dock = document.getElementById('confirm-dock');
    if (!dock) return;
    dock.hidden = true;
    dock.innerHTML = '';
    pendingRepId = null;
}

function renderConfirmCard(summary, repId) {
    pendingRepId = repId;
    const dock = document.getElementById('confirm-dock');
    if (!dock) return;

    const isMeta = (summary.analysis || '').toLowerCase().includes('metadata')
        || (summary.forecast_representation || '').toLowerCase().includes('catalog');

    const rows = isMeta
        ? [
            ['Request', summary.analysis],
            ['Entity', summary.entity],
            ['Looking up', summary.locations],
        ]
        : [
            ['Analysis', summary.analysis],
            ['Entity', summary.entity],
            ['Locations', summary.locations],
            ['Time period', summary.forecast_horizon],
            ['Initialization', summary.initialization],
            ['Resolved to', summary.initialization_resolved, true],
            ['Representation', summary.forecast_representation],
            ['Chart', summary.chart],
        ].filter(([, value]) => {
            const v = (value || '').toString().trim();
            return v && v.toUpperCase() !== 'N/A' && v !== 'None';
        });

    const dl = rows.map(([label, value, resolved]) => {
        const cls = resolved ? ' class="resolved"' : '';
        return `<dt>${escapeHtml(label)}</dt><dd${cls}>${escapeHtml(value || '—')}</dd>`;
    }).join('');

    dock.innerHTML = `
        <div class="confirm-card" id="confirm-card">
            <h3>${isMeta ? 'Confirm this lookup' : 'Quick check'}</h3>
            <p class="confirm-lede">${isMeta
                ? 'These are the details I’ll use for the catalog lookup.'
                : 'These are the concrete values behind the plan above.'}</p>
            <dl class="confirm-grid">${dl}</dl>
            <div class="confirm-actions">
                <button class="btn-primary" id="btn-confirm-plan" onclick="confirmPlan('confirm')">Yes, confirm</button>
                <button class="btn-secondary" id="btn-revise-plan" onclick="confirmPlan('reject')">No, revise</button>
            </div>
        </div>
    `;
    dock.hidden = false;
    scrollToBottom();
}

function renderConfirmedCard(summary, message) {
    const dock = document.getElementById('confirm-dock');
    if (!dock) return;
    dock.innerHTML = `
        <div class="confirm-card confirmed-banner">
            <h3>Plan confirmed</h3>
            <p class="confirm-lede">${escapeHtml(message)}</p>
            <dl class="confirm-grid">
                <dt>Analysis</dt><dd>${escapeHtml((summary && summary.analysis) || '—')}</dd>
                <dt>Entity</dt><dd>${escapeHtml((summary && summary.entity) || '—')}</dd>
                <dt>Horizon</dt><dd>${escapeHtml((summary && summary.forecast_horizon) || '—')}</dd>
                <dt>Initialization</dt><dd class="resolved">${escapeHtml((summary && summary.initialization_resolved) || '—')}</dd>
            </dl>
        </div>
    `;
    dock.hidden = false;
    pendingRepId = null;
}

function mintSessionId() {
    sessionId = 'analytics_' + Date.now().toString(36);
    updateSessionInfo();
}

function renderHistoryList(items) {
    const list = document.getElementById('history-list');
    if (!list) return;

    if (!items?.length) {
        list.innerHTML = '<div class="history-empty" id="history-empty">No conversations yet</div>';
        return;
    }

    list.innerHTML = items.map(item => {
        const time = formatMessageTime(item.updated_at) || '';
        const title = truncateText(item.title || 'Untitled conversation', 80);
        const turns = item.turn_count > 0 ? `${item.turn_count} turn${item.turn_count === 1 ? '' : 's'}` : '';
        return `
            <div class="history-session-item" data-session-id="${escapeHtml(item.session_id || '')}">
                <button type="button" class="history-session-open"
                    data-session-id="${escapeHtml(item.session_id || '')}"
                    data-title="${escapeHtml(item.title || '')}"
                    onclick="onHistorySessionClick(this)">
                    <span class="history-session-time">${escapeHtml(time)}</span>
                    <span class="history-session-title">${escapeHtml(title)}</span>
                    ${turns ? `<span class="history-session-turns">${escapeHtml(turns)}</span>` : ''}
                </button>
                <button type="button" class="history-session-edit" title="Rename"
                    data-session-id="${escapeHtml(item.session_id || '')}"
                    data-title="${escapeHtml(item.title || '')}"
                    onclick="event.stopPropagation(); openTitleEditModal(this)">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                    </svg>
                </button>
                <button type="button" class="history-session-delete" title="Delete"
                    data-session-id="${escapeHtml(item.session_id || '')}"
                    data-title="${escapeHtml(item.title || '')}"
                    onclick="event.stopPropagation(); openDeleteModal(this)">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M3 6h18M8 6V4h8v2M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>
                        <path d="M10 11v6M14 11v6"/>
                    </svg>
                </button>
            </div>`;
    }).join('');
}

async function loadHistory() {
    try {
        const res = await fetch('/api/v2/history', { headers: authHeaders() });
        if (res.status === 401) {
            logout();
            return;
        }
        if (!res.ok) return;
        const data = await res.json();
        renderHistoryList(data.items || []);
        highlightHistorySession(sessionId);
    } catch (err) {
        console.warn('Failed to load analytics history', err);
    }
}

function onHistorySessionClick(btn) {
    const resumeSessionId = btn.dataset.sessionId;
    if (!resumeSessionId) return;
    resumeHistorySession(resumeSessionId);
}

function openTitleEditModal(btn) {
    pendingTitleEdit = {
        sessionId: btn.dataset.sessionId,
        title: btn.dataset.title || '',
    };
    const input = document.getElementById('history-title-input');
    if (input) {
        input.value = pendingTitleEdit.title;
        input.focus();
    }
    const modal = document.getElementById('history-title-modal');
    if (modal) modal.hidden = false;
}

function closeTitleEditModal() {
    const modal = document.getElementById('history-title-modal');
    if (modal) modal.hidden = true;
    pendingTitleEdit = null;
}

function handleTitleInputKeyDown(e) {
    if (e.key === 'Enter') {
        e.preventDefault();
        saveSessionTitle();
    }
}

async function saveSessionTitle() {
    if (!pendingTitleEdit?.sessionId) {
        closeTitleEditModal();
        return;
    }
    const input = document.getElementById('history-title-input');
    const title = input?.value?.trim() || '';
    if (!title) return;

    try {
        const res = await fetch(
            `/api/v2/history/sessions/${encodeURIComponent(pendingTitleEdit.sessionId)}`,
            {
                method: 'PATCH',
                headers: authHeaders(),
                body: JSON.stringify({ title }),
            }
        );
        if (res.status === 401) {
            logout();
            return;
        }
        if (!res.ok) return;
        closeTitleEditModal();
        await loadHistory();
    } catch (err) {
        console.warn('Failed to update title', err);
    }
}

function openDeleteModal(btn) {
    pendingDelete = {
        sessionId: btn.dataset.sessionId,
        title: btn.dataset.title || '',
    };
    const preview = document.getElementById('history-delete-preview');
    if (preview) preview.textContent = pendingDelete.title || 'Untitled conversation';
    const modal = document.getElementById('history-delete-modal');
    if (modal) modal.hidden = false;
}

function closeDeleteModal() {
    const modal = document.getElementById('history-delete-modal');
    if (modal) modal.hidden = true;
    pendingDelete = null;
}

async function confirmDeleteSession() {
    if (!pendingDelete?.sessionId) {
        closeDeleteModal();
        return;
    }
    const deletedId = pendingDelete.sessionId;
    closeDeleteModal();

    try {
        const res = await fetch(
            `/api/v2/history/sessions/${encodeURIComponent(deletedId)}`,
            {
                method: 'DELETE',
                headers: authHeaders(),
            }
        );
        if (res.status === 401) {
            logout();
            return;
        }
        if (!res.ok) return;
        if (deletedId === sessionId) {
            mintSessionId();
            clearConfirmDock();
            const container = document.getElementById('chat-container');
            container.innerHTML = welcomeHtml();
        }
        await loadHistory();
    } catch (err) {
        console.warn('Failed to delete session', err);
    }
}

function highlightHistorySession(targetSessionId) {
    document.querySelectorAll('.history-session-item.active').forEach(el => el.classList.remove('active'));
    if (!targetSessionId) return;
    document.querySelectorAll('.history-session-item').forEach(el => {
        if (el.dataset.sessionId === targetSessionId) {
            el.classList.add('active');
        }
    });
}

async function resumeHistorySession(resumeSessionId) {
    sessionId = resumeSessionId;
    updateSessionInfo();
    clearConfirmDock();

    try {
        const res = await fetch('/api/v2/history/hydrate', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ session_id: resumeSessionId }),
        });
        if (res.status === 401) {
            logout();
            return;
        }
        const container = document.getElementById('chat-container');
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            container.innerHTML = welcomeHtml();
            appendMessage(
                'assistant',
                formatAnswer(err.detail || 'Failed to load conversation history.'),
                '',
                new Date()
            );
            return;
        }
        const data = await res.json();
        const turns = data.turns || [];
        container.innerHTML = '';

        if (!turns.length) {
            container.innerHTML = welcomeHtml();
            highlightHistorySession(sessionId);
            return;
        }

        turns.forEach(turn => {
            const role = turn.role === 'user' ? 'user' : 'assistant';
            const sentAt = turn.created_at || new Date().toISOString();
            const chip = role === 'assistant' ? phaseChipFromAep(turn.aep) : '';
            appendMessage(role, formatAnswer(turn.content || ''), chip, sentAt);
        });

        if (data.pending_rep && data.pending_rep.rep_id) {
            renderConfirmCard(data.pending_rep.summary || {}, data.pending_rep.rep_id);
        }

        highlightHistorySession(sessionId);
    } catch (err) {
        const container = document.getElementById('chat-container');
        container.innerHTML = welcomeHtml();
        appendMessage(
            'assistant',
            formatAnswer('Network error loading history: ' + err.message),
            '',
            new Date()
        );
    }
}

async function submitMessage() {
    if (isLoading) return;
    const input = document.getElementById('question-input');
    const message = (input.value || '').trim();
    if (!message) return;

    clearConfirmDock();
    appendMessage('user', formatAnswer(message), '', new Date());
    input.value = '';
    autoResize(input);
    setLoading(true);

    const thinking = appendMessage('assistant', '<em>Consulting…</em>', 'llm1', new Date());

    try {
        const res = await fetch('/api/v2/consult', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ message, session_id: sessionId }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            const detail = data.detail;
            const msg = typeof detail === 'string'
                ? detail
                : (detail && detail.message) || res.statusText || 'Request failed';
            thinking.querySelector('.message-content').innerHTML = formatAnswer(msg);
            return;
        }

        sessionId = data.session_id || sessionId;
        updateSessionInfo();

        const contentEl = thinking.querySelector('.message-content');
        const chipEl = thinking.querySelector('.phase-chip');

        if (data.phase === 'answered') {
            if (chipEl) chipEl.textContent = 'answered';
            contentEl.innerHTML = formatAnswer(
                data.assistant_message || 'Happy to help — what would you like to analyze?'
            );
            await loadHistory();
            return;
        }

        if (data.phase === 'clarify') {
            const body = data.assistant_message || (data.questions || []).join('\n') || 'Need more detail.';
            if (chipEl) chipEl.textContent = 'clarify';
            contentEl.innerHTML = formatAnswer(body);
            const questions = data.questions || [];
            const extras = questions.filter(q => body.indexOf(q) === -1);
            if (extras.length) {
                const list = extras.map(q => `<li>${escapeHtml(q)}</li>`).join('');
                contentEl.innerHTML += `<ul style="margin-top:8px;padding-left:18px">${list}</ul>`;
            }
            await loadHistory();
            return;
        }

        if (data.phase === 'confirm') {
            if (chipEl) chipEl.textContent = 'confirm';
            contentEl.innerHTML = formatAnswer(
                data.assistant_message || 'Does this look right?'
            );
            renderConfirmCard(data.summary || {}, data.rep_id);
            await loadHistory();
            return;
        }

        contentEl.innerHTML = formatAnswer(
            data.assistant_message || JSON.stringify(data)
        );
        await loadHistory();
    } catch (err) {
        const contentEl = thinking.querySelector('.message-content');
        if (contentEl) {
            contentEl.innerHTML = formatAnswer(
                'Network error: ' + (err && err.message ? err.message : err)
            );
        }
    } finally {
        setLoading(false);
    }
}

async function confirmPlan(action) {
    if (!pendingRepId || isLoading) return;
    setLoading(true);
    const confirmBtn = document.getElementById('btn-confirm-plan');
    const reviseBtn = document.getElementById('btn-revise-plan');
    if (confirmBtn) confirmBtn.disabled = true;
    if (reviseBtn) reviseBtn.disabled = true;

    try {
        const res = await fetch('/api/v2/confirm', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({
                session_id: sessionId,
                rep_id: pendingRepId,
                action,
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            appendSystemNote(
                typeof data.detail === 'string' ? data.detail : 'Confirm failed'
            );
            if (confirmBtn) confirmBtn.disabled = false;
            if (reviseBtn) reviseBtn.disabled = false;
            return;
        }

        if (data.phase === 'confirmed') {
            renderConfirmedCard(data.summary, data.message);
            appendSystemNote(data.message);
        } else {
            clearConfirmDock();
            appendMessage('assistant', formatAnswer(data.message || 'Tell me what to change.'), 'revise', new Date());
        }
        await loadHistory();
    } catch (err) {
        appendSystemNote('Network error during confirm');
        if (confirmBtn) confirmBtn.disabled = false;
        if (reviseBtn) reviseBtn.disabled = false;
    } finally {
        setLoading(false);
    }
}

function askSuggestion(el) {
    const input = document.getElementById('question-input');
    input.value = el.textContent.trim();
    submitMessage();
}

function clearAnalyticsSession() {
    mintSessionId();
    clearConfirmDock();
    const container = document.getElementById('chat-container');
    container.innerHTML = welcomeHtml();
    document.querySelectorAll('.history-session-item.active').forEach(el => el.classList.remove('active'));
}

function logout() {
    clearAuth();
    window.location.href = '/';
}

async function initAnalytics() {
    const user = await verifySession();
    if (!user && (await fetch('/api/me', { headers: authHeaders() }).then(r => r.status) === 401)) {
        window.location.href = '/';
        return;
    }
    const u = user || getUser() || {};
    const nameEl = document.getElementById('user-name');
    const emailEl = document.getElementById('user-email');
    if (nameEl) nameEl.textContent = displayName(u);
    if (emailEl) emailEl.textContent = u.email || '';

    const statusDot = document.getElementById('status-dot');
    const statusText = document.getElementById('status-text');
    try {
        const ready = await fetch('/api/ready');
        if (ready.ok) {
            statusDot.className = 'status-dot connected';
            statusText.textContent = 'Ready';
        } else {
            statusDot.className = 'status-dot';
            statusText.textContent = 'Degraded';
        }
    } catch {
        statusDot.className = 'status-dot';
        statusText.textContent = 'Offline';
    }

    await loadHistory();
    const input = document.getElementById('question-input');
    if (input) input.focus();
}

initAnalytics();
