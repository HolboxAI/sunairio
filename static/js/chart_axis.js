/** Shared chart x-axis detection and formatting. */

const DATETIME_AXIS_COLUMNS = new Set([
    'valid_datetime',
    'hour_beginning',
    'sim_datetime',
    'local_date',
    'date',
]);

const HOUR_AXIS_COLUMNS = new Set([
    'local_hour',
    'hour',
    'hour_of_day',
]);

function looksLikeIsoDate(value) {
    if (value == null || value === '') return false;
    return /^\d{4}-\d{2}-\d{2}/.test(String(value));
}

function isHourIntegerValue(value) {
    if (value == null || value === '') return true;
    const n = Number(value);
    return Number.isInteger(n) && n >= 0 && n <= 23;
}

function isHourOnlyAxis(colName, rows, xIdx) {
    if (xIdx < 0 || !rows?.length) return false;
    const lower = String(colName || '').toLowerCase();
    const samples = rows.filter(r => r[xIdx] != null && r[xIdx] !== '');
    if (!samples.length) return false;

    if (HOUR_AXIS_COLUMNS.has(lower)) {
        return samples.every(r => isHourIntegerValue(r[xIdx]));
    }
    if (lower.includes('hour') && !DATETIME_AXIS_COLUMNS.has(lower)) {
        return samples.every(r => isHourIntegerValue(r[xIdx]));
    }
    return false;
}

function resolveXAxisMode(colName, rows, xIdx) {
    if (xIdx < 0) return 'category';
    if (isHourOnlyAxis(colName, rows, xIdx)) return 'hour';

    const lower = String(colName || '').toLowerCase();
    if (DATETIME_AXIS_COLUMNS.has(lower)) return 'datetime';

    const sample = rows.map(r => r[xIdx]).find(v => v != null && v !== '');
    if (looksLikeIsoDate(sample)) return 'datetime';

    return 'category';
}

function formatHourLabel(hour) {
    const n = Number(hour);
    if (!Number.isFinite(n)) return String(hour ?? '');
    return `${String(Math.floor(n)).padStart(2, '0')}:00`;
}

function formatHourHover(hour, timeZone) {
    const label = formatHourLabel(hour);
    return timeZone ? `${label} (${timeZone})` : label;
}

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

function formatXHoverValue(value, xAxisMode, timeZone) {
    if (xAxisMode === 'hour') return formatHourHover(value, timeZone);
    if (xAxisMode === 'datetime') return formatAxisTime(value, timeZone);
    return value;
}

function xAxisTitle(chartDetails, xAxisMode, timeZone) {
    const xUnit = chartDetails.x_unit?.[0] || '';
    if (xAxisMode === 'hour') {
        if (xUnit) return xUnit;
        return timeZone ? `Hour (${timeZone})` : 'Hour';
    }
    if (xAxisMode === 'datetime' && timeZone) return timeZone;
    return xUnit || chartDetails.x_axis?.[0] || '';
}

function applyXAxisTicks(layout, traces, xAxisMode, timeZone) {
    if (!traces.length) return;
    const rawX = traces[0].x;
    if (!rawX?.length) return;

    if (xAxisMode === 'datetime' && timeZone) {
        layout.xaxis.type = 'date';
        const tickCount = Math.min(8, rawX.length);
        const step = Math.max(1, Math.floor((rawX.length - 1) / Math.max(tickCount - 1, 1)));
        const tickvals = [];
        for (let i = 0; i < rawX.length; i += step) tickvals.push(rawX[i]);
        if (rawX.length && tickvals[tickvals.length - 1] !== rawX[rawX.length - 1]) {
            tickvals.push(rawX[rawX.length - 1]);
        }
        layout.xaxis.tickvals = tickvals;
        layout.xaxis.ticktext = tickvals.map(v => formatAxisTime(v, timeZone));
        return;
    }

    if (xAxisMode === 'hour') {
        layout.xaxis.type = 'linear';
        const tickvals = [...new Set(rawX)].sort((a, b) => Number(a) - Number(b));
        layout.xaxis.tickvals = tickvals;
        layout.xaxis.ticktext = tickvals.map(v => formatHourLabel(v));
    }
}

function compareXValues(a, b, xAxisMode) {
    if (xAxisMode === 'hour') {
        const na = Number(a);
        const nb = Number(b);
        if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
    }
    const da = new Date(a);
    const db = new Date(b);
    if (!Number.isNaN(da.getTime()) && !Number.isNaN(db.getTime())) return da - db;
    if (a == null) return -1;
    if (b == null) return 1;
    return String(a).localeCompare(String(b));
}
