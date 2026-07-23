let sessionId = 'session_' + Date.now().toString(36);
let isLoading = false;
let messageCount = 0;
let pendingTitleEdit = null;
let pendingDelete = null;

function updateSessionInfo() {
    const el = document.getElementById('session-info');
    if (el) el.textContent = 'Session: ' + sessionId.slice(0, 16);
}

updateSessionInfo();

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
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

function formatLlmModelName(modelId) {
    if (!modelId) return '';
    const parts = modelId.split('.');
    return parts[parts.length - 1] || modelId;
}

function formatLlmUsage(usage) {
    if (!usage) return '';
    const model = formatLlmModelName(usage.model_id);
    const input = Number(usage.input_tokens) || 0;
    const output = Number(usage.output_tokens) || 0;
    if (!model && !input && !output) return '';
    const parts = [];
    if (model) parts.push(model);
    if (input || output) {
        parts.push(`${input.toLocaleString()} in · ${output.toLocaleString()} out`);
    }
    return parts.join(' · ');
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
        submitQuestion();
    }
}

function scrollToBottom() {
    const container = document.getElementById('chat-container');
    setTimeout(() => { container.scrollTop = container.scrollHeight; }, 50);
}

function askSuggestion(el) {
    document.getElementById('question-input').value = el.textContent.trim();
    submitQuestion();
}

