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

function renderSuggestionsSection(suggestions, msgId, readOnly) {
    const items = (suggestions || []).map((s) => String(s).trim()).filter(Boolean);
    if (!items.length) return '';

    const sectionId = `suggestions-${msgId}`;
    if (readOnly) {
        const body = `<ul>${items.map((s) => `<li>${escapeHtml(s)}</li>`).join('')}</ul>`;
        return renderCopySection('suggestions-section', 'Suggestions', body, items.join('\n'), sectionId);
    }

    const body = (
        `<div class="suggestions-picker">` +
        `<ul class="suggestions-picker-list">` +
        items.map((s, i) => (
            `<li class="suggestion-option">` +
            `<label>` +
            `<input type="checkbox" class="suggestion-checkbox" data-suggestion-idx="${i}" checked>` +
            `<span>${escapeHtml(s)}</span>` +
            `</label>` +
            `</li>`
        )).join('') +
        `</ul>` +
        `<button type="button" class="btn-secondary suggestions-submit" ` +
        `onclick="submitSelectedSuggestions('${sectionId}')">Apply selected</button>` +
        `</div>`
    );
    return renderCopySection('suggestions-section', 'Suggestions', body, items.join('\n'), sectionId);
}

async function submitSelectedSuggestions(sectionId) {
    if (isLoading) return;
    const section = document.getElementById(sectionId);
    if (!section) return;

    let items = [];
    try {
        items = JSON.parse(section.dataset.suggestions || '[]');
    } catch (_) {
        items = [];
    }

    const selected = [];
    section.querySelectorAll('.suggestion-checkbox:checked').forEach((cb) => {
        const idx = Number(cb.dataset.suggestionIdx);
        if (items[idx] != null) {
            selected.push(String(items[idx]).trim());
        }
    });
    if (!selected.length) return;

    section.querySelector('.suggestions-submit')?.setAttribute('disabled', 'disabled');
    section.querySelectorAll('.suggestion-checkbox').forEach((cb) => {
        cb.disabled = true;
    });

    await submitQuestion(selected.join('\n\n'));
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
            <div class="message-content">${renderCopySection('question-section', '', escapeHtml(text), text)}</div>
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

function renderDataTable(data, msgId, options = {}) {
    const visibleClass = options.visible ? ' visible' : '';
    const maxRows = 100;
    let html = `<div class="data-table-wrapper${visibleClass}" id="table-${msgId}"><div class="data-table-scroll"><table class="data-table"><thead><tr>`;
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

function buildResultsHtml(msgId, data, options = {}) {
    const {
        resultSummary = '',
        chartApplicable = false,
        chartDetails = null,
        answerType = 'Sql',
    } = options;
    let html = '';

    if (resultSummary) {
        html += `<div class="result-summary">${escapeHtml(resultSummary)}</div>`;
    }

    if (answerType === 'Sql' && chartApplicable && chartDetails && data?.rows?.length) {
        html += `<div class="chart-wrapper"><div id="chart-${msgId}"></div></div>`;
    }

    if (data?.rows?.length) {
        html += renderDataTable(data, msgId);
        html += `<div class="download-bar">
            <button class="btn-download" onclick="downloadCSV(${msgId})">Download CSV</button>
        </div>`;
    }

    return html;
}

function renderChart(chartDetails, data, msgId, timeZone) {
    const el = document.getElementById('chart-' + msgId);
    if (!el || !chartDetails || !data) return;

    if (chartDetails.series_column) {
        renderSeriesChart(el, chartDetails, data, timeZone);
        return;
    }

    const cols = data.columns;
    const rows = data.rows;
    const xCol = chartDetails.x_axis[0];
    const xIdx = cols.indexOf(xCol);
    const xAxisMode = resolveXAxisMode(xCol, rows, xIdx);
    const colors = ['#0ea5e9', '#6366f1', '#34d399', '#fbbf24', '#f87171'];
    const traces = [];
    const yUnits = chartDetails.y_unit || [];

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
        const yUnit = (yUnits[i] || '').trim();
        const unitSuffix = yUnit ? ` ${yUnit}` : '';
        if (xAxisMode !== 'category') {
            trace.customdata = rawX.map(v => formatXHoverValue(v, xAxisMode, timeZone));
            trace.hovertemplate = '%{customdata}<br>' + yCol + ': %{y}' + unitSuffix + '<extra></extra>';
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

    if (!traces.length || typeof Plotly === 'undefined') return;

    const layout = buildPlotLayout(chartDetails, traces, { xAxisMode, timeZone });
    Plotly.newPlot(el, traces, layout, { responsive: true, displaylogo: false });
}

function buildPlotLayout(chartDetails, traces, { xAxisMode, timeZone }) {
    const xLabel = xAxisTitle(chartDetails, xAxisMode, timeZone);
    const yUnits = chartDetails.y_unit || [];
    const dual = !!(chartDetails.dual_axis && traces.length >= 2);
    const yLabel = yUnits.filter(Boolean).join(', ') || chartDetails.y_axis.join(', ');

    const layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { color: '#94a3b8', size: 11 },
        margin: { t: 28, r: dual ? 72 : 16, b: 48, l: 56 },
        xaxis: {
            title: xLabel,
            gridcolor: '#2a3548',
            type: xAxisMode === 'datetime' ? 'date' : undefined,
        },
        yaxis: { title: dual ? (yUnits[0] || traces[0].name) : yLabel, gridcolor: '#2a3548' },
        legend: { orientation: 'h', y: dual ? 1.18 : 1.12 },
    };

    if (dual) {
        traces[0].yaxis = 'y';
        traces[1].yaxis = 'y2';
        layout.yaxis.title = yUnits[0] || traces[0].name;
        layout.yaxis2 = {
            title: yUnits[1] || traces[1].name,
            overlaying: 'y',
            side: 'right',
            gridcolor: '#2a3548',
            showgrid: false,
        };
    }

    applyXAxisTicks(layout, traces, xAxisMode, timeZone);

    return layout;
}

function colIndex(columns, name) {
    if (!name) return -1;
    const lower = String(name).toLowerCase();
    return columns.findIndex(c => String(c).toLowerCase() === lower);
}

function compareX(a, b, xAxisMode = 'category') {
    return compareXValues(a, b, xAxisMode);
}

function renderSeriesChart(el, chartDetails, data, timeZone) {
    const cols = data.columns;
    const rows = data.rows;
    const xCol = chartDetails.x_axis[0];
    const xIdx = colIndex(cols, xCol);
    const seriesIdx = colIndex(cols, chartDetails.series_column);
    const yCol = chartDetails.y_axis[0];
    const yIdx = colIndex(cols, yCol);
    if (seriesIdx < 0 || yIdx < 0) return;

    const xAxisMode = resolveXAxisMode(xCol, rows, xIdx);
    const colors = ['#0ea5e9', '#6366f1', '#34d399', '#fbbf24', '#f87171', '#a78bfa', '#fb7185'];
    const groups = new Map();

    rows.forEach(row => {
        const key = row[seriesIdx];
        if (key == null || key === '') return;
        const label = String(key);
        if (!groups.has(label)) groups.set(label, []);
        groups.get(label).push(row);
    });

    const traces = [];
    let colorIdx = 0;
    [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0])).forEach(([name, groupRows]) => {
        const sorted = groupRows.slice().sort((a, b) => compareX(
            xIdx >= 0 ? a[xIdx] : null,
            xIdx >= 0 ? b[xIdx] : null,
            xAxisMode,
        ));
        const rawX = xIdx >= 0 ? sorted.map(r => r[xIdx]) : sorted.map((_, j) => j);
        const trace = {
            x: rawX,
            y: sorted.map(r => r[yIdx]),
            name,
            marker: { color: colors[colorIdx % colors.length] },
        };
        colorIdx += 1;
        if (xAxisMode !== 'category') {
            const yUnit = (chartDetails.y_unit?.[0] || '').trim();
            const unitSuffix = yUnit ? ` ${yUnit}` : '';
            trace.customdata = rawX.map(v => formatXHoverValue(v, xAxisMode, timeZone));
            trace.hovertemplate = '%{customdata}<br>' + name + ': %{y}' + unitSuffix + '<extra></extra>';
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

    if (!traces.length || typeof Plotly === 'undefined') return;

    const xLabel = xAxisTitle(chartDetails, xAxisMode, timeZone);
    const yLabel = chartDetails.y_unit?.filter(Boolean).join(', ') || yCol || '';

    const layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { color: '#94a3b8', size: 11 },
        margin: { t: 24, r: 16, b: 48, l: 56 },
        xaxis: {
            title: xLabel,
            gridcolor: '#2a3548',
            type: xAxisMode === 'datetime' ? 'date' : undefined,
        },
        yaxis: { title: yLabel, gridcolor: '#2a3548' },
        legend: { orientation: 'h', y: 1.12 },
    };

    applyXAxisTicks(layout, traces, xAxisMode, timeZone);

    Plotly.newPlot(el, traces, layout, { responsive: true, displaylogo: false });
}

function renderSqlSection(sql, msgId, { visible = true, showExecute = false } = {}) {
    const formatted = formatSqlDisplay(sql);
    const visClass = visible ? ' visible' : '';
    const sectionId = `sql-section-${msgId}`;
    let html = `<div class="sql-section copy-section sql-section-panel" id="${sectionId}" data-copy-text="${escapeAttr(formatted)}">`;
    html += renderSqlToolbar('Copy SQL', `toggleSqlEdit(${msgId})`);
    html += `<pre class="sql-block sql-block-inline${visClass}" id="sql-${msgId}">${escapeHtml(formatted)}</pre>`;
    html += `<textarea class="sql-editor" id="sql-editor-${msgId}" hidden aria-label="Edit SQL"></textarea>`;
    if (showExecute) {
        html += renderExecuteBarViewMode(msgId);
    }
    html += '</div>';
    return html;
}

function formatResolvedValue(value) {
    if (value == null) return '—';
    if (Array.isArray(value)) return value.map(formatResolvedValue).join(', ');
    if (typeof value === 'number') return String(value);
    return String(value);
}

function renderLookupItems(queryPlan) {
    const steps = (queryPlan && queryPlan.steps) || [];
    const finalId = queryPlan && queryPlan.final_step;
    const lookups = steps.filter((s) => s.id !== finalId);
    if (!lookups.length) return '';
    const items = lookups.map((step) => {
        const resolved = step.resolved || {};
        const valueParts = Object.keys(resolved).map(
            (k) => `${k}: ${formatResolvedValue(resolved[k])}`
        );
        const sql = step.sql || '';
        let html = `<li class="lookup-item">`;
        html += `<div class="lookup-purpose">${escapeHtml(step.purpose || step.id)}</div>`;
        if (sql) {
            html += `<div class="lookup-sql-label">Internal lookup</div>`;
            html += `<pre class="lookup-sql">${escapeHtml(formatSqlDisplay(sql))}</pre>`;
        }
        if (valueParts.length) {
            html += `<div class="lookup-resolved">Resolved value: ${escapeHtml(valueParts.join(' · '))}</div>`;
        } else if (step.target) {
            html += `<div class="lookup-resolved">Target: ${escapeHtml(step.target)}</div>`;
        }
        html += `</li>`;
        return html;
    });
    return renderCopySection(
        'assumptions-section lookups-section',
        'Internal lookups',
        `<ul class="lookup-list">${items.join('')}</ul>`,
        lookups.map((s) => s.purpose || s.id).join('\n'),
    );
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
            const clarifyBody = `<ul class="clarify-list">${response.clarifying_question
                .map(q => `<li>${escapeHtml(q)}</li>`)
                .join('')}</ul>`;
            html += renderCopySection('clarify-section', 'Clarification', clarifyBody, response.clarifying_question.join('\n'));
        }
    } else if (response.answer_type) {
        const typeClass = (response.answer_type || '').toLowerCase();
        html += `<span class="type-badge ${typeClass}">${escapeHtml(response.answer_type)}</span>`;
    }

    const understanding = (response.understanding || '').trim();
    const resolvedQuestion = (response.question || '').trim();
    if (understanding) {
        html += renderCopySection(
            'understood-section',
            'Understanding',
            `<p class="understood-text">${escapeHtml(understanding)}</p>`,
            understanding,
        );
    } else if (resolvedQuestion) {
        html += renderCopySection(
            'understood-section',
            'Here is what I understood',
            `<p class="understood-text">${escapeHtml(resolvedQuestion)}</p>`,
            resolvedQuestion,
        );
    }

    const timeframeHint = (response.timeframe_rationale || '').trim();
    if (timeframeHint) {
        html += renderCopySection(
            'timeframe-hint-section',
            'Why this timeframe',
            `<p class="timeframe-hint-text">${escapeHtml(timeframeHint)}</p>`,
            timeframeHint,
        );
    }

    const assumptions = response.assumptions || response.assumption || [];
    if (assumptions.length) {
        const assumptionBody = `<ul>${assumptions.map(a => `<li>${escapeHtml(a)}</li>`).join('')}</ul>`;
        html += renderCopySection(
            'assumptions-section',
            'Assumptions / Resolved values',
            assumptionBody,
            assumptions.join('\n'),
        );
    }

    if (response.query_plan) {
        html += renderLookupItems(response.query_plan);
    }

    if (response.lookup_error) {
        html += `<div class="execute-error">${escapeHtml(response.lookup_error)}</div>`;
    }

    if (response.suggestions?.length) {
        html += renderSuggestionsSection(response.suggestions, msgId, fromHistory);
    }

    if (response.answer_type === 'Awareness' && response.answer) {
        html += `<div class="answer-text">${formatAnswer(response.answer)}</div>`;
    }

    const finalSql = response.final_sql || (response.answer_type === 'Sql' ? response.answer : null);
    const hasData = !!(response.data?.rows?.length);
    const isSql = response.answer_type === 'Sql';
    const isMetadata = response.answer_type === 'Metadata';
    const pendingExecute = (isSql || isMetadata) && finalSql && !hasData && !response.clarity_required;

    if (isMetadata && hasData && response.answer) {
        html += '<div class="answer-label">Answer</div>';
        html += `<div class="answer-text">${formatAnswer(response.answer)}</div>`;
    }

    let toggles = '';
    if ((isSql || isMetadata) && finalSql) {
        toggles += `<button class="section-toggle active" onclick="toggleSection(this, 'sql-${msgId}')">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/></svg>
            Final SQL
        </button>`;
    }
    if (hasData) {
        toggles += `<button class="section-toggle" onclick="toggleSection(this, 'table-${msgId}')">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/></svg>
            Data (${response.data.row_count} rows)
        </button>`;
    }
    if (toggles) html += `<div style="margin-bottom:8px">${toggles}</div>`;

    if ((isSql || isMetadata) && finalSql) {
        html += renderSqlSection(finalSql, msgId, {
            visible: true,
            showExecute: pendingExecute,
        });
    }

    html += `<div id="results-${msgId}">`;
    if (hasData) {
        html += buildResultsHtml(msgId, response.data, {
            resultSummary: response.result_summary || '',
            chartApplicable: !!(isSql && response.chart_applicable && response.chart_details),
            chartDetails: response.chart_details || null,
            answerType: response.answer_type,
        });
    }
    html += '</div>';

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
    if (response.suggestions?.length && !fromHistory) {
        const section = msg.querySelector(`#suggestions-${msgId}`);
        if (section) {
            section.dataset.suggestions = JSON.stringify(response.suggestions);
        }
    }
    if (response.data && (isSql || isMetadata)) {
        msg.dataset.columns = JSON.stringify(response.data.columns);
        msg.dataset.rows = JSON.stringify(response.data.rows);
    }
    if (pendingExecute) {
        msg.dataset.sql = finalSql;
        msg.dataset.answerType = response.answer_type;
        msg.dataset.question = response.question || '';
        if (response.result_template) msg.dataset.resultTemplate = response.result_template;
        if (response.chart_applicable) msg.dataset.chartApplicable = '1';
        if (response.chart_details) msg.dataset.chartDetails = JSON.stringify(response.chart_details);
        if (response.timezone) msg.dataset.timezone = response.timezone;
        if (response.query_plan) msg.dataset.queryPlan = JSON.stringify(response.query_plan);
    }
    container.appendChild(msg);

    if (isSql && response.chart_applicable && response.chart_details && hasData) {
        setTimeout(
            () => renderChart(response.chart_details, response.data, msgId, response.timezone),
            100
        );
    }
    scrollToBottom();
}
async function executeQuery(msgId) {
    const msgEl = document.querySelector(`[data-msg-id="${msgId}"]`);
    if (!msgEl || msgEl.dataset.executing === '1') return;

    const editor = getSqlEditor(msgId);
    const section = getSqlSection(msgId);
    if (section?.classList.contains('editing') && editor) {
        const edited = editor.value.trim();
        if (edited) msgEl.dataset.sql = edited;
    }

    const sql = msgEl.dataset.sql;
    if (!sql) return;

    const btn = document.getElementById('btn-execute-' + msgId);
    const executeBar = document.getElementById('execute-bar-' + msgId);
    msgEl.dataset.executing = '1';
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Executing…';
    }

    const body = {
        sql,
        answer_type: msgEl.dataset.answerType || 'Sql',
        question: msgEl.dataset.question || undefined,
        result_template: msgEl.dataset.resultTemplate || undefined,
        query_plan: msgEl.dataset.queryPlan ? JSON.parse(msgEl.dataset.queryPlan) : undefined,
    };

    try {
        const res = await fetch('/api/v3/execute', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(body),
        });
        if (res.status === 401) {
            logout();
            return;
        }
        const data = await res.json();
        if (!res.ok) {
            const detail = data.detail;
            const message = typeof detail === 'string' ? detail : (detail?.message || 'Execution failed.');
            if (executeBar) {
                executeBar.innerHTML = `<div class="execute-error">${escapeHtml(message)}</div>${renderExecuteButton(msgId)}`;
            }
            return;
        }

        if (executeBar) {
            executeBar.innerHTML = renderExecuteButton(msgId);
        }

        const answerType = msgEl.dataset.answerType;
        const contentEl = msgEl.querySelector('.message-content');
        const togglesHost = contentEl?.querySelector('.section-toggle')?.parentElement;

        if (answerType === 'Metadata' && data.answer) {
            const answerBlock = document.createElement('div');
            answerBlock.innerHTML = `<div class="answer-label">Answer</div><div class="answer-text">${formatAnswer(data.answer)}</div>`;
            if (togglesHost) {
                togglesHost.before(answerBlock);
            } else if (contentEl) {
                contentEl.prepend(answerBlock);
            }
        }

        if (togglesHost && data.data?.rows?.length) {
            togglesHost.innerHTML += `<button class="section-toggle" onclick="toggleSection(this, 'table-${msgId}')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/></svg>
                Data (${data.data.row_count} rows)
            </button>`;
        }

        const resultsEl = document.getElementById('results-' + msgId);
        if (resultsEl && data.data?.rows?.length) {
            const chartApplicable = msgEl.dataset.chartApplicable === '1';
            const chartDetails = msgEl.dataset.chartDetails
                ? JSON.parse(msgEl.dataset.chartDetails)
                : null;
            resultsEl.innerHTML = buildResultsHtml(msgId, data.data, {
                resultSummary: data.result_summary || '',
                chartApplicable,
                chartDetails,
                answerType,
            });

            msgEl.dataset.columns = JSON.stringify(data.data.columns);
            msgEl.dataset.rows = JSON.stringify(data.data.rows);

            if (answerType === 'Sql' && chartApplicable && chartDetails) {
                setTimeout(
                    () => renderChart(chartDetails, data.data, msgId, msgEl.dataset.timezone),
                    100
                );
            }
        }

        if (data.context_warnings?.length && contentEl) {
            const warnEl = document.createElement('div');
            warnEl.className = 'warnings';
            warnEl.innerHTML = data.context_warnings.map(escapeHtml).join('<br>');
            contentEl.appendChild(warnEl);
        }

        scrollToBottom();
    } catch (err) {
        if (executeBar) {
            executeBar.innerHTML = `<div class="execute-error">${escapeHtml('Network error: ' + err.message)}</div>${renderExecuteButton(msgId)}`;
        }
    } finally {
        msgEl.dataset.executing = '0';
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Execute query';
        }
    }
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
    await fetch('/api/v3/query/clear', {
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
        const res = await fetch('/api/v3/history', { headers: authHeaders() });
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
        const res = await fetch(`/api/v3/history/sessions/${encodeURIComponent(pendingTitleEdit.sessionId)}`, {
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
        const res = await fetch(`/api/v3/history/sessions/${encodeURIComponent(deletedId)}`, {
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
        const res = await fetch('/api/v3/history/hydrate', {
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

        const pairs = [];
        let pendingUser = null;
        turns.forEach(turn => {
            if (turn.role === 'user') {
                pendingUser = turn;
            } else {
                const env = turn.envelope || {};
                pairs.push({
                    question: (pendingUser && pendingUser.content) || env.original_question || env.question || '',
                    sentAt: (pendingUser && pendingUser.created_at) || turn.created_at,
                    response: Object.assign({
                        response_time: turn.created_at,
                        answer: turn.content,
                    }, env),
                });
                pendingUser = null;
            }
        });
        if (pendingUser) {
            addUserMessage(pendingUser.content, pendingUser.created_at);
        }
        pairs.forEach(pair => {
            addUserMessage(pair.question, pair.sentAt);
            addAssistantMessage(pair.response, { fromHistory: true });
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

async function submitQuestion(presetText) {
    const input = document.getElementById('question-input');
    const question = (typeof presetText === 'string' ? presetText : input.value).trim();
    if (!question || isLoading) return;

    isLoading = true;
    if (typeof presetText !== 'string') {
        input.value = '';
        autoResize(input);
    }
    document.getElementById('btn-send').disabled = true;

    addUserMessage(question);
    addLoadingMessage();

    try {
        const res = await fetch('/api/v3/query', {
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
