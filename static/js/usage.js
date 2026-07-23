let currentGranularity = 'day';

function fmt(n) {
    return Number(n || 0).toLocaleString();
}

function fmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}

async function loadUsage(granularity) {
    currentGranularity = granularity || 'day';
    document.querySelectorAll('#usage-tabs .usage-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.granularity === currentGranularity);
    });

    const res = await fetch(`/api/usage?granularity=${currentGranularity}`, { headers: authHeaders() });
    if (res.status === 401) {
        logout();
        return;
    }
    const data = await res.json();
    renderSummary(data);
    renderBreakdown(data.breakdown || []);
}

function renderSummary(data) {
    const banner = document.getElementById('usage-banner');
    const card = document.getElementById('usage-summary');

    if (data.status === 'pending_limit') {
        banner.hidden = false;
        banner.className = 'usage-banner warning';
        banner.textContent = 'Your account is pending activation. An admin must set your monthly token limit before you can run queries.';
        card.innerHTML = '<p class="table-empty">No usage data — waiting for admin activation.</p>';
        return;
    }

    banner.hidden = true;
    const s = data.summary;
    if (!s) {
        card.innerHTML = '<p class="table-empty">No limit configured.</p>';
        return;
    }

    const pct = s.effective_limit ? Math.min(100, (s.used_tokens / s.effective_limit) * 100) : 0;
    const nearLimit = pct >= 90;
    if (nearLimit) {
        banner.hidden = false;
        banner.className = 'usage-banner warning';
        banner.textContent = `You have used ${fmt(s.used_tokens)} of ${fmt(s.effective_limit)} tokens (${pct.toFixed(0)}%) this cycle.`;
    }

    card.innerHTML = `
        <div class="usage-summary-grid large">
            <div class="usage-stat highlight">
                <span class="label">Used / Limit</span>
                <span class="value">${fmt(s.used_tokens)} / ${fmt(s.effective_limit)}</span>
            </div>
            <div class="usage-stat">
                <span class="label">Remaining</span>
                <span class="value">${fmt(s.remaining_tokens)}</span>
            </div>
            <div class="usage-stat">
                <span class="label">Input tokens</span>
                <span class="value">${fmt(s.used_input_tokens)}</span>
            </div>
            <div class="usage-stat">
                <span class="label">Output tokens</span>
                <span class="value">${fmt(s.used_output_tokens)}</span>
            </div>
            <div class="usage-stat">
                <span class="label">Queries</span>
                <span class="value">${fmt(s.query_count)}</span>
            </div>
            <div class="usage-stat">
                <span class="label">Cycle</span>
                <span class="value">${fmtDate(s.cycle_start)} – ${fmtDate(s.cycle_end)}</span>
            </div>
        </div>
        <div class="usage-progress">
            <div class="usage-progress-bar" style="width: ${pct}%"></div>
        </div>`;
}

function renderBreakdown(rows) {
    const tbody = document.getElementById('usage-breakdown');
    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="table-empty">No usage in the current cycle</td></tr>';
        return;
    }
    tbody.innerHTML = rows.map(row => `
        <tr>
            <td>${escapeHtml(row.label)}</td>
            <td>${fmt(row.input_tokens)}</td>
            <td>${fmt(row.output_tokens)}</td>
            <td>${fmt(row.total_tokens)}</td>
            <td>${fmt(row.query_count)}</td>
        </tr>`).join('');
}

async function initUsage() {
    const user = await verifySession();
    if (!user) {
        window.location.href = '/';
        return;
    }
    document.getElementById('usage-user-badge').textContent = user.email || '';
    document.querySelectorAll('#usage-tabs .usage-tab').forEach(btn => {
        btn.addEventListener('click', () => loadUsage(btn.dataset.granularity));
    });
    await loadUsage('day');
}

initUsage();