function toggleSection(btn, targetId) {
    const target = document.getElementById(targetId);
    if (!target) return;
    target.classList.toggle('visible');
    btn.classList.toggle('active');
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
            <h2>How can I help you today?</h2>
            <p>
                Ask questions about weather forecasts, energy demand, renewable generation,
                and market prices for your entities and locations.
            </p>
            <div class="suggestions">
                <div class="suggestion" onclick="askSuggestion(this)">
                    What is the average forecasted temperature in Houston for the next 7 days?
                </div>
                <div class="suggestion" onclick="askSuggestion(this)">
                    Compare wind generation forecast vs. load for my project this week
                </div>
                <div class="suggestion" onclick="askSuggestion(this)">
                    What's the probability that temperature exceeds 35°C next week?
                </div>
                <div class="suggestion" onclick="askSuggestion(this)">
                    Show the P10, P50, and P90 solar generation forecast over the next 5 days
                </div>
            </div>
        </div>`;
}

function addUserMessage(text, sentAt = new Date()) {
    const welcome = document.getElementById('welcome');
    if (welcome) welcome.remove();

    const container = document.getElementById('chat-container');
    const msg = document.createElement('div');
    msg.className = 'message user';
    const timeLabel = formatMessageTime(sentAt);
    msg.innerHTML = `
        <div class="message-avatar">You</div>
        <div class="message-body">
            <div class="message-meta">
                <span class="message-time">${escapeHtml(timeLabel)}</span>
            </div>
            <div class="message-content">${escapeHtml(text)}</div>
        </div>`;
    container.appendChild(msg);
    scrollToBottom();
}

function addLoadingMessage() {
    const container = document.getElementById('chat-container');
    const msg = document.createElement('div');
    msg.className = 'message assistant';
    msg.id = 'loading-msg';
    msg.innerHTML = `
        <div class="message-avatar">AI</div>
        <div class="message-body">
            <div class="message-content">
                <div class="typing"><span></span><span></span><span></span></div>
            </div>
        </div>`;
    container.appendChild(msg);
    scrollToBottom();
}

function removeLoadingMessage() {
    const el = document.getElementById('loading-msg');
    if (el) el.remove();
}

function renderDataTable(data, msgId) {
    const maxRows = 100;
    let html = `<div class="data-table-wrapper" id="table-${msgId}"><div class="data-table-scroll"><table class="data-table"><thead><tr>`;
    data.columns.forEach(col => { html += `<th>${escapeHtml(col)}</th>`; });
    html += '</tr></thead><tbody>';
    data.rows.slice(0, maxRows).forEach(row => {
        html += '<tr>';
        row.forEach(val => {
            const display = val === null
                ? '<span style="color:var(--text-muted)">null</span>'
                : escapeHtml(String(val));
            html += `<td>${display}</td>`;
        });
        html += '</tr>';
    });
    html += '</tbody></table></div>';
    if (data.rows.length > maxRows) {
        html += `<div style="padding:8px 12px;font-size:11px;color:var(--text-muted);border-top:1px solid var(--border)">Showing ${maxRows} of ${data.row_count} rows</div>`;
    }
    html += '</div>';
    return html;
}

const TIME_AXIS_COLUMNS = new Set([
    'valid_datetime',
    'hour_beginning',
    'sim_datetime',
    'local_hour',
    'local_date',
]);

function formatAxisTime(value, timeZone) {
    if (value == null || value === '') return value;
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;
    if (!timeZone) return d.toISOString();
    return new Intl.DateTimeFormat('en-US', {
        timeZone,
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        hour12: false,
    }).format(d);
}

function renderChart(chartDetails, data, msgId, timeZone) {
    const el = document.getElementById('chart-' + msgId);
    if (!el || !chartDetails || !data) return;

    const cols = data.columns;
    const rows = data.rows;
    const xCol = chartDetails.x_axis[0];
    const xIdx = cols.indexOf(xCol);
    const isTimeAxis = xIdx >= 0 && TIME_AXIS_COLUMNS.has(xCol.toLowerCase());
    const colors = ['#0ea5e9', '#6366f1', '#34d399', '#fbbf24', '#f87171'];
    const traces = [];

    chartDetails.y_axis.forEach((yCol, i) => {
        const yIdx = cols.indexOf(yCol);
        if (yIdx < 0) return;
        const rawX = xIdx >= 0 ? rows.map(r => r[xIdx]) : rows.map((_, j) => j);
        const trace = {
            x: rawX,
            y: rows.map(r => r[yIdx]),
            name: yCol,
            marker: { color: colors[i % colors.length] },
        };
        if (isTimeAxis && timeZone) {
            trace.customdata = rawX.map(v => formatAxisTime(v, timeZone));
            trace.hovertemplate = '%{customdata}<br>' + yCol + ': %{y}<extra></extra>';
        }
        if (chartDetails.chart_type === 'bar') {
            trace.type = 'bar';
        } else if (chartDetails.chart_type === 'scatter') {
            trace.type = 'scatter';
            trace.mode = 'markers';
        } else {
            trace.type = 'scatter';
            trace.mode = 'lines+markers';
            trace.marker.size = 4;
        }
        traces.push(trace);
    });

    const xUnit = chartDetails.x_unit?.[0] || '';
    const xLabel = isTimeAxis && timeZone
        ? timeZone
        : (xUnit || chartDetails.x_axis[0] || '');
    const yLabel = chartDetails.y_unit?.filter(Boolean).join(', ') || chartDetails.y_axis.join(', ');

    const layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { color: '#94a3b8', size: 11 },
        margin: { t: 24, r: 16, b: 48, l: 56 },
        xaxis: { title: xLabel, gridcolor: '#2a3548', type: isTimeAxis ? 'date' : undefined },
        yaxis: { title: yLabel, gridcolor: '#2a3548' },
        legend: { orientation: 'h', y: 1.12 },
    };

    if (isTimeAxis && timeZone && traces.length) {
        const rawX = traces[0].x;
        const tickCount = Math.min(8, rawX.length);
        const step = Math.max(1, Math.floor((rawX.length - 1) / Math.max(tickCount - 1, 1)));
        const tickvals = [];
        for (let i = 0; i < rawX.length; i += step) {
            tickvals.push(rawX[i]);
        }
        if (rawX.length && tickvals[tickvals.length - 1] !== rawX[rawX.length - 1]) {
            tickvals.push(rawX[rawX.length - 1]);
        }
        layout.xaxis.tickvals = tickvals;
        layout.xaxis.ticktext = tickvals.map(v => formatAxisTime(v, timeZone));
    }

    Plotly.newPlot(el, traces, layout, { responsive: true, displaylogo: false });
}

function addAssistantMessage(response, options = {}) {
    const fromHistory = !!options.fromHistory;
    const container = document.getElementById('chat-container');
    const msgId = ++messageCount;
    const msg = document.createElement('div');
    msg.className = 'message assistant';

    const timeLabel = response.response_time ? formatMessageTime(response.response_time) : '';
    let html = '<div class="message-avatar">AI</div><div class="message-body">';
    if (timeLabel) {
        html += `<div class="message-meta"><span class="message-time">${escapeHtml(timeLabel)}</span></div>`;
    }
    html += '<div class="message-content">';

    if (response.clarity_required) {
        html += '<span class="type-badge clarify">Clarification needed</span>';
        if (response.clarifying_question?.length) {
            html += '<ul class="clarify-list">';
            response.clarifying_question.forEach(q => {
                html += `<li>${escapeHtml(q)}</li>`;
            });
            html += '</ul>';
        }
    } else if (response.answer_type) {
        const typeClass = response.answer_type.toLowerCase();
        html += `<span class="type-badge ${typeClass}">${escapeHtml(response.answer_type)}</span>`;
    }

    if (response.answer_type === 'Awareness' && response.answer) {
        html += `<div class="answer-text">${formatAnswer(response.answer)}</div>`;
    }

    if (response.assumption?.length) {
        html += '<div class="assumptions"><strong>Assumptions</strong><ul>';
        response.assumption.forEach(a => { html += `<li>${escapeHtml(a)}</li>`; });
        html += '</ul></div>';
    }

    let toggles = '';
    const isSql = response.answer_type === 'Sql' || response.answer_type === 'Metadata';
    if (isSql && response.answer) {
        toggles += `<button class="section-toggle active" onclick="toggleSection(this, 'sql-${msgId}')">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/></svg>
            SQL Query
        </button>`;
    }
    if (response.data?.rows?.length) {
        toggles += `<button class="section-toggle" onclick="toggleSection(this, 'table-${msgId}')">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/></svg>
            Data (${response.data.row_count} rows)
        </button>`;
    }
    if (toggles) html += `<div style="margin-bottom:8px">${toggles}</div>`;

    if (isSql && response.answer) {
        html += `<div class="sql-block visible" id="sql-${msgId}">${escapeHtml(response.answer)}</div>`;
    }

    if (response.data?.rows?.length) {
        html += renderDataTable(response.data, msgId);
    }

    if (response.chart_applicable && response.chart_details && response.data?.rows?.length) {
        html += `<div class="chart-wrapper"><div id="chart-${msgId}"></div></div>`;
    }

    if (response.data?.rows?.length) {
        html += `<div class="download-bar">
            <button class="btn-download" onclick="downloadCSV(${msgId})">Download CSV</button>
        </div>`;
    }

    if (response.context_warnings?.length) {
        html += `<div class="warnings">${response.context_warnings.map(escapeHtml).join('<br>')}</div>`;
    }

    if (fromHistory) {
        html += '<div class="stored-note">Stored answer — result data not kept.</div>';
    }

    const llmUsageLabel = formatLlmUsage(response.llm_usage);
    if (llmUsageLabel) {
        html += `<div class="llm-usage">${escapeHtml(llmUsageLabel)}</div>`;
    }

    html += '</div></div>';
    msg.innerHTML = html;
    msg.dataset.msgId = msgId;
    if (response.data) {
        msg.dataset.columns = JSON.stringify(response.data.columns);
        msg.dataset.rows = JSON.stringify(response.data.rows);
    }
    container.appendChild(msg);

    if (response.chart_applicable && response.chart_details && response.data) {
        setTimeout(
            () => renderChart(response.chart_details, response.data, msgId, response.timezone),
            100
        );
    }
    scrollToBottom();
}

function downloadCSV(msgId) {
    const msgEl = document.querySelector(`[data-msg-id="${msgId}"]`);
    if (!msgEl) return;
    const columns = JSON.parse(msgEl.dataset.columns);
    const rows = JSON.parse(msgEl.dataset.rows);
    let csv = columns.join(',') + '\n';
    rows.forEach(row => {
        csv += row.map(v => {
            if (v === null) return '';
            const s = String(v);
            return s.includes(',') || s.includes('"') ? `"${s.replace(/"/g, '""')}"` : s;
        }).join(',') + '\n';
    });
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'sunairio_data_' + new Date().toISOString().slice(0, 10) + '.csv';
    a.click();
    URL.revokeObjectURL(url);
}

