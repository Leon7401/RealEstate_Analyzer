/**
 * 不動産投資判定マップ - メインJS
 */

let map;
let landPriceLayer = null;
let transactionLayer = null;
let propertyLayer = null;
let landListingLayer = null;
let heatmapLayer = null;
let stationLayer = null;
let stationsData = [];
let sampleProperties = [];
const ANALYSIS_CACHE_KEY = 're_analysis_cache_v1';
let analysisCache = {};
let rankingPanelCollapsed = false;

function _safeLower(v) {
    return (v || '').toString().trim().toLowerCase();
}

function getPropertyCacheKeys(p) {
    const keys = [];
    if (p && p.id != null) keys.push(`id:${p.id}`);
    if (p && p.source_url) keys.push(`url:${_safeLower(p.source_url)}`);
    if (p && p.address) keys.push(`addr:${_safeLower(p.address)}`);
    if (p && p.name && p.address) keys.push(`nameaddr:${_safeLower(p.name)}|${_safeLower(p.address)}`);
    return keys;
}

function loadAnalysisCache() {
    try {
        const raw = localStorage.getItem(ANALYSIS_CACHE_KEY);
        analysisCache = raw ? JSON.parse(raw) : {};
    } catch (e) {
        console.warn('分析キャッシュ読込失敗:', e);
        analysisCache = {};
    }
}

function saveAnalysisCache() {
    try {
        localStorage.setItem(ANALYSIS_CACHE_KEY, JSON.stringify(analysisCache));
    } catch (e) {
        console.warn('分析キャッシュ保存失敗:', e);
    }
}

function applyCachedAnalysisToProperty(p) {
    const keys = getPropertyCacheKeys(p);
    let cached = null;
    for (const k of keys) {
        if (analysisCache[k]) {
            cached = analysisCache[k];
            break;
        }
    }
    if (!cached) return;

    if (cached.grade) p.grade = cached.grade;
    if (cached.gross_yield != null) p.gross_yield = cached.gross_yield;
    if (cached.net_yield != null) p.net_yield = cached.net_yield;
    if (cached.score != null) p._analysis_score = cached.score;
    if (cached.recommendation) p._analysis_recommendation = cached.recommendation;
    if (cached.scenario) p._analysis_scenario = cached.scenario;
    if (cached.confidence != null) p._analysis_confidence = cached.confidence;
    if (cached.as_is) p._scenario_as_is = cached.as_is;
    if (cached.rebuild) p._scenario_rebuild = cached.rebuild;
    if (cached.updated_at) p._analysis_updated_at = cached.updated_at;
}

function persistPropertyAnalysisResult(p, row) {
    if (!p || !row) return;
    const selected = row.selected || {};
    const payload = {
        grade: selected.grade || p.grade || null,
        gross_yield: selected.gross_yield ?? p.gross_yield ?? null,
        net_yield: selected.net_yield ?? p.net_yield ?? null,
        score: selected.score ?? p._analysis_score ?? null,
        recommendation: selected.recommendation ?? p._analysis_recommendation ?? null,
        scenario: selected.scenario ?? p._analysis_scenario ?? null,
        confidence: selected.confidence ?? p._analysis_confidence ?? null,
        as_is: row.as_is || p._scenario_as_is || null,
        rebuild: row.rebuild || p._scenario_rebuild || null,
        updated_at: new Date().toISOString(),
    };
    const keys = getPropertyCacheKeys(p);
    keys.forEach(k => { analysisCache[k] = payload; });
}

// ===== スクレイパー切替 =====

function switchScraperPanel(mode) {
    document.querySelectorAll('.scraper-sub-panel').forEach(p => p.classList.remove('active'));
    const target = document.getElementById('scraper-' + mode);
    if (target) target.classList.add('active');
}

// ===== ユーティリティ =====

function gradeColor(grade) {
    const colors = {S:'#1a9641',A:'#4dac26',B:'#b8e186',C:'#fdb863',D:'#e66101',F:'#d7191c'};
    return colors[grade] || '#546e7a';
}

// ===== 初期化 =====

function initMap(center, zoom) {
    map = L.map('map').setView(center, zoom);
    loadAnalysisCache();

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 19,
    }).addTo(map);

    // タブ
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });

    // イベント
    document.getElementById('prefecture-select').addEventListener('change', loadCities);
    document.getElementById('btn-load-data').addEventListener('click', loadAreaData);
    document.getElementById('btn-analyze').addEventListener('click', analyzeProperty);
    document.getElementById('btn-batch-analyze').addEventListener('click', batchAnalyze);
    const autoAnalyzeBtn = document.getElementById('btn-auto-analyze-map');
    if (autoAnalyzeBtn) autoAnalyzeBtn.addEventListener('click', autoAnalyzeAndReflect);
    const rankingCloseBtn = document.getElementById('ranking-close-btn');
    if (rankingCloseBtn) rankingCloseBtn.addEventListener('click', hideRankingPanel);
    const rankingToggleBtn = document.getElementById('ranking-toggle-btn');
    if (rankingToggleBtn) rankingToggleBtn.addEventListener('click', toggleRankingPanel);
    document.getElementById('btn-upload-csv').addEventListener('click', uploadCSV);
    document.getElementById('btn-scrape').addEventListener('click', scrapeProperties);
    document.getElementById('btn-scrape-url').addEventListener('click', scrapeUrl);
    const reportsBtn = document.getElementById('btn-refresh-reports');
    if (reportsBtn) reportsBtn.addEventListener('click', loadReports);
    document.getElementById('preset-select').addEventListener('change', fillPreset);

    // 土地タブイベント
    document.getElementById('btn-land-scrape').addEventListener('click', scrapeLandListings);
    document.getElementById('btn-land-csv').addEventListener('click', uploadLandCSV);
    document.getElementById('btn-generate-plans').addEventListener('click', batchGeneratePlans);
    document.getElementById('btn-geocode').addEventListener('click', batchGeocode);
    document.getElementById('btn-batch-judge').addEventListener('click', batchJudgeLand);
    document.getElementById('btn-collect-data').addEventListener('click', collectAllData);
    document.getElementById('btn-ingest-api').addEventListener('click', ingestRealData);
    document.getElementById('btn-save-config').addEventListener('click', saveScrapeConfig);
    document.getElementById('btn-competition').addEventListener('click', loadCompetition);
    document.getElementById('btn-refresh-land').addEventListener('click', () => loadLandListings());
    const unifiedBtn = document.getElementById('btn-unified-load');
    if (unifiedBtn) unifiedBtn.addEventListener('click', () => loadUnifiedData(0));

    // 賃料スクレイピング
    const rentalBtn = document.getElementById('btn-rental-scrape');
    if (rentalBtn) rentalBtn.addEventListener('click', scrapeRentals);

    // 歪み分析
    const distBtn = document.getElementById('btn-run-distortion');
    if (distBtn) distBtn.addEventListener('click', runDistortionAnalysis);
    const quickLandBtn = document.getElementById('btn-quick-land-eval');
    if (quickLandBtn) quickLandBtn.addEventListener('click', quickEvaluateLand);

    // レイヤートグル（旧互換）
    const lpEl = document.getElementById('layer-land-price');
    if (lpEl) lpEl.addEventListener('change', toggleLayer);
    const txEl = document.getElementById('layer-transactions');
    if (txEl) txEl.addEventListener('change', toggleLayer);
    const prEl = document.getElementById('layer-properties');
    if (prEl) prEl.addEventListener('change', toggleLayer);
    const llEl = document.getElementById('layer-land-listings');
    if (llEl) llEl.addEventListener('change', toggleLayer);
    const stEl = document.getElementById('layer-stations');
    if (stEl) stEl.addEventListener('change', e => {
        if (e.target.checked) { loadStationMarkers(); } else if (stationLayer) { map.removeLayer(stationLayer); }
    });
    const hmEl = document.getElementById('layer-heatmap');
    if (hmEl) hmEl.addEventListener('change', toggleHeatmap);

    // ハザードマップ
    ['flood', 'landslide', 'tsunami', 'storm', 'terrain'].forEach(type => {
        const el = document.getElementById(`layer-hazard-${type}`);
        if (el) el.addEventListener('change', e => toggleHazardLayer(type, e.target.checked));
    });

    // ===== 250mメッシュレイヤー =====
    const meshMetrics = {
        'layer-mesh-landprice': 'land_price',
        'layer-mesh-rent':      'rent',
        'layer-mesh-tx':        'tx_price',
        'layer-mesh-yield':     'yield',
        // layer-mesh-pop is handled separately with dropdown
        'layer-mesh-facility':  'facility',
        // 'layer-mesh-zoning' removed: 事実ベースレイヤーに移行
    };
    Object.entries(meshMetrics).forEach(([id, metric]) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', e => {
            if (e.target.checked) loadMeshLayer(metric, true);
            else {
                if (meshLayers[metric]) { map.removeLayer(meshLayers[metric]); }
                delete meshLayers[metric];
                delete meshCache[metric];
                delete meshRectCache[metric];
            }
            setTimeout(updateLegend, 300);
        });
    });

    // 人口レイヤー（密度/増減率 切替）
    const popEl = document.getElementById('layer-mesh-pop');
    if (popEl) popEl.addEventListener('change', e => {
        const popMetric = document.getElementById('pop-metric-select').value;
        if (e.target.checked) {
            loadMeshLayer(popMetric, true);
        } else {
            ['population', 'pop_density'].forEach(m => {
                if (meshLayers[m]) { map.removeLayer(meshLayers[m]); }
                delete meshLayers[m]; delete meshCache[m]; delete meshRectCache[m];
            });
        }
        setTimeout(updateLegend, 300);
    });

    // 事実ベースレイヤー（用途地域ポイント・路線別駅）
    const zpEl = document.getElementById('layer-zoning-points');
    if (zpEl) zpEl.addEventListener('change', e => {
        if (e.target.checked) loadZoningPoints();
        else if (zoningPointsLayer) { map.removeLayer(zoningPointsLayer); zoningPointsLayer = null; }
    });
    const rlEl = document.getElementById('layer-railway-lines');
    if (rlEl) rlEl.addEventListener('change', e => {
        if (e.target.checked) loadRailwayLines();
        else if (railwayLinesLayer) { map.removeLayer(railwayLinesLayer); railwayLinesLayer = null; }
    });
    const rdEl = document.getElementById('layer-road-type');
    if (rdEl) rdEl.addEventListener('change', e => {
        toggleRoadTypeLayer(e.target.checked);
        setTimeout(updateLegend, 500);
    });

    // 駅・圏域分析レイヤー
    const ivLayers = {
        'layer-iv-stationpower':{ load: loadIVStationPower, clear: () => { if(ivStationPowerLayer) map.removeLayer(ivStationPowerLayer); ivStationPowerLayer=null; } },
        'layer-iv-yield':        { load: loadIVYield,        clear: () => { if(ivYieldLayer) map.removeLayer(ivYieldLayer); ivYieldLayer=null; } },
    };
    Object.entries(ivLayers).forEach(([id, cfg]) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', e => {
            if (e.target.checked) cfg.load();
            else cfg.clear();
            setTimeout(updateLegend, 300);
        });
    });

    // メッシュ自動リロード (パン/ズーム時)
    _setupMeshAutoReload();

    // 凡例
    try { addLegendControl(); _hookLegendUpdate(); } catch(e) {}

    // 初期読込
    loadCities();
    loadSampleProperties();
    loadRentalStats();
    loadReports();
    loadLandListings();
    loadScrapeConfigs();
    loadLandStats();
    loadStationMarkers();
}

function switchTab(tabId) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.querySelector(`.tab[data-tab="${tabId}"]`).classList.add('active');
    document.getElementById(tabId).classList.add('active');
    if (tabId === 'tab-data') loadCompareTable();
}

// ===== 市区町村ロード =====

async function loadCities() {
    const pref = document.getElementById('prefecture-select').value;
    try {
        const resp = await fetch(`/api/cities/${pref}`);
        const data = await resp.json();
        const sel = document.getElementById('city-select');
        sel.innerHTML = '';
        data.cities.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c.code;
            opt.textContent = c.name;
            sel.appendChild(opt);
        });
        // 駅マーカーも更新
        loadStationMarkers();
    } catch (e) {
        console.error('市区町村取得エラー:', e);
    }
}

// ===== 統合物件一覧 =====

let _selectedPropIdx = -1;

async function loadSampleProperties() {
    const listEl = document.getElementById('prop-list');
    const countEl = document.getElementById('prop-total-count');
    if (listEl) listEl.innerHTML = '<div style="padding:8px;color:#78909c;font-size:0.75rem;">読込中...</div>';

    // フィルタ・ソートパラメータ取得
    const stationFilter = (document.getElementById('prop-filter-station')?.value || '').trim();
    const typeFilter = document.getElementById('prop-filter-type')?.value || '';
    const sortBy = document.getElementById('prop-sort')?.value || 'yield_desc';
    const includeRebuild = document.getElementById('auto-include-rebuild')?.checked !== false;

    // ランク系はクライアント側で自動分析後に並べる
    const apiSortBy = (sortBy === 'rank_desc' || sortBy === 'rank_asc') ? 'updated_at' : sortBy;
    let url = `/api/sample-properties?sort_by=${apiSortBy}&include_land=true`;
    if (stationFilter) url += `&station_filter=${encodeURIComponent(stationFilter)}`;

    try {
        const resp = await fetch(url);
        const data = await resp.json();
        sampleProperties = data.properties || [];
        sampleProperties.forEach(applyCachedAnalysisToProperty);

        // クライアントサイド種別フィルタ
        let filtered = getFilteredProperties(sampleProperties, typeFilter);

        if (sortBy === 'rank_desc' || sortBy === 'rank_asc') {
            // ランクソート時は未分析分を自動分析してから並べる
            await analyzePropertiesForRanking(filtered, includeRebuild, null);
            filtered = getFilteredProperties(sampleProperties, typeFilter);
        }
        const sorted = sortPropertiesForView(filtered, sortBy);
        applyRankOrderToProperties(sorted);

        if (countEl) countEl.textContent = `(${sorted.length}/${data.total || sampleProperties.length}件)`;

        // hidden preset select（後方互換用）
        const sel = document.getElementById('preset-select');
        if (sel) {
            sel.innerHTML = '<option value="">--</option>';
            sampleProperties.forEach((p, i) => {
                const opt = document.createElement('option');
                opt.value = i;
                opt.textContent = p.name;
                sel.appendChild(opt);
            });
        }

        // リスト表示
        renderPropertyList(sorted);
        // 地図プロット
        plotSampleProperties(sorted);
        showRanking(buildRankingFromProperties(sorted));
    } catch (e) {
        console.error('物件取得エラー:', e);
        if (listEl) listEl.innerHTML = '<div style="padding:8px;color:#ef5350;">読込エラー</div>';
    }
}

function getFilteredProperties(source, typeFilter = null) {
    const tf = typeFilter != null ? typeFilter : (document.getElementById('prop-filter-type')?.value || '');
    let filtered = source || [];
    if (tf) {
        filtered = filtered.filter(p => (p._type || 'property') === tf);
    }
    return filtered;
}

function gradeScore(grade) {
    const order = { S: 6, A: 5, B: 4, C: 3, D: 2, F: 1 };
    return order[String(grade || '').toUpperCase()] || 0;
}

function sortPropertiesForView(props, sortBy) {
    const rows = [...(props || [])];
    const key = sortBy || 'yield_desc';
    rows.sort((a, b) => {
        const ay = Number(a.net_yield ?? a.gross_yield ?? 0);
        const by = Number(b.net_yield ?? b.gross_yield ?? 0);
        const ap = Number(a.asking_price ?? 0);
        const bp = Number(b.asking_price ?? 0);
        const as = Number(a._analysis_score ?? 0);
        const bs = Number(b._analysis_score ?? 0);
        const ag = gradeScore(a.grade);
        const bg = gradeScore(b.grade);
        const ad = Number(a.station_distance_min ?? 999);
        const bd = Number(b.station_distance_min ?? 999);
        const au = String(a.updated_at || a.fetched_at || '');
        const bu = String(b.updated_at || b.fetched_at || '');

        if (key === 'rank_desc') return (bg - ag) || (bs - as) || (by - ay) || (ap - bp);
        if (key === 'rank_asc') return (ag - bg) || (as - bs) || (ay - by) || (bp - ap);
        if (key === 'yield_desc') return (by - ay) || (bg - ag);
        if (key === 'price_asc') return (ap - bp) || (bg - ag);
        if (key === 'price_desc') return (bp - ap) || (bg - ag);
        if (key === 'grade') return (bg - ag) || (bs - as);
        if (key === 'station_near') return (ad - bd) || (bg - ag);
        return bu.localeCompare(au);
    });
    return rows;
}

function applyRankOrderToProperties(sortedProps) {
    sampleProperties.forEach(p => { delete p._rank_order; });
    (sortedProps || []).forEach((p, idx) => {
        p._rank_order = idx + 1;
    });
}

function buildRankingFromProperties(props) {
    return (props || [])
        .filter(p => p.grade || p._analysis_score != null)
        .map(p => ({
            name: p.name || p.address || '物件',
            grade: p.grade || '?',
            score: Number(p._analysis_score || 0),
            recommendation: p._analysis_recommendation || '',
            net_yield: p.net_yield ?? null,
            hold_sell_roi: p._scenario_as_is?.simulation?.hold_sell_roi ?? p._scenario_rebuild?.simulation?.hold_sell_roi ?? null,
            exit_cap_rate: p._scenario_as_is?.valuation?.exit_cap_rate ?? p._scenario_rebuild?.valuation?.exit_cap_rate ?? null,
            scenario: p._analysis_scenario || null,
        }));
}

async function analyzePropertiesForRanking(targets, includeRebuild = true, btn = null) {
    const pending = (targets || []).filter(p => p && (!p.grade || p._analysis_score == null));
    if (!pending.length) return;
    const payloadProps = pending.map(p => ({
        ...p,
        _client_index: sampleProperties.indexOf(p),
    })).filter(p => p._client_index >= 0);
    if (!payloadProps.length) return;

    const oldText = btn ? btn.textContent : '';
    if (btn) {
        btn.disabled = true;
        btn.textContent = `自動分析中... (${payloadProps.length}件)`;
    }
    try {
        const resp = await fetch('/api/properties/auto-analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                properties: payloadProps,
                include_rebuild: includeRebuild,
                limit: 500,
            }),
        });
        const data = await resp.json();
        const rows = data.results || [];
        rows.forEach(r => {
            if (r.error) return;
            const idx = Number(r.client_index);
            if (Number.isNaN(idx) || !sampleProperties[idx]) return;
            const p = sampleProperties[idx];
            const selected = r.selected || {};
            p.grade = selected.grade || p.grade;
            p.gross_yield = selected.gross_yield || p.gross_yield;
            if (selected.net_yield != null) p.net_yield = selected.net_yield;
            p._analysis_score = selected.score;
            p._analysis_recommendation = selected.recommendation;
            p._analysis_scenario = selected.scenario;
            p._analysis_confidence = selected.confidence;
            p._scenario_as_is = r.as_is || null;
            p._scenario_rebuild = r.rebuild || null;
            p._analysis_updated_at = new Date().toISOString();
            persistPropertyAnalysisResult(p, r);
        });
        saveAnalysisCache();
    } catch (err) {
        console.error('自動分析エラー:', err);
        throw err;
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = oldText || '自動分析→ランク反映';
        }
    }
}

function renderPropertyList(props) {
    const listEl = document.getElementById('prop-list');
    if (!listEl) return;

    if (!props || props.length === 0) {
        listEl.innerHTML = '<div style="padding:8px;color:#78909c;font-size:0.75rem;">該当物件なし</div>';
        return;
    }

    let html = '';
    props.forEach((p, filteredIdx) => {
        // sampleProperties配列中のインデックスを探す
        const realIdx = sampleProperties.indexOf(p);
        const price = p.asking_price ? (p.asking_price >= 1e8
            ? (p.asking_price / 1e8).toFixed(1) + '億'
            : Math.round(p.asking_price / 1e4).toLocaleString() + '万') : '?';
        const yldVal = p.net_yield || p.gross_yield || 0;
        const yld = yldVal ? (yldVal * 100).toFixed(1) + '%' : '?';
        const grade = p.grade || '';
        const isLand = (p._type === 'land');
        const scenario = p._analysis_scenario === 'rebuild' ? '建替' : (p._analysis_scenario === 'as_is' ? '現況' : '');
        const gradeColors = {S:'#4caf50',A:'#66bb6a',B:'#ffd54f',C:'#ffa726',D:'#ef5350',F:'#b71c1c'};
        const gc = gradeColors[grade] || '#546e7a';
        const typeBadge = isLand
            ? '<span style="background:#1565c0;color:#fff;padding:0 4px;border-radius:2px;font-size:0.6rem;margin-right:4px;">土地</span>'
            : '<span style="background:#2e7d32;color:#fff;padding:0 4px;border-radius:2px;font-size:0.6rem;margin-right:4px;">収益</span>';
        const station = p.nearest_station ? `${p.nearest_station}${p.station_distance_min ? ' ' + p.station_distance_min + '分' : ''}` : '';
        const selected = realIdx === _selectedPropIdx ? 'background:#1a3a5f;' : '';
        const rankTag = p._rank_order ? `<span style="background:#263238;color:#fff;padding:0 5px;border-radius:10px;font-size:0.58rem;">#${p._rank_order}</span>` : '';

        html += `<div onclick="selectProperty(${realIdx})" style="padding:5px 8px;border-bottom:1px solid #1a2744;cursor:pointer;font-size:0.72rem;${selected}display:flex;align-items:center;gap:6px;"
                      onmouseover="this.style.background='#1a3a5f'" onmouseout="this.style.background='${realIdx === _selectedPropIdx ? '#1a3a5f' : ''}'">
            <div style="flex:1;min-width:0;">
                <div style="display:flex;align-items:center;gap:2px;">
                    ${rankTag}
                    ${typeBadge}
                    ${grade ? `<span style="color:${gc};font-weight:bold;font-size:0.7rem;">${grade}</span>` : ''}
                    ${scenario ? `<span style="background:#455a64;color:#fff;padding:0 4px;border-radius:2px;font-size:0.58rem;">${scenario}</span>` : ''}
                    <span style="color:#e0e0e0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${p.name || p.address || '物件'}</span>
                </div>
                <div style="color:#78909c;font-size:0.65rem;margin-top:1px;">
                    ${station ? station + ' | ' : ''}${price}
                </div>
            </div>
            <div style="text-align:right;white-space:nowrap;">
                <div style="color:${parseFloat(yld) >= 6 ? '#66bb6a' : parseFloat(yld) >= 4 ? '#ffd54f' : '#ef5350'};font-weight:bold;">${yld}</div>
            </div>
        </div>`;
    });
    listEl.innerHTML = html;
}

function selectProperty(idx) {
    _selectedPropIdx = idx;
    const p = sampleProperties[idx];
    if (!p) return;

    // フォームに反映
    fillPropertyForm(idx);

    // 地図パン
    if (p.latitude && p.longitude) {
        map.setView([p.latitude, p.longitude], 16);
    }

    // リストのハイライト更新
    const typeFilter = document.getElementById('prop-filter-type')?.value || '';
    let filtered = sampleProperties;
    if (typeFilter) filtered = sampleProperties.filter(pp => (pp._type || 'property') === typeFilter);
    renderPropertyList(filtered);
}

