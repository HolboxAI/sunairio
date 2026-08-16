let sessionId = 'analytics_' + Date.now().toString(36);
let isLoading = false;
let pendingRepId = null;
let pendingTitleEdit = null;
let pendingDelete = null;

function analyticsApi(path) {
    return '/api/v2' + path;
}

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

function toggleSqlBlock(id) {
    const pre = document.getElementById(id);
    if (!pre) return;
    const panel = pre.closest('.sql-section-panel');
    const target = panel || pre;
    target.classList.toggle('visible');
    const btn = panel
        ? panel.previousElementSibling
        : pre.previousElementSibling;
    if (btn && btn.classList.contains('btn-sql-toggle')) {
        btn.textContent = target.classList.contains('visible') ? 'Hide SQL' : 'View SQL';
    }
}

function renderSqlInline(sql, uid, meta = {}) {
    const text = formatSqlDisplay(sql);
    if (!text) return '';
    const id = uid || ('sql-' + Math.random().toString(36).slice(2, 10));
    const sectionId = `sql-wrap-${id}`;
    const editable = meta.editable !== false;
    const viewUid = meta.viewUid || '';
    let sectionAttrs = `id="${sectionId}" data-copy-text="${escapeAttr(text)}" data-raw-sql="${escapeAttr(sql)}"`;
    if (viewUid) sectionAttrs += ` data-view-uid="${escapeAttr(viewUid)}"`;

    return (
        `<div class="sql-inline-wrap">` +
        `<button type="button" class="btn-sql-toggle" onclick="toggleSqlBlock('${id}')">View SQL</button>` +
        `<div class="sql-section copy-section sql-section-panel" ${sectionAttrs}>` +
        renderSqlToolbar('Copy SQL', editable ? `toggleSqlEditUid('${id}')` : '', editable) +
        `<pre class="sql-block sql-block-inline" id="${id}">${escapeHtml(text)}</pre>` +
        `<textarea class="sql-editor" id="sql-editor-${id}" hidden aria-label="Edit SQL"></textarea>` +
        (editable ? `<div class="execute-bar execute-bar-compact" id="execute-bar-${id}" hidden></div>` : '') +
        `</div></div>`
    );
}

async function executeEditedSqlV2(uid) {
    const section = getSqlSectionByUid(uid);
    if (!section) return;

    const sql = (section.dataset.rawSql || '').trim();
    const viewUid = section.dataset.viewUid || '';
    if (!sql) return;

    const executeBar = document.getElementById('execute-bar-' + uid);
    const runBtn = executeBar?.querySelector('.btn-execute');
    if (runBtn) {
        runBtn.disabled = true;
        runBtn.textContent = 'Executing…';
    }

    try {
        const res = await fetch('/api/sql', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ sql }),
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
                executeBar.hidden = false;
                executeBar.innerHTML = `<div class="execute-error">${escapeHtml(message)}</div>`;
            }
            return;
        }

        const payload = data.data;
        if (viewUid && payload) {
            const wrap = document.querySelector(`[data-view-uid="${viewUid}"]`);
            if (wrap) {
                wrap.dataset.payload = JSON.stringify(payload);
                const chartPanel = document.getElementById('chart-' + viewUid);
                let displayColumns = null;
                if (chartPanel?.dataset.chart) {
                    try {
                        displayColumns = JSON.parse(chartPanel.dataset.chart).display_columns;
                    } catch {
                        displayColumns = null;
                    }
                }
                const tablePanel = document.getElementById('table-' + viewUid);
                if (tablePanel) {
                    tablePanel.innerHTML = renderResultTable(payload, displayColumns);
                }
                if (chartPanel) {
                    chartPanel.dataset.mounted = '0';
                    mountResultCharts(wrap);
                }
            }
        }

        if (executeBar) {
            executeBar.hidden = true;
            executeBar.innerHTML = '';
        }
    } catch (err) {
        if (executeBar) {
            executeBar.hidden = false;
            executeBar.innerHTML = `<div class="execute-error">${escapeHtml('Network error: ' + err.message)}</div>`;
        }
    } finally {
        if (runBtn) {
            runBtn.disabled = false;
            runBtn.textContent = 'Save & Execute';
        }
    }
}