function mintSessionId() {
    sessionId = 'session_' + Date.now().toString(36);
    updateSessionInfo();
}

async function clearSession() {
    await fetch('/api/query/clear', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ session_id: sessionId }),
    });
    mintSessionId();
    const container = document.getElementById('chat-container');
    container.innerHTML = welcomeHtml();
    messageCount = 0;
    document.querySelectorAll('.history-session-item.active').forEach(el => el.classList.remove('active'));
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
        const res = await fetch('/api/history', { headers: authHeaders() });
        if (res.status === 401) {
            logout();
            return;
        }
        if (!res.ok) return;
        const data = await res.json();
        renderHistoryList(data.items || []);
    } catch (err) {
        console.warn('Failed to load history', err);
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
        const res = await fetch(`/api/history/sessions/${encodeURIComponent(pendingTitleEdit.sessionId)}`, {
            method: 'PATCH',
            headers: authHeaders(),
            body: JSON.stringify({ title }),
        });
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
        const res = await fetch(`/api/history/sessions/${encodeURIComponent(deletedId)}`, {
            method: 'DELETE',
            headers: authHeaders(),
        });
        if (res.status === 401) {
            logout();
            return;
        }
        if (!res.ok) return;
        if (deletedId === sessionId) {
            mintSessionId();
            const container = document.getElementById('chat-container');
            container.innerHTML = welcomeHtml();
            messageCount = 0;
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
    messageCount = 0;

    try {
        const res = await fetch('/api/history/hydrate', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ session_id: resumeSessionId }),
        });
        if (res.status === 401) {
            logout();
            return;
        }
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            const container = document.getElementById('chat-container');
            container.innerHTML = welcomeHtml();
            addAssistantMessage({
                answer_type: 'Awareness',
                answer: err.detail || 'Failed to load conversation history.',
                response_time: new Date().toISOString(),
            });
            return;
        }
        const data = await res.json();
        const turns = data.turns || [];
        const container = document.getElementById('chat-container');
        container.innerHTML = '';

        if (!turns.length) {
            container.innerHTML = welcomeHtml();
            highlightHistorySession(sessionId);
            return;
        }

        turns.forEach(turn => {
            const question = turn.question || '';
            const sentAt = turn.request_time || turn.response_time || new Date().toISOString();
            addUserMessage(question, sentAt);
            addAssistantMessage(turn, { fromHistory: true });
        });
        messageCount = turns.length;
        highlightHistorySession(sessionId);
    } catch (err) {
        const container = document.getElementById('chat-container');
        container.innerHTML = welcomeHtml();
        addAssistantMessage({
            answer_type: 'Awareness',
            answer: 'Network error loading history: ' + err.message,
            response_time: new Date().toISOString(),
        });
    }
}

