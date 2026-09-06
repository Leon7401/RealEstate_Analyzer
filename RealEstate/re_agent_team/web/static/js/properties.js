/**
 * 収益物件 分析一覧ページ
 */

let rowsCache = [];
let selectedId = null;
let currentSort = 'judge_score';

const PREF_LABEL = {
    '13': '東京', '14': '神奈川', '11': '埼玉', '12': '千葉',
};

function selectedPrefs() {
    const all = document.querySelector('input[data-pref="all"]');
    if (all?.checked) return '';
    const codes = [...document.querySelectorAll('input[data-pref]:checked')]
        .map(el => el.dataset.pref)
        .filter(v => v && v !== 'all');
    return codes.join(',');
}

function fmtYen(v) {
    if (v == null || v === '' || Number.isNaN(Number(v))) return '-';
    const n = Number(v);
    if (n >= 10000) return `${Math.round(n / 10000).toLocaleString()}万`;
    return `¥${Math.round(n).toLocaleString()}`;
}

function fmtYield(v) {
    if (v == null || Number.isNaN(Number(v))) return '-';
    const n = Number(v);
    const pct = n > 1 ? n : n * 100;
    return `${pct.toFixed(1)}%`;
}

function fmtDate(v) {
    if (!v) return '-';
    return String(v).replace('T', ' ').slice(0, 16);
}

function gradeClass(g) {
    const x = String(g || '').toUpperCase();
    if (['S', 'A', 'B', 'C', 'D', 'F'].includes(x)) return `grade-${x}`;
    return 'grade-none';
}

function linkStatusBadge(row) {
    const code = row.link_status || 'unchecked';
    const label = row.link_status_label || ({
        alive: '掲載中', unchecked: '未確認', suspect: '要確認',
        dead: 'リンク切れ', no_url: 'URLなし',
    }[code] || code);
    const title = [
        row.verify_note ? `メモ: ${row.verify_note}` : '',
        row.last_verified_at ? `確認: ${fmtDate(row.last_verified_at)}` : '',
        row.last_verified_http_status != null ? `HTTP ${row.last_verified_http_status}` : '',
        row.verify_fail_count ? `連続失敗 ${row.verify_fail_count}` : '',
    ].filter(Boolean).join(' / ');
    return `<span class="link-pill link-${code}" title="${escapeHtml(title)}">${escapeHtml(label)}</span>`;
}

function sortRows(rows, sortBy) {
    const key = sortBy || currentSort;
    const arr = [...rows];
    const gradeOrder = { S: 6, A: 5, B: 4, C: 3, D: 2, F: 1 };
    arr.sort((a, b) => {
        if (key === 'judge_grade') {
            return (gradeOrder[b.judge_grade] || 0) - (gradeOrder[a.judge_grade] || 0)
                || (Number(b.judge_score) || 0) - (Number(a.judge_score) || 0);
        }
        if (key === 'name' || key === 'nearest_station' || key === 'structure' || key === 'source' || key === 'updated_at') {
            return String(b[key] || '').localeCompare(String(a[key] || ''), 'ja');
        }
        if (key === 'asking_price' || key === 'station_distance_min') {
            return (Number(a[key]) || 1e15) - (Number(b[key]) || 1e15);
        }
        return (Number(b[key]) || 0) - (Number(a[key]) || 0);
    });
    return arr;
}

