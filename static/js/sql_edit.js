/** SQL section edit, save, and execute helpers. */

function sqlEditIcon() {
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" ' +
        'width="14" height="14" aria-hidden="true">' +
        '<path d="M12 20h9"/>' +
        '<path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>' +
        '</svg>'
    );
}

function renderSqlEditButton(onclick, hidden = false) {
    const hiddenAttr = hidden ? ' hidden' : '';
    return (
        `<button type="button" class="btn-sql-edit" onclick="${onclick}" ` +
        `title="Edit SQL" aria-label="Edit SQL"${hiddenAttr}>${sqlEditIcon()}</button>`
    );
}

function renderSqlToolbar(copyTitle, editOnclick, showEdit = true) {
    return (
        `<div class="sql-section-toolbar">` +
        renderCopyButton(copyTitle || 'Copy SQL') +
        (showEdit && editOnclick ? renderSqlEditButton(editOnclick) : '') +
        `</div>`
    );
}

function renderExecuteButton(msgId) {
    return (
        `<button type="button" class="btn-execute" id="btn-execute-${msgId}" onclick="executeQuery(${msgId})">` +
        `<svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14" aria-hidden="true">` +
        `<path d="M8 5v14l11-7z"/>` +
        `</svg>` +
        `Execute query` +
        `</button>`
    );
}

function renderExecuteBarViewMode(msgId) {
    return `<div class="execute-bar" id="execute-bar-${msgId}">${renderExecuteButton(msgId)}</div>`;
}

function renderExecuteBarEditMode(msgId) {
    return (
        `<div class="execute-bar" id="execute-bar-${msgId}">` +
        `<div class="execute-bar-actions">` +
        `<button type="button" class="btn-execute-secondary" onclick="cancelSqlEdit(${msgId})">Cancel</button>` +
        `<button type="button" class="btn-execute-secondary" onclick="saveSqlEdit(${msgId}, false)">Save</button>` +
        `<button type="button" class="btn-execute" onclick="saveSqlEdit(${msgId}, true)">Save &amp; Execute</button>` +
        `</div>` +
        `</div>`
    );
}

function renderExecuteBarEditModeUid(uid) {
    return (
        `<div class="execute-bar" id="execute-bar-${uid}">` +
        `<div class="execute-bar-actions">` +
        `<button type="button" class="btn-execute-secondary" onclick="cancelSqlEditUid('${uid}')">Cancel</button>` +
        `<button type="button" class="btn-execute-secondary" onclick="saveSqlEditUid('${uid}', false)">Save</button>` +
        `<button type="button" class="btn-execute" onclick="saveSqlEditUid('${uid}', true)">Save &amp; Execute</button>` +
        `</div>` +
        `</div>`
    );
}

function getSqlSection(msgId) {
    return document.getElementById('sql-section-' + msgId);
}

function getSqlEditor(msgId) {
    return document.getElementById('sql-editor-' + msgId);
}

function getSqlPre(msgId) {
    return document.getElementById('sql-' + msgId);
}

function getSqlMessageEl(msgId) {
    return document.querySelector(`[data-msg-id="${msgId}"]`);
}

function getSqlSectionByUid(uid) {
    return document.getElementById('sql-wrap-' + uid);
}

function getSqlEditorByUid(uid) {
    return document.getElementById('sql-editor-' + uid);
}

function getSqlPreByUid(uid) {
    return document.getElementById(uid);
}

function updateSqlSectionDisplay(section, pre, sql) {
    const formatted = formatSqlDisplay(sql);
    if (pre) pre.textContent = formatted;
    if (section) section.dataset.copyText = formatted;
}

function enterSqlEditMode({ section, pre, editor, executeBar, sql, editModeBarHtml }) {
    if (!section || !pre || !editor) return;
    editor.value = sql || pre.textContent || '';
    section.classList.add('editing');
    pre.hidden = true;
    editor.hidden = false;
    if (executeBar && editModeBarHtml) {
        executeBar.hidden = false;
        executeBar.innerHTML = editModeBarHtml;
    }
    const editBtn = section.querySelector('.btn-sql-edit');
    if (editBtn) editBtn.hidden = true;
    editor.focus();
}

function exitSqlEditMode({ section, pre, editor, executeBar, viewBarHtml, hideExecuteBar = false }) {
    if (!section || !pre || !editor) return;
    section.classList.remove('editing');
    pre.hidden = false;
    editor.hidden = true;
    if (executeBar) {
        if (hideExecuteBar) {
            executeBar.hidden = true;
            executeBar.innerHTML = '';
        } else if (viewBarHtml !== undefined) {
            executeBar.hidden = false;
            executeBar.innerHTML = viewBarHtml;
        }
    }
    const editBtn = section.querySelector('.btn-sql-edit');
    if (editBtn) editBtn.hidden = false;
}