function plotSampleProperties(propsToPlot) {
    if (propertyLayer) map.removeLayer(propertyLayer);
    propertyLayer = L.layerGroup();

    const props = propsToPlot || sampleProperties;

    props.forEach((p, fi) => {
        if (!p.latitude || !p.longitude) return;

        const realIdx = sampleProperties.indexOf(p);
        const price = p.asking_price ? `${(p.asking_price/10000).toLocaleString()}万円` : '?';
        const yldBase = p.net_yield || p.gross_yield || (p.current_rent_annual && p.asking_price ? (p.current_rent_annual / p.asking_price) : 0);
        const yld = yldBase ? `${(yldBase*100).toFixed(1)}%` : '?';
        const grade = p.grade || '?';
        const source = p.source || '';
        const sourceLink = p.source_url ? `<a href="${p.source_url}" target="_blank" rel="noopener" style="color:#4fc3f7;">物件ページ</a>` : '';
        const structure = p.structure || '';
        const age = p.building_age != null && p.building_age > 0 ? `築${p.building_age}年` : p._type === 'land' ? '新築' : '';
        const isLand = (p._type === 'land');
        const rankNo = p._rank_order || null;

        const gradeColor = grade === 'S' ? '#4caf50' : grade === 'A' ? '#66bb6a' : grade === 'B' ? '#ffd54f' : grade === 'C' ? '#ffa726' : grade === 'D' ? '#ef5350' : grade === 'F' ? '#b71c1c' : (isLand ? '#42a5f5' : '#78909c');
        const markerShape = isLand ? { radius: 7, weight: 2, dashArray: '3' } : { radius: 8, weight: 2 };

        const marker = L.circleMarker([p.latitude, p.longitude], {
            ...markerShape, color: gradeColor, fillColor: gradeColor, fillOpacity: 0.7,
        });

        const estimated = p._coords_estimated ? '<span style="color:#ffa726;font-size:0.6rem;">(推定位置)</span>' : '';
        const typeBadge = isLand ? '<span style="background:#1565c0;color:#fff;padding:1px 5px;border-radius:3px;font-size:0.65rem;margin-left:4px;">土地</span>' : '';
        const scenarioBadge = p._analysis_scenario === 'rebuild'
            ? '<span style="background:#455a64;color:#fff;padding:1px 5px;border-radius:3px;font-size:0.62rem;margin-left:4px;">建替案採用</span>'
            : (p._analysis_scenario === 'as_is'
                ? '<span style="background:#37474f;color:#fff;padding:1px 5px;border-radius:3px;font-size:0.62rem;margin-left:4px;">現況採用</span>'
                : '');
        marker.bindPopup(`
            <div style="min-width:220px;font-size:0.8rem;">
                <strong>${p.name || p.address || '物件'}</strong>
                ${rankNo ? `<span style="background:#263238;color:#fff;padding:1px 6px;border-radius:10px;font-size:0.65rem;margin-left:4px;">#${rankNo}</span>` : ''}
                ${source ? `<span style="background:#1e3a5f;color:#4fc3f7;padding:1px 5px;border-radius:3px;font-size:0.65rem;margin-left:4px;">${source}</span>` : ''}
                ${typeBadge} ${scenarioBadge} ${estimated}
                <br>
                <span style="color:#888;">${p.address || ''}</span><br>
                <table style="margin:4px 0;font-size:0.75rem;">
                    <tr><td>価格</td><td><strong>${price}</strong></td></tr>
                    <tr><td>利回り</td><td><strong style="color:${parseFloat(yld) >= 6 ? '#66bb6a' : '#ffd54f'}">${yld}</strong></td></tr>
                    ${structure ? `<tr><td>構造</td><td>${structure} ${age}</td></tr>` : ''}
                    ${p.nearest_station || p.station_distance_min ? `<tr><td>最寄駅</td><td>${p.nearest_station || '?'} ${p.station_distance_min ? '徒歩'+p.station_distance_min+'分' : ''}</td></tr>` : ''}
                    ${p.land_area ? `<tr><td>土地</td><td>${p.land_area}㎡</td></tr>` : ''}
                </table>
                ${sourceLink}
                <br>
                <button onclick="selectProperty(${realIdx})" style="margin-top:4px;padding:3px 10px;background:#4fc3f7;color:#000;border:none;border-radius:3px;cursor:pointer;font-size:0.72rem;">選択して分析</button>
            </div>
        `);
        if (rankNo && rankNo <= 50) {
            marker.bindTooltip(`#${rankNo}`, {
                permanent: true,
                direction: 'top',
                className: 'rank-tooltip',
                offset: [0, -6],
            });
        }

        propertyLayer.addLayer(marker);
    });

    if (document.getElementById('layer-properties')?.checked) {
        propertyLayer.addTo(map);
    }

    // 物件利回りヒートマップデータも生成
    _propertyHeatData = props
        .filter(p => p.latitude && p.longitude)
        .map(p => {
            const yld = p.net_yield || p.gross_yield || (p.current_rent_annual && p.asking_price ? p.current_rent_annual / p.asking_price : 0);
            return [p.latitude, p.longitude, Math.min(yld * 10, 1.0)];
        })
        .filter(d => d[2] > 0);
}

let _propertyHeatData = [];

function fillPropertyForm(idx) {
    const p = sampleProperties[idx];
    if (!p) return;
    // Fill form
    const setVal = (id, val) => { const el = document.getElementById(id); if (el && val != null) el.value = val; };
    setVal('prop-name', p.name || p.address || '');
    setVal('prop-address', p.address || '');
    setVal('prop-price', p.asking_price ? Math.round(p.asking_price / 10000) : '');
    setVal('prop-land-area', p.land_area || '');
    setVal('prop-building-area', p.building_area || '');
    setVal('prop-age', p.building_age || '');
    setVal('prop-station', p.station_distance_min || '');
    setVal('prop-rent', p.current_rent_annual ? Math.round(p.current_rent_annual / 10000) : '');
    setVal('prop-units', p.units || '');
    if (p.structure) {
        const sel = document.getElementById('prop-structure');
        if (sel) sel.value = p.structure;
    }
    // preset selectも同期
    const presetSel = document.getElementById('preset-select');
    if (presetSel) presetSel.value = idx;
}

function fillPreset() {
    const idx = document.getElementById('preset-select').value;
    if (idx === '') return;
    selectProperty(parseInt(idx));
}

// ===== 賃料統計 =====

async function loadRentalStats() {
    try {
        const resp = await fetch('/api/rental-stats');
        const data = await resp.json();
        const el = document.getElementById('rental-stats');

        if (data.count === 0) {
            el.innerHTML = '<span class="hint">データなし</span>';
            return;
        }

        let html = `
            <div class="rental-stat-row"><span>事例数</span><span>${data.count}件</span></div>
            <div class="rental-stat-row"><span>平均m2賃料</span><span>&yen;${Math.round(data.avg_rent_per_sqm).toLocaleString()}</span></div>
            <div class="rental-stat-row"><span>中央値m2賃料</span><span>&yen;${Math.round(data.median_rent_per_sqm).toLocaleString()}</span></div>
        `;

        if (data.by_structure) {
            html += '<div style="margin-top:6px;font-size:0.72rem;color:#78909c">構造別:</div>';
            for (const [k, v] of Object.entries(data.by_structure)) {
                html += `<div class="rental-station-row"><span>${k} (${v.count}件)</span><span>&yen;${Math.round(v.avg).toLocaleString()}/m2</span></div>`;
            }
        }

        if (data.by_city) {
            html += '<div style="margin-top:6px;font-size:0.72rem;color:#78909c">区別:</div>';
            for (const [k, v] of Object.entries(data.by_city)) {
                html += `<div class="rental-station-row"><span>${k} (${v.count}件)</span><span>&yen;${Math.round(v.avg).toLocaleString()}/m2</span></div>`;
            }
        }

        el.innerHTML = html;
    } catch (e) {
        document.getElementById('rental-stats').innerHTML = '<span class="hint">読込エラー</span>';
    }
}

// ===== データ欠損補完 =====

async function fillGaps(target) {
    const el = document.getElementById('fill-gaps-result');
    el.textContent = `${target}データ収集中...`;
    try {
        const resp = await fetch(`/api/collection/fill-gaps?target=${target}&max_stations=30`, { method: 'POST' });
        const data = await resp.json();
        el.innerHTML = `<span style="color:#4caf50">${data.message}</span>`;
    } catch(e) {
        el.innerHTML = `<span style="color:#ef5350">エラー: ${e.message}</span>`;
    }
}

// ===== エリアデータ =====

async function loadAreaData() {
    const btn = document.getElementById('btn-load-data');
    const pref = document.getElementById('prefecture-select').value;
    const city = document.getElementById('city-select').value;

    btn.disabled = true;
    btn.textContent = '地価+賃料 読込中...';

    try {
        // 1. 既存地価データ + 取引データ
        const [lpResp, txResp] = await Promise.all([
            fetch(`/api/land-prices/${pref}?city_code=${city}`),
            fetch(`/api/transactions/${pref}?city_code=${city}`),
        ]);
        const lpData = await lpResp.json();
        renderLandPrices(lpData.geojson);
        showAreaStats(lpData.summary);
        const txData = await txResp.json();
        renderTransactions(txData.geojson);

        // 2. 公示地価API (bounds→DB保存) + 賃料スクレイピングを同時実行
        btn.textContent = 'API地価+賃料取得中...';
        const [apiLpResp, rentalResp] = await Promise.all([
            fetch(`/api/reinfolib/land-prices?${_mapBoundsParams()}&zoom=13&force_fetch=true`),
            fetch('/api/scrape/rental', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({prefecture_code: pref, max_pages: 5}),
            }),
        ]);

        const apiLpData = await apiLpResp.json();
        if (apiLpData.count > 0) {
            // 公示地価レイヤーを自動表示
            const olpCheck = document.getElementById('layer-official-land-price');
            if (olpCheck && !olpCheck.checked) olpCheck.checked = true;
            loadOfficialLandPriceLayer();
            console.log(`公示地価API: ${apiLpData.count}件取得 (${apiLpData.source})`);
        }

        const rentalData = await rentalResp.json();
        if (rentalData.count > 0) {
            console.log(`賃料スクレイピング: ${rentalData.count}件`);
            loadRentalStats();
        }
    } catch (err) {
        console.error('データ読込エラー:', err);
    } finally {
        btn.disabled = false;
        btn.textContent = '地価データ読込';
    }
}

// ===== 地図レンダリング =====

function renderLandPrices(geojson) {
    if (landPriceLayer) map.removeLayer(landPriceLayer);

    landPriceLayer = L.geoJSON(geojson, {
        pointToLayer: (f, ll) => L.circleMarker(ll, {
            radius: 6, fillColor: f.properties.color,
            color: '#fff', weight: 1, fillOpacity: 0.8,
        }),
        onEachFeature: (f, layer) => {
            const p = f.properties;
            let changeStr = '';
            if (p.change_rate != null) {
                const sign = p.change_rate >= 0 ? '+' : '';
                changeStr = `<br>前年比: ${sign}${(p.change_rate * 100).toFixed(1)}%`;
            }
            layer.bindPopup(`
                <div class="popup-title">${p.type}</div>
                <div class="popup-price">${p.price_label}</div>
                <div class="popup-detail">
                    ${p.address}<br>用途: ${p.use_zone}${changeStr}
                    ${p.station ? '<br>最寄駅: ' + p.station : ''}
                </div>
            `);
        },
    });
    if (document.getElementById('layer-land-price').checked) landPriceLayer.addTo(map);
}

function renderTransactions(geojson) {
    if (transactionLayer) map.removeLayer(transactionLayer);

    transactionLayer = L.geoJSON(geojson, {
        pointToLayer: (f, ll) => L.circleMarker(ll, {
            radius: 5, fillColor: '#ff9800',
            color: '#fff', weight: 1, fillOpacity: 0.7,
        }),
        onEachFeature: (f, layer) => {
            const p = f.properties;
            layer.bindPopup(`
                <div class="popup-title">取引事例 (${p.type})</div>
                <div class="popup-price">${p.price_label}</div>
                <div class="popup-detail">
                    ${p.address}<br>時期: ${p.date}<br>
                    面積: ${p.area ? p.area + 'm2' : '不明'}<br>用途: ${p.use}
                </div>
            `);
        },
    });
    if (document.getElementById('layer-transactions').checked) transactionLayer.addTo(map);
}

function showAreaStats(summary) {
    const panel = document.getElementById('stats-panel');
    panel.style.display = 'block';
    const cityName = summary.city_name ? ` (${summary.city_name})` : '';

    document.getElementById('area-stats').innerHTML = `
        <div class="stat-row"><span>エリア${cityName}</span><span>${summary.count}地点</span></div>
        <div class="stat-row"><span>平均m2単価</span><span>&yen;${Math.round(summary.avg_price).toLocaleString()}</span></div>
        <div class="stat-row"><span>中央値m2単価</span><span>&yen;${Math.round(summary.median_price).toLocaleString()}</span></div>
        <div class="stat-row"><span>最低m2単価</span><span>&yen;${Math.round(summary.min_price).toLocaleString()}</span></div>
        <div class="stat-row"><span>最高m2単価</span><span>&yen;${Math.round(summary.max_price).toLocaleString()}</span></div>
        ${summary.change_rate != null ? `<div class="stat-row"><span>平均変動率</span><span>${(summary.change_rate * 100).toFixed(1)}%</span></div>` : ''}
    `;
}

// ===== 物件分析 =====

async function analyzeProperty() {
    const btn = document.getElementById('btn-analyze');
    btn.disabled = true;
    btn.textContent = '分析中...';

    const pref = document.getElementById('prefecture-select').value;
    const city = document.getElementById('city-select').value;

    const propData = {
        name: document.getElementById('prop-name').value || '無題物件',
        address: document.getElementById('prop-address').value || '',
        prefecture_code: pref,
        city_code: city || pref + '101',
        asking_price: (parseInt(document.getElementById('prop-price').value) || 0) * 10000,
        structure: document.getElementById('prop-structure').value,
        land_area: parseFloat(document.getElementById('prop-land-area').value) || null,
        building_area: parseFloat(document.getElementById('prop-building-area').value) || null,
        building_age: parseInt(document.getElementById('prop-age').value) || null,
        station_distance_min: parseInt(document.getElementById('prop-station').value) || null,
        current_rent_annual: (parseInt(document.getElementById('prop-rent').value) || 0) * 10000,
        units: parseInt(document.getElementById('prop-units').value) || null,
    };

    // 選択中の物件から座標を取得
    const presetIdx = document.getElementById('preset-select').value;
    if (presetIdx !== '' && sampleProperties[parseInt(presetIdx)]) {
        const sp = sampleProperties[parseInt(presetIdx)];
        if (sp.latitude) propData.latitude = sp.latitude;
        if (sp.longitude) propData.longitude = sp.longitude;
        if (sp.road_frontage) propData.road_frontage = sp.road_frontage;
        if (sp.land_shape) propData.land_shape = sp.land_shape;
    }

    const analysisInputBefore = {
        asking_price: propData.asking_price || null,
        price_per_sqm: propData.price_per_sqm || null,
        current_rent_annual: propData.current_rent_annual || null,
        nearest_station: propData.nearest_station || null,
        station_distance_min: propData.station_distance_min || null,
    };

    try {
        const resp = await fetch('/api/analyze-full', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(propData),
        });
        const data = await resp.json();
        showJudgmentResult(
            data.judgment,
            data.valuation,
            data.simulation,
            data.critic_review,
            data.market_context,
            data.market_benchmark,
            data.auto_filled,
            data.analysis_input_before || analysisInputBefore,
            data.analysis_input_after || null
        );
        if (data.asset_score) showAssetScoreInResult(data.asset_score);
        switchTab('tab-property');
    } catch (err) {
        console.error('分析エラー:', err);
        alert('分析に失敗しました');
    } finally {
        btn.disabled = false;
        btn.textContent = '投資判定実行';
    }
}

async function batchAnalyze() {
    const btn = document.getElementById('btn-batch-analyze');
    btn.disabled = true;
    btn.textContent = '一括分析中...';

    try {
        const resp = await fetch('/api/analyze-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ properties: sampleProperties }),
        });
        const data = await resp.json();
        showRanking(data.ranking);
    } catch (err) {
        console.error('一括分析エラー:', err);
    } finally {
        btn.disabled = false;
        btn.textContent = '全サンプルを一括判定';
    }
}

async function autoAnalyzeAndReflect() {
    const btn = document.getElementById('btn-auto-analyze-map');
    if (!btn) return;
    const includeRebuild = document.getElementById('auto-include-rebuild')?.checked !== false;
    const typeFilter = document.getElementById('prop-filter-type')?.value || '';
    const sortBy = document.getElementById('prop-sort')?.value || 'rank_desc';
    const targets = getFilteredProperties(sampleProperties, typeFilter);

    if (!targets.length) {
        alert('分析対象の物件がありません');
        return;
    }

    try {
        await analyzePropertiesForRanking(targets, includeRebuild, btn);
        const refreshed = getFilteredProperties(sampleProperties, typeFilter);
        const sorted = sortPropertiesForView(refreshed, sortBy);
        applyRankOrderToProperties(sorted);
        renderPropertyList(sorted);
        plotSampleProperties(sorted);
        showRanking(buildRankingFromProperties(sorted));
    } catch (err) {
        console.error('自動分析エラー:', err);
        alert('自動分析に失敗しました');
    }
}

// ===== 結果表示 =====

function showJudgmentResult(
    judgment,
    valuation,
    simulation,
    critic,
    marketContext,
    marketBenchmark,
    autoFilled,
    analysisInputBefore,
    analysisInputAfter
) {
    const panel = document.getElementById('result-panel');
    panel.style.display = 'block';

    // Grade badge (keep as-is)
    const gradeColors = {S:'#4caf50',A:'#66bb6a',B:'#ffd54f',C:'#ffa726',D:'#ef5350',F:'#b71c1c'};
    document.getElementById('result-grade').textContent = judgment.grade;
    document.getElementById('result-grade').style.background = gradeColors[judgment.grade] || '#78909c';
    document.getElementById('result-recommendation').textContent = judgment.recommendation;
    document.getElementById('result-score').textContent = `スコア: ${judgment.overall_score?.toFixed(1)} / 確信度: ${(judgment.confidence*100).toFixed(0)}%`;

    // --- Top: 3 key numbers ---
    const grossYield = valuation?.gross_yield ? `${(valuation.gross_yield*100).toFixed(1)}%` : '-';
    const netYield = valuation?.net_yield ? `${(valuation.net_yield*100).toFixed(1)}%` : '-';
    const landRatio = valuation?.land_value_ratio_in_price != null ? `${(valuation.land_value_ratio_in_price*100).toFixed(0)}%` : '-';

    document.getElementById('result-scores').innerHTML = `
        <div class="result-key-numbers">
            <div class="result-key-num"><div class="num-label">表面利回り</div><div class="num-value">${grossYield}</div></div>
            <div class="result-key-num"><div class="num-label">実質利回り</div><div class="num-value">${netYield}</div></div>
            <div class="result-key-num"><div class="num-label">土地値比率</div><div class="num-value">${landRatio}</div></div>
        </div>
    `;

    // --- Hold & Sell section (8年保有→売却) - MOST IMPORTANT ---
    let holdSellHtml = '';
    if (simulation) {
        const s = simulation;
        const fmt = (v) => v != null ? `${(v/10000).toLocaleString()}万` : '-';
        const roiFmt = (v) => v != null ? `${(v*100).toFixed(0)}%` : '-';
        const capBase = s.hold_sell_exit_cap_base != null ? `${(s.hold_sell_exit_cap_base * 100).toFixed(1)}%` : '動的';
        holdSellHtml = `
            <div class="hold-sell-section">
                <div class="hs-title">8年保有 → 売却シミュレーション</div>
                <div class="hold-sell-grid">
                    <div class="hs-item"><span class="hs-label">8年累積CF</span><span class="hs-val">${fmt(s.hold_sell_cumulative_cf)}</span></div>
                    <div class="hs-item"><span class="hs-label">売却価格(${capBase})</span><span class="hs-val">${fmt(s.hold_sell_exit_price_65)}</span></div>
                    <div class="hs-item"><span class="hs-label">トータルリターン</span><span class="hs-val">${fmt(s.hold_sell_total_return_65)}</span></div>
                    <div class="hs-item"><span class="hs-label">ROI</span><span class="hs-val">${roiFmt(s.hold_sell_roi_65)}</span></div>
                </div>
            </div>
        `;
    }

    // --- Heatmap連携コンテキスト ---
    let marketHtml = '';
    if (marketContext) {
        const lp = marketContext.land_price_sqm
            ? `¥${Math.round(marketContext.land_price_sqm).toLocaleString()}/㎡`
            : '-';
        const rent = marketContext.rent_sqm
            ? `¥${Math.round(marketContext.rent_sqm).toLocaleString()}/㎡`
            : '-';
        const tx = marketContext.tx_price_sqm
            ? `¥${Math.round(marketContext.tx_price_sqm).toLocaleString()}/㎡`
            : '-';
        const yld = marketContext.implied_yield
            ? `${(Number(marketContext.implied_yield) * 100).toFixed(1)}%`
            : '-';
        const st = marketContext.nearest_station || marketContext.station_name || '-';
        const src = marketContext.source || '-';
        const autoKeys = autoFilled ? Object.keys(autoFilled) : [];
        const before = analysisInputBefore || {};
        const after = analysisInputAfter || {};
        const fmtYen = (v) => (v != null && v !== '' && !Number.isNaN(Number(v)))
            ? `¥${Math.round(Number(v)).toLocaleString()}`
            : '-';
        const fmtUnit = (v) => (v != null && v !== '' && !Number.isNaN(Number(v)))
            ? `¥${Math.round(Number(v)).toLocaleString()}/㎡`
            : '-';
        const fmtMin = (v) => (v != null && v !== '' && !Number.isNaN(Number(v))) ? `${Math.round(Number(v))}分` : '-';

        const compareRows = [
            { label: '売出価格', before: fmtYen(before.asking_price), after: fmtYen(after.asking_price), changed: before.asking_price !== after.asking_price },
            { label: '地価単価', before: fmtUnit(before.price_per_sqm), after: fmtUnit(after.price_per_sqm), changed: before.price_per_sqm !== after.price_per_sqm },
            { label: '年額賃料', before: fmtYen(before.current_rent_annual), after: fmtYen(after.current_rent_annual), changed: before.current_rent_annual !== after.current_rent_annual },
            { label: '最寄駅', before: before.nearest_station || '-', after: after.nearest_station || '-', changed: (before.nearest_station || '') !== (after.nearest_station || '') },
            { label: '駅距離', before: fmtMin(before.station_distance_min), after: fmtMin(after.station_distance_min), changed: before.station_distance_min !== after.station_distance_min },
        ];

        marketHtml = `
            <div class="hold-sell-section" style="margin-top:8px;">
                <div class="hs-title">ヒートマップ連動（分析入力補完）</div>
                <div style="font-size:0.72rem;color:#b0bec5;">
                    参照元: <b>${src}</b> / 最寄駅: <b>${st}</b>
                </div>
                <div class="hold-sell-grid" style="margin-top:6px;">
                    <div class="hs-item"><span class="hs-label">地価</span><span class="hs-val">${lp}</span></div>
                    <div class="hs-item"><span class="hs-label">賃料</span><span class="hs-val">${rent}</span></div>
                    <div class="hs-item"><span class="hs-label">取引単価</span><span class="hs-val">${tx}</span></div>
                    <div class="hs-item"><span class="hs-label">想定利回り</span><span class="hs-val">${yld}</span></div>
                    <div class="hs-item"><span class="hs-label">自動補完</span><span class="hs-val">${autoKeys.length}項目</span></div>
                </div>
                ${marketBenchmark && marketBenchmark.market_net_yield != null && marketBenchmark.property_net_yield != null ? `
                <div style="margin-top:6px;font-size:0.7rem;color:#b0bec5;">
                    市場正味利回り ${((marketBenchmark.market_net_yield||0)*100).toFixed(1)}%
                    / 物件正味利回り ${((marketBenchmark.property_net_yield||0)*100).toFixed(1)}%
                    / 乖離 ${((marketBenchmark.net_yield_gap||0)*100).toFixed(1)}%
                </div>` : ''}
                <div style="margin-top:8px;font-size:0.68rem;">
                    <div style="color:#90a4ae;margin-bottom:3px;">補完前 / 補完後</div>
                    ${compareRows.map(r => `
                        <div style="display:grid;grid-template-columns:72px 1fr 1fr;gap:6px;border-bottom:1px solid #1a2744;padding:2px 0;">
                            <span style="color:#78909c;">${r.label}</span>
                            <span style="color:#b0bec5;">${r.before}</span>
                            <span style="color:${r.changed ? '#4fc3f7' : '#90a4ae'};font-weight:${r.changed ? 'bold' : 'normal'};">${r.after}</span>
                        </div>
                    `).join('')}
                </div>
                ${autoKeys.length ? `<div style="font-size:0.68rem;color:#78909c;margin-top:4px;">${autoKeys.join(', ')}</div>` : ''}
            </div>
        `;
    }

    // --- Collapsible details ---
    let detailsHtml = '<div class="result-details-section">';

    // 1. 収支詳細
    let financeBody = '';
    if (valuation) {
        const v = valuation;
        const finItems = [
            ['推定土地価格', v.estimated_land_value ? `${(v.estimated_land_value/10000).toLocaleString()}万` : '-'],
            ['推定建物価格', v.estimated_building_value ? `${(v.estimated_building_value/10000).toLocaleString()}万` : '-'],
            ['経費率', v.expense_rate ? `${(v.expense_rate*100).toFixed(1)}%` : '-'],
            ['価格妥当性', v.price_assessment || '-'],
            ['相場乖離', v.price_deviation_pct != null ? `${v.price_deviation_pct.toFixed(1)}%` : '-'],
            ['相場賃料(月)', v.estimated_market_rent_monthly ? `${v.estimated_market_rent_monthly.toLocaleString()}円` : '-'],
            ['現行vs相場', v.current_rent_vs_market ? `${(v.current_rent_vs_market*100).toFixed(0)}%` : '-'],
        ];
        finItems.forEach(([l, val]) => {
            financeBody += `<div style="display:flex;justify-content:space-between;padding:2px 4px;border-bottom:1px solid #1a2744;"><span style="color:#78909c;">${l}</span><span>${val}</span></div>`;
        });
    }
    if (simulation) {
        const s = simulation;
        const capStress = s.hold_sell_exit_cap_stress != null ? `${(s.hold_sell_exit_cap_stress * 100).toFixed(1)}%` : '保守';
        const simItems = [
            ['売出価格', s.purchase_price ? `${(s.purchase_price/10000).toLocaleString()}万` : '-'],
            ['総投資額(諸費用込)', s.initial_investment ? `${(s.initial_investment/10000).toLocaleString()}万` : '-'],
            ['ローン額', s.loan_amount ? `${(s.loan_amount/10000).toLocaleString()}万` : '-'],
            ['初年度CF', s.year1_cash_flow != null ? `${(s.year1_cash_flow/10000).toLocaleString()}万` : '-'],
            ['初年度CCR', s.year1_cash_on_cash != null ? `${(s.year1_cash_on_cash*100).toFixed(1)}%` : '-'],
            ['DSCR', s.dscr ? s.dscr.toFixed(2) : '-'],
            ['IRR', s.irr != null ? `${(s.irr*100).toFixed(1)}%` : '-'],
            ['NPV', s.npv != null ? `${(s.npv/10000).toLocaleString()}万` : '-'],
            ['投資回収', s.payback_years ? `${s.payback_years}年` : '回収不能'],
            ['損益分岐稼働率', s.break_even_occupancy ? `${(s.break_even_occupancy*100).toFixed(0)}%` : '-'],
            ['10年後売却益', s.exit_profit != null ? `${(s.exit_profit/10000).toLocaleString()}万` : '-'],
            [`8年後売却(${capStress})`, s.hold_sell_exit_price_70 ? `${(s.hold_sell_exit_price_70/10000).toLocaleString()}万` : '-'],
        ];
        simItems.forEach(([l, val]) => {
            financeBody += `<div style="display:flex;justify-content:space-between;padding:2px 4px;border-bottom:1px solid #1a2744;"><span style="color:#78909c;">${l}</span><span>${val}</span></div>`;
        });
    }
    detailsHtml += `<details><summary>収支詳細</summary><div class="detail-body">${financeBody}</div></details>`;

    // 2. SWOT・リスク
    let swotBody = '';
    const swotSections = [
        {key: 'strengths', label: '強み', color: '#66bb6a', icon: '◎'},
        {key: 'weaknesses', label: '弱み', color: '#ffa726', icon: '△'},
        {key: 'risks', label: 'リスク', color: '#ef5350', icon: '✗'},
        {key: 'opportunities', label: '機会', color: '#4fc3f7', icon: '○'},
    ];
    swotSections.forEach(({key, label, color, icon}) => {
        const items = judgment[key] || [];
        if (items.length > 0) {
            swotBody += `<div style="margin-bottom:4px;"><span style="color:${color};font-weight:600;font-size:0.76rem;">${icon} ${label}</span>`;
            items.forEach(item => {
                swotBody += `<div style="font-size:0.7rem;color:#b0bec5;margin-left:14px;">• ${item}</div>`;
            });
            swotBody += '</div>';
        }
    });
    detailsHtml += `<details><summary>SWOT・リスク</summary><div class="detail-body">${swotBody}</div></details>`;

    // 3. 資産性スコア (placeholder - filled by showAssetScoreInResult)
    detailsHtml += `<details id="result-asset-score-details"><summary>資産性スコア</summary><div class="detail-body" id="result-asset-score-body">データなし</div></details>`;

    // 4. 批判レビュー
    let criticBody = '<div style="color:#78909c;">レビューなし</div>';
    if (critic && critic.reliability_grade) {
        const cColor = critic.usable_for_investment ? '#66bb6a' : '#ef5350';
        criticBody = `
            <div style="margin-bottom:6px;">
                <span style="font-weight:600;color:${cColor};">${critic.reliability_grade}</span>
                ${critic.usable_for_investment ? ' ✓ 投資判断利用可' : ' ✗ 投資判断利用不可'}
                <span style="color:#78909c;margin-left:6px;">品質: ${critic.data_quality_score?.toFixed(0)}/100</span>
            </div>
            ${(critic.issues || []).filter(i => i.severity === 'critical' || i.severity === 'major').map(i =>
                `<div style="font-size:0.7rem;color:#ffa726;margin-bottom:2px;">[${i.severity}] ${i.message}</div>`
            ).join('')}
            ${(critic.recommendations || []).map(r =>
                `<div style="font-size:0.7rem;color:#78909c;margin-bottom:1px;">→ ${r}</div>`
            ).join('')}
        `;
    }
    detailsHtml += `<details><summary>批判レビュー</summary><div class="detail-body">${criticBody}</div></details>`;

    detailsHtml += '</div>';

    // Assemble: holdSell goes into result-metrics, collapsible details into result-swot
    document.getElementById('result-metrics').innerHTML = holdSellHtml + marketHtml;
    document.getElementById('result-swot').innerHTML = detailsHtml;

    // Scroll to results
    panel.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function showAssetScoreInResult(data) {
    /**
     * 投資判定結果パネル内に資産性スコアを統合表示する
     * Now targets the collapsible <details> section inside result-swot
     */
    if (!data) return;
    const container = document.getElementById('result-asset-score-body');
    if (!container) return;

    const gc = gradeColor(data.grade);
    const bars = [
        { label: '接道状況', score: data.road_info?.road_score },
        { label: 'ハザード', score: data.hazard_info?.hazard_score },
        { label: '地形・標高', score: data.elevation_info?.terrain_score },
        { label: '敷地形状', score: data.lot_shape?.shape_score },
        { label: '人口動態', score: data.population?.population_score },
        { label: '駅距離', score: data.station_distance_score },
    ];

    let html = `
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
            <span style="background:${gc};color:#fff;padding:2px 8px;border-radius:4px;font-weight:bold;">${data.grade}</span>
            <span style="font-weight:600;">資産性 ${(data.overall_score || 0).toFixed(1)}/100</span>
            <span style="font-size:0.68rem;color:#78909c;">${data.summary || ''}</span>
        </div>`;

    bars.forEach(b => {
        const pct = Math.max(0, Math.min(100, b.score || 0));
        const color = pct >= 70 ? '#4caf50' : pct >= 50 ? '#ffd54f' : pct >= 30 ? '#ffa726' : '#ef5350';
        html += `
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;font-size:0.7rem;">
                <span style="width:70px;color:#90a4ae;text-align:right;">${b.label}</span>
                <div style="flex:1;background:#1a2744;border-radius:3px;height:10px;overflow:hidden;">
                    <div style="width:${pct}%;height:100%;background:${color};border-radius:3px;"></div>
                </div>
                <span style="width:24px;color:${color};text-align:right;font-weight:bold;">${pct.toFixed(0)}</span>
            </div>`;
    });

    container.innerHTML = html;
}

function showRanking(ranking) {
    const panel = document.getElementById('ranking-panel');
    const wrap = document.getElementById('ranking-list-wrap');
    const toggleBtn = document.getElementById('ranking-toggle-btn');
    if (toggleBtn) toggleBtn.textContent = rankingPanelCollapsed ? '展開' : '折りたたむ';
    if (wrap) wrap.style.display = rankingPanelCollapsed ? 'none' : 'block';
    panel.style.display = 'block';

    let html = '';
    ranking.forEach((r, i) => {
        const scenarioLabel = r.scenario === 'rebuild' ? '建替案' : (r.scenario === 'as_is' ? '現況案' : '');
        const netY = r.net_yield != null ? ` / 正味${(r.net_yield * 100).toFixed(1)}%` : '';
        const holdRoi = r.hold_sell_roi != null ? ` / 8年ROI${(r.hold_sell_roi * 100).toFixed(0)}%` : '';
        const exitCap = r.exit_cap_rate != null ? ` / 出口Cap${(r.exit_cap_rate * 100).toFixed(1)}%` : '';
        html += `
            <div class="ranking-item">
                <span class="ranking-rank">#${i + 1}</span>
                <span class="ranking-grade grade-badge grade-${r.grade}" style="width:28px;height:28px;font-size:0.85rem;border-radius:6px;">${r.grade}</span>
                <div class="ranking-info">
                    <div class="ranking-name">${r.name}</div>
                    <div class="ranking-detail">${r.recommendation} | Score: ${r.score.toFixed(1)}${netY}${holdRoi}${exitCap}${scenarioLabel ? ` | ${scenarioLabel}` : ''}</div>
                </div>
            </div>`;
    });
    document.getElementById('ranking-list').innerHTML = html;
}

function hideRankingPanel() {
    const panel = document.getElementById('ranking-panel');
    if (!panel) return;
    panel.style.display = 'none';
}

function toggleRankingPanel() {
    rankingPanelCollapsed = !rankingPanelCollapsed;
    const wrap = document.getElementById('ranking-list-wrap');
    const toggleBtn = document.getElementById('ranking-toggle-btn');
    if (wrap) wrap.style.display = rankingPanelCollapsed ? 'none' : 'block';
    if (toggleBtn) toggleBtn.textContent = rankingPanelCollapsed ? '展開' : '折りたたむ';
}

// ===== CSV取込 =====

async function uploadCSV() {
    const fileInput = document.getElementById('csv-upload');
    if (!fileInput.files.length) {
        document.getElementById('csv-result').textContent = 'ファイルを選択してください';
        return;
    }

    const btn = document.getElementById('btn-upload-csv');
    btn.disabled = true;

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);

    try {
        const resp = await fetch('/api/rental-comps/upload', {
            method: 'POST',
            body: formData,
        });
        const data = await resp.json();
        document.getElementById('csv-result').innerHTML =
            `<span style="color:#66bb6a">${data.imported}件取込 (合計: ${data.total}件)</span>`;
        loadRentalStats();
    } catch (e) {
        document.getElementById('csv-result').innerHTML =
            `<span style="color:#ef5350">エラー: ${e.message}</span>`;
    } finally {
        btn.disabled = false;
    }
}

