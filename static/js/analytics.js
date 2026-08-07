let sessionId = 'analytics_' + Date.now().toString(36);
let isLoading = false;
let pendingRepId = null;

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

function hideWelcome() {
    const welcome = document.getElementById('welcome');
    if (welcome) welcome.hidden = true;
}

function appendMessage(role, html, phaseChip) {
    hideWelcome();
    const container = document.getElementById('chat-container');
    const div = document.createElement('div');
    div.className = 'message ' + role;
    let chip = '';
    if (phaseChip) {
        chip = `<div class="phase-chip">${escapeHtml(phaseChip)}</div>`;
    }
    div.innerHTML = `${chip}<div class="message-body">${html}</div>`;
    container.appendChild(div);
    scrollToBottom();
    return div;
}

function appendSystemNote(text) {
    hideWelcome();
    const container = document.getElementById('chat-container');
    const div = document.createElement('div');
    div.className = 'message system-note';
    div.textContent = text;
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
    const rows = [
        ['Analysis', summary.analysis],
        ['Entity', summary.entity],
        ['Locations', summary.locations],
        ['Forecast horizon', summary.forecast_horizon],
        ['Initialization', summary.initialization],
        ['Resolved to', summary.initialization_resolved, true],
        ['Representation', summary.forecast_representation],
        ['Chart', summary.chart],
    ];
    const dl = rows.map(([label, value, resolved]) => {
        const cls = resolved ? ' class="resolved"' : '';
        return `<dt>${escapeHtml(label)}</dt><dd${cls}>${escapeHtml(value || '—')}</dd>`;
    }).join('');

    dock.innerHTML = `
        <div class="confirm-card" id="confirm-card">
            <h3>Confirm resolved plan</h3>
            <p class="confirm-lede">Review the concrete values below. Confirming locks the plan (SQL generation comes in Phase 2).</p>
            <dl class="confirm-grid">${dl}</dl>
            <div class="confirm-actions">
                <button class="btn-primary" id="btn-confirm-plan" onclick="confirmPlan('confirm')">Confirm</button>
                <button class="btn-secondary" id="btn-revise-plan" onclick="confirmPlan('reject')">Revise</button>
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

async function submitMessage() {
    if (isLoading) return;
    const input = document.getElementById('question-input');
    const message = (input.value || '').trim();
    if (!message) return;

    clearConfirmDock();
    appendMessage('user', formatAnswer(message));
    input.value = '';
    autoResize(input);
    setLoading(true);

    const thinking = appendMessage('assistant', '<em>Consulting…</em>', 'llm1');

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
            thinking.querySelector('.message-body').innerHTML = formatAnswer(msg);
            return;
        }

        sessionId = data.session_id || sessionId;
        updateSessionInfo();

        if (data.phase === 'clarify') {
            const body = data.assistant_message || (data.questions || []).join('\n') || 'Need more detail.';
            thinking.querySelector('.phase-chip').textContent = 'clarify';
            thinking.querySelector('.message-body').innerHTML = formatAnswer(body);
            if (data.questions && data.questions.length) {
                const list = data.questions.map(q => `<li>${escapeHtml(q)}</li>`).join('');
                thinking.querySelector('.message-body').innerHTML += `<ul style="margin-top:8px;padding-left:18px">${list}</ul>`;
            }
            return;
        }

        if (data.phase === 'confirm') {
            thinking.querySelector('.phase-chip').textContent = 'resolved';
            thinking.querySelector('.message-body').innerHTML = formatAnswer(
                data.assistant_message || 'Resolved plan ready for confirmation.'
            );
            renderConfirmCard(data.summary || {}, data.rep_id);
            return;
        }

        thinking.querySelector('.message-body').innerHTML = formatAnswer(
            data.assistant_message || JSON.stringify(data)
        );
    } catch (err) {
        thinking.querySelector('.message-body').innerHTML = formatAnswer(
            'Network error: ' + (err && err.message ? err.message : err)
        );
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
            appendMessage('assistant', formatAnswer(data.message || 'Tell me what to change.'), 'revise');
        }
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
    sessionId = 'analytics_' + Date.now().toString(36);
    updateSessionInfo();
    clearConfirmDock();
    const container = document.getElementById('chat-container');
    container.innerHTML = `
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
        </div>
    `;
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
}

initAnalytics();
