/** Pretty-print SQL for display (chat + analytics). */

function formatSqlDisplay(sql) {
    const text = (sql || '').trim();
    if (!text) return '';

    // Already multi-line / indented — keep LLM2-style formatting as-is.
    if (/\n\s*(SELECT|FROM|WHERE|WITH|AND)\b/i.test(text)) {
        return text;
    }

    if (typeof sqlFormatter !== 'undefined' && typeof sqlFormatter.format === 'function') {
        try {
            return sqlFormatter.format(text, {
                language: 'postgresql',
                tabWidth: 2,
                keywordCase: 'upper',
                linesBetweenQueries: 1,
            });
        } catch (_) {
            /* fall through to simple formatter */
        }
    }

    return fallbackFormatSql(text);
}

function fallbackFormatSql(sql) {
    const strings = [];
    let protectedSql = sql.replace(/'([^']|'')*'/g, (match) => {
        strings.push(match);
        return `__S${strings.length - 1}__`;
    });

    const breaks = [
        'UNION ALL',
        'UNION',
        'INNER JOIN',
        'LEFT JOIN',
        'RIGHT JOIN',
        'FULL OUTER JOIN',
        'FULL JOIN',
        'CROSS JOIN',
        'JOIN',
        'GROUP BY',
        'ORDER BY',
        'HAVING',
        'LIMIT',
        'OFFSET',
        'SELECT',
        'FROM',
        'WHERE',
        'WITH',
    ];

    breaks.forEach((kw) => {
        const pattern = new RegExp(`\\s+${kw.replace(/ /g, '\\s+')}\\s+`, 'gi');
        protectedSql = protectedSql.replace(pattern, `\n${kw.toUpperCase()} `);
    });

    protectedSql = protectedSql.replace(/\s+AND\s+/gi, '\n  AND ');
    protectedSql = protectedSql.replace(/\s+OR\s+/gi, '\n  OR ');
    protectedSql = protectedSql.replace(/,\s*(?=[\w"(])/g, ',\n    ');

    strings.forEach((value, index) => {
        protectedSql = protectedSql.replace(`__S${index}__`, value);
    });

    return protectedSql.trim();
}