// ===== URL物件取込 =====

async function scrapeUrl() {
    const url = document.getElementById('scrape-url').value.trim();
    if (!url) {
        document.getElementById('scrape-url-result').innerHTML =
            '<span style="color:#ffa726">URLを入力してください</span>';
        return;
    }

    const btn = document.getElementById('btn-scrape-url');
    btn.disabled = true;
    btn.textContent = '取込中...';
    document.getElementById('scrape-url-result').innerHTML = '<div class="loading">クロール＋OCR処理中...</div>';

    const useOcr = document.getElementById('scrape-ocr').checked;
    const useBrowser = document.getElementById('scrape-browser').checked;
    const autoAnalyze = document.getElementById('scrape-auto-analyze').checked;

    try {
        const resp = await fetch('/api/scrape-url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: url,
                use_ocr: useOcr,
                use_browser: useBrowser,
                auto_analyze: autoAnalyze,
            }),
        });
        const data = await resp.json();

        if (data.error) {
            document.getElementById('scrape-url-result').innerHTML =
                `<span style="color:#ef5350">エラー: ${data.error}</span>`;
            return;
        }

        const p = data.property;
        let html = `
            <div style="background:#1a2332;padding:10px;border-radius:6px;margin-top:6px;">
                <div style="color:#4fc3f7;font-weight:bold;">${p.name || '名称不明'}</div>
                <div style="font-size:0.78rem;color:#b0bec5;margin-top:4px;">
                    ${p.address || '住所不明'}<br>
                    ${p.asking_price ? (p.asking_price / 10000).toLocaleString() + '万円' : '価格不明'}
                    ${p.gross_yield ? ' | 利回り ' + (p.gross_yield * 100).toFixed(1) + '%' : ''}
                    ${p.structure ? ' | ' + p.structure : ''}
                    ${p.building_age != null ? ' | 築' + p.building_age + '年' : ''}
                    ${p.land_area ? '<br>土地: ' + p.land_area + '㎡' : ''}
                    ${p.building_area ? ' / 建物: ' + p.building_area + '㎡' : ''}
                    ${p.nearest_station ? '<br>最寄: ' + p.nearest_station : ''}
                    ${p.station_distance_min ? ' 徒歩' + p.station_distance_min + '分' : ''}
                </div>
                <div style="font-size:0.7rem;color:#66bb6a;margin-top:4px;">DB保存済</div>
            </div>`;

        if (data.judgment) {
            const j = data.judgment;
            html += `
                <div style="margin-top:8px;padding:8px;background:#1a2332;border-radius:6px;">
                    <span class="grade-badge grade-${j.grade}" style="display:inline-block;width:30px;height:30px;line-height:30px;text-align:center;border-radius:6px;font-weight:bold;">${j.grade}</span>
                    <span style="margin-left:8px;color:#b0bec5;">${j.recommendation} | Score: ${j.overall_score.toFixed(1)}</span>
                </div>`;
        }

        // フォームにも反映
        if (p.name) document.getElementById('prop-name').value = p.name;
        if (p.address) document.getElementById('prop-address').value = p.address;
        if (p.asking_price) document.getElementById('prop-price').value = Math.round(p.asking_price / 10000);
        if (p.structure) document.getElementById('prop-structure').value = p.structure;
        if (p.land_area) document.getElementById('prop-land-area').value = p.land_area;
        if (p.building_area) document.getElementById('prop-building-area').value = p.building_area;
        if (p.building_age != null) document.getElementById('prop-age').value = p.building_age;
        if (p.station_distance_min) document.getElementById('prop-station').value = p.station_distance_min;
        if (p.current_rent_annual) document.getElementById('prop-rent').value = Math.round(p.current_rent_annual / 10000);
        if (p.units) document.getElementById('prop-units').value = p.units;

        document.getElementById('scrape-url-result').innerHTML = html;

    } catch (e) {
        document.getElementById('scrape-url-result').innerHTML =
            `<span style="color:#ef5350">通信エラー: ${e.message}</span>`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'URL取込';
    }
}

// ===== スクレイピング =====

async function scrapeProperties() {
    const btn = document.getElementById('btn-scrape');
    btn.disabled = true;
    btn.textContent = 'スクレイピング中...';
    document.getElementById('scrape-result').innerHTML = '<div class="loading">複数ソースから取得中...</div>';

    const pref = document.getElementById('scrape-pref').value;
    const pages = document.getElementById('scrape-pages').value;

    // マルチソース選択
    const sources = [];
    if (document.getElementById('scrape-rakumachi')?.checked) sources.push('rakumachi');
    if (document.getElementById('scrape-kenbiya')?.checked) sources.push('kenbiya');
    if (document.getElementById('scrape-rals')?.checked) sources.push('rals');
    if (sources.length === 0) sources.push('rakumachi');
    const splitPrice = document.getElementById('scrape-split-price')?.checked ? '&split_by_price=true' : '';

    try {
        const resp = await fetch(
            `/api/scrape?prefecture_code=${pref}&max_pages=${pages}` +
            `&sources=${sources.join(',')}${splitPrice}`
        );
        const data = await resp.json();

        if (data.count > 0) {
            document.getElementById('scrape-result').innerHTML =
                `<span style="color:#66bb6a">${data.count}件取得 (${(data.sources||[]).join(', ')})</span>`;
            sampleProperties = sampleProperties.concat(data.properties);
            loadSampleProperties();
            plotSampleProperties();
        } else {
            document.getElementById('scrape-result').innerHTML =
                '<span style="color:#ffa726">物件が見つかりませんでした</span>';
        }
    } catch (e) {
        document.getElementById('scrape-result').innerHTML =
            `<span style="color:#ef5350">エラー: ${e.message}</span>`;
    } finally {
        btn.disabled = false;
        btn.textContent = '収益物件スクレイピング';
    }
}

async function quickEvaluateLand() {
    const btn = document.getElementById('btn-quick-land-eval');
    const resultEl = document.getElementById('quick-land-result');
    const address = (document.getElementById('quick-land-address')?.value || '').trim();
    const landArea = parseFloat(document.getElementById('quick-land-area')?.value || '0');
    const landPriceRaw = document.getElementById('quick-land-price')?.value || '';
    const bcrRaw = document.getElementById('quick-land-bcr')?.value || '';
    const farRaw = document.getElementById('quick-land-far')?.value || '';

    if (!address || !landArea) {
        resultEl.innerHTML = '<span style="color:#ef5350">住所と土地面積を入力してください</span>';
        return;
    }

    btn.disabled = true;
    btn.textContent = '算出中...';
    resultEl.innerHTML = '<div class="loading">市場データ・建築プランから診断中...</div>';

    try {
        const payload = {
            address: address,
            land_area_sqm: landArea,
        };
        if (landPriceRaw) payload.land_price = Number(landPriceRaw);
        if (bcrRaw) payload.building_coverage_ratio = Number(bcrRaw);
        if (farRaw) payload.floor_area_ratio = Number(farRaw);

        const resp = await fetch('/api/land/quick-evaluate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.error || '算出に失敗しました');
        }

        const p = data.profitability || {};
        const fair = data.fair_price_line || {};
        const m = data.market_context || {};
        const yld = p.gross_yield ? (p.gross_yield * 100).toFixed(2) + '%' : 'N/A';
        const fmt = v => (v == null ? 'N/A' : Number(v).toLocaleString());
        const implied = m.implied_yield ? (m.implied_yield * 100).toFixed(2) + '%' : 'N/A';

        resultEl.innerHTML = `
            <div style="border:1px solid #1e3a5f;border-radius:6px;padding:8px;background:#0e1829;">
                <div style="color:#90caf9;font-weight:600;margin-bottom:4px;">収益性</div>
                <div>推奨プラン: ${p.plan_label || 'プラン生成なし'}</div>
                <div>想定表面利回り: <span style="color:#66bb6a">${yld}</span></div>
                <div>想定年収: ${fmt(p.annual_income)} 円</div>
                <div style="margin-top:6px;color:#90caf9;font-weight:600;">価格妥当ライン</div>
                <div>下限: ${fmt(fair.low)} 円 / 中央: ${fmt(fair.mid)} 円 / 上限: ${fmt(fair.high)} 円</div>
                <div>判定: <span style="color:#ffd54f">${fair.judgment || 'N/A'}</span></div>
                <div style="margin-top:6px;color:#78909c;font-size:0.72rem;">
                    市場基準: 地価 ${fmt(m.land_price_sqm)} 円/㎡, 取引 ${fmt(m.tx_price_sqm)} 円/㎡, 想定利回り ${implied}
                </div>
            </div>
        `;

        const coords = data.coordinates || {};
        if (coords.lat && coords.lng && map) {
            map.setView([coords.lat, coords.lng], Math.max(map.getZoom(), 15));
            L.popup()
                .setLatLng([coords.lat, coords.lng])
                .setContent(`<b>クイック診断地点</b><br>${address}`)
                .openOn(map);
        }
    } catch (e) {
        resultEl.innerHTML = `<span style="color:#ef5350">エラー: ${e.message}</span>`;
    } finally {
        btn.disabled = false;
        btn.textContent = '収益性・価格ライン算出';
    }
}

// ===== 賃料スクレイピング =====

async function scrapeRentals() {
    const btn = document.getElementById('btn-rental-scrape');
    btn.disabled = true;
    btn.textContent = '賃料取得中...';
    const resultEl = document.getElementById('rental-scrape-result');
    resultEl.innerHTML = '<div class="loading">SUUMO賃貸から取得中...</div>';

    const pref = document.getElementById('rental-scrape-pref').value;
    const pages = document.getElementById('rental-scrape-pages').value;

    try {
        const resp = await fetch(`/api/scrape-rentals?prefecture_code=${pref}&max_pages=${pages}`);
        const data = await resp.json();
        resultEl.innerHTML = `<span style="color:#66bb6a">` +
            `${data.count}件取得, ${data.saved}件DB保存</span>`;
        loadRentalStats();
    } catch (e) {
        resultEl.innerHTML = `<span style="color:#ef5350">エラー: ${e.message}</span>`;
    } finally {
        btn.disabled = false;
        btn.textContent = '賃料スクレイピング';
    }
}

// ===== 歪み分析（旧/analysis画面の機能を統合） =====

async function runDistortionAnalysis() {
    const btn = document.getElementById('btn-run-distortion');
    btn.disabled = true;
    btn.textContent = '分析中...';
    const pref = document.getElementById('prefecture-select').value;

    try {
        const resp = await fetch(`/api/analysis/distortion?prefecture_code=${pref}`);
        const data = await resp.json();
        const panel = document.getElementById('distortion-panel');
        panel.style.display = 'block';

        const ranking = data.ranking || [];
        const el = document.getElementById('distortion-ranking');
        if (ranking.length === 0) {
            el.innerHTML = '<p style="color:#78909c">データなし。先にバッチデータ収集を実行してください。</p>';
        } else {
            el.innerHTML = ranking.slice(0, 30).map((r, i) => {
                const score = (r.distortion_score || 0).toFixed(2);
                const yld = ((r.implied_yield || 0) * 100).toFixed(1);
                return `<div style="display:flex;gap:6px;padding:3px 0;border-bottom:1px solid #1e3a5f;cursor:pointer;" ` +
                    `onclick="focusStation('${r.station_id||''}','${r.station_name||''}')">` +
                    `<span style="color:#78909c;width:20px;">${i+1}</span>` +
                    `<span style="flex:1;">${r.station_name||'?'}</span>` +
                    `<span style="color:#4fc3f7;width:50px;">${score}</span>` +
                    `<span style="color:#66bb6a;width:45px;">${yld}%</span>` +
                    `</div>`;
            }).join('');
        }
    } catch (e) {
        console.error('歪み分析エラー:', e);
    } finally {
        btn.disabled = false;
        btn.textContent = '歪み分析';
    }
}

function focusStation(stationId, stationName) {
    if (!stationId) return;
    // 駅詳細を表示
    const panel = document.getElementById('station-detail-panel');
    panel.style.display = 'block';
    document.getElementById('station-detail-title').textContent = stationName + ' 駅データ';
    fetch(`/api/analysis/station-detail/${stationId}`)
        .then(r => r.json())
        .then(data => {
            const m = data.metrics || {};
            document.getElementById('station-detail-content').innerHTML =
                `<table style="width:100%;font-size:0.75rem;">` +
                `<tr><td>平均地価</td><td>${(m.avg_land_price_sqm||0).toLocaleString()} 円/㎡</td></tr>` +
                `<tr><td>平均賃料</td><td>${(m.avg_rent_per_sqm||0).toLocaleString()} 円/㎡</td></tr>` +
                `<tr><td>想定利回り</td><td>${((m.implied_yield||0)*100).toFixed(1)}%</td></tr>` +
                `<tr><td>歪みスコア</td><td>${(m.distortion_score||0).toFixed(2)}</td></tr>` +
                `<tr><td>地価サンプル</td><td>${m.sample_count_land||0}件</td></tr>` +
                `<tr><td>賃料サンプル</td><td>${m.sample_count_rent||0}件</td></tr>` +
                `</table>`;
        });
}

// ===== レポート =====

async function loadReports() {
    try {
        const resp = await fetch('/api/reports');
        const data = await resp.json();
        const el = document.getElementById('reports-list');

        if (!data.reports.length) {
            el.innerHTML = '<span class="hint">まだ判定レポートがありません</span>';
            return;
        }

        let html = '';
        data.reports.forEach(r => {
            const gradeColor = {S:'#1a9641',A:'#4dac26',B:'#b8e186',C:'#fdb863',D:'#e66101',F:'#d7191c'}[r.grade] || '#999';
            html += `
                <div class="report-item" onclick="loadReport('${r.filename}')">
                    <div class="report-name">
                        <span style="color:${gradeColor};font-weight:bold">[${r.grade || '?'}]</span>
                        ${r.property_name || r.filename}
                    </div>
                    <div class="report-meta">
                        ${r.recommendation || ''} | Score: ${(r.score || 0).toFixed(1)}
                        ${r.generated_at ? ' | ' + new Date(r.generated_at).toLocaleString('ja-JP') : ''}
                    </div>
                </div>`;
        });
        el.innerHTML = html;
    } catch (e) {
        console.error('レポート取得エラー:', e);
    }
}

async function loadReport(filename) {
    try {
        const resp = await fetch(`/api/reports/${filename}`);
        const data = await resp.json();

        if (data.judgment) {
            showJudgmentResult(data.judgment, data.valuation, data.simulation, data.critic_review);
            switchTab('tab-property');
        }
    } catch (e) {
        console.error('レポート読込エラー:', e);
    }
}

// ===== レイヤー切替 =====

function toggleLayer(e) {
    const id = e.target.id;
    const on = e.target.checked;

    if (id === 'layer-land-price' && landPriceLayer) {
        on ? landPriceLayer.addTo(map) : map.removeLayer(landPriceLayer);
    }
    if (id === 'layer-transactions' && transactionLayer) {
        on ? transactionLayer.addTo(map) : map.removeLayer(transactionLayer);
    }
    if (id === 'layer-properties' && propertyLayer) {
        on ? propertyLayer.addTo(map) : map.removeLayer(propertyLayer);
    }
    if (id === 'layer-land-listings' && landListingLayer) {
        on ? landListingLayer.addTo(map) : map.removeLayer(landListingLayer);
    }
}

// ===== ヒートマップ =====

// ===== 駅マーカーレイヤー =====