function renderAssumptionsSection(items) {
    if (!items?.length) return '';
    const body = `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
    return renderCopySection('assumptions-section', 'Assumptions', body, items.join('\n'));
}

function renderClarifySection(items, bodyHtml, plainText) {
    const questions = Array.isArray(items) ? items : [];
    const body = bodyHtml || `<ul class="clarify-list">${questions
        .map((q, i) => `<li>${questions.length > 1 ? `${i + 1}. ` : ''}${escapeHtml(q)}</li>`)
        .join('')}</ul>`;
    const plain = plainText || questions.join('\n');
    if (!plain.trim()) return '';
    return renderCopySection('clarify-section', 'Suggestions', body, plain);
}

function formatConfirmResult(data) {
    const parts = [];
    const payload = data.data || {};
    const sql = data.sql || payload.sql;
    const hasRows = payload && Array.isArray(payload.rows) && payload.rows.length;
    const chartApplicable = !!(data.chart_applicable && data.chart_details && hasRows);
    const viewUid = data.view_uid || data.sql_uid || ('view-' + Math.random().toString(36).slice(2, 10));
    const assumptions = Array.isArray(data.assumptions) ? data.assumptions : [];

    if (assumptions.length) {
        parts.push(renderAssumptionsSection(assumptions));
    }

    if (data.result_summary) {
        parts.push(`<div class="result-summary">${escapeHtml(data.result_summary)}</div>`);
    } else if (data.message && !hasRows) {
        parts.push(formatAnswer(data.message));
    }
    if (hasRows) {
        parts.push(renderResultView({
            payload,
            chartApplicable,
            chartDetails: data.chart_details,
            timezone: data.timezone || payload.timezone,
            viewUid,
        }));
        if (data.message && data.result_summary && data.message !== data.result_summary) {
            const notesIdx = data.message.indexOf('Notes:');
            if (notesIdx >= 0) {
                parts.push(formatAnswer(data.message.slice(notesIdx)));
            }
        }
    }
    if (sql) {
        parts.push(renderSqlInline(sql, data.sql_uid || viewUid, { viewUid, editable: true }));
    }
    if (!parts.length) {
        return formatAnswer(data.message || 'Done.');
    }
    return parts.join('');
}

function renderChart(chartDetails, data, viewUid, timeZone) {
    const el = document.getElementById('chart-' + viewUid);
    if (!el || !chartDetails || !data) return;

    if (chartDetails.series_column) {
        renderSeriesChart(el, chartDetails, data, timeZone);
        return;
    }

    const cols = data.columns || [];
    const rows = data.rows || [];
    const xCol = chartDetails.x_axis[0];
    const xIdx = cols.indexOf(xCol);
    const xAxisMode = resolveXAxisMode(xCol, rows, xIdx);
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
        if (xAxisMode !== 'category') {
            trace.customdata = rawX.map(v => formatXHoverValue(v, xAxisMode, timeZone));
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

    if (!traces.length || typeof Plotly === 'undefined') return;

    const layout = buildPlotLayout(chartDetails, traces, { xAxisMode, timeZone });
    _applyZeroSafeYRange(layout, traces);
    Plotly.newPlot(el, traces, layout, { responsive: true, displaylogo: false });
}

function _applyZeroSafeYRange(layout, traces) {
    const values = traces.flatMap(t => (t.y || []).map(Number).filter(v => !Number.isNaN(v)));
    if (!values.length) return;
    const maxVal = Math.max(...values);
    const minVal = Math.min(...values);
    if (maxVal === 0 && minVal === 0) {
        layout.yaxis.rangemode = 'tozero';
        layout.yaxis.range = [0, 1];
        return;
    }
    if (minVal >= 0) {
        layout.yaxis.rangemode = 'tozero';
    }
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
    const cols = data.columns || [];
    const rows = data.rows || [];
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
            trace.customdata = rawX.map(v => formatXHoverValue(v, xAxisMode, timeZone));
            trace.hovertemplate = '%{customdata}<br>' + name + ': %{y}<extra></extra>';
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

function renderResultView({ payload, chartApplicable, chartDetails, timezone, viewUid }) {
    if (!chartApplicable) {
        return renderResultTable(payload);
    }
    const selectId = 'view-select-' + viewUid;
    const chartId = 'chart-' + viewUid;
    const tableId = 'table-' + viewUid;
    return (
        `<div class="result-view-wrap" data-view-uid="${escapeHtml(viewUid)}" ` +
        `data-payload='${escapeHtml(JSON.stringify(payload))}'>` +
        `<div class="result-view-toolbar">` +
        `<label for="${selectId}">View</label>` +
        `<select id="${selectId}" class="result-view-select" onchange="toggleResultView('${viewUid}')">` +
        `<option value="chart" selected>Chart</option>` +
        `<option value="table">Table</option>` +
        `</select>` +
        `</div>` +
        `<div id="${chartId}" class="chart-wrapper result-chart-panel" ` +
        `data-chart='${escapeHtml(JSON.stringify(chartDetails || {}))}' ` +
        `data-timezone="${escapeHtml(timezone || '')}"></div>` +
        `<div id="${tableId}" class="result-table-panel" hidden>${renderResultTable(payload, chartDetails?.display_columns)}</div>` +
        `</div>`
    );
}

function mountResultCharts(root) {
    const scope = root || document;
    scope.querySelectorAll('.result-chart-panel[data-chart]').forEach(el => {
        if (el.dataset.mounted === '1') return;
        const wrap = el.closest('.result-view-wrap');
        const viewUid = wrap?.getAttribute('data-view-uid');
        if (!viewUid) return;
        const tablePanel = document.getElementById('table-' + viewUid);
        if (tablePanel && !tablePanel.hidden) return;
        let chartDetails = null;
        let data = null;
        try {
            chartDetails = JSON.parse(el.dataset.chart || '{}');
            data = JSON.parse(wrap.dataset.payload || '{}');
        } catch {
            return;
        }
        if (!data?.rows?.length || !chartDetails?.y_axis?.length) return;
        renderChart(chartDetails, data, viewUid, el.dataset.timezone || '');
        el.dataset.mounted = '1';
    });
}

function toggleResultView(viewUid) {
    const select = document.getElementById('view-select-' + viewUid);
    const chartPanel = document.getElementById('chart-' + viewUid);
    const tablePanel = document.getElementById('table-' + viewUid);
    if (!select || !chartPanel || !tablePanel) return;
    const showChart = select.value === 'chart';
    chartPanel.hidden = !showChart;
    tablePanel.hidden = showChart;
    if (showChart) {
        chartPanel.dataset.mounted = '0';
        mountResultCharts(chartPanel.parentElement);
        if (typeof Plotly !== 'undefined') {
            Plotly.Plots.resize(chartPanel);
        }
    }
}

function renderResultTable(data, displayColumns) {
    const allCols = data.columns || [];
    const cols = Array.isArray(displayColumns) && displayColumns.length
        ? displayColumns.filter(col => allCols.includes(col))
        : allCols;
    const colIndexes = cols.map(col => allCols.indexOf(col));
    const rows = data.rows || [];
    if (!cols.length) return '';
    let html = '<div class="result-table-wrap"><table class="result-table"><thead><tr>';
    cols.forEach(col => { html += `<th>${escapeHtml(col)}</th>`; });
    html += '</tr></thead><tbody>';
    rows.slice(0, 168).forEach(row => {
        html += '<tr>';
        colIndexes.forEach(i => {
            const cell = Array.isArray(row) ? row[i] : '';
            html += `<td>${escapeHtml(cell == null ? '' : String(cell))}</td>`;
        });
        html += '</tr>';
    });
    html += '</tbody></table></div>';
    if (rows.length > 168) {
        html += `<div class="result-table-note">Showing 168 of ${rows.length} rows.</div>`;
    }
    return html;
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

function appendMessage(role, html, phaseChip, sentAt = new Date(), plainText = '') {
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
    const content = role === 'user'
        ? renderCopySection('question-section', '', html, plainText || html.replace(/<[^>]*>/g, ''))
        : html;
    div.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-body">
            ${meta}
            ${chip}
            <div class="message-content">${content}</div>
        </div>`;
    container.appendChild(div);
    scrollToBottom();
    mountResultCharts(div);
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

function renderTurnContent(turn) {
    if (turn.role === 'user') {
        return formatAnswer(turn.content || '');
    }
    const rd = turn.result_data;
    if (rd && ((Array.isArray(rd.rows) && rd.rows.length) || rd.sql)) {
        return formatConfirmResult({
            message: turn.content || '',
            data: rd,
            sql: rd.sql,
            result_summary: null,
            chart_applicable: rd.chart_applicable,
            chart_details: rd.chart_details,
            timezone: rd.timezone,
            sql_uid: 'sql-turn-' + (turn.id || Math.random().toString(36).slice(2, 8)),
            view_uid: 'view-turn-' + (turn.id || Math.random().toString(36).slice(2, 8)),
        });
    }
    return formatAnswer(turn.content || '');
}

function buildConfirmGridRows(summary) {
    const isMeta = (summary.analysis || '').toLowerCase().includes('metadata')
        || (summary.forecast_representation || '').toLowerCase().includes('catalog');

    if (isMeta) {
        return [
            ['Request', summary.analysis],
            ['Entity', summary.entity],
            ['Looking up', summary.locations],
        ];
    }

    const narrative = (summary.plan_narrative || summary.computation_summary || '').trim();
    const rows = [];

    if (narrative) {
        rows.push(['Plan', narrative]);
    }

    rows.push(
        ['Entity', summary.entity],
        ['Locations', summary.locations],
        ['Time period', summary.forecast_horizon],
        ['Initialization', summary.initialization],
        ['Resolved to', summary.initialization_resolved, true],
    );

    const chart = (summary.chart || '').trim();
    if (chart && chart.toUpperCase() !== 'N/A' && chart !== 'None') {
        rows.push(['Chart', chart]);
    }

    return rows.filter(([, value]) => {
        const v = (value || '').toString().trim();
        return v && v.toUpperCase() !== 'N/A' && v !== 'None';
    });
}

function renderConfirmPanelHtml(summary, repId) {
    pendingRepId = repId;
    const isMeta = (summary.analysis || '').toLowerCase().includes('metadata')
        || (summary.forecast_representation || '').toLowerCase().includes('catalog');
    const rows = buildConfirmGridRows(summary || {});

    const dl = rows.map(([label, value, resolved]) => {
        const cls = resolved ? ' class="resolved"' : '';
        const cell = (label === 'Plan')
            ? `<dd${cls} style="white-space:pre-wrap">${escapeHtml(value || '—')}</dd>`
            : `<dd${cls}>${escapeHtml(value || '—')}</dd>`;
        return `<dt>${escapeHtml(label)}</dt>${cell}`;
    }).join('');

    const terms = Array.isArray(summary.plan_terms) ? summary.plan_terms : [];
    const questions = Array.isArray(summary.plan_questions) ? summary.plan_questions : [];
    const assumptionSection = renderAssumptionsSection(terms);
    const clarifySection = renderClarifySection(questions);

    return `
        <div class="confirm-card confirm-panel-inline" id="confirm-card">
            <h3>${isMeta ? 'Confirm this lookup' : 'Quick check'}</h3>
            <p class="confirm-lede">${isMeta
                ? 'Verify these catalog lookup details before I proceed.'
                : 'Review the plan below. Confirm if it matches what you want, or revise.'}</p>
            ${assumptionSection}
            ${clarifySection}
            <dl class="confirm-grid">${dl}</dl>
            <div class="confirm-actions">
                <button class="btn-primary" id="btn-confirm-plan" onclick="confirmPlan('confirm')">Yes, confirm</button>
                <button class="btn-secondary" id="btn-revise-plan" onclick="confirmPlan('reject')">No, revise</button>
            </div>
        </div>`;
}

function renderConfirmCard(summary, repId) {
    const dock = document.getElementById('confirm-dock');
    if (dock) {
        dock.hidden = true;
        dock.innerHTML = '';
    }
    return renderConfirmPanelHtml(summary, repId);
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
        const res = await fetch(analyticsApi('/history'), { headers: authHeaders() });
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
            analyticsApi(`/history/sessions/${encodeURIComponent(pendingTitleEdit.sessionId)}`),
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
            analyticsApi(`/history/sessions/${encodeURIComponent(deletedId)}`),
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
        const res = await fetch(analyticsApi('/history/hydrate'), {
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
            appendMessage(
                role,
                renderTurnContent(turn),
                chip,
                sentAt,
                role === 'user' ? (turn.content || '') : '',
            );
        });

        if (data.pending_rep && data.pending_rep.rep_id) {
            appendMessage(
                'assistant',
                renderConfirmPanelHtml(data.pending_rep.summary || {}, data.pending_rep.rep_id),
                'confirm',
                new Date()
            );
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
    appendMessage('user', formatAnswer(message), '', new Date(), message);
    input.value = '';
    autoResize(input);
    setLoading(true);

    const thinking = appendMessage('assistant', '<em>Consulting…</em>', 'llm1', new Date());

    try {
        const res = await fetch(analyticsApi('/consult'), {
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
            const questions = data.questions || [];
            const extras = questions.filter(q => body.indexOf(q) === -1);
            let clarifyBody = formatAnswer(body);
            if (extras.length) {
                clarifyBody += `<ul class="clarify-list">${extras.map(q => `<li>${escapeHtml(q)}</li>`).join('')}</ul>`;
            }
            const plain = [body, ...extras].filter(Boolean).join('\n');
            contentEl.innerHTML = renderClarifySection(questions, clarifyBody, plain);
            await loadHistory();
            return;
        }

        if (data.phase === 'confirm') {
            if (chipEl) chipEl.textContent = 'confirm';
            clearConfirmDock();
            contentEl.innerHTML = renderConfirmPanelHtml(data.summary || {}, data.rep_id);
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
        const res = await fetch(analyticsApi('/confirm'), {
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

        if (data.phase === 'answered' || data.phase === 'confirmed') {
            clearConfirmDock();
            appendMessage(
                'assistant',
                formatConfirmResult({ ...data, sql_uid: 'sql-live-' + Date.now() }),
                data.phase === 'answered' ? 'answered' : 'confirmed',
                new Date(),
            );
            pendingRepId = null;
        } else if (data.phase === 'error') {
            clearConfirmDock();
            appendMessage(
                'assistant',
                formatConfirmResult({ ...data, sql_uid: 'sql-live-' + Date.now() })
                    || formatAnswer(data.message || 'Query failed.'),
                'error',
                new Date(),
            );
            pendingRepId = null;
        } else {
            clearConfirmDock();
            appendMessage('assistant', formatAnswer(data.message || 'Tell me what to change.'), 'revise', new Date());
            pendingRepId = null;
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
