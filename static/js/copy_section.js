/** Copy-to-clipboard helpers for bordered content sections. */

let copySectionCounter = 0;

function copySectionIcon() {
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" ' +
        'width="14" height="14" aria-hidden="true">' +
        '<rect x="9" y="9" width="11" height="11" rx="1.5"/>' +
        '<path d="M7 15H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v1"/>' +
        '</svg>'
    );
}

function renderCopyButton(title) {
    const label = title || 'Copy';
    return (
        `<button type="button" class="btn-copy" onclick="copyFromSection(this)" ` +
        `title="${label}" aria-label="${label}">${copySectionIcon()}</button>`
    );
}

function escapeAttr(text) {
    return String(text || '')
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/\n/g, '&#10;');
}

function nextCopySectionId(prefix) {
    copySectionCounter += 1;
    return `${prefix}-${copySectionCounter}-${Date.now().toString(36)}`;
}

function renderCopySection(className, label, bodyHtml, plainText, sectionId) {
    const id = sectionId || nextCopySectionId('copy-section');
    const labelHtml = label ? `<div class="copy-section-label">${label}</div>` : '';
    return (
        `<div class="copy-section ${className}" id="${id}" data-copy-text="${escapeAttr(plainText)}">` +
        renderCopyButton('Copy') +
        labelHtml +
        `<div class="copy-section-body">${bodyHtml}</div>` +
        `</div>`
    );
}

function copyFromSection(btn) {
    const section = btn.closest('.copy-section, .sql-section');
    if (!section) return;

    const text = section.dataset.copyText
        || section.querySelector('.copy-section-body')?.innerText
        || section.querySelector('.sql-block')?.innerText
        || '';

    if (!text) return;

    const write = () => {
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(text);
        }
        const helper = document.createElement('textarea');
        helper.value = text;
        helper.setAttribute('readonly', '');
        helper.style.position = 'fixed';
        helper.style.left = '-9999px';
        helper.style.top = '0';
        helper.style.opacity = '0';
        document.body.appendChild(helper);
        helper.select();
        let ok = false;
        try {
            ok = document.execCommand('copy');
        } finally {
            helper.remove();
        }
        return ok ? Promise.resolve() : Promise.reject(new Error('Copy failed'));
    };

    write()
        .then(() => showCopyFeedback(btn))
        .catch(() => {});
}

function showCopyFeedback(btn) {
    const previous = btn.getAttribute('title') || 'Copy';
    btn.classList.add('copied');
    btn.setAttribute('title', 'Copied');
    setTimeout(() => {
        btn.classList.remove('copied');
        btn.setAttribute('title', previous);
    }, 1200);
}