function renderTable(rows) {
    const tbody = document.getElementById('props-tbody');
    const countEl = document.getElementById('props-count');
    if (!tbody) return;

    const sorted = sortRows(rows, currentSort);
    if (countEl) {
        const judged = sorted.filter(r => r.judge_grade).length;
        countEl.textContent = `${sorted.length}件表示 / 判定済 ${judged}件`;
    }

    if (!sorted.length) {
        tbody.innerHTML = '<tr><td colspan="16" class="empty">条件に合う収益物件がありません</td></tr>';
        return;
    }

    tbody.innerHTML = sorted.map(r => {
        const grade = r.judge_grade || '-';
        const selected = selectedId === String(r.id) ? 'is-selected' : '';
        const mapUrl = `/?focus_property=${encodeURIComponent(r.id || '')}`;
        const src = r.source_url
            ? `<a href="${r.source_url}" target="_blank" rel="noopener" onclick="event.stopPropagation()">元ページ</a>`
            : '';
        return `<tr class="${selected}" data-id="${r.id || ''}">
            <td><span class="grade-pill ${gradeClass(grade)}">${grade}</span></td>
            <td class="num">${r.judge_score != null ? Number(r.judge_score).toFixed(0) : '-'}</td>
            <td title="${(r.address || '').replace(/"/g, '&quot;')}">
                <div>${escapeHtml(r.name || r.address || '名称なし')}</div>
                <div class="muted">${escapeHtml(r.address || '')}</div>
            </td>
            <td>${escapeHtml(r.nearest_station || '-')}</td>
            <td class="num">${r.station_distance_min != null ? `${r.station_distance_min}分` : '-'}</td>
            <td class="num">${fmtYen(r.asking_price)}</td>
            <td class="num">${fmtYield(r.gross_yield)}</td>
            <td class="num">${fmtYen(r.current_rent_annual)}</td>
            <td class="num">${r.land_area != null ? Number(r.land_area).toFixed(1) : '-'}</td>
            <td class="num">${r.building_area != null ? Number(r.building_area).toFixed(1) : '-'}</td>
            <td>${escapeHtml(r.structure || '-')}</td>
            <td class="num">${r.building_age != null ? `築${r.building_age}` : (r.built_year || '-')}</td>
            <td>${escapeHtml(r.source || '-')}</td>
            <td>${linkStatusBadge(r)}</td>
            <td class="muted">${fmtDate(r.updated_at || r.judged_at)}</td>
            <td class="row-actions">
                <button type="button" data-detail="${r.id}">詳細</button>
                <a href="${mapUrl}">地図</a>
                ${src}
            </td>
        </tr>`;
    }).join('');

    tbody.querySelectorAll('tr[data-id]').forEach(tr => {
        tr.addEventListener('click', (e) => {
            if (e.target.closest('a,button')) return;
            openDetail(tr.dataset.id);
        });
    });
    tbody.querySelectorAll('button[data-detail]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            openDetail(btn.dataset.detail);
        });
    });
}

function escapeHtml(s) {
    return String(s || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function openDetail(id) {
    const row = rowsCache.find(r => String(r.id) === String(id));
    const panel = document.getElementById('props-detail');
    const body = document.getElementById('props-detail-body');
    if (!row || !panel || !body) return;
    selectedId = String(id);
    renderTable(rowsCache);

    const metrics = row.key_metrics || {};
    const metricHtml = Object.keys(metrics).length
        ? `<div class="detail-metrics"><strong>判定指標</strong><br>${
            Object.entries(metrics).map(([k, v]) => `${escapeHtml(k)}: ${escapeHtml(v)}`).join('<br>')
        }</div>`
        : '';

    body.innerHTML = `
        <h3>${escapeHtml(row.name || row.address || '物件')}</h3>
        <dl class="detail-grid">
            <dt>判定</dt><dd><span class="grade-pill ${gradeClass(row.judge_grade)}">${row.judge_grade || '-'}</span>
                ${row.judge_score != null ? ` / ${Number(row.judge_score).toFixed(1)}` : ''}</dd>
            <dt>推奨</dt><dd>${escapeHtml(row.recommendation || '-')}</dd>
            <dt>住所</dt><dd>${escapeHtml(row.address || '-')}</dd>
            <dt>駅</dt><dd>${escapeHtml(row.nearest_station || '-')}
                ${row.station_distance_min != null ? ` 徒歩${row.station_distance_min}分` : ''}</dd>
            <dt>価格</dt><dd>${fmtYen(row.asking_price)}</dd>
            <dt>利回り</dt><dd>${fmtYield(row.gross_yield)}</dd>
            <dt>年賃料</dt><dd>${fmtYen(row.current_rent_annual)}</dd>
            <dt>土地/建物</dt><dd>${row.land_area != null ? row.land_area + '㎡' : '-'} / ${row.building_area != null ? row.building_area + '㎡' : '-'}</dd>
            <dt>構造</dt><dd>${escapeHtml(row.structure || '-')} ${row.building_age != null ? `築${row.building_age}年` : ''}</dd>
            <dt>出典</dt><dd>${escapeHtml(row.source || '-')}</dd>
            <dt>元ページ</dt><dd>${linkStatusBadge(row)}
                ${row.last_verified_at ? `<span class="muted">（${fmtDate(row.last_verified_at)}）</span>` : ''}
                ${row.verify_note ? `<div class="muted">${escapeHtml(row.verify_note)}</div>` : ''}
            </dd>
        </dl>
        ${metricHtml}
        <div style="margin-top:12px;display:flex;gap:8px;">
            <a class="btn-secondary" href="/?focus_property=${encodeURIComponent(row.id || '')}">地図で開く</a>
            ${row.source_url ? `<a class="btn-secondary" href="${row.source_url}" target="_blank" rel="noopener">元ページ</a>` : ''}
        </div>
    `;
    panel.hidden = false;
}

async function loadRows() {
    const tbody = document.getElementById('props-tbody');
    if (tbody) tbody.innerHTML = '<tr><td colspan="16" class="empty">読込中...</td></tr>';

    const prefs = selectedPrefs();
    const station = document.getElementById('filter-station')?.value || '';
    const grade = document.getElementById('filter-grade')?.value || '';
    const sortBy = document.getElementById('filter-sort')?.value || 'judge_score';
    const minYieldRaw = document.getElementById('filter-min-yield')?.value;
    currentSort = sortBy;

    const params = new URLSearchParams();
    if (prefs) params.set('prefecture_code', prefs);
    if (station) params.set('q', station);
    if (grade) params.set('grade', grade === 'unjudged' ? 'UNJUDGED' : grade.toUpperCase());
    if (sortBy) params.set('sort_by', sortBy);
    if (minYieldRaw) params.set('min_yield', String(Number(minYieldRaw) / 100));
    const linkStatus = document.getElementById('filter-link-status')?.value || '';
    if (linkStatus) params.set('link_status', linkStatus);
    params.set('include_delisted', 'true');
    params.set('limit', '500');

    try {
        const resp = await fetch(`/api/properties/analysis-table?${params}`);
        const data = await resp.json();
        rowsCache = data.rows || [];
        renderTable(rowsCache);
    } catch (e) {
        console.error(e);
        if (tbody) tbody.innerHTML = `<tr><td colspan="16" class="empty">読込エラー: ${escapeHtml(e.message)}</td></tr>`;
    }
}

async function analyzeVisible() {
    const status = document.getElementById('props-analyze-status');
    const btn = document.getElementById('btn-analyze-visible');
    const targets = rowsCache.filter(r => !r.judge_grade).slice(0, 50);
    if (!targets.length) {
        if (status) status.textContent = '未判定の表示物件はありません';
        return;
    }
    if (btn) { btn.disabled = true; btn.textContent = '判定中...'; }
    if (status) status.textContent = `未判定 ${targets.length}件を判定中...`;
    try {
        const resp = await fetch('/api/properties/analyze-unanalyzed', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                properties: targets,
                include_rebuild: true,
                limit: targets.length,
            }),
        });
        const data = await resp.json();
        if (status) {
            status.textContent = `完了: 判定${data.analyzed || data.count || 0}件`
                + (data.errors ? ` / エラー${data.errors}` : '');
        }
        await loadRows();
    } catch (e) {
        if (status) status.textContent = `判定エラー: ${e.message}`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '表示中を一括判定'; }
    }
}