async function loadStationMarkers() {
    if (stationLayer) map.removeLayer(stationLayer);

    const pref = document.getElementById('prefecture-select').value;
    try {
        const resp = await fetch(`/api/stations/${pref}`);
        const data = await resp.json();
        stationsData = data.stations || [];

        const markers = [];
        stationsData.forEach(s => {
            if (!s.lat || !s.lon) return;

            // 色: 利回りベース
            const y = s.implied_yield || 0;
            const color = y >= 0.06 ? '#1a9641' : y >= 0.04 ? '#4dac26' : y >= 0.02 ? '#fdb863' : '#78909c';

            const marker = L.circleMarker([s.lat, s.lon], {
                radius: 8,
                fillColor: color,
                color: '#fff',
                weight: 1.5,
                fillOpacity: 0.85,
            });

            // ホバーでツールチップ（データマトリクス）
            const lp = s.avg_land_price_sqm ? `¥${Math.round(s.avg_land_price_sqm).toLocaleString()}/㎡` : '-';
            const rent = s.avg_rent_per_sqm ? `¥${Math.round(s.avg_rent_per_sqm).toLocaleString()}/㎡` : '-';
            const yieldLabel = y > 0 ? `${(y * 100).toFixed(1)}%` : '-';
            const pax = s.passengers_daily ? `${(s.passengers_daily / 1000).toFixed(0)}千人/日` : '-';
            const vac = s.vacancy_rate ? `${(s.vacancy_rate * 100).toFixed(1)}%` : '-';

            marker.bindTooltip(`
                <div style="font-size:12px;min-width:180px;">
                    <strong>${s.name}</strong> <span style="color:#888;font-size:10px;">${s.line || ''}</span>
                    <table style="width:100%;margin-top:4px;font-size:11px;border-collapse:collapse;">
                        <tr><td style="color:#888;">地価</td><td style="text-align:right;font-weight:bold;">${lp}</td></tr>
                        <tr><td style="color:#888;">賃料</td><td style="text-align:right;font-weight:bold;">${rent}</td></tr>
                        <tr><td style="color:#888;">利回り</td><td style="text-align:right;font-weight:bold;color:${y>=0.05?'#4caf50':'#ff9800'};">${yieldLabel}</td></tr>
                        <tr><td style="color:#888;">乗降客</td><td style="text-align:right;">${pax}</td></tr>
                        <tr><td style="color:#888;">空室率</td><td style="text-align:right;color:${(s.vacancy_rate||0)>0.1?'#ff5722':'#4caf50'};">${vac}</td></tr>
                        <tr><td style="color:#888;">地価データ</td><td style="text-align:right;">${s.sample_count_land || 0}件</td></tr>
                        <tr><td style="color:#888;">賃料データ</td><td style="text-align:right;">${s.sample_count_rent || 0}件</td></tr>
                    </table>
                </div>
            `, { sticky: true, direction: 'top', offset: [0, -10] });

            // クリックでサイドバー詳細
            marker.on('click', () => showStationDetail(s));

            markers.push(marker);
        });

        stationLayer = L.layerGroup(markers);
        if (document.getElementById('layer-stations').checked) {
            stationLayer.addTo(map);
        }
    } catch (e) {
        console.error('駅マーカー読込エラー:', e);
    }
}

function showStationDetail(s) {
    const panel = document.getElementById('station-detail-panel');
    panel.style.display = 'block';
    document.getElementById('station-detail-title').textContent = `${s.name}駅 (${s.line || ''})`;

    const lp = s.avg_land_price_sqm ? `¥${Math.round(s.avg_land_price_sqm).toLocaleString()}/㎡` : 'データなし';
    const rent = s.avg_rent_per_sqm ? `¥${Math.round(s.avg_rent_per_sqm).toLocaleString()}/㎡` : 'データなし';
    const y = s.implied_yield || 0;

    let html = `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:0.75rem;">
            <div style="background:#0f3460;padding:6px;border-radius:4px;text-align:center;">
                <div style="color:#78909c;font-size:0.65rem;">地価</div>
                <div style="color:#4fc3f7;font-weight:bold;">${lp}</div>
            </div>
            <div style="background:#0f3460;padding:6px;border-radius:4px;text-align:center;">
                <div style="color:#78909c;font-size:0.65rem;">賃料</div>
                <div style="color:#66bb6a;font-weight:bold;">${rent}</div>
            </div>
            <div style="background:#0f3460;padding:6px;border-radius:4px;text-align:center;">
                <div style="color:#78909c;font-size:0.65rem;">想定利回り</div>
                <div style="color:${y>=0.05?'#66bb6a':'#ffa726'};font-weight:bold;">${y > 0 ? (y*100).toFixed(1)+'%' : '-'}</div>
            </div>
            <div style="background:#0f3460;padding:6px;border-radius:4px;text-align:center;">
                <div style="color:#78909c;font-size:0.65rem;">乗降客数</div>
                <div style="color:#e0e0e0;font-weight:bold;">${s.passengers_daily ? (s.passengers_daily/1000).toFixed(0)+'千人/日' : '-'}</div>
            </div>
            <div style="background:#0f3460;padding:6px;border-radius:4px;text-align:center;">
                <div style="color:#78909c;font-size:0.65rem;">空室率</div>
                <div style="color:${(s.vacancy_rate||0)>0.1?'#ef5350':'#66bb6a'};font-weight:bold;">${s.vacancy_rate ? (s.vacancy_rate*100).toFixed(1)+'%' : '-'}</div>
            </div>
            <div style="background:#0f3460;padding:6px;border-radius:4px;text-align:center;">
                <div style="color:#78909c;font-size:0.65rem;">データ数</div>
                <div style="color:#e0e0e0;">${(s.sample_count_land||0)+(s.sample_count_rent||0)}件</div>
            </div>
        </div>
    `;

    // この駅の土地物件リンク
    html += `<div style="margin-top:8px;">
        <button onclick="document.getElementById('comp-station').value='${s.name}';loadCompetition();switchTab('tab-property');" class="btn-secondary" style="width:100%;font-size:0.72rem;">
            ${s.name}駅の競合分析を開く
        </button>
    </div>`;

    document.getElementById('station-detail-content').innerHTML = html;
    panel.scrollIntoView({ behavior: 'smooth' });
}

// ===== ヒートマップ =====

async function toggleHeatmap(e) {
    if (e.target.checked) {
        await loadHeatmap();
    } else if (heatmapLayer) {
        map.removeLayer(heatmapLayer);
    }
}

async function loadHeatmap() {
    if (heatmapLayer) map.removeLayer(heatmapLayer);
    try {
        // Use land_prices data for heatmap
        const resp = await fetch('/api/db/land-prices?limit=5000');
        const data = await resp.json();

        const points = [];
        (data.data || []).forEach(lp => {
            if (lp.latitude && lp.longitude && lp.price_per_sqm > 0) {
                // Normalize intensity: 100k=0.2, 500k=0.5, 1M+=1.0
                const intensity = Math.min(lp.price_per_sqm / 1000000, 1.0);
                points.push([lp.latitude, lp.longitude, intensity]);
            }
        });

        // Also add land_listings prices
        const resp2 = await fetch('/api/land-listings?limit=5000');
        const data2 = await resp2.json();
        (data2.listings || []).forEach(ll => {
            if (ll.latitude && ll.longitude && ll.land_price > 0 && ll.land_area_sqm > 0) {
                const ppsqm = ll.land_price / ll.land_area_sqm;
                const intensity = Math.min(ppsqm / 1000000, 1.0);
                points.push([ll.latitude, ll.longitude, intensity]);
            }
        });

        if (points.length > 0) {
            heatmapLayer = L.heatLayer(points, {
                radius: 25,
                blur: 15,
                maxZoom: 17,
                gradient: {0.2: '#2196f3', 0.4: '#4caf50', 0.6: '#ffeb3b', 0.8: '#ff9800', 1.0: '#f44336'},
            }).addTo(map);
        }
    } catch (e) {
        console.error('Heatmap error:', e);
    }
}

// ===== 競合分析 =====

async function loadCompetition() {
    const station = document.getElementById('comp-station').value.trim();
    const el = document.getElementById('competition-result');
    el.innerHTML = '<div class="loading">分析中...</div>';

    try {
        const resp = await fetch(`/api/analysis/competition?station=${encodeURIComponent(station)}`);
        const data = await resp.json();

        let html = `<div style="font-size:0.72rem;color:#78909c;margin-bottom:4px;">事例数: ${data.total_comps}件</div>`;

        // 駅統計
        if (data.station_stats && data.station_stats.station_name) {
            const ss = data.station_stats;
            html += `<div style="margin-bottom:8px;padding:6px;background:#0f3460;border-radius:4px;">
                <strong style="color:#81d4fa;font-size:0.78rem;">${ss.station_name}駅</strong>
                <div style="display:flex;gap:12px;margin-top:4px;font-size:0.72rem;color:#b0bec5;">
                    ${ss.passengers_daily ? `<span>乗降客: <strong style="color:#4fc3f7;">${(ss.passengers_daily/1000).toFixed(0)}千人/日</strong></span>` : ''}
                    ${ss.vacancy_rate ? `<span>空室率: <strong style="color:${ss.vacancy_rate > 0.1 ? '#ffa726' : '#66bb6a'};">${(ss.vacancy_rate*100).toFixed(1)}%</strong></span>` : ''}
                    ${ss.avg_land_price_sqm ? `<span>地価: ¥${Math.round(ss.avg_land_price_sqm).toLocaleString()}/㎡</span>` : ''}
                    ${ss.avg_rent_per_sqm ? `<span>賃料: ¥${Math.round(ss.avg_rent_per_sqm).toLocaleString()}/㎡</span>` : ''}
                </div>
            </div>`;
        }

        // 間取りサイズ別
        if (Object.keys(data.size_distribution).length > 0) {
            html += '<div style="margin-bottom:8px;"><strong style="color:#81d4fa;font-size:0.78rem;">間取り別賃料</strong>';
            html += '<table style="width:100%;font-size:0.72rem;border-collapse:collapse;color:#b0bec5;margin-top:4px;">';
            html += '<tr style="border-bottom:1px solid #37474f;"><th style="text-align:left;padding:2px 4px;">サイズ</th><th>件数</th><th>平均賃料</th><th>㎡単価</th></tr>';
            for (const [size, s] of Object.entries(data.size_distribution)) {
                html += `<tr style="border-bottom:1px solid #263238;">
                    <td style="padding:2px 4px;color:#4fc3f7;">${size}</td>
                    <td style="text-align:center;">${s.count}</td>
                    <td style="text-align:right;">¥${s.avg_rent.toLocaleString()}</td>
                    <td style="text-align:right;">¥${s.avg_rpsqm.toLocaleString()}/㎡</td>
                </tr>`;
            }
            html += '</table></div>';
        }

        // 構造別
        if (Object.keys(data.structure_distribution).length > 0) {
            html += '<div style="margin-bottom:8px;"><strong style="color:#81d4fa;font-size:0.78rem;">構造別㎡単価</strong>';
            for (const [struct, s] of Object.entries(data.structure_distribution)) {
                const barWidth = Math.min(s.avg_rpsqm / 50, 100);
                html += `<div style="display:flex;align-items:center;gap:6px;margin-top:2px;">
                    <span style="width:40px;font-size:0.7rem;">${struct}</span>
                    <div style="flex:1;height:12px;background:#0f3460;border-radius:3px;overflow:hidden;">
                        <div style="width:${barWidth}%;height:100%;background:#4fc3f7;border-radius:3px;"></div>
                    </div>
                    <span style="font-size:0.7rem;width:70px;text-align:right;">¥${s.avg_rpsqm.toLocaleString()}/㎡</span>
                </div>`;
            }
            html += '</div>';
        }

        // 地価推移
        if (Object.keys(data.price_trend).length > 0) {
            html += '<div><strong style="color:#81d4fa;font-size:0.78rem;">地価推移</strong>';
            html += '<div style="display:flex;align-items:flex-end;gap:2px;height:60px;margin-top:4px;">';
            const values = Object.values(data.price_trend).map(v => v.avg);
            const maxVal = Math.max(...values);
            for (const [year, v] of Object.entries(data.price_trend)) {
                const h = Math.max((v.avg / maxVal) * 50, 2);
                html += `<div style="flex:1;display:flex;flex-direction:column;align-items:center;">
                    <div style="width:100%;height:${h}px;background:#4fc3f7;border-radius:2px 2px 0 0;" title="${year}: ¥${v.avg.toLocaleString()}/㎡"></div>
                    <span style="font-size:0.55rem;color:#546e7a;margin-top:1px;">${year.slice(2)}</span>
                </div>`;
            }
            html += '</div></div>';
        }

        el.innerHTML = html || '<span class="hint">データなし</span>';
    } catch (e) {
        el.innerHTML = `<span style="color:#ef5350;">エラー: ${e.message}</span>`;
    }
}

// ===== 土地物件機能 =====

async function scrapeLandListings() {
    const btn = document.getElementById('btn-land-scrape');
    btn.disabled = true;
    btn.textContent = 'スクレイピング中...';
    document.getElementById('land-scrape-result').innerHTML = '<div class="loading">取得中...</div>';

    const body = {
        source: document.getElementById('land-source').value,
        prefecture_code: document.getElementById('land-pref').value,
        price_min: parseInt(document.getElementById('land-price-min').value) || null,
        price_max: parseInt(document.getElementById('land-price-max').value) || null,
        area_min: parseFloat(document.getElementById('land-area-min').value) || null,
        walk_max: parseInt(document.getElementById('land-walk-max').value) || null,
        max_pages: parseInt(document.getElementById('land-max-pages').value) || 3,
    };

    try {
        const resp = await fetch('/api/land-listings/scrape', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        console.log('scrape response:', resp.status, data);
        if (resp.ok && (data.status === 'ok' || data.status === 'running')) {
            document.getElementById('land-scrape-result').innerHTML =
                `<span style="color:#66bb6a">${data.message || '開始しました'}</span>
                 <div id="scrape-progress" style="color:#ffa726;font-size:0.72rem;margin-top:4px;">実行中...</div>`;
            pollTaskStatus();
        } else {
            const errMsg = data.error || data.detail || JSON.stringify(data);
            document.getElementById('land-scrape-result').innerHTML =
                `<span style="color:#ef5350">エラー (${resp.status}): ${errMsg}</span>`;
        }
    } catch (e) {
        document.getElementById('land-scrape-result').innerHTML =
            `<span style="color:#ef5350">通信エラー: ${e.message}</span>`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'スクレイピング実行';
    }
}

async function uploadLandCSV() {
    const fileInput = document.getElementById('land-csv-upload');
    if (!fileInput.files.length) {
        document.getElementById('land-csv-result').textContent = 'ファイルを選択してください';
        return;
    }

    const btn = document.getElementById('btn-land-csv');
    btn.disabled = true;
    btn.textContent = '取込中...';

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);

    try {
        const resp = await fetch('/api/land-listings/import-csv', {
            method: 'POST',
            body: formData,
        });
        const data = await resp.json();
        if (data.status === 'ok') {
            document.getElementById('land-csv-result').innerHTML =
                `<span style="color:#66bb6a">${data.listings_imported}件取込, ${data.plans_generated}プラン生成</span>`;
            loadLandListings();
        } else {
            document.getElementById('land-csv-result').innerHTML =
                `<span style="color:#ef5350">エラー: ${data.error}</span>`;
        }
    } catch (e) {
        document.getElementById('land-csv-result').innerHTML =
            `<span style="color:#ef5350">通信エラー: ${e.message}</span>`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'CSV取込';
    }
}

async function batchGeneratePlans() {
    const btn = document.getElementById('btn-generate-plans');
    btn.disabled = true;
    btn.textContent = 'プラン生成中...';

    try {
        const resp = await fetch('/api/land-listings/batch-analyze', { method: 'POST' });
        const data = await resp.json();
        document.getElementById('batch-action-result').innerHTML =
            `<span style="color:#66bb6a">${data.plans_generated}プラン生成</span>`;
        loadLandListings();
    } catch (e) {
        document.getElementById('batch-action-result').innerHTML =
            `<span style="color:#ef5350">エラー: ${e.message}</span>`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'プラン生成';
    }
}

async function loadLandListings() {
    const sortEl = document.getElementById('land-sort');
    const listEl = document.getElementById('land-listings-list');
    const countEl = document.getElementById('land-count');

    if (!sortEl || !listEl) {
        console.error('土地タブ要素が見つかりません');
        return;
    }

    listEl.innerHTML = '<div class="loading">読込中...</div>';
    const sortBy = sortEl.value;
    try {
        const resp = await fetch(`/api/best-plans?sort_by=${sortBy}&limit=200`);
        if (!resp.ok) {
            console.warn('best-plans API:', resp.status);
            // best-plansがない場合、land-listingsにフォールバック
        }
        const data = resp.ok ? await resp.json() : { plans: [], count: 0 };

        countEl.textContent = `(${data.count || 0}件)`;

        // 一覧表示
        if (!data.plans || data.plans.length === 0) {
            // プラン未生成の場合、生データを表示
            const rawResp = await fetch('/api/land-listings?limit=200');
            if (!rawResp.ok) {
                listEl.innerHTML = `<span class="hint">土地API未対応 (${rawResp.status}) - サーバーを再起動してください</span>`;
                return;
            }
            const rawData = await rawResp.json();
            countEl.textContent = `(${rawData.count || 0}件)`;

            if (!rawData.listings || rawData.listings.length === 0) {
                listEl.innerHTML = '<span class="hint">土地物件データなし - 「スクレイピング実行」または「CSV取込」で追加</span>';
                return;
            }

            let html = '';
            rawData.listings.forEach(l => {
                const priceLabel = l.land_price
                    ? (l.land_price >= 100000000
                        ? (l.land_price / 100000000).toFixed(1) + '億円'
                        : (l.land_price / 10000).toLocaleString() + '万円')
                    : '価格不明';
                const assetBadge = l.asset_grade && l.asset_grade !== '?'
                    ? `<span style="background:${gradeColor(l.asset_grade)};color:#fff;padding:1px 5px;border-radius:3px;font-size:0.65rem;font-weight:bold;margin-left:4px;">${l.asset_grade}${l.asset_score ? ' ' + l.asset_score.toFixed(0) : ''}</span>`
                    : '';
                html += `
                    <div class="land-listing-item" onclick="showLandDetail(${l.id})" style="padding:8px;border-bottom:1px solid #263238;cursor:pointer;">
                        <div style="display:flex;justify-content:space-between;align-items:center;">
                            <div style="color:#4fc3f7;font-size:0.82rem;font-weight:bold;">${l.address || '住所不明'}</div>
                            ${assetBadge}
                        </div>
                        <div style="font-size:0.75rem;color:#b0bec5;margin-top:2px;">
                            ${priceLabel}
                            ${l.land_area_sqm ? ' | ' + Number(l.land_area_sqm).toFixed(2) + '㎡' : ''}
                            ${l.station ? ' | ' + l.station : ''}
                            ${l.walk_minutes ? ' 徒歩' + l.walk_minutes + '分' : ''}
                        </div>
                        <div style="font-size:0.7rem;margin-top:2px;">
                            <span style="color:${l.analysis_status === 'ok' ? '#66bb6a' : '#ffa726'};">${l.analysis_status === 'ok' ? 'プラン生成済' : '未分析'}</span>
                            ${l.asset_summary ? `<span style="color:#78909c;margin-left:6px;">${l.asset_summary}</span>` : ''}
                            ${l.source_url ? `<a href="${l.source_url}" target="_blank" style="color:#4fc3f7;font-size:0.65rem;margin-left:6px;" onclick="event.stopPropagation();">▶物件</a>` : ''}
                        </div>
                    </div>`;
            });
            listEl.innerHTML = html;
            plotLandListings(rawData.listings);
            return;
        }

        let html = '';
        const plotData = [];
        data.plans.forEach(p => {
            const priceLabel = p.land_price
                ? (p.land_price >= 100000000
                    ? (p.land_price / 100000000).toFixed(1) + '億円'
                    : (p.land_price / 10000).toLocaleString() + '万円')
                : '価格不明';
            const yieldLabel = p.estimated_yield ? (p.estimated_yield * 100).toFixed(2) + '%' : '';
            const assetBadge = p.asset_grade && p.asset_grade !== '?'
                ? `<span style="background:${gradeColor(p.asset_grade)};color:#fff;padding:1px 5px;border-radius:3px;font-size:0.65rem;font-weight:bold;">${p.asset_grade}${p.asset_score ? ' ' + p.asset_score.toFixed(0) : ''}</span>`
                : '';
            html += `
                <div class="land-listing-item" onclick="showLandDetail(${p.id})" style="padding:8px;border-bottom:1px solid #263238;cursor:pointer;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div style="color:#4fc3f7;font-size:0.82rem;font-weight:bold;">${p.address || '住所不明'}</div>
                        <div style="display:flex;gap:4px;align-items:center;">
                            ${assetBadge}
                            <span style="color:#66bb6a;font-weight:bold;font-size:0.85rem;">${yieldLabel}</span>
                        </div>
                    </div>
                    <div style="font-size:0.75rem;color:#b0bec5;margin-top:2px;">
                        ${priceLabel}
                        ${p.land_area_sqm ? ' | ' + Number(p.land_area_sqm).toFixed(2) + '㎡' : ''}
                        ${p.station ? ' | ' + p.station : ''}
                    </div>
                    <div style="font-size:0.7rem;margin-top:2px;">
                        <span style="color:#78909c;">最適: ${p.structure_type || ''}${p.floors || ''}F / ${p.unit_size_sqm || ''}㎡×${p.max_units || ''}戸</span>
                        ${p.asset_summary ? `<span style="color:#90a4ae;margin-left:6px;font-size:0.65rem;">${p.asset_summary}</span>` : ''}
                        ${p.source_url ? `<a href="${p.source_url}" target="_blank" style="color:#4fc3f7;font-size:0.65rem;margin-left:6px;" onclick="event.stopPropagation();">▶物件</a>` : ''}
                    </div>
                </div>`;
            plotData.push(p);
        });
        listEl.innerHTML = html;
        plotLandListings(plotData);
    } catch (e) {
        console.error('土地一覧取得エラー:', e);
        const el = document.getElementById('land-listings-list');
        if (el) {
            el.innerHTML = `<span style="color:#ef5350;font-size:0.75rem;">読込エラー: ${e.message}</span>`;
        }
    }
}

function plotLandListings(listings) {
    if (landListingLayer) map.removeLayer(landListingLayer);

    const features = listings
        .filter(l => l.latitude && l.longitude)
        .map(l => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [l.longitude, l.latitude] },
            properties: {
                id: l.id,
                address: l.address || '',
                price: l.land_price
                    ? (l.land_price >= 100000000
                        ? (l.land_price / 100000000).toFixed(1) + '億円'
                        : (l.land_price / 10000).toLocaleString() + '万円')
                    : '価格不明',
                area: l.land_area_sqm ? l.land_area_sqm + '㎡' : '',
                station: l.station || '',
                walk: l.walk_minutes ? '徒歩' + l.walk_minutes + '分' : '',
                yield: l.estimated_yield ? (l.estimated_yield * 100).toFixed(2) + '%' : '',
                yieldNum: l.estimated_yield || 0,
                status: l.analysis_status || 'pending',
            },
        }));

    landListingLayer = L.geoJSON({ type: 'FeatureCollection', features }, {
        pointToLayer: (f, ll) => {
            const y = f.properties.yieldNum || 0;
            const bg = y >= 0.08 ? '#1a9641' : y >= 0.06 ? '#4dac26' : y >= 0.04 ? '#fdb863' : y >= 0.02 ? '#e66101' : '#78909c';
            const label = y > 0 ? (y * 100).toFixed(1) : '地';
            return L.marker(ll, {
                icon: L.divIcon({
                    className: '',
                    html: `<div style="background:${bg};color:#fff;min-width:28px;height:20px;border-radius:3px;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:bold;border:1.5px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,0.4);padding:0 3px;">${label}</div>`,
                    iconSize: [28, 20], iconAnchor: [14, 10],
                }),
            });
        },
        onEachFeature: (f, layer) => {
            const p = f.properties;
            layer.bindPopup(`
                <div class="popup-title">${p.address}</div>
                <div class="popup-price">${p.price}</div>
                <div class="popup-detail">
                    ${p.area} | ${p.station} ${p.walk}<br>
                    ${p.yield ? '最高利回り: ' + p.yield : ''}
                </div>
            `);
            layer.on('click', () => showLandDetail(p.id));
        },
    });

    if (document.getElementById('layer-land-listings').checked) {
        landListingLayer.addTo(map);
    }
}