function ensureExecuteBar(msgId, section) {
    let bar = document.getElementById('execute-bar-' + msgId);
    if (!bar && section) {
        bar = document.createElement('div');
        bar.className = 'execute-bar';
        bar.id = 'execute-bar-' + msgId;
        section.appendChild(bar);
    }
    return bar;
}

function toggleSqlEdit(msgId) {
    const section = getSqlSection(msgId);
    const pre = getSqlPre(msgId);
    const editor = getSqlEditor(msgId);
    const msgEl = getSqlMessageEl(msgId);
    const executeBar = ensureExecuteBar(msgId, section);
    if (!section || !pre || !editor || !msgEl) return;

    enterSqlEditMode({
        section,
        pre,
        editor,
        executeBar,
        sql: msgEl.dataset.sql || pre.textContent,
        editModeBarHtml: (
            `<div class="execute-bar-actions">` +
            `<button type="button" class="btn-execute-secondary" onclick="cancelSqlEdit(${msgId})">Cancel</button>` +
            `<button type="button" class="btn-execute-secondary" onclick="saveSqlEdit(${msgId}, false)">Save</button>` +
            `<button type="button" class="btn-execute" onclick="saveSqlEdit(${msgId}, true)">Save &amp; Execute</button>` +
            `</div>`
        ),
    });
}

function cancelSqlEdit(msgId) {
    const section = getSqlSection(msgId);
    const pre = getSqlPre(msgId);
    const editor = getSqlEditor(msgId);
    const executeBar = document.getElementById('execute-bar-' + msgId);
    if (!section || !pre || !editor) return;

    exitSqlEditMode({
        section,
        pre,
        editor,
        executeBar,
        viewBarHtml: executeBar ? renderExecuteButton(msgId) : '',
    });
}

function saveSqlEdit(msgId, execute = false) {
    const section = getSqlSection(msgId);
    const pre = getSqlPre(msgId);
    const editor = getSqlEditor(msgId);
    const msgEl = getSqlMessageEl(msgId);
    const executeBar = document.getElementById('execute-bar-' + msgId);
    if (!section || !pre || !editor || !msgEl) return;

    const sql = editor.value.trim();
    if (!sql) return;

    msgEl.dataset.sql = sql;
    updateSqlSectionDisplay(section, pre, sql);

    exitSqlEditMode({
        section,
        pre,
        editor,
        executeBar,
        viewBarHtml: executeBar ? renderExecuteButton(msgId) : '',
    });

    if (execute && typeof executeQuery === 'function') {
        executeQuery(msgId);
    }
}

function toggleSqlEditUid(uid) {
    const section = getSqlSectionByUid(uid);
    const pre = getSqlPreByUid(uid);
    const editor = getSqlEditorByUid(uid);
    const executeBar = document.getElementById('execute-bar-' + uid);
    if (!section || !pre || !editor) return;

    enterSqlEditMode({
        section,
        pre,
        editor,
        executeBar,
        sql: section.dataset.rawSql || pre.textContent,
        editModeBarHtml: (
            `<div class="execute-bar-actions">` +
            `<button type="button" class="btn-execute-secondary" onclick="cancelSqlEditUid('${uid}')">Cancel</button>` +
            `<button type="button" class="btn-execute-secondary" onclick="saveSqlEditUid('${uid}', false)">Save</button>` +
            `<button type="button" class="btn-execute" onclick="saveSqlEditUid('${uid}', true)">Save &amp; Execute</button>` +
            `</div>`
        ),
    });
}

function cancelSqlEditUid(uid) {
    const section = getSqlSectionByUid(uid);
    const pre = getSqlPreByUid(uid);
    const editor = getSqlEditorByUid(uid);
    const executeBar = document.getElementById('execute-bar-' + uid);
    if (!section || !pre || !editor) return;

    exitSqlEditMode({ section, pre, editor, executeBar, viewBarHtml: '', hideExecuteBar: true });
}

function saveSqlEditUid(uid, execute = false) {
    const section = getSqlSectionByUid(uid);
    const pre = getSqlPreByUid(uid);
    const editor = getSqlEditorByUid(uid);
    const executeBar = document.getElementById('execute-bar-' + uid);
    if (!section || !pre || !editor) return;

    const sql = editor.value.trim();
    if (!sql) return;

    section.dataset.rawSql = sql;
    updateSqlSectionDisplay(section, pre, sql);

    exitSqlEditMode({ section, pre, editor, executeBar, viewBarHtml: '', hideExecuteBar: true });

    if (execute && typeof executeEditedSqlV2 === 'function') {
        executeEditedSqlV2(uid);
    }
}