function bindEvents() {
    document.getElementById('btn-reload')?.addEventListener('click', loadRows);
    document.getElementById('filter-link-status')?.addEventListener('change', loadRows);
    document.getElementById('btn-verify-links')?.addEventListener('click', verifySourceLinks);
    document.getElementById('btn-analyze-visible')?.addEventListener('click', analyzeVisible);
    document.getElementById('detail-close')?.addEventListener('click', () => {
        document.getElementById('props-detail').hidden = true;
    });

    let timer = null;
    document.getElementById('filter-station')?.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(loadRows, 280);
    });
    ['filter-grade', 'filter-sort', 'filter-min-yield'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', loadRows);
    });
    document.querySelectorAll('input[data-pref]').forEach(el => {
        el.addEventListener('change', (e) => {
            if (e.target.dataset.pref === 'all' && e.target.checked) {
                document.querySelectorAll('input[data-pref]:not([data-pref="all"])').forEach(x => { x.checked = false; });
            } else if (e.target.dataset.pref !== 'all' && e.target.checked) {
                const all = document.querySelector('input[data-pref="all"]');
                if (all) all.checked = false;
            }
            loadRows();
        });
    });

    document.querySelectorAll('#props-table thead th[data-sort]').forEach(th => {
        th.addEventListener('click', () => {
            currentSort = th.dataset.sort;
            const sel = document.getElementById('filter-sort');
            if (sel && [...sel.options].some(o => o.value === currentSort)) {
                sel.value = currentSort;
            }
            renderTable(rowsCache);
        });
    });
}

async function verifySourceLinks() {
    const btn = document.getElementById('btn-verify-links');
    const status = document.getElementById('props-verify-status');
    if (btn) { btn.disabled = true; btn.textContent = '走査中...'; }
    if (status) status.textContent = '元ページを確認しています...';
    try {
        const resp = await fetch('/api/listings/verify-source', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ limit: 80, stale_hours: 24 }),
        });
        const data = await resp.json();
        if (!resp.ok || data.error) throw new Error(data.error || `HTTP ${resp.status}`);
        const prop = (data.result || {}).properties || {};
        if (status) {
            status.textContent = `確認 ${prop.checked || 0}件 / 問題 ${prop.failed || 0}件`;
        }
        await loadRows();
    } catch (e) {
        if (status) status.textContent = `走査失敗: ${e.message}`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '元ページ走査'; }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    bindEvents();
    loadRows();
});