async function submitQuestion() {
    const input = document.getElementById('question-input');
    const question = input.value.trim();
    if (!question || isLoading) return;

    isLoading = true;
    input.value = '';
    autoResize(input);
    document.getElementById('btn-send').disabled = true;

    addUserMessage(question);
    addLoadingMessage();

    try {
        const res = await fetch('/api/query', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ question, session_id: sessionId }),
        });
        if (res.status === 401) {
            logout();
            return;
        }
        const data = await res.json();
        removeLoadingMessage();
        if (!res.ok) {
            const detail = data.detail;
            let message = 'Request failed.';
            if (typeof detail === 'string') {
                message = detail;
            } else if (detail && typeof detail === 'object') {
                message = detail.message || JSON.stringify(detail);
            }
            addAssistantMessage({
                answer_type: 'Awareness',
                answer: message + (res.status === 403 || res.status === 429 ? ' View your usage at /usage.' : ''),
                response_time: new Date().toISOString(),
            });
            if (res.status === 403 || res.status === 429) await loadUsageBadge();
        } else {
            addAssistantMessage(data);
            await loadHistory();
            await loadUsageBadge();
        }
    } catch (err) {
        removeLoadingMessage();
        addAssistantMessage({
            answer_type: 'Awareness',
            answer: 'Network error: ' + err.message,
            response_time: new Date().toISOString(),
        });
    } finally {
        isLoading = false;
        document.getElementById('btn-send').disabled = false;
        input.focus();
    }
}