async function showLandDetail(listingId) {
    const panel = document.getElementById('land-detail-panel');
    panel.style.display = 'block';

    try {
        const resp = await fetch(`/api/land-listings/${listingId}`);
        const data = await resp.json();
        const l = data.listing;
        const plans = data.plans || [];
        const j = data.judgment;
        const as = data.asset_score;
        const lv = data.land_value;

        const priceLabel = l.land_price
            ? (l.land_price >= 100000000
                ? (l.land_price / 100000000).toFixed(1) + '億円'
                : (l.land_price / 10000).toLocaleString() + '万円')
            : '価格不明';

        // ===== ヘルパー =====
        const pf = (ok, label) => ok
            ? `<span style="color:#66bb6a;font-weight:bold;">OK</span> <span style="color:#b0bec5;">${label}</span>`
            : `<span style="color:#ef5350;font-weight:bold;">NG</span> <span style="color:#78909c;">${label}</span>`;
        const warn = (ok, label) => ok
            ? `<span style="color:#66bb6a;">OK</span> ${label}`
            : `<span style="color:#ffa726;">注意</span> ${label}`;
        const scoreBar = (score, max=100) => {
            const pct = Math.max(0, Math.min(100, (score/max)*100));
            const c = pct >= 70 ? '#4caf50' : pct >= 40 ? '#ffa726' : '#ef5350';
            return `<div style="display:inline-block;width:60px;height:8px;background:#263238;border-radius:4px;vertical-align:middle;margin:0 4px;overflow:hidden;"><div style="width:${pct}%;height:100%;background:${c};border-radius:4px;"></div></div><span style="color:${c};font-size:0.7rem;">${score?.toFixed?.(0) ?? '?'}</span>`;
        };
        const section = (id, icon, title, summary, statusColor, content) => `
            <details class="dash-section" id="dash-${id}">
                <summary style="display:flex;align-items:center;gap:6px;padding:6px 8px;cursor:pointer;border-bottom:1px solid #1a2744;list-style:none;">
                    <span style="font-size:0.9rem;">${icon}</span>
                    <span style="flex:1;font-weight:600;font-size:0.78rem;color:#e0e0e0;">${title}</span>
                    <span style="font-size:0.72rem;">${summary}</span>
                    <span style="width:8px;height:8px;border-radius:50%;background:${statusColor};"></span>
                </summary>
                <div style="padding:6px 8px 10px;font-size:0.72rem;color:#b0bec5;">${content}</div>
            </details>`;

        // ===== データ抽出 =====
        const ri = as?.road_info || {};
        const hi = as?.hazard_info || {};
        const ei = as?.elevation_info || {};
        const ls = as?.lot_shape_info || {};
        const pi = as?.population || {};
        const best = plans[0];

        // ===== 総合判定ヘッダ =====
        const jGrade = j?.grade || (as?.grade) || '?';
        const jScore = j?.overall_score || as?.overall_score || 0;
        const jRec = j?.recommendation || '';
        const gc = gradeColor(jGrade);

        // 投資判断の1行サマリー生成
        const verdictText = jGrade === '?' ? '未判定 — 判定を実行してください'
            : jGrade === 'S' || jGrade === 'A' ? '投資推奨'
            : jGrade === 'B' ? '条件付き検討'
            : '見送り推奨';
        const verdictColor = jGrade === 'S' || jGrade === 'A' ? '#66bb6a'
            : jGrade === 'B' ? '#ffd54f' : jGrade === '?' ? '#78909c' : '#ef5350';

        // 推奨プラン1行
        const planLine = best
            ? `${best.structure_type}${best.floors}F / ${best.unit_size_sqm}㎡×${best.max_units}戸 → 利回り${(best.estimated_yield*100).toFixed(1)}% / 総投資${best.total_investment ? (best.total_investment/10000).toLocaleString() + '万' : '?'}`
            : 'プラン未生成';

        // 判断理由（主な強み・弱み各1つ）
        const fr = j?.full_result || {};
        const topStrength = (fr.strengths || j?.strengths || [])[0] || '';
        const topWeakness = (fr.weaknesses || j?.weaknesses || fr.risks || j?.risks || [])[0] || '';

        let html = `
            <div style="background:#1a2332;padding:10px;border-radius:6px;margin-bottom:6px;">
                <div style="display:flex;align-items:center;gap:10px;">
                    <span style="background:${gc};color:#fff;width:42px;height:42px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:1.4rem;">${jGrade}</span>
                    <div style="flex:1;">
                        <div style="color:#4fc3f7;font-weight:bold;font-size:0.88rem;">${l.address}</div>
                        <div style="font-size:0.75rem;color:#b0bec5;">${priceLabel} | ${l.land_area_sqm ? l.land_area_sqm.toFixed(1) + '㎡' : '?'}</div>
                    </div>
                    <div style="text-align:right;">
                        ${best ? `<div style="color:#66bb6a;font-weight:bold;font-size:0.9rem;">${(best.estimated_yield*100).toFixed(1)}%</div><div style="font-size:0.65rem;color:#78909c;">最高利回り</div>` : ''}
                    </div>
                </div>
                <!-- 投資判断サマリー -->
                <div style="margin-top:8px;padding:8px;background:#0d1b2a;border-radius:6px;border-left:3px solid ${verdictColor};">
                    <div style="font-weight:bold;color:${verdictColor};font-size:0.85rem;">${verdictText}</div>
                    <div style="font-size:0.72rem;color:#b0bec5;margin-top:3px;">推奨: ${planLine}</div>
                    ${topStrength ? `<div style="font-size:0.68rem;color:#66bb6a;margin-top:2px;">◎ ${topStrength}</div>` : ''}
                    ${topWeakness ? `<div style="font-size:0.68rem;color:#ffa726;">△ ${topWeakness}</div>` : ''}
                </div>
                <div style="margin-top:4px;display:flex;gap:4px;">
                    ${l.source_url ? `<a href="${l.source_url}" target="_blank" style="color:#4fc3f7;font-size:0.68rem;">物件ページ</a>` : ''}
                    <button onclick="editLandListing(${l.id})" style="background:#2a3a5e;color:#b0bec5;border:1px solid #37474f;border-radius:3px;padding:1px 6px;font-size:0.68rem;cursor:pointer;margin-left:auto;">編集</button>
                    <button onclick="judgeLand(${l.id})" style="background:#1a4a2e;color:#66bb6a;border:1px solid #2e7d32;border-radius:3px;padding:1px 6px;font-size:0.68rem;cursor:pointer;">判定実行</button>
                </div>
            </div>
            <div style="background:#0d1b2a;border-radius:6px;overflow:hidden;">`;

        // ===== 1. 立地・駅力 =====
        const walkOk = l.walk_minutes && l.walk_minutes <= 12;
        const walkLabel = l.walk_minutes ? `${l.station || '?'} 徒歩${l.walk_minutes}分` : '駅情報なし';
        const stationScore = as?.station_distance_score;
        html += section('location', '🚉', '立地・駅力',
            `${walkLabel} ${stationScore != null ? scoreBar(stationScore) : ''}`,
            walkOk ? '#4caf50' : (l.walk_minutes && l.walk_minutes <= 15) ? '#ffa726' : '#ef5350',
            `<div>${pf(walkOk, l.walk_minutes ? `徒歩${l.walk_minutes}分（10分以内推奨）` : '徒歩分数不明')}</div>
             <div>${l.railway_line ? `路線: ${l.railway_line}` : ''}</div>
             ${pi.change_rate_5y != null ? `<div>人口動態: ${pi.change_rate_5y > 0 ? '+' : ''}${(pi.change_rate_5y*100).toFixed(1)}%/5y ${pi.elderly_ratio ? `高齢者${(pi.elderly_ratio*100).toFixed(0)}%` : ''}</div>` : ''}
             ${stationScore != null ? `<div>駅スコア: ${stationScore.toFixed(0)}/100</div>` : ''}`
        );

        // ===== 2. 敷地・地形 =====
        const areaOk = l.land_area_sqm && l.land_area_sqm >= 70;
        const shapeLabel = ls.shape_label || (l.land_shape || '不明');
        const frontageOk = ls.frontage_m ? ls.frontage_m >= 6.5 : null;
        const retWall = l.has_retaining_wall || ls.has_retaining_wall;
        const shapeScore = as?.lot_shape_score;
        html += section('site', '📐', '敷地・地形',
            `${l.land_area_sqm ? l.land_area_sqm.toFixed(1) + '㎡' : '?'} ${shapeLabel} ${shapeScore != null ? scoreBar(shapeScore) : ''}`,
            retWall ? '#ef5350' : (areaOk ? '#4caf50' : '#ffa726'),
            `<div>${pf(areaOk, `敷地面積 ${l.land_area_sqm ? l.land_area_sqm.toFixed(1) + '㎡' : '不明'}（70㎡以上推奨）`)}</div>
             <div>${pf(!retWall, retWall ? '擁壁あり → 検討対象外' : '擁壁なし')}</div>
             ${ls.frontage_m ? `<div>${pf(frontageOk, `間口 ${ls.frontage_m.toFixed(1)}m（6.5m以上で1層2戸可）`)}</div>` : ''}
             ${ls.depth_m ? `<div>奥行: ${ls.depth_m.toFixed(1)}m${ls.frontage_depth_ratio ? ` (間口/奥行比: ${ls.frontage_depth_ratio.toFixed(2)})` : ''}</div>` : ''}
             ${ei.elevation_m != null ? `<div>標高: ${ei.elevation_m.toFixed(1)}m${ei.is_fill_land ? ' ⚠盛土推定' : ''}${ei.slope_degree ? ` 傾斜${ei.slope_degree.toFixed(1)}°` : ''}</div>` : ''}`
        );

        // ===== 3. 接道・建築規制 =====
        const roadW = l.road_width_m || ri.max_road_width || 0;
        const roadOk = roadW >= 4.0;
        const cornerLot = l.corner_lot || ri.is_corner_lot;
        const northRoad = l.north_road;
        const roadScore = as?.road_score;
        html += section('road', '🛣️', '接道・建築規制',
            `${roadW > 0 ? roadW.toFixed(1) + 'm' : '幅員不明'} ${cornerLot ? '角地' : ''} ${roadScore != null ? scoreBar(roadScore) : ''}`,
            roadOk ? '#4caf50' : '#ffa726',
            `<div>${pf(roadOk, `前面道路幅員 ${roadW > 0 ? roadW.toFixed(1) + 'm' : '不明'}（4m未満→セットバック）`)}</div>
             ${l.road_legal_type ? `<div>道路種別: ${l.road_legal_type}</div>` : ''}
             <div>${warn(cornerLot, cornerLot ? '角地（建蔽率+10%緩和）' : '角地なし')}</div>
             <div>${warn(northRoad, northRoad ? '北側道路（北側斜線緩和）' : '北側道路なし')}</div>
             ${ri.has_setback ? '<div style="color:#ffa726;">セットバック要</div>' : ''}
             ${ri.is_flag_lot ? '<div style="color:#ef5350;">旗竿地 → 要注意</div>' : ''}`
        );

        // ===== 4. 都市計画 =====
        const zoningOk = l.zoning && !l.zoning.includes('工業専用');
        const covOk = l.building_coverage_ratio && l.building_coverage_ratio >= 0.6;
        const farOk = l.floor_area_ratio && l.floor_area_ratio >= 2.0;
        const hazardScore = as?.hazard_score;
        html += section('zoning', '🏛️', '都市計画・ハザード',
            `${l.zoning || '不明'} ${hazardScore != null ? scoreBar(hazardScore) : ''}`,
            (zoningOk && covOk) ? '#4caf50' : '#ffa726',
            `<div>${pf(zoningOk, `用途地域: ${l.zoning || '不明'}`)}</div>
             <div>${pf(covOk, `建蔽率: ${l.building_coverage_ratio ? (l.building_coverage_ratio*100).toFixed(0) + '%' : '不明'}（60%以上推奨）`)}</div>
             <div>${pf(farOk, `容積率: ${l.floor_area_ratio ? (l.floor_area_ratio*100).toFixed(0) + '%' : '不明'}（200%以上推奨）`)}</div>
             ${l.quasi_fireproof ? '<div>準防火地域</div>' : ''}
             ${hi.flood_risk_level && hi.flood_risk_level !== 'unknown' ? `<div>${pf(hi.flood_risk_level === 'low', `洪水リスク: ${hi.flood_risk_level}${hi.flood_depth_m != null ? ' ('+hi.flood_depth_m.toFixed(1)+'m)' : ''}`)}</div>` : ''}
             ${hi.landslide_risk ? '<div style="color:#ef5350;">⚠ 土砂災害警戒</div>' : ''}
             ${hi.liquefaction_risk && hi.liquefaction_risk !== 'unknown' ? `<div>液状化: ${hi.liquefaction_risk}</div>` : ''}`
        );

        // ===== 5. 収益性 =====
        let profitContent = '';
        if (best) {
            profitContent += `
                <div style="margin-bottom:6px;">
                    <strong style="color:#66bb6a;">推奨: ${best.structure_type}${best.floors}F / ${best.unit_size_sqm}㎡×${best.max_units}戸</strong><br>
                    利回り: <strong style="color:#66bb6a;">${(best.estimated_yield*100).toFixed(2)}%</strong> |
                    年収: ${(best.estimated_annual_income/10000).toLocaleString()}万円 |
                    月額賃料/戸: ${best.estimated_monthly_rent_per_unit?.toLocaleString()}円<br>
                    総投資: ${best.total_investment ? (best.total_investment/10000).toLocaleString() + '万円' : '-'}
                    (土地${l.land_price ? (l.land_price/10000).toLocaleString() : '?'}万 + 建築${(best.estimated_construction_cost/10000).toLocaleString()}万 + 諸費用)
                </div>`;
            // プランマトリクス
            const grouped = {};
            plans.forEach(p => { const k = `${p.structure_type}${p.floors}F`; if (!grouped[k]) grouped[k] = {}; grouped[k][p.unit_size_sqm] = p; });
            profitContent += '<table style="width:100%;border-collapse:collapse;font-size:0.68rem;"><thead><tr style="border-bottom:1px solid #37474f;"><th style="text-align:left;padding:3px;">構造</th><th style="text-align:right;">20㎡</th><th style="text-align:right;">25㎡</th><th style="text-align:right;">30㎡</th><th style="text-align:right;">35㎡</th></tr></thead><tbody>';
            for (const [key, sizes] of Object.entries(grouped)) {
                profitContent += `<tr style="border-bottom:1px solid #1a2744;"><td style="padding:2px;color:#4fc3f7;">${key}</td>`;
                [20,25,30,35].forEach(sz => {
                    const p = sizes[sz];
                    if (p && p.max_units >= 2) {
                        const yc = p.estimated_yield >= 0.06 ? '#66bb6a' : p.estimated_yield >= 0.04 ? '#ffd54f' : '#ef5350';
                        profitContent += `<td style="text-align:right;padding:2px;">${p.max_units}戸 <span style="color:${yc}">${(p.estimated_yield*100).toFixed(1)}%</span></td>`;
                    } else { profitContent += '<td style="text-align:right;color:#455a64;">-</td>'; }
                });
                profitContent += '</tr>';
            }
            profitContent += '</tbody></table>';
        } else {
            profitContent = `<button onclick="analyzeLand(${listingId})" class="btn-primary" style="font-size:0.72rem;">プラン生成</button>`;
        }
        html += section('profit', '💰', '収益性',
            best ? `${(best.estimated_yield*100).toFixed(1)}% ${best.structure_type}${best.floors}F×${best.max_units}戸` : 'プラン未生成',
            best ? (best.estimated_yield >= 0.06 ? '#4caf50' : best.estimated_yield >= 0.04 ? '#ffa726' : '#ef5350') : '#78909c',
            profitContent
        );

        // ===== 6. 地価・資産性 =====
        let landContent = '';
        if (lv) {
            landContent += `
                <div>推定㎡単価: <strong>${lv.price_per_sqm ? (lv.price_per_sqm/10000).toFixed(1) + '万円/㎡' : '不明'}</strong> (${lv.method || ''})</div>
                <div>推定土地価格: ${lv.estimated_price ? (lv.estimated_price/10000).toLocaleString() + '万円' : '不明'}</div>
                <div>公示地価比: ${lv.ratio_to_official ? (lv.ratio_to_official*100).toFixed(0) + '%' : '不明'} (参考公示: ${lv.official_price_per_sqm ? (lv.official_price_per_sqm/10000).toFixed(1) + '万/㎡' : '不明'})</div>
                <div>取引事例: ${lv.sample_count || 0}件</div>`;
        } else {
            landContent = '<div style="color:#78909c;">地価データ取得中...</div>';
        }
        const lvRatio = lv?.ratio_to_official;
        html += section('land-value', '📊', '地価・資産性',
            `${lv?.method || '?'} ${lv?.sample_count || 0}件`,
            lv?.sample_count > 0 ? '#4caf50' : '#ffa726',
            landContent
        );

        // ===== 7. 総合判定 =====
        let judgeContent = '';
        if (j) {
            const km = j.key_metrics || {};
            judgeContent += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:3px;margin-bottom:6px;">';
            for (const [k, v] of Object.entries(km)) {
                judgeContent += `<div style="background:#0d1b2a;padding:2px 6px;border-radius:3px;display:flex;justify-content:space-between;"><span style="color:#78909c;">${k}</span><span>${v}</span></div>`;
            }
            judgeContent += '</div>';
            // SWOT
            const swotItems = [
                {key:'strengths',label:'強み',color:'#66bb6a',icon:'◎'},
                {key:'weaknesses',label:'弱み',color:'#ffa726',icon:'△'},
                {key:'risks',label:'リスク',color:'#ef5350',icon:'✗'},
                {key:'opportunities',label:'機会',color:'#4fc3f7',icon:'○'},
            ];
            const fr = j.full_result || {};
            swotItems.forEach(({key,label,color,icon}) => {
                const items = fr[key] || j[key] || [];
                if (items.length) {
                    judgeContent += `<div style="margin-top:3px;"><span style="color:${color};font-weight:600;">${icon} ${label}</span>`;
                    items.forEach(i => { judgeContent += `<div style="margin-left:12px;color:#90a4ae;">• ${i}</div>`; });
                    judgeContent += '</div>';
                }
            });
        } else {
            judgeContent = `<button onclick="judgeLand(${listingId})" class="btn-primary" style="font-size:0.72rem;">投資判定を実行</button>`;
        }
        html += section('verdict', '⚖️', '総合判定',
            j ? `${jGrade}ランク ${jScore.toFixed(0)}点 ${jRec}` : '未判定',
            j ? gc : '#78909c',
            judgeContent
        );

        html += '</div>'; // close dash container

        document.getElementById('land-detail').innerHTML = html;
        document.getElementById('building-plans-table').innerHTML = '';

        // 地図をパン
        if (l.latitude && l.longitude) {
            map.setView([l.latitude, l.longitude], 15);
        }

        panel.scrollIntoView({ behavior: 'smooth' });
    } catch (e) {
        console.error('土地詳細取得エラー:', e);
    }
}