async function checkHealth() {
    try {
        const res = await fetch('/api/ready');
        const data = await res.json();
        const dot = document.getElementById('status-dot');
        const text = document.getElementById('status-text');
        if (data.status === 'ready') {
            dot.className = 'status-dot connected';
            text.textContent = 'All backends connected';
        } else if (data.status === 'degraded') {
            dot.className = 'status-dot disconnected';
            text.textContent = 'Degraded: some backends unavailable';
        } else {
            dot.className = 'status-dot disconnected';
            text.textContent = data.detail || 'Backend issue';
        }
    } catch {
        document.getElementById('status-dot').className = 'status-dot disconnected';
        document.getElementById('status-text').textContent = 'Server unreachable';
    }
}

async function loadUsageBadge() {
    const badge = document.getElementById('token-badge');
    const banner = document.getElementById('limit-banner');
    if (!badge) return;
    try {
        const res = await fetch('/api/usage?granularity=summary', { headers: authHeaders() });
        if (!res.ok) return;
        const data = await res.json();
        if (data.status === 'pending_limit') {
            badge.hidden = true;
            if (banner) {
                banner.hidden = false;
                banner.className = 'limit-banner warning';
                banner.textContent = 'Account pending — an admin must set your monthly token limit before you can query.';
            }
            return;
        }
        const s = data.summary;
        if (!s) {
            badge.hidden = true;
            return;
        }
        badge.hidden = false;
        badge.textContent = `${Number(s.used_tokens).toLocaleString()} / ${Number(s.effective_limit).toLocaleString()} (${Number(s.used_input_tokens).toLocaleString()} in · ${Number(s.used_output_tokens).toLocaleString()} out)`;
        if (banner) {
            if (s.remaining_tokens <= 0) {
                banner.hidden = false;
                banner.className = 'limit-banner error';
                banner.textContent = 'Monthly token limit reached for this cycle.';
            } else if (s.used_tokens / s.effective_limit >= 0.9) {
                banner.hidden = false;
                banner.className = 'limit-banner warning';
                banner.textContent = `Approaching token limit: ${Number(s.remaining_tokens).toLocaleString()} tokens remaining this cycle.`;
            } else {
                banner.hidden = true;
            }
        }
    } catch {
        /* ignore */
    }
}

async function initChat() {
    const user = await verifySession();
    if (!user) {
        window.location.href = '/';
        return;
    }
    document.getElementById('user-name').textContent = displayName(user);
    document.getElementById('user-email').textContent = user.email || '';
    if (user.role === 'admin') {
        const dashLink = document.getElementById('dashboard-link');
        if (dashLink) dashLink.hidden = false;
    }
    await loadUsageBadge();
    await checkHealth();
    setInterval(checkHealth, 60000);
    await loadHistory();
    document.getElementById('question-input').focus();
}

initChat();