async function runAssetScore(listingId) {
    try {
        const resp = await fetch(`/api/land-listings/${listingId}/asset-score`, { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'ok') {
            showLandDetail(listingId);
            loadLandListings();
        } else {
            alert('資産性分析エラー: ' + (data.error || ''));
        }
    } catch (e) {
        alert('通信エラー: ' + e.message);
    }
}

async function analyzeLand(listingId) {
    try {
        const resp = await fetch(`/api/land-listings/${listingId}/analyze`, { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'ok') {
            showLandDetail(listingId);
            loadLandListings();
        } else {
            alert('プラン生成エラー: ' + (data.error || ''));
        }
    } catch (e) {
        alert('通信エラー: ' + e.message);
    }
}

// ===== ジオコーディング =====

async function batchGeocode() {
    const el = document.getElementById('batch-action-result');
    el.innerHTML = '<span style="color:#4fc3f7;">座標取得中...</span>';
    try {
        const resp = await fetch('/api/land-listings/geocode', { method: 'POST' });
        const data = await resp.json();
        el.innerHTML = `<span style="color:#66bb6a;">座標取得完了: ${data.geocoded || 0}件</span>`;
        loadLandListings();
        loadLandStats();
    } catch (e) {
        el.innerHTML = `<span style="color:#ef5350;">エラー: ${e.message}</span>`;
    }
}

// ===== バックグラウンドタスク進捗ポーリング =====

let _pollTimer = null;
function pollTaskStatus() {
    if (_pollTimer) clearInterval(_pollTimer);
    _pollTimer = setInterval(async () => {
        try {
            const resp = await fetch('/api/task-status');
            const data = await resp.json();
            const el = document.getElementById('scrape-progress');
            if (el) {
                el.textContent = data.step || '実行中...';
            }
            if (!data.running) {
                clearInterval(_pollTimer);
                _pollTimer = null;
                if (el) {
                    if (data.error) {
                        el.innerHTML = `<span style="color:#ef5350;">エラー: ${data.error}</span>`;
                    } else {
                        const r = data.result || {};
                        el.innerHTML = `<span style="color:#66bb6a;">完了: ${r.listings_saved || 0}物件, ${r.plans_generated || 0}プラン, ${r.asset_scores_generated || 0}スコア</span>`;
                    }
                }
                loadLandListings();
            }
        } catch (e) {
            console.debug('poll error:', e);
        }
    }, 5000);
}

// ===== 一括資産性スコアリング =====

async function batchAssetScore() {
    const el = document.getElementById('batch-action-result');
    el.innerHTML = '<span style="color:#4fc3f7;">資産性スコアリング中...</span>';
    try {
        const resp = await fetch('/api/land-listings/batch-asset-score', { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'running') {
            el.innerHTML = `<span style="color:#ffa726;">${data.message}</span>`;
            return;
        }
        el.innerHTML = `<span style="color:#66bb6a;">${data.message || '開始しました'}</span>
            <div id="scrape-progress" style="color:#ffa726;font-size:0.72rem;margin-top:2px;">実行中...</div>`;
        pollTaskStatus();
    } catch (e) {
        el.innerHTML = `<span style="color:#ef5350;">エラー: ${e.message}</span>`;
    }
}

// ===== 一括投資判定 =====

async function batchJudgeLand() {
    const el = document.getElementById('batch-action-result');
    el.innerHTML = '<span style="color:#4fc3f7;">資産性分析+投資判定をバックグラウンドで実行中...</span>';
    try {
        const resp = await fetch('/api/land-listings/batch-judge', { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'running') {
            el.innerHTML = `<span style="color:#ffa726;">${data.message}</span>`;
            return;
        }
        el.innerHTML = `<span style="color:#66bb6a;">${data.message || '開始しました'}</span>
            <div id="scrape-progress" style="color:#ffa726;font-size:0.72rem;margin-top:2px;">実行中...</div>`;
        pollTaskStatus();
    } catch (e) {
        el.innerHTML = `<span style="color:#ef5350;">エラー: ${e.message}</span>`;
    }
}

// ===== 個別投資判定 =====

async function judgeLand(listingId) {
    try {
        const resp = await fetch(`/api/land-listings/${listingId}/judge`, { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'ok' && data.judgment) {
            showLandDetail(listingId);
        } else {
            alert('判定エラー: ' + (data.error || ''));
        }
    } catch (e) {
        alert('通信エラー: ' + e.message);
    }
}

// ===== 検索条件保存 =====

async function saveScrapeConfig() {
    const name = document.getElementById('config-name').value.trim();
    if (!name) {
        alert('設定名を入力してください');
        return;
    }

    const config = {
        name: name,
        source: document.getElementById('land-source').value,
        prefecture_codes: [document.getElementById('land-pref').value],
        price_min: parseInt(document.getElementById('land-price-min').value) || null,
        price_max: parseInt(document.getElementById('land-price-max').value) || null,
        area_min: parseFloat(document.getElementById('land-area-min').value) || null,
        walk_max: parseInt(document.getElementById('land-walk-max').value) || null,
        max_pages: parseInt(document.getElementById('land-max-pages').value) || 3,
        run_interval_hours: parseInt(document.getElementById('config-interval').value) || 24,
    };

    try {
        const resp = await fetch('/api/scrape-config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config),
        });
        const data = await resp.json();
        if (data.status === 'ok') {
            document.getElementById('config-name').value = '';
            loadScrapeConfigs();
        }
    } catch (e) {
        alert('保存エラー: ' + e.message);
    }
}

async function loadScrapeConfigs() {
    try {
        const resp = await fetch('/api/scrape-configs');
        const data = await resp.json();
        const el = document.getElementById('saved-configs');
        if (!data.configs || data.configs.length === 0) {
            el.innerHTML = '<span class="hint">保存済み設定なし</span>';
            return;
        }
        let html = '';
        data.configs.forEach(c => {
            html += `<div style="padding:4px 0;border-bottom:1px solid #263238;">
                <span style="color:#4fc3f7;">${c.name}</span>
                <span style="color:#78909c;margin-left:8px;">${c.source} | ${c.run_interval_hours || 24}h毎</span>
                ${c.last_run_at ? '<span style="color:#66bb6a;margin-left:8px;">最終: ' + c.last_run_at + '</span>' : ''}
            </div>`;
        });
        el.innerHTML = html;
    } catch (e) {
        console.error('設定読込エラー:', e);
    }
}

// ===== DB統計表示 =====

async function loadLandStats() {
    try {
        const resp = await fetch('/api/db/stats');
        const s = resp.ok ? await resp.json() : {};
        const el = document.getElementById('land-stats-bar');
        if (el) {
            el.innerHTML = `
                <span>物件: <strong style="color:#ff8a65;">${(s.properties || 0).toLocaleString()}</strong></span>
                <span>土地: <strong style="color:#4fc3f7;">${(s.land_listings || 0).toLocaleString()}</strong></span>
                <span>プラン: <strong style="color:#66bb6a;">${(s.building_plans || 0).toLocaleString()}</strong></span>
                <span>判定: <strong style="color:#ffd54f;">${(s.land_judgments + (s.judgments || 0) || 0).toLocaleString()}</strong></span>
                <span>賃料: <strong style="color:#ce93d8;">${(s.rental_comps || 0).toLocaleString()}</strong></span>
                <span>地価: ${(s.land_prices || 0).toLocaleString()}</span>
            `;
        }
        // 履歴・データタブのDB統計も更新
        const dbEl = document.getElementById('db-stats-display');
        if (dbEl) {
            dbEl.innerHTML = Object.entries(s)
                .filter(([k, v]) => v > 0)
                .map(([k, v]) => `<span style="margin-right:10px;">${k}: <strong>${v.toLocaleString()}</strong></span>`)
                .join('');
        }
    } catch (e) { console.debug('stats error:', e); }
}

// ===== API実データ取得 =====

async function ingestRealData() {
    const el = document.getElementById('batch-action-result');
    el.innerHTML = '<span style="color:#4fc3f7;">API実データ取得中...</span>';
    try {
        const resp = await fetch('/api/ingest/real-data', { method: 'POST' });
        const data = await resp.json();
        el.innerHTML = `<span style="color:${data.api_configured ? '#66bb6a' : '#ffa726'};">${data.message}</span>`;
    } catch (e) {
        el.innerHTML = `<span style="color:#ef5350;">エラー: ${e.message}</span>`;
    }
}

// ===== 全データ収集 =====

async function collectAllData() {
    const el = document.getElementById('batch-action-result');
    el.innerHTML = '<span style="color:#4fc3f7;">全データ収集を開始...</span>';
    try {
        const resp = await fetch('/api/collect/run', { method: 'POST' });
        const data = await resp.json();
        el.innerHTML = `<span style="color:#66bb6a;">${data.message}</span>
            <div style="color:#78909c;font-size:0.72rem;margin-top:2px;">現在: ${data.current_listings}件</div>`;
    } catch (e) {
        el.innerHTML = `<span style="color:#ef5350;">エラー: ${e.message}</span>`;
    }
}

// ===== 物件編集 =====

async function editLandListing(listingId) {
    const resp = await fetch(`/api/land-listings/${listingId}`);
    const data = await resp.json();
    const l = data.listing;

    const panel = document.getElementById('land-detail-panel');
    panel.style.display = 'block';

    document.getElementById('land-detail').innerHTML = `
        <div style="background:#1a2332;padding:10px;border-radius:6px;">
            <h4 style="color:#4fc3f7;margin-bottom:8px;">物件情報編集 (ID: ${l.id})</h4>
            <div class="form-group"><label>住所</label><input type="text" id="edit-address" value="${l.address || ''}"></div>
            <div class="form-row">
                <div class="form-group"><label>路線</label><input type="text" id="edit-line" value="${l.railway_line || ''}"></div>
                <div class="form-group"><label>駅</label><input type="text" id="edit-station" value="${l.station || ''}"></div>
                <div class="form-group"><label>徒歩(分)</label><input type="number" id="edit-walk" value="${l.walk_minutes || ''}"></div>
            </div>
            <div class="form-row">
                <div class="form-group"><label>土地価格(円)</label><input type="number" id="edit-price" value="${l.land_price || ''}"></div>
                <div class="form-group"><label>面積(㎡)</label><input type="number" id="edit-area" value="${l.land_area_sqm || ''}" step="0.01"></div>
            </div>
            <div class="form-row">
                <div class="form-group"><label>建蔽率(%)</label><input type="number" id="edit-coverage" value="${l.building_coverage_ratio ? (l.building_coverage_ratio*100).toFixed(0) : ''}" step="1"></div>
                <div class="form-group"><label>容積率(%)</label><input type="number" id="edit-far" value="${l.floor_area_ratio ? (l.floor_area_ratio*100).toFixed(0) : ''}" step="1"></div>
            </div>
            <div class="form-group"><label>用途地域</label>
                <select id="edit-zoning">
                    <option value="">不明</option>
                    <option value="第一種低層住居専用地域">第一種低層住居専用地域</option>
                    <option value="第二種低層住居専用地域">第二種低層住居専用地域</option>
                    <option value="第一種中高層住居専用地域">第一種中高層住居専用地域</option>
                    <option value="第二種中高層住居専用地域">第二種中高層住居専用地域</option>
                    <option value="第一種住居地域">第一種住居地域</option>
                    <option value="第二種住居地域">第二種住居地域</option>
                    <option value="準住居地域">準住居地域</option>
                    <option value="近隣商業地域">近隣商業地域</option>
                    <option value="商業地域">商業地域</option>
                    <option value="準工業地域">準工業地域</option>
                </select>
            </div>
            <div class="form-group"><label>メモ</label><input type="text" id="edit-memo" value="${l.memo || ''}"></div>
            <div class="form-row" style="margin-top:8px;">
                <button onclick="saveLandEdit(${l.id})" class="btn-primary" style="flex:1;">保存</button>
                <button onclick="showLandDetail(${l.id})" class="btn-secondary" style="flex:1;">キャンセル</button>
            </div>
        </div>`;

    // Set zoning select value
    document.getElementById('edit-zoning').value = l.zoning || '';
    document.getElementById('building-plans-table').innerHTML = '';
    panel.scrollIntoView({ behavior: 'smooth' });
}

async function saveLandEdit(listingId) {
    const coverage = parseFloat(document.getElementById('edit-coverage').value);
    const far = parseFloat(document.getElementById('edit-far').value);

    const body = {
        address: document.getElementById('edit-address').value,
        railway_line: document.getElementById('edit-line').value,
        station: document.getElementById('edit-station').value,
        walk_minutes: parseInt(document.getElementById('edit-walk').value) || null,
        land_price: parseInt(document.getElementById('edit-price').value) || null,
        land_area_sqm: parseFloat(document.getElementById('edit-area').value) || null,
        building_coverage_ratio: coverage ? coverage / 100 : null,
        floor_area_ratio: far ? far / 100 : null,
        zoning: document.getElementById('edit-zoning').value,
        memo: document.getElementById('edit-memo').value,
    };

    try {
        const resp = await fetch(`/api/land-listings/${listingId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (data.status === 'ok') {
            showLandDetail(listingId);
            loadLandListings();
        } else {
            alert('保存エラー: ' + (data.error || ''));
        }
    } catch (e) {
        alert('通信エラー: ' + e.message);
    }
}

// ===== 統合データベースビュー =====

let unifiedOffset = 0;
const UNIFIED_PAGE_SIZE = 50;

// ===== 全物件比較テーブル =====

async function loadCompareTable() {
    const station = (document.getElementById('compare-station')?.value || '').trim();
    const sortBy = document.getElementById('compare-sort')?.value || 'estimated_yield';
    const grade = document.getElementById('compare-grade')?.value || '';
    const el = document.getElementById('compare-table');
    const totalEl = document.getElementById('compare-total');
    if (!el) return;

    el.innerHTML = '<div style="padding:12px;color:#4fc3f7;">読込中...</div>';

    try {
        const params = new URLSearchParams({sort_by: sortBy, limit: '800'});
        if (station) params.set('station', station);
        if (grade) params.set('grade', grade);

        const resp = await fetch(`/api/compare?${params}`);
        const data = await resp.json();
        const rows = data.rows || [];
        if (totalEl) totalEl.textContent = `(${rows.length}件)`;

        if (!rows.length) {
            el.innerHTML = '<div style="padding:12px;color:#78909c;">データなし</div>';
            return;
        }

        let html = `<table style="width:100%;border-collapse:collapse;white-space:nowrap;">
            <thead><tr style="background:#0d1b2a;position:sticky;top:0;z-index:1;">
                <th style="padding:4px;text-align:left;border-bottom:2px solid #1e3a5f;">種別</th>
                <th style="padding:4px;text-align:left;border-bottom:2px solid #1e3a5f;max-width:160px;">住所</th>
                <th style="padding:4px;text-align:left;border-bottom:2px solid #1e3a5f;">駅</th>
                <th style="padding:4px;text-align:right;border-bottom:2px solid #1e3a5f;">徒歩</th>
                <th style="padding:4px;text-align:right;border-bottom:2px solid #1e3a5f;">価格</th>
                <th style="padding:4px;text-align:right;border-bottom:2px solid #1e3a5f;">面積</th>
                <th style="padding:4px;text-align:right;border-bottom:2px solid #1e3a5f;">利回り</th>
                <th style="padding:4px;text-align:center;border-bottom:2px solid #1e3a5f;">プラン</th>
                <th style="padding:4px;text-align:center;border-bottom:2px solid #1e3a5f;">資産性</th>
                <th style="padding:4px;text-align:center;border-bottom:2px solid #1e3a5f;">判定</th>
            </tr></thead><tbody>`;

        rows.forEach((r, i) => {
            const typeLabel = r.type === 'land'
                ? '<span style="color:#4fc3f7;">土地</span>'
                : '<span style="color:#ffa726;">収益</span>';
            const price = r.land_price
                ? (r.land_price >= 100000000
                    ? (r.land_price / 100000000).toFixed(1) + '億'
                    : (r.land_price / 10000).toLocaleString() + '万')
                : '-';
            const yld = r.estimated_yield
                ? `<span style="color:${r.estimated_yield >= 0.06 ? '#66bb6a' : r.estimated_yield >= 0.04 ? '#ffd54f' : '#ef5350'};font-weight:bold;">${(r.estimated_yield * 100).toFixed(1)}%</span>`
                : '-';
            const plan = r.structure_type
                ? `${r.structure_type}${r.floors || ''}F ${r.max_units || ''}戸`
                : '-';
            const asGrade = r.asset_grade
                ? `<span style="background:${gradeColor(r.asset_grade)};color:#fff;padding:0 4px;border-radius:2px;">${r.asset_grade}</span> ${r.asset_score ? r.asset_score.toFixed(0) : ''}`
                : '<span style="color:#546e7a;">-</span>';
            const jGrade = r.judge_grade
                ? `<span style="background:${gradeColor(r.judge_grade)};color:#fff;padding:0 4px;border-radius:2px;">${r.judge_grade}</span> ${r.judge_score ? r.judge_score.toFixed(0) : ''}`
                : '<span style="color:#546e7a;">-</span>';
            const clickFn = r.type === 'land'
                ? `showLandDetail(${r.id});switchTab('tab-property');`
                : `selectPropertyById('${r.id}');switchTab('tab-property');`;

            html += `<tr style="border-bottom:1px solid #1a2744;cursor:pointer;" onclick="${clickFn}"
                         onmouseover="this.style.background='#1a2744'" onmouseout="this.style.background=''">
                <td style="padding:3px;">${typeLabel}</td>
                <td style="padding:3px;max-width:160px;overflow:hidden;text-overflow:ellipsis;" title="${r.address || ''}">${r.address || '-'}</td>
                <td style="padding:3px;">${r.station || '-'}</td>
                <td style="padding:3px;text-align:right;${r.walk_minutes && r.walk_minutes > 12 ? 'color:#ef5350;' : ''}">${r.walk_minutes || '-'}</td>
                <td style="padding:3px;text-align:right;">${price}</td>
                <td style="padding:3px;text-align:right;">${r.land_area_sqm ? r.land_area_sqm.toFixed(0) : '-'}</td>
                <td style="padding:3px;text-align:right;">${yld}</td>
                <td style="padding:3px;text-align:center;font-size:0.62rem;">${plan}</td>
                <td style="padding:3px;text-align:center;">${asGrade}</td>
                <td style="padding:3px;text-align:center;">${jGrade}</td>
            </tr>`;
        });

        html += '</tbody></table>';
        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = `<div style="padding:12px;color:#ef5350;">エラー: ${e.message}</div>`;
    }
}

async function loadUnifiedData(offset) {
    unifiedOffset = offset || 0;
    const station = document.getElementById('unified-station-filter').value.trim();
    const sortBy = document.getElementById('unified-sort').value;

    const el = document.getElementById('unified-table');
    el.innerHTML = '<div class="loading">読込中...</div>';

    try {
        const params = new URLSearchParams({
            limit: UNIFIED_PAGE_SIZE,
            offset: unifiedOffset,
            sort_by: sortBy,
            sort_dir: 'DESC',
        });
        if (station) params.append('station', station);

        const resp = await fetch(`/api/unified-data?${params}`);
        const data = await resp.json();

        document.getElementById('unified-total').textContent = `(${data.total}件)`;

        if (!data.data || data.data.length === 0) {
            el.innerHTML = '<span class="hint">データなし</span>';
            return;
        }

        let html = '<table style="width:100%;border-collapse:collapse;color:#b0bec5;">';
        html += `<thead><tr style="border-bottom:2px solid #37474f;font-size:0.68rem;color:#81d4fa;">
            <th style="padding:3px;text-align:left;">住所</th>
            <th>駅</th>
            <th>価格</th>
            <th>面積</th>
            <th>建蔽</th>
            <th>容積</th>
            <th>利回り</th>
            <th>Grade</th>
            <th>ソース</th>
            <th></th>
        </tr></thead><tbody>`;

        data.data.forEach(d => {
            const priceLabel = d.land_price ? (d.land_price >= 1e8 ? (d.land_price/1e8).toFixed(1)+'億' : (d.land_price/1e4).toLocaleString()+'万') : '-';
            const yieldLabel = d.best_yield ? (d.best_yield*100).toFixed(1)+'%' : '-';
            const gradeColors = {S:'#1a9641',A:'#4dac26',B:'#b8e186',C:'#fdb863',D:'#e66101',F:'#d7191c'};
            const gc = gradeColors[d.grade] || '#546e7a';

            html += `<tr style="border-bottom:1px solid #1a2744;cursor:pointer;" onclick="showLandDetail(${d.id});switchTab('tab-property');">
                <td style="padding:3px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${d.address}">${d.address || '-'}</td>
                <td style="text-align:center;">${d.station || '-'}</td>
                <td style="text-align:right;">${priceLabel}</td>
                <td style="text-align:right;">${d.land_area_sqm ? d.land_area_sqm.toFixed(0) : '-'}</td>
                <td style="text-align:center;">${d.building_coverage_ratio ? (d.building_coverage_ratio*100).toFixed(0)+'%' : '-'}</td>
                <td style="text-align:center;">${d.floor_area_ratio ? (d.floor_area_ratio*100).toFixed(0)+'%' : '-'}</td>
                <td style="text-align:right;color:${d.best_yield >= 0.06 ? '#66bb6a' : d.best_yield >= 0.04 ? '#ffd54f' : '#ef5350'};">${yieldLabel}</td>
                <td style="text-align:center;"><span style="color:${gc};font-weight:bold;">${d.grade || '-'}</span></td>
                <td style="text-align:center;font-size:0.6rem;color:#546e7a;">${d.source_url ? `<a href="${d.source_url}" target="_blank" style="color:#4fc3f7;" onclick="event.stopPropagation();">` + (d.source || '物件') + '</a>' : (d.source || '')}</td>
                <td><button onclick="event.stopPropagation();editLandListing(${d.id});switchTab('tab-property');" style="background:none;border:1px solid #37474f;color:#78909c;border-radius:3px;padding:1px 4px;cursor:pointer;font-size:0.6rem;">編</button></td>
            </tr>`;
        });
        html += '</tbody></table>';
        el.innerHTML = html;

        // Pager
        const totalPages = Math.ceil(data.total / UNIFIED_PAGE_SIZE);
        const currentPage = Math.floor(unifiedOffset / UNIFIED_PAGE_SIZE) + 1;
        let pagerHtml = '';
        if (currentPage > 1) pagerHtml += `<button onclick="loadUnifiedData(${(currentPage-2)*UNIFIED_PAGE_SIZE})" style="background:#2a3a5e;color:#b0bec5;border:none;border-radius:3px;padding:2px 8px;cursor:pointer;margin:0 2px;">前</button>`;
        pagerHtml += `<span style="color:#78909c;font-size:0.72rem;margin:0 8px;">${currentPage}/${totalPages}</span>`;
        if (currentPage < totalPages) pagerHtml += `<button onclick="loadUnifiedData(${currentPage*UNIFIED_PAGE_SIZE})" style="background:#2a3a5e;color:#b0bec5;border:none;border-radius:3px;padding:2px 8px;cursor:pointer;margin:0 2px;">次</button>`;
        document.getElementById('unified-pager').innerHTML = pagerHtml;

    } catch (e) {
        el.innerHTML = `<span style="color:#ef5350;">エラー: ${e.message}</span>`;
    }
}

function selectPropertyById(propertyId) {
    const idx = sampleProperties.findIndex(p => String(p.id) === String(propertyId));
    if (idx >= 0) {
        selectProperty(idx);
        return;
    }
    // 最新データに未読込のIDがある場合は再読込後に再解決
    loadSampleProperties().then(() => {
        const reIdx = sampleProperties.findIndex(p => String(p.id) === String(propertyId));
        if (reIdx >= 0) selectProperty(reIdx);
    }).catch(() => {});
}

// =====================================================
// ===== ハザードマップ・資産性分析・Google Earth =====
// =====================================================

let hazardLayers = {};
let isochroneLayer = null;
let populationLayer = null;
let hazardTileUrls = null;

// ===== ハザードマップタイルオーバーレイ =====

async function loadHazardTileUrls() {
    if (hazardTileUrls) return hazardTileUrls;
    try {
        const resp = await fetch('/api/hazard-tiles');
        const data = await resp.json();
        hazardTileUrls = data.tiles;
        return hazardTileUrls;
    } catch (e) {
        console.error('ハザードタイルURL取得エラー:', e);
        return null;
    }
}

async function toggleHazardLayer(type, show) {
    if (!show) {
        if (hazardLayers[type]) {
            map.removeLayer(hazardLayers[type]);
            delete hazardLayers[type];
        }
        return;
    }

    const urls = await loadHazardTileUrls();
    if (!urls) return;

    const typeMap = {
        'flood': 'flood',
        'landslide': 'landslide',
        'tsunami': 'tsunami',
        'storm': 'storm_surge',
        'terrain': 'terrain_class',
    };

    const urlKey = typeMap[type];
    const url = urls[urlKey];
    if (!url) return;

    hazardLayers[type] = L.tileLayer(url, {
        opacity: 0.55,
        maxZoom: 17,
        attribution: 'ハザードマップポータルサイト',
    });
    hazardLayers[type].addTo(map);
}


// ===== 資産性スコア表示 =====

function showAssetScore(data) {
    const panel = document.getElementById('asset-score-panel');
    if (!panel || !data) return;
    panel.style.display = 'block';

    const gradeColors = {S:'#4caf50',A:'#66bb6a',B:'#ffd54f',C:'#ffa726',D:'#ef5350',F:'#b71c1c'};
    const gradeEl = document.getElementById('asset-grade');
    gradeEl.textContent = data.grade || '?';
    gradeEl.style.background = gradeColors[data.grade] || '#78909c';

    document.getElementById('asset-score-value').textContent = `資産性スコア: ${data.overall_score?.toFixed(1)} / 100`;
    document.getElementById('asset-summary').textContent = data.summary || '';

    // スコアバー
    const bars = [
        { label: '接道状況', score: data.road_info?.road_score, key: 'road' },
        { label: 'ハザード安全性', score: data.hazard_info?.hazard_score, key: 'hazard' },
        { label: '地形・標高', score: data.elevation_info?.terrain_score, key: 'elevation' },
        { label: '敷地形状', score: data.lot_shape?.shape_score, key: 'lot_shape' },
        { label: '人口動態', score: data.population?.population_score, key: 'population' },
        { label: '駅距離', score: data.station_distance_score, key: 'station' },
    ];

    let barsHtml = '';
    bars.forEach(b => {
        const score = b.score != null ? b.score.toFixed(0) : '?';
        const pct = b.score != null ? Math.min(b.score, 100) : 0;
        const color = pct >= 70 ? '#4caf50' : pct >= 50 ? '#ffd54f' : pct >= 30 ? '#ffa726' : '#ef5350';
        barsHtml += `
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;font-size:0.72rem;">
                <span style="width:80px;color:#b0bec5;">${b.label}</span>
                <div style="flex:1;background:#1a2744;border-radius:3px;height:12px;overflow:hidden;">
                    <div style="width:${pct}%;height:100%;background:${color};border-radius:3px;transition:width 0.5s;"></div>
                </div>
                <span style="width:30px;text-align:right;color:${color};font-weight:bold;">${score}</span>
            </div>`;
    });
    document.getElementById('asset-score-bars').innerHTML = barsHtml;

    // 詳細テーブル
    let detailHtml = '<table style="width:100%;font-size:0.7rem;border-collapse:collapse;">';

    // 接道詳細
    const ri = data.road_info;
    if (ri) {
        detailHtml += `<tr style="border-bottom:1px solid #1a2744;">
            <td style="color:#78909c;padding:3px;">前面道路</td>
            <td>${ri.max_road_width > 0 ? ri.max_road_width.toFixed(1) + 'm' : '不明'}</td>
            <td style="color:#78909c;">接道数</td>
            <td>${ri.road_count || '?'}</td>
        </tr>`;
        let roadTags = [];
        if (ri.is_corner_lot) roadTags.push('<span style="color:#4caf50;">角地</span>');
        if (ri.is_flag_lot) roadTags.push('<span style="color:#ef5350;">旗竿地</span>');
        if (ri.has_setback) roadTags.push('<span style="color:#ffa726;">セットバック要</span>');
        if (roadTags.length) {
            detailHtml += `<tr style="border-bottom:1px solid #1a2744;"><td style="color:#78909c;padding:3px;">特記</td><td colspan="3">${roadTags.join(' ')}</td></tr>`;
        }
    }

    // 標高詳細
    const ei = data.elevation_info;
    if (ei) {
        detailHtml += `<tr style="border-bottom:1px solid #1a2744;">
            <td style="color:#78909c;padding:3px;">標高</td>
            <td>${ei.elevation_m != null ? ei.elevation_m.toFixed(1) + 'm' : '不明'}</td>
            <td style="color:#78909c;">周辺差</td>
            <td>${ei.relative_elevation != null ? (ei.relative_elevation > 0 ? '+' : '') + ei.relative_elevation.toFixed(1) + 'm' : '不明'}</td>
        </tr>`;
        let terrainTags = [];
        if (ei.is_fill_land) terrainTags.push('<span style="color:#ef5350;">盛土推定</span>');
        if (ei.is_cut_land) terrainTags.push('<span style="color:#ffa726;">切土推定</span>');
        if (ei.slope_degree != null && ei.slope_degree > 10) terrainTags.push(`<span style="color:#ffa726;">傾斜${ei.slope_degree.toFixed(1)}度</span>`);
        if (terrainTags.length) {
            detailHtml += `<tr style="border-bottom:1px solid #1a2744;"><td style="color:#78909c;padding:3px;">地形</td><td colspan="3">${terrainTags.join(' ')}</td></tr>`;
        }
    }

    // ハザード詳細
    const hi = data.hazard_info;
    if (hi) {
        const floodLabels = {very_high:'非常に高い',high:'高い',medium:'中程度',low:'低い',unknown:'不明'};
        const floodColor = {very_high:'#b71c1c',high:'#ef5350',medium:'#ffa726',low:'#4caf50',unknown:'#78909c'};
        detailHtml += `<tr style="border-bottom:1px solid #1a2744;">
            <td style="color:#78909c;padding:3px;">洪水リスク</td>
            <td><span style="color:${floodColor[hi.flood_risk_level] || '#78909c'};">${floodLabels[hi.flood_risk_level] || '不明'}</span></td>
            <td style="color:#78909c;">液状化</td>
            <td>${hi.liquefaction_risk || '不明'}</td>
        </tr>`;
        if (hi.landslide_risk) {
            detailHtml += `<tr style="border-bottom:1px solid #1a2744;"><td style="color:#78909c;padding:3px;">土砂災害</td><td colspan="3" style="color:#ef5350;">警戒区域内</td></tr>`;
        }
    }

    // 敷地形状詳細
    const ls = data.lot_shape;
    if (ls) {
        detailHtml += `<tr style="border-bottom:1px solid #1a2744;">
            <td style="color:#78909c;padding:3px;">形状</td>
            <td>${ls.shape_label || '不明'}</td>
            <td style="color:#78909c;">角地</td>
            <td>${ls.is_corner ? '<span style="color:#4caf50;">角地</span>' : '-'}</td>
        </tr>`;
    }

    // 人口詳細
    const pop = data.population;
    if (pop && pop.change_rate_5y != null) {
        const sign = pop.change_rate_5y > 0 ? '+' : '';
        const popColor = pop.change_rate_5y > 0 ? '#4caf50' : pop.change_rate_5y > -0.02 ? '#ffd54f' : '#ef5350';
        detailHtml += `<tr style="border-bottom:1px solid #1a2744;">
            <td style="color:#78909c;padding:3px;">人口変化(5y)</td>
            <td style="color:${popColor};">${sign}${(pop.change_rate_5y * 100).toFixed(1)}%</td>
            <td style="color:#78909c;">高齢化率</td>
            <td>${pop.elderly_ratio != null ? (pop.elderly_ratio * 100).toFixed(1) + '%' : '不明'}</td>
        </tr>`;
    }

    detailHtml += '</table>';
    document.getElementById('asset-details').innerHTML = detailHtml;
}


// ===== アイソクロン（駅到達圏） =====

async function loadIsochroneForStations() {
    if (isochroneLayer) map.removeLayer(isochroneLayer);
    isochroneLayer = L.layerGroup();

    // 現在表示中の駅から主要なものを取得
    const visibleStations = stationsData.filter(s =>
        s.lat && s.lon && map.getBounds().contains([s.lat, s.lon])
    ).slice(0, 5); // 最大5駅

    for (const station of visibleStations) {
        try {
            // 5分, 10分, 15分の到達圏
            for (const minutes of [5, 10, 15]) {
                const resp = await fetch(`/api/isochrone?lat=${station.lat}&lng=${station.lon}&range_seconds=${minutes * 60}`);
                const data = await resp.json();
                if (data.geojson && data.geojson.features) {
                    const colors = {5: '#4caf50', 10: '#ffd54f', 15: '#ef5350'};
                    L.geoJSON(data.geojson, {
                        style: {
                            color: colors[minutes],
                            weight: 1,
                            fillOpacity: 0.1,
                            opacity: 0.6,
                        },
                    }).bindTooltip(`${station.name} 徒歩${minutes}分圏`).addTo(isochroneLayer);
                }
            }
        } catch (e) {
            console.warn(`アイソクロン取得失敗: ${station.name}`, e);
        }
    }

    isochroneLayer.addTo(map);
}


// ===== 投資分析レイヤー =====

let ivLandPriceLayer = null;
let ivRentLayer = null;
let ivYieldLayer = null;
let ivTransLayer = null;
let ivPopLayer = null;
let ivFacLayer = null;
let ivStationPowerLayer = null;
let ivMesh250Layer = null;

// --- 旧互換 ---
let facilityLayer = null;
let officialLandPriceLayer = null;

function _mapBoundsParams() {
    const b = map.getBounds();
    return `south=${b.getSouth()}&west=${b.getWest()}&north=${b.getNorth()}&east=${b.getEast()}`;
}

// 価格→連続グラデーション色 (10段階)
const UNIFIED_COLOR_PALETTE_10 = [
    [66, 133, 244],   // blue
    [41, 182, 246],   // light blue
    [38, 166, 154],   // teal
    [76, 175, 80],    // green
    [156, 204, 101],  // light green
    [255, 235, 59],   // yellow
    [255, 193, 7],    // amber
    [255, 152, 0],    // orange
    [244, 67, 54],    // red
    [136, 14, 79],    // deep purple
];

function _buildStopsFromThresholds(thresholds) {
    return thresholds.map((t, i) => [t, UNIFIED_COLOR_PALETTE_10[Math.min(i, UNIFIED_COLOR_PALETTE_10.length - 1)]]);
}

const MESH_COLOR_SCALES = {
    land_price: {
        label: '地価',
        unit: '円/m²',
        stops: _buildStopsFromThresholds([30000, 80000, 150000, 220000, 300000, 400000, 550000, 800000, 1500000, 5000000]),
    },
    tx_price: {
        label: '取引単価',
        unit: '円/m²',
        // 地価と同レンジで統一
        stops: _buildStopsFromThresholds([30000, 80000, 150000, 220000, 300000, 400000, 550000, 800000, 1500000, 5000000]),
    },
    rent: {
        label: '賃料',
        unit: '円/m²',
        stops: _buildStopsFromThresholds([1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 6000, 8000]),
    },
    yield: {
        label: '想定利回り',
        unit: '%',
        stops: _buildStopsFromThresholds([2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 7.0, 8.5, 10.0]),
    },
    population: {
        label: '人口増減率',
        unit: '%',
        stops: _buildStopsFromThresholds([-20, -12, -8, -4, -2, 0, 2, 5, 10, 20]),
    },
    pop_density: {
        label: '人口密度',
        unit: '人/km²',
        stops: _buildStopsFromThresholds([0, 1000, 3000, 5000, 8000, 12000, 16000, 22000, 35000, 60000]),
    },
    facility: {
        label: '施設密度',
        unit: '件/mesh',
        stops: _buildStopsFromThresholds([0, 1, 2, 3, 4, 5, 6, 8, 10, 15]),
    },
};

function _priceColor(price) {
    return _interpolateColor(price, MESH_COLOR_SCALES.land_price.stops);
}

// 汎用連続グラデーション補間
function _interpolateColor(value, stops) {
    if (value <= stops[0][0]) return `rgb(${stops[0][1].join(',')})`;
    if (value >= stops[stops.length-1][0]) return `rgb(${stops[stops.length-1][1].join(',')})`;
    for (let i = 1; i < stops.length; i++) {
        if (value <= stops[i][0]) {
            const t = (value - stops[i-1][0]) / (stops[i][0] - stops[i-1][0]);
            const c = stops[i-1][1].map((v, j) => Math.round(v + (stops[i][1][j] - v) * t));
            return `rgb(${c.join(',')})`;
        }
    }
    return `rgb(${stops[stops.length-1][1].join(',')})`;
}

// 人口変動率→色 (連続グラデーション)
function _popChangeColor(rate) {
    if (rate == null) return '#546e7a';
    return _interpolateColor(rate, MESH_COLOR_SCALES.population.stops);
}


// =====================================================
// ===== 250mメッシュ統合レイヤー =====
// =====================================================

let meshLayers = {};       // metric -> L.layerGroup
let meshCache = {};        // metric -> { meshId -> featureData }
let meshRectCache = {};    // metric -> { meshId -> L.rectangle }
let meshLoadingLock = {};  // metric -> boolean (debounce)
const MESH_CACHE_MAX = 8000;  // メッシュキャッシュ上限

function _meshColor(metric, value) {
    if (metric === 'population') return _popChangeColor(value);
    const scale = MESH_COLOR_SCALES[metric];
    if (scale) return _interpolateColor(value, scale.stops);
    return '#78909c';
}

function _meshOpacity(metric, value, estimated) {
    const base = estimated ? 0.30 : 0.55;
    if (metric === 'population') {
        if (value == null) return 0.08;
        return Math.min(base, 0.12 + Math.abs(value) / 25);
    }
    if (metric === 'pop_density') {
        if (!value) return 0.06;
        return Math.min(0.6, 0.15 + value / 50000);
    }
    if (metric === 'facility') return Math.min(base + 0.05, 0.18 + value * 0.06);
    return base;
}

function _buildMeshRect(metric, f) {
    const p = f.properties, c = f.geometry.coordinates;
    const v = p.value;
    const est = p.estimated || false;
    const dLat = 0.00208 / 2;
    const dLng = 0.003125 / 2;

    let color = _meshColor(metric, v);
    let opacity = _meshOpacity(metric, v, est);

    // 用途地域メトリクス: 用途種別で色分け
    if (metric === 'zoning' && p.zoning) {
        const z = p.zoning;
        const zoningColors = {
            '第一種低層住居専用地域': '#81c784', '第二種低層住居専用地域': '#a5d6a7',
            '第一種中高層住居専用地域': '#4caf50', '第二種中高層住居専用地域': '#66bb6a',
            '第一種住居地域': '#26a69a', '第二種住居地域': '#4db6ac',
            '準住居地域': '#80cbc4',
            '近隣商業地域': '#ffb74d', '商業地域': '#ff9800',
            '準工業地域': '#90a4ae', '工業地域': '#78909c', '工業専用地域': '#546e7a',
        };
        color = zoningColors[z] || '#b0bec5';
        opacity = 0.45;
    }

    const bounds = [[c[1] - dLat, c[0] - dLng], [c[1] + dLat, c[0] + dLng]];
    const rect = L.rectangle(bounds, {
        color: '#fff', fillColor: color,
        fillOpacity: opacity, weight: 0.2, opacity: 0.1,
    });

    // 統合ツールチップ
    let tip = `<div style="min-width:150px">`;
    if (p.station) tip += `<b>${p.station}</b>`;
    if (p.station_km != null) tip += ` <span style="color:#90a4ae">${p.station_km}km</span>`;
    if (est) tip += ` <span style="color:#ef6c00;font-size:10px">推定</span>`;

    if (p.land_price) tip += `<br>💰 地価: ¥${p.land_price.toLocaleString()}/m²`;
    if (p.rent) tip += `<br>🏠 賃料: ¥${p.rent.toLocaleString()}/m²`;
    if (p.tx_price) tip += `<br>📊 取引: ¥${p.tx_price.toLocaleString()}/m² (${p.tx_count > 0 ? p.tx_count + '件' : '推定'})`;
    if (p.yield != null) {
        const yc = p.yield > 6 ? '#4caf50' : p.yield > 4 ? '#fbc02d' : '#ef5350';
        tip += `<br>📈 利回: <b style="color:${yc}">${p.yield}%</b>`;
    }
    if (p.pop) {
        tip += `<br>👥 ${Math.round(p.pop)}人`;
        if (p.pop_density) tip += ` (${p.pop_density.toLocaleString()}人/km²)`;
        if (p.pop_change != null) {
            const s = p.pop_change > 0 ? '+' : '';
            tip += ` <span style="color:${p.pop_change>0?'#4caf50':'#ef5350'}">${s}${p.pop_change}%</span>`;
        }
        if (p.pop_future) tip += `<br>将来: ${Math.round(p.pop_future)}人`;
    }
    if (p.fac_total) tip += `<br>🏫${p.schools||0} 🏥${p.medical||0} 👶${p.childcare||0}`;
    if (p.zoning) tip += `<br>📋 ${p.zoning}`;
    if (p.coverage && p.far) tip += ` (建${p.coverage}/容${p.far})`;
    if (p.front_road) tip += `<br>🛣️ ${p.front_road}`;
    if (p.road_width) tip += ` 幅員${(p.road_width/10).toFixed(1)}m`;
    tip += `</div>`;

    rect.bindTooltip(tip, { sticky: true });
    return rect;
}

async function loadMeshLayer(metric, forceRefresh) {
    // キャッシュ初期化
    if (!meshCache[metric]) meshCache[metric] = {};
    if (!meshRectCache[metric]) meshRectCache[metric] = {};

    // デバウンス: 高速パン中の連続呼び出しを抑制
    if (meshLoadingLock[metric]) return;
    meshLoadingLock[metric] = true;
    setTimeout(() => { meshLoadingLock[metric] = false; }, 150);

    try {
        // 広域bounds取得 (現ビューポート + マージン20%で先読み)
        const b = map.getBounds();
        const latPad = (b.getNorth() - b.getSouth()) * 0.2;
        const lngPad = (b.getEast() - b.getWest()) * 0.2;
        const south = b.getSouth() - latPad;
        const north = b.getNorth() + latPad;
        const west = b.getWest() - lngPad;
        const east = b.getEast() + lngPad;

        let url = `/api/layers/mesh-250m?south=${south}&west=${west}&north=${north}&east=${east}&metric=${metric}`;
        if (metric === 'rent' || metric === 'yield') {
            const layoutEl = document.getElementById('mesh-rent-layout');
            if (layoutEl && layoutEl.value) url += `&layout=${encodeURIComponent(layoutEl.value)}`;
        }
        const resp = await fetch(url);
        const data = await resp.json();

        // レイヤー未作成 or 強制更新時は作り直し
        if (!meshLayers[metric] || forceRefresh) {
            if (meshLayers[metric]) map.removeLayer(meshLayers[metric]);
            meshLayers[metric] = L.layerGroup();
            meshRectCache[metric] = {};
            meshLayers[metric].addTo(map);
        }

        let newCount = 0;
        (data.features || []).forEach(f => {
            const mid = f.properties.mesh_id;
            // 既存キャッシュにあればスキップ
            if (meshRectCache[metric][mid]) return;

            // キャッシュに追加 & レイヤーに描画
            meshCache[metric][mid] = f;
            const rect = _buildMeshRect(metric, f);
            meshRectCache[metric][mid] = rect;
            meshLayers[metric].addLayer(rect);
            newCount++;
        });

        // キャッシュ上限超過時: ビューポート外のメッシュを削除
        const cacheSize = Object.keys(meshRectCache[metric]).length;
        if (cacheSize > MESH_CACHE_MAX) {
            const bounds = map.getBounds();
            const toRemove = [];
            for (const [mid, rect] of Object.entries(meshRectCache[metric])) {
                if (!bounds.intersects(rect.getBounds())) toRemove.push(mid);
            }
            toRemove.slice(0, cacheSize - MESH_CACHE_MAX + 500).forEach(mid => {
                meshLayers[metric].removeLayer(meshRectCache[metric][mid]);
                delete meshRectCache[metric][mid];
                delete meshCache[metric][mid];
            });
        }

        console.log(`mesh ${metric}: +${newCount}件 (cache ${Object.keys(meshRectCache[metric]).length}件)`);
    } catch(e) { console.error(`mesh ${metric} error:`, e); }
}

// ビューポート変更時にアクティブなメッシュレイヤーを自動更新
function _setupMeshAutoReload() {
    let reloadTimer = null;
    map.on('moveend', () => {
        clearTimeout(reloadTimer);
        reloadTimer = setTimeout(() => {
            // メッシュレイヤー
            Object.keys(meshLayers).forEach(metric => {
                if (meshLayers[metric] && map.hasLayer(meshLayers[metric])) {
                    loadMeshLayer(metric);
                }
            });
            // 事実ベースレイヤーも自動リロード
            if (zoningPointsLayer && map.hasLayer(zoningPointsLayer)) loadZoningPoints();
            if (railwayLinesLayer && map.hasLayer(railwayLinesLayer)) loadRailwayLines();
            if (roadTypeLayer && map.hasLayer(roadTypeLayer)) loadRoadTypeLayer();
        }, 300);
    });
}


// =====================================================
// ===== 事実ベースレイヤー（用途地域ポイント・路線別駅） =====
// =====================================================

let zoningPointsLayer = null;
let railwayLinesLayer = null;

async function loadZoningPoints() {
    if (zoningPointsLayer) map.removeLayer(zoningPointsLayer);
    try {
        const resp = await fetch(`/api/layers/zoning-points?${_mapBoundsParams()}`);
        const data = await resp.json();
        zoningPointsLayer = L.geoJSON(data, {
            style: (feature) => {
                const p = feature.properties || {};
                return {
                    color: p.color || '#78909c',
                    fillColor: p.color || '#90a4ae',
                    weight: 1.1,
                    opacity: 0.9,
                    fillOpacity: 0.34,
                };
            },
            onEachFeature: (feature, layer) => {
                const p = feature.properties || {};
                let tip = `<div style="min-width:170px">`;
                tip += `<b style="color:${p.color || '#90a4ae'}">${p.zoning || '用途地域不明'}</b>`;
                if (p.coverage || p.far) tip += `<br>建蔽率 ${p.coverage || '-'} / 容積率 ${p.far || '-'}`;
                if (p.fire_prevention) tip += `<br>防火: ${p.fire_prevention}`;
                if (p.price) tip += `<br>地価: ¥${Number(p.price).toLocaleString()}/m²`;
                if (p.station) tip += `<br>最寄駅: ${p.station}`;
                if (p.place) tip += `<br>${p.place}`;
                tip += `</div>`;
                layer.bindTooltip(tip, {sticky: true});
            },
        });
        zoningPointsLayer.addTo(map);
        console.log(`用途地域領域: ${data.features?.length || 0}件`);
    } catch(e) { console.error('用途地域領域エラー:', e); }
}

async function loadRailwayLines() {
    if (railwayLinesLayer) map.removeLayer(railwayLinesLayer);
    railwayLinesLayer = L.layerGroup();
    try {
        const resp = await fetch(`/api/layers/railway-lines?${_mapBoundsParams()}`);
        const data = await resp.json();
        const zoom = map.getZoom();
        const r = zoom >= 14 ? 7 : zoom >= 12 ? 5 : 3;

        const segmentFeatures = (data.features || []).filter(f => ['segment', 'track'].includes(f.properties?.feature_kind));
        const stationFeatures = (data.features || []).filter(f => !f.properties?.feature_kind || f.properties?.feature_kind === 'station');
        segmentFeatures.forEach(f => {
            const p = f.properties || {};
            const coords = (f.geometry?.coordinates || []).map(c => [c[1], c[0]]);
            if (coords.length < 2) return;
            const polyline = L.polyline(coords, {
                color: p.color || '#90a4ae',
                weight: 3,
                opacity: 0.75,
            });
            polyline.bindTooltip(`<b>${p.line || '路線'}</b>`, {sticky: true});
            railwayLinesLayer.addLayer(polyline);
        });

        stationFeatures.forEach(f => {
            const p = f.properties || {};
            const c = f.geometry?.coordinates || [];
            if (c.length < 2) return;
            const lat = c[1], lng = c[0];
            const marker = L.circleMarker([lat, lng], {
                radius: p.is_transfer ? r + 2 : r,
                color: p.is_transfer ? '#ffd54f' : '#fff',
                fillColor: p.color || '#90a4ae',
                fillOpacity: 0.92,
                weight: p.is_transfer ? 2.2 : 1.2,
                opacity: 0.88,
            });
            const lines = (p.transfer_lines && p.transfer_lines.length)
                ? p.transfer_lines
                : (p.lines && p.lines.length ? p.lines : [p.line || '不明']);
            const lineHtml = lines.join(' / ');
            const transferTag = p.is_transfer ? `<br><span style="color:#ffd54f;">乗換 ${p.transfer_count || lines.length}路線</span>` : '';
            marker.bindTooltip(
                `<b>${p.name || '駅'}</b><br><span style="color:${p.color || '#90a4ae'}">${lineHtml}</span>${transferTag}`,
                {sticky: true}
            );
            railwayLinesLayer.addLayer(marker);
        });

        railwayLinesLayer.addTo(map);
        const lineCount = Object.keys(data._meta?.lines || {}).length;
        console.log(`路線表示: ${lineCount}路線, ${stationFeatures.length}駅, ${segmentFeatures.length}接続`);
    } catch(e) { console.error('路線表示エラー:', e); }
}


// =====================================================
// ===== 道路種別レイヤー（国土地理院タイル） =====
// =====================================================

let roadTypeLayer = null;
let roadLoadingLock = false;

async function loadRoadTypeLayer() {
    if (roadLoadingLock) return;
    roadLoadingLock = true;
    setTimeout(() => { roadLoadingLock = false; }, 500);

    if (!roadTypeLayer) {
        roadTypeLayer = L.layerGroup();
        roadTypeLayer.addTo(map);
    }

    // ズーム14以上のみ取得（広域は重い）
    if (map.getZoom() < 14) {
        console.log('道路種別: zoom < 14 のため非表示');
        roadTypeLayer.clearLayers();
        return;
    }

    try {
        const resp = await fetch(`/api/layers/roads?${_mapBoundsParams()}`);
        const data = await resp.json();
        roadTypeLayer.clearLayers();

        (data.features || []).forEach(f => {
            const p = f.properties;
            const coords = f.geometry.coordinates.map(c => [c[1], c[0]]);

            const polyline = L.polyline(coords, {
                color: p.color,
                weight: p.weight,
                opacity: 0.85,
            });

            let tip = `<div style="min-width:140px">`;
            tip += `<b style="color:${p.color}">${p.road_type}</b>`;
            if (p.legal) tip += `<br>📋 <b>${p.legal}</b>`;
            if (p.name) tip += `<br>${p.name}`;
            if (p.ref) tip += ` <span style="color:#90a4ae">(${p.ref})</span>`;
            if (p.width) tip += `<br>幅員: ${p.width}m`;
            else tip += `<br>幅員: 不明`;
            if (p.lanes) tip += ` (${p.lanes}車線)`;
            if (p.surface) tip += `<br>路面: ${p.surface}`;
            tip += `</div>`;
            polyline.bindTooltip(tip, {sticky: true});

            roadTypeLayer.addLayer(polyline);
        });

        console.log(`道路種別: ${data.features?.length || 0}本`);
    } catch(e) { console.error('道路種別エラー:', e); }
}

function toggleRoadTypeLayer(show) {
    if (!show) {
        if (roadTypeLayer) { map.removeLayer(roadTypeLayer); roadTypeLayer = null; }
        return;
    }
    loadRoadTypeLayer();
}


// =====================================================
// ===== 投資分析レイヤー描画関数（駅単位） =====
// =====================================================

async function loadIVLandPrice() {
    if (ivLandPriceLayer) map.removeLayer(ivLandPriceLayer);
    ivLandPriceLayer = L.layerGroup();
    try {
        const resp = await fetch(`/api/layers/land-price?${_mapBoundsParams()}`);
        const data = await resp.json();
        const zoom = map.getZoom();
        const r = zoom >= 14 ? 7 : zoom >= 12 ? 5 : 3;
        (data.features || []).forEach(f => {
            const p = f.properties, c = f.geometry.coordinates;
            const color = _priceColor(p.price);
            const m = L.circleMarker([c[1], c[0]], {
                radius: r, color: '#fff', fillColor: color,
                fillOpacity: 0.85, weight: 0.6, opacity: 0.4,
            });
            let tip = `<b>¥${p.price.toLocaleString()}/m²</b> <span style="color:#90a4ae">${p.type}</span>`;
            if (p.place) tip += `<br>${p.place}`;
            if (p.change_rate != null) {
                const s = p.change_rate > 0 ? '+' : '';
                tip += `<br>変動: <b style="color:${p.change_rate>0?'#4caf50':'#ef5350'}">${s}${p.change_rate}%</b>`;
            }
            m.bindTooltip(tip, {sticky:true});
            ivLandPriceLayer.addLayer(m);
        });
        ivLandPriceLayer.addTo(map);
        console.log(`地価: ${data.features?.length || 0}件`);
    } catch(e) { console.error('地価レイヤーエラー:', e); }
}

async function loadIVRent() {
    if (ivRentLayer) map.removeLayer(ivRentLayer);
    ivRentLayer = L.layerGroup();
    try {
        // ズームレベルに応じて粒度を切替
        const zoom = map.getZoom();
        const mode = zoom >= 14 ? 'detail' : zoom >= 12 ? 'station' : 'area';
        const resp = await fetch(`/api/layers/rent?${_mapBoundsParams()}&mode=${mode}`);
        const data = await resp.json();

        function _rentColor(r) {
            // 2000→青, 3000→緑, 4000→黄, 5000→橙, 6000+→赤
            return r > 6000 ? '#c62828' : r > 5000 ? '#e65100' :
                   r > 4000 ? '#f9a825' : r > 3000 ? '#43a047' :
                   r > 2500 ? '#1565c0' : '#5c6bc0';
        }

        (data.features || []).forEach(f => {
            const p = f.properties, c = f.geometry.coordinates;

            if (mode === 'detail') {
                // 個別物件ドット
                const rsqm = p.rent_sqm;
                const color = _rentColor(rsqm);
                const isTarget = p.area >= 15 && p.area <= 35;  // 投資用1K帯
                const m = L.circleMarker([c[1], c[0]], {
                    radius: isTarget ? 5 : 3,
                    color: isTarget ? '#fff' : color,
                    fillColor: color,
                    fillOpacity: isTarget ? 0.9 : 0.5,
                    weight: isTarget ? 1 : 0.3,
                });
                let tip = `<b>¥${p.rent.toLocaleString()}/月</b> (¥${rsqm.toLocaleString()}/m²)`;
                tip += `<br>${p.layout || '?'} ${p.area}m² ${p.structure || ''}`;
                if (p.age != null) tip += ` 築${p.age}年`;
                tip += `<br>${p.station}`;
                if (p.walk) tip += ` 徒歩${p.walk}分`;
                m.bindTooltip(tip, {sticky:true});
                ivRentLayer.addLayer(m);
            } else if (mode === 'station') {
                // 駅×徒歩帯
                const rent = p.avg_rent;
                const color = _rentColor(rent);
                const r = Math.max(8, Math.min(16, Math.sqrt(p.samples) * 2.5));
                const m = L.circleMarker([c[1], c[0]], {
                    radius: r, color: color, fillColor: color,
                    fillOpacity: 0.3, weight: 2, opacity: 0.8,
                });
                let tip = `<b>${p.station}</b> ${p.walk_band}`;
                tip += `<br>平均: <b>¥${rent.toLocaleString()}/m²</b>`;
                tip += `<br>幅: ¥${p.min_rent.toLocaleString()} ~ ¥${p.max_rent.toLocaleString()}`;
                tip += `<br>面積: ${p.avg_area}m² (${p.samples}件)`;
                m.bindTooltip(tip, {sticky:true});
                ivRentLayer.addLayer(m);
            } else {
                // 駅単位サマリー
                const rent = p.avg_rent;
                const color = _rentColor(rent);
                const m = L.circleMarker([c[1], c[0]], {
                    radius: 10, color: color, fillColor: color,
                    fillOpacity: 0.25, weight: 2, opacity: 0.8,
                });
                let tip = `<b>${p.station}</b>`;
                tip += `<br>賃料: <b>¥${rent.toLocaleString()}/m²</b> (${p.samples}件)`;
                m.bindTooltip(tip, {sticky:true});
                ivRentLayer.addLayer(m);
            }
        });
        ivRentLayer.addTo(map);
        console.log(`賃料(${mode}): ${data.features?.length || 0}件`);
    } catch(e) { console.error('賃料レイヤーエラー:', e); }
}

async function loadIVYield() {
    if (ivYieldLayer) map.removeLayer(ivYieldLayer);
    ivYieldLayer = L.layerGroup();
    try {
        const resp = await fetch(`/api/layers/yield-distortion?${_mapBoundsParams()}`);
        const data = await resp.json();
        (data.features || []).forEach(f => {
            const p = f.properties, c = f.geometry.coordinates;
            // 利回り: 高い=緑, 低い=赤
            const y = p.yield;
            const color = y > 8 ? '#1b5e20' : y > 6 ? '#43a047' :
                          y > 5 ? '#66bb6a' : y > 4 ? '#fbc02d' :
                          y > 3 ? '#ff9800' : '#e53935';
            const r = Math.max(8, Math.min(20, p.distortion * 1.5));
            const m = L.circleMarker([c[1], c[0]], {
                radius: r, color: '#fff', fillColor: color,
                fillOpacity: 0.6, weight: 1.5, opacity: 0.7,
            });
            let tip = `<b>${p.station}</b>`;
            if (p.line) tip += ` <span style="color:#90a4ae">${p.line}</span>`;
            tip += `<br>想定利回り: <b style="color:${color}">${y}%</b>`;
            tip += `<br>歪みスコア: <b>${p.distortion}</b>`;
            tip += `<br>地価¥${p.land_price.toLocaleString()}/m² 賃料¥${p.rent.toLocaleString()}/m²`;
            m.bindTooltip(tip, {sticky:true});
            ivYieldLayer.addLayer(m);
        });
        ivYieldLayer.addTo(map);
        console.log(`利回り: ${data.features?.length || 0}件`);
    } catch(e) { console.error('利回りレイヤーエラー:', e); }
}

async function loadIVTransactions() {
    if (ivTransLayer) map.removeLayer(ivTransLayer);
    ivTransLayer = L.layerGroup();
    try {
        // ズームに応じてメッシュ粒度 or 市区町村集計
        const zoom = map.getZoom();
        const url = zoom >= 12
            ? `/api/layers/mesh-transactions?${_mapBoundsParams()}`
            : `/api/layers/transactions?${_mapBoundsParams()}`;
        const resp = await fetch(url);
        const data = await resp.json();

        (data.features || []).forEach(f => {
            const p = f.properties, c = f.geometry.coordinates;
            const psm = p.avg_price_sqm || p.avg_price_sqm;
            const color = p.color || (psm > 1000000 ? '#880e4f' : psm > 500000 ? '#d32f2f' :
                          psm > 300000 ? '#ff6f00' : psm > 150000 ? '#fbc02d' : '#66bb6a');
            const r = Math.max(3, Math.min(12, Math.log10(Math.max(1, p.count)) * 3));
            const m = L.circleMarker([c[1], c[0]], {
                radius: r, color: '#fff', fillColor: color,
                fillOpacity: 0.6, weight: 0.5, opacity: 0.4,
            });
            const pType = p.type || p.property_type || '';
            let tip = `<b>¥${psm.toLocaleString()}/m²</b>`;
            if (pType) tip += ` <span style="color:#90a4ae">${pType}</span>`;
            if (p.city_name) tip += `<br>${p.city_name}`;
            if (p.min_price_sqm) tip += `<br>幅: ¥${p.min_price_sqm.toLocaleString()}~${p.max_price_sqm.toLocaleString()}`;
            const total = p.avg_total || p.avg_total_price;
            if (total) tip += `<br>平均総額: ¥${(total/10000).toFixed(0)}万円`;
            tip += `<br>${p.count.toLocaleString()}件`;
            m.bindTooltip(tip, {sticky:true});
            ivTransLayer.addLayer(m);
        });
        ivTransLayer.addTo(map);
        console.log(`取引(${zoom>=12?'mesh':'area'}): ${data.features?.length || 0}件`);
    } catch(e) { console.error('取引レイヤーエラー:', e); }
}

async function loadIVPopulation() {
    if (ivPopLayer) map.removeLayer(ivPopLayer);
    ivPopLayer = L.layerGroup();
    try {
        const resp = await fetch(`/api/layers/population?${_mapBoundsParams()}`);
        const data = await resp.json();
        (data.features || []).forEach(f => {
            const p = f.properties;
            const rate = p.change_rate;
            const color = _popChangeColor(rate);
            const opacity = rate == null ? 0.1 : Math.min(0.5, 0.12 + Math.abs(rate) / 35);
            if (f.geometry && f.geometry.type === 'Polygon') {
                const coords = f.geometry.coordinates[0].map(c => [c[1], c[0]]);
                const poly = L.polygon(coords, {
                    color: '#fff', fillColor: color,
                    fillOpacity: opacity, weight: 0.2, opacity: 0.15,
                });
                const sign = rate > 0 ? '+' : '';
                const pC = p.pop_current, pF = p.pop_future;
                poly.bindTooltip(`
                    <div style="min-width:110px">
                    <b>${rate != null ? sign + rate + '%' : '?'}</b> 人口増減<br>
                    現在: ${pC ? Math.round(pC).toLocaleString() : '?'}人<br>
                    将来: ${pF ? Math.round(pF).toLocaleString() : '?'}人
                    </div>
                `, {sticky:true});
                ivPopLayer.addLayer(poly);
            }
        });
        ivPopLayer.addTo(map);
        console.log(`人口: ${data.features?.length || 0}件`);
    } catch(e) { console.error('人口レイヤーエラー:', e); }
}

async function loadIVFacilities() {
    if (ivFacLayer) map.removeLayer(ivFacLayer);
    ivFacLayer = L.layerGroup();
    try {
        const resp = await fetch(`/api/layers/facilities?${_mapBoundsParams()}`);
        const data = await resp.json();
        const icons = {
            school:    '🏫', medical: '🏥', childcare: '👶', shelter: '🏠',
        };
        const labels = {
            school: '学校', medical: '医療', childcare: '保育', shelter: '避難',
        };
        (data.features || []).forEach(f => {
            const p = f.properties, c = f.geometry.coordinates;
            const cat = p.category || 'school';
            const icon = L.divIcon({
                className: '',
                html: `<div style="font-size:13px;line-height:1;text-shadow:0 1px 2px rgba(0,0,0,0.5)">${icons[cat]||'📍'}</div>`,
                iconSize: [16, 16], iconAnchor: [8, 8],
            });
            const m = L.marker([c[1], c[0]], { icon: icon });
            m.bindTooltip(`<b>${p.name||'施設'}</b><br>${labels[cat]||cat}`, {sticky:true});
            ivFacLayer.addLayer(m);
        });
        ivFacLayer.addTo(map);
        console.log(`施設: ${data.features?.length || 0}件`);
    } catch(e) { console.error('施設レイヤーエラー:', e); }
}


async function loadIVStationPower() {
    if (ivStationPowerLayer) map.removeLayer(ivStationPowerLayer);
    ivStationPowerLayer = L.layerGroup();
    try {
        const resp = await fetch(`/api/layers/station-power?${_mapBoundsParams()}`);
        const data = await resp.json();
        const meta = data._meta || {};
        const maxScore = meta.score_range ? meta.score_range[1] : 80;

        (data.features || []).forEach(f => {
            const p = f.properties, c = f.geometry.coordinates;
            const score = p.score;
            // スコア→色: 高=赤～橙, 中=黄, 低=青
            const ratio = Math.min(1, score / Math.max(maxScore, 1));
            const hue = (1 - ratio) * 240; // 240(青)→0(赤)
            const color = `hsl(${hue}, 80%, 50%)`;
            // 圏域サイズ: スコアに比例（150m～400m）
            const radius = 150 + ratio * 250;

            const circle = L.circle([c[1], c[0]], {
                radius: radius,
                color: color,
                fillColor: color,
                fillOpacity: 0.25 + ratio * 0.2,
                weight: 1,
                opacity: 0.5,
            });

            let tip = `<div style="min-width:170px">`;
            tip += `<b style="font-size:1.2em">${p.station}</b>`;
            tip += ` <b style="color:${color};font-size:1.1em">${score}</b><span style="color:#90a4ae">/100</span>`;
            tip += `<br><span style="color:#546e7a;font-size:0.8em">───── 内訳 ─────</span>`;
            tip += `<br>🚉 ターミナル: ${p.terminal_raw} <span style="color:#78909c">→${p.terminal_name} ${p.terminal_dist}km</span>`;
            tip += `<br>🛤️ 路線数: ${p.lines_raw} <span style="color:#78909c">(${p.line_count}路線)</span>`;
            const lpColor = p.land_price > 0 ? '#cfd8dc' : '#ef5350';
            tip += `<br>💰 地価: <span style="color:${lpColor}">${p.lp_raw}</span> <span style="color:#78909c">${p.land_price > 0 ? '¥'+p.land_price.toLocaleString()+'/m²' : '未取得'}</span>`;
            const rentColor = p.rent > 0 ? '#cfd8dc' : '#ef5350';
            tip += `<br>🏠 賃料: <span style="color:${rentColor}">${p.rent_raw}</span> <span style="color:#78909c">${p.rent > 0 ? '¥'+p.rent.toLocaleString()+'/m²' : '未取得'}</span>`;
            const txColor = p.tx_count > 0 ? '#cfd8dc' : '#ef5350';
            tip += `<br>📊 取引: <span style="color:${txColor}">${p.tx_raw}</span> <span style="color:#78909c">${p.tx_count > 0 ? p.tx_count.toLocaleString()+'件' : '未取得'}</span>`;
            tip += `<br>👥 人口: ${p.pop_raw}`;
            if (p.missing && p.missing.length > 0) {
                tip += `<br><span style="color:#ef5350;font-size:0.8em">⚠ 未取得: ${p.missing.join(', ')}</span>`;
            }
            tip += `</div>`;

            circle.bindTooltip(tip, { sticky: true });
            ivStationPowerLayer.addLayer(circle);
        });

        ivStationPowerLayer.addTo(map);
        console.log(`駅力: ${data.features?.length || 0}駅 (score: ${meta.score_range?.[0]}~${meta.score_range?.[1]})`);
    } catch(e) { console.error('駅力レイヤーエラー:', e); }
}


async function loadIVMesh250() {
    if (ivMesh250Layer) map.removeLayer(ivMesh250Layer);
    ivMesh250Layer = L.layerGroup();
    try {
        // 表示メトリクスはズームで自動選択: 遠い=地価、近い=利回り
        const zoom = map.getZoom();
        const metric = zoom >= 14 ? 'yield' : 'land_price';
        const resp = await fetch(`/api/layers/mesh-250m?${_mapBoundsParams()}&metric=${metric}`);
        const data = await resp.json();

        (data.features || []).forEach(f => {
            const p = f.properties, c = f.geometry.coordinates;
            const v = p.value;

            let color;
            if (metric === 'yield') {
                color = v > 8 ? '#1b5e20' : v > 6 ? '#43a047' : v > 5 ? '#66bb6a' :
                        v > 4 ? '#fbc02d' : v > 3 ? '#ff9800' : '#e53935';
            } else {
                color = _priceColor(v);
            }

            // 250mメッシュ矩形（約250m四方）
            const dLat = 0.00208 / 2;  // 250mメッシュ高さの半分
            const dLng = 0.003125 / 2; // 250mメッシュ幅の半分
            const bounds = [
                [c[1] - dLat, c[0] - dLng],
                [c[1] + dLat, c[0] + dLng],
            ];
            const rect = L.rectangle(bounds, {
                color: '#fff', fillColor: color,
                fillOpacity: 0.45, weight: 0.3, opacity: 0.15,
            });

            let tip = `<div style="min-width:160px">`;
            tip += `<b style="font-size:0.75em;color:#78909c">${p.mesh_id}</b>`;
            if (p.station) tip += ` ${p.station}`;
            if (p.station_km) tip += ` <span style="color:#90a4ae">${p.station_km}km</span>`;

            if (p.land_price) tip += `<br>💰 地価: ¥${p.land_price.toLocaleString()}/m² <span style="color:#78909c">(${p.lp_count}件)</span>`;
            if (p.rent) tip += `<br>🏠 賃料: ¥${p.rent.toLocaleString()}/m² <span style="color:#78909c">(${p.rent_count}件)</span>`;
            if (p.tx_price) tip += `<br>📊 取引: ¥${p.tx_price.toLocaleString()}/m² <span style="color:#78909c">(${p.tx_count}件)</span>`;
            if (p.yield) {
                const yColor = p.yield > 6 ? '#4caf50' : p.yield > 4 ? '#fbc02d' : '#ef5350';
                tip += `<br>📈 利回り: <b style="color:${yColor}">${p.yield}%</b>`;
            }
            if (p.pop) tip += `<br>👥 人口: ${Math.round(p.pop)}人`;
            if (p.pop_change != null) {
                const sign = p.pop_change > 0 ? '+' : '';
                tip += ` (${sign}${p.pop_change}%)`;
            }
            if (p.schools || p.medical) tip += `<br>🏫${p.schools} 🏥${p.medical}`;
            tip += `</div>`;

            rect.bindTooltip(tip, { sticky: true });
            ivMesh250Layer.addLayer(rect);
        });
        ivMesh250Layer.addTo(map);
        console.log(`250m mesh(${metric}): ${data.features?.length || 0}件`);
    } catch(e) { console.error('250mメッシュエラー:', e); }
}


// ===== 人口動態レイヤー（旧互換） =====

async function loadPopulationLayer(forceFetch) {
    if (populationLayer) map.removeLayer(populationLayer);
    populationLayer = L.layerGroup();

    const force = forceFetch ? '&force_fetch=true' : '';

    try {
        const resp = await fetch(`/api/reinfolib/population?${_mapBoundsParams()}&zoom=13${force}`);
        const data = await resp.json();

        if (data.geojson && data.geojson.features && data.geojson.features.length > 0) {
            data.geojson.features.forEach(f => {
                const props = f.properties;
                const rate = props.change_rate;
                const color = _popChangeColor(rate);
                const opacity = rate == null ? 0.12 : Math.min(0.55, 0.15 + Math.abs(rate) / 40);

                if (f.geometry && f.geometry.type === 'Polygon') {
                    const coords = f.geometry.coordinates[0].map(c => [c[1], c[0]]);
                    const poly = L.polygon(coords, {
                        color: '#ffffff', fillColor: color,
                        fillOpacity: opacity, weight: 0.3, opacity: 0.2,
                    });
                    const sign = rate > 0 ? '+' : '';
                    const popC = props.pop_current;
                    const popF = props.pop_future;
                    poly.bindTooltip(`
                        <div style="min-width:120px">
                        <b style="font-size:1.05em">${rate != null ? sign + rate + '%' : '?'}</b>
                        <span style="color:#90a4ae;font-size:0.8em"> 人口増減</span><br>
                        <span style="color:#b0bec5;font-size:0.8em">現在:</span> ${popC ? Math.round(popC).toLocaleString() : '?'}人<br>
                        <span style="color:#b0bec5;font-size:0.8em">将来:</span> ${popF ? Math.round(popF).toLocaleString() : '?'}人
                        </div>
                    `, { sticky: true, className: 'pop-tooltip' });
                    populationLayer.addLayer(poly);
                }
            });
            populationLayer.addTo(map);
        }
    } catch (e) {
        console.warn('人口メッシュ取得失敗:', e);
    }
}


// ===== 周辺施設レイヤー =====

async function loadFacilityLayer(types) {
    if (facilityLayer) map.removeLayer(facilityLayer);
    facilityLayer = L.layerGroup();

    types = types || 'school,medical,childcare';

    try {
        const resp = await fetch(`/api/reinfolib/facilities?${_mapBoundsParams()}&types=${types}&zoom=14`);
        const data = await resp.json();
        if (!data.geojson || !data.geojson.features) return;

        const icons = {
            school:    { bg: '#1565c0', border: '#90caf9', icon: '🏫', label: '学校' },
            medical:   { bg: '#c62828', border: '#ef9a9a', icon: '🏥', label: '医療' },
            childcare: { bg: '#e65100', border: '#ffcc80', icon: '👶', label: '保育' },
            shelter:   { bg: '#2e7d32', border: '#a5d6a7', icon: '🏠', label: '避難' },
        };

        data.geojson.features.forEach(f => {
            const props = f.properties;
            const coords = f.geometry.coordinates;
            const cat = props.category || 'school';
            const cfg = icons[cat] || icons.school;

            const markerIcon = L.divIcon({
                className: '',
                html: `<div style="
                    font-size:14px;line-height:1;
                    text-shadow:0 1px 2px rgba(0,0,0,0.6);
                    filter:drop-shadow(0 1px 1px rgba(0,0,0,0.3));
                ">${cfg.icon}</div>`,
                iconSize: [18, 18],
                iconAnchor: [9, 9],
            });

            const marker = L.marker([coords[1], coords[0]], { icon: markerIcon });
            let tip = `<b>${props.name || '施設'}</b><br><span style="color:${cfg.bg}">${cfg.label}</span>`;
            if (props.address) tip += `<br><span style="color:#90a4ae;font-size:0.85em">${props.address}</span>`;
            marker.bindTooltip(tip, { sticky: true });
            facilityLayer.addLayer(marker);
        });

        facilityLayer.addTo(map);
        console.log(`施設レイヤー: ${data.count}件 (${data.source || 'unknown'})`);
    } catch (e) {
        console.error('施設レイヤー取得エラー:', e);
    }
}


// ===== 公示地価レイヤー =====

async function loadOfficialLandPriceLayer(forceFetch) {
    if (officialLandPriceLayer) map.removeLayer(officialLandPriceLayer);
    officialLandPriceLayer = L.layerGroup();

    const force = forceFetch ? '&force_fetch=true' : '';

    try {
        const resp = await fetch(`/api/reinfolib/land-prices?${_mapBoundsParams()}&zoom=13${force}`);
        const data = await resp.json();
        if (!data.geojson || !data.geojson.features) return;

        const zoom = map.getZoom();
        const baseRadius = zoom >= 14 ? 8 : zoom >= 12 ? 6 : 4;

        data.geojson.features.forEach(f => {
            const props = f.properties;
            const coords = f.geometry.coordinates;
            const priceNum = props.price_num || 0;
            if (priceNum <= 0) return;

            const color = _priceColor(priceNum);

            const marker = L.circleMarker([coords[1], coords[0]], {
                radius: baseRadius,
                color: '#ffffff',
                fillColor: color,
                fillOpacity: 0.85,
                weight: 0.8,
                opacity: 0.5,
            });

            const priceLabel = `¥${priceNum.toLocaleString()}/m²`;
            const typeLabel = props.land_price_type === 0 ? '公示' : props.land_price_type === 1 ? '基準' : '地価';
            let tip = `<div style="min-width:130px">`;
            tip += `<b style="font-size:1.1em">${priceLabel}</b>`;
            tip += ` <span style="color:#90a4ae;font-size:0.8em">${typeLabel}</span>`;
            if (props.place_name) tip += `<br>${props.place_name}`;
            if (props.change_rate != null) {
                const cr = props.change_rate;
                const sign = cr > 0 ? '+' : '';
                const crColor = cr > 0 ? '#4caf50' : cr < 0 ? '#ef5350' : '#90a4ae';
                tip += `<br>変動率: <b style="color:${crColor}">${sign}${cr}%</b>`;
            }
            if (props.zoning && props.zoning !== '住宅地') tip += `<br><span style="color:#90a4ae">${props.zoning}</span>`;
            tip += `</div>`;

            marker.bindTooltip(tip, { sticky: true });
            officialLandPriceLayer.addLayer(marker);
        });

        officialLandPriceLayer.addTo(map);
        console.log(`公示地価: ${data.count}件 (${data.source || 'unknown'})`);
    } catch (e) {
        console.error('公示地価レイヤー取得エラー:', e);
    }
}


// ===== 凡例コントロール =====

function addLegendControl() {
    const legend = L.control({ position: 'bottomright' });
    legend.onAdd = function() {
        const div = L.DomUtil.create('div', 'map-legend');
        div.id = 'map-legend';
        div.style.cssText = 'background:rgba(10,20,40,0.9);padding:8px 10px;border-radius:6px;color:#cfd8dc;font-size:0.7rem;line-height:1.6;display:none;';
        return div;
    };
    legend.addTo(map);
}

function _formatLegendValue(metric, v) {
    if (metric === 'yield' || metric === 'population') return `${Number(v).toFixed(1)}%`;
    if (metric === 'land_price' || metric === 'tx_price' || metric === 'rent' || metric === 'pop_density') {
        return Number(v).toLocaleString();
    }
    return `${v}`;
}

function _renderScaleLegend(metric) {
    const scale = MESH_COLOR_SCALES[metric];
    if (!scale) return '';
    const stops = scale.stops || [];
    if (!stops.length) return '';

    const minV = stops[0][0];
    const maxV = stops[stops.length - 1][0];
    const range = Math.max(1e-9, maxV - minV);
    const gradientStops = stops
        .map(([v, rgb]) => {
            const pct = ((v - minV) / range) * 100;
            return `rgb(${rgb.join(',')}) ${pct.toFixed(2)}%`;
        })
        .join(', ');
    const tickIdx = [0, 2, 4, 6, 8, stops.length - 1].filter((v, i, arr) => arr.indexOf(v) === i);
    const tickHtml = tickIdx
        .map(idx => `<span style="flex:1;text-align:${idx===0?'left':(idx===stops.length-1?'right':'center')};">${_formatLegendValue(metric, stops[idx][0])}</span>`)
        .join('');

    return `
        <div style="margin-bottom:6px;">
            <div><b>${scale.label}</b> <span style="color:#90a4ae;">(${scale.unit})</span></div>
            <div style="height:8px;border-radius:5px;background:linear-gradient(to right, ${gradientStops});margin:2px 0 3px 0;border:1px solid rgba(255,255,255,0.2);"></div>
            <div style="display:flex;gap:0;font-size:0.62rem;color:#b0bec5;line-height:1.2;">${tickHtml}</div>
        </div>
    `;
}

function updateLegend() {
    const div = document.getElementById('map-legend');
    if (!div) return;
    let html = '';
    let show = false;

    function _dot(c) { return `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${c};margin:0 2px;"></span>`; }
    function _sq(c)  { return `<span style="display:inline-block;width:9px;height:9px;background:${c};margin:0 2px;"></span>`; }

    // メッシュレイヤー凡例（色定義と完全同期）
    ['land_price', 'rent', 'tx_price', 'yield', 'population', 'pop_density', 'facility'].forEach(metric => {
        if (meshLayers[metric] && map.hasLayer(meshLayers[metric])) {
            show = true;
            html += _renderScaleLegend(metric);
        }
    });
    if (meshLayers['zoning'] && map.hasLayer(meshLayers['zoning'])) {
        show = true;
        html += '<b>用途地域</b><br>';
        html += `${_sq('#81c784')}低層住専 ${_sq('#4caf50')}中高層 ${_sq('#26a69a')}住居 ${_sq('#ff9800')}商業 ${_sq('#78909c')}工業<br>`;
    }
    // 駅レイヤー
    if (ivStationPowerLayer && map.hasLayer(ivStationPowerLayer)) {
        show = true;
        html += '<b>駅力</b> ';
        html += `${_dot('hsl(0,80%,50%)')}高 ${_dot('hsl(60,80%,50%)')}中 ${_dot('hsl(120,80%,50%)')}中低 ${_dot('hsl(240,80%,50%)')}低<br>`;
    }
    if (ivYieldLayer && map.hasLayer(ivYieldLayer)) {
        show = true;
        html += '<b>駅別利回り</b><br>';
    }
    // 道路種別
    if (roadTypeLayer && map.hasLayer(roadTypeLayer)) {
        show = true;
        html += '<b>道路種別(建築基準法)</b><br>';
        function _ln(c,w) { return `<span style="display:inline-block;width:18px;height:${w}px;background:${c};margin:0 2px;vertical-align:middle;border:${c==='#fdd835'||c==='#b0bec5'?'0.5px solid #666':'none'}"></span>`; }
        html += `${_ln('#e53935',4)}1項1号(国道) ${_ln('#fb8c00',3)}1項1号(都道府県道) ${_ln('#fdd835',2)}1項1号(市道)<br>`;
        html += `${_ln('#b0bec5',2)}1項1号(生活道路4m+) ${_ln('#ff6f00',2)}2項道路(4m未満) ${_ln('#7e57c2',1)}1項5号(位置指定) ${_ln('#d32f2f',1)}建築不可<br>`;
    }

    div.innerHTML = html;
    div.style.display = show ? 'block' : 'none';
}

function _hookLegendUpdate() {
    // 新レイヤーIDと旧レイヤーID両方フック
    ['layer-mesh-landprice','layer-mesh-rent','layer-mesh-tx','layer-mesh-yield',
     'layer-mesh-pop','layer-mesh-facility','layer-mesh-zoning',
     'layer-iv-stationpower','layer-iv-yield'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => setTimeout(updateLegend, 500));
    });
}


// ===== Google Earth連携 =====

function openGoogleEarth() {
    const kmlUrl = window.location.origin + '/api/export/kml';
    const msg = `Google Earth連携方法:\n\n` +
        `1. 「KMLダウンロード」ボタンでKMLファイルを保存\n` +
        `2. Google Earth Pro（デスクトップ版）でファイルを開く\n` +
        `   または\n` +
        `3. earth.google.com/web にアクセス\n` +
        `4. メニュー → プロジェクト → KMLファイルをインポート\n\n` +
        `KML URL: ${kmlUrl}`;
    alert(msg);
    window.open(kmlUrl, '_blank');
}

