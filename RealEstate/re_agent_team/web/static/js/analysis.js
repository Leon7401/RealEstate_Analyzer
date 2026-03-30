/**
 * 駅単位エリア歪み分析 - フロントエンドJS
 */

let map;
let distortionLayer = null;
let landPriceDetailLayer = null;
let currentDistortionData = null;

function initAnalysisMap(center, zoom) {
    map = L.map('map').setView(center, zoom);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 19,
    }).addTo(map);

    document.getElementById('btn-analyze').addEventListener('click', runAnalysis);
    document.getElementById('btn-batch').addEventListener('click', runBatch);
    document.getElementById('view-mode').addEventListener('change', updateVisualization);

    // 駅検索
    const searchInput = document.getElementById('station-search');
    if (searchInput) {
        searchInput.addEventListener('input', filterRanking);
    }

    loadDbStats();
    runAnalysis();
}

// ===== DB統計 =====

async function loadDbStats() {
    try {
        const resp = await fetch('/api/db/stats');
        const stats = await resp.json();
        document.getElementById('db-stats').innerHTML = `
            <div class="detail-grid">
                <span class="label">駅マスタ</span><span class="value">${(stats.stations || 0).toLocaleString()}駅</span>
                <span class="label">地価データ</span><span class="value">${stats.land_prices.toLocaleString()}件</span>
                <span class="label">取引データ</span><span class="value">${stats.transactions.toLocaleString()}件</span>
                <span class="label">賃料データ</span><span class="value">${stats.rental_comps.toLocaleString()}件</span>
                <span class="label">物件データ</span><span class="value">${stats.properties.toLocaleString()}件</span>
                <span class="label">駅分析</span><span class="value">${(stats.station_metrics || 0).toLocaleString()}駅</span>
            </div>
        `;
    } catch (e) {
        document.getElementById('db-stats').textContent = 'エラー';
    }
}

// ===== 歪み分析 =====

async function runAnalysis() {
    const btn = document.getElementById('btn-analyze');
    const pref = document.getElementById('pref-select').value;
    btn.disabled = true;
    btn.textContent = '分析中...';

    try {
        const resp = await fetch(`/api/analysis/distortion?prefecture_code=${pref}`);
        const data = await resp.json();
        currentDistortionData = data;

        renderDistortionMap(data.geojson);
        renderRanking(data.ranking);

        document.getElementById('ranking-panel').style.display = 'block';
        document.getElementById('ranking-subtitle').textContent = `(${data.count}駅)`;
    } catch (e) {
        console.error('分析エラー:', e);
    } finally {
        btn.disabled = false;
        btn.textContent = '分析実行';
    }
}

function renderDistortionMap(geojson) {
    if (distortionLayer) map.removeLayer(distortionLayer);

    const mode = document.getElementById('view-mode').value;

    distortionLayer = L.geoJSON(geojson, {
        pointToLayer: (feature, latlng) => {
            const p = feature.properties;
            const radius = p.radius || 12;
            const color = getColorForMode(p, mode);

            const marker = L.circleMarker(latlng, {
                radius: radius,
                fillColor: color,
                color: 'rgba(255,255,255,0.6)',
                weight: 1.5,
                fillOpacity: 0.7,
            });

            // ラベル（ズームレベルに応じて表示）
            const label = L.marker(latlng, {
                icon: L.divIcon({
                    className: 'area-bubble-label',
                    html: `<div class="bubble-name">${p.station_name}</div>
                           <div class="bubble-score">${getValueForMode(p, mode)}</div>`,
                    iconSize: [56, 28],
                    iconAnchor: [28, 14],
                }),
            });

            return L.layerGroup([marker, label]);
        },
        onEachFeature: (feature, layer) => {
            const p = feature.properties;
            const compClass = getComparisonClass(p.comparison);

            layer.bindPopup(`
                <div class="analysis-popup">
                    <h4>${p.station_name} <small style="color:#999">${p.line_name || ''}</small></h4>
                    <div style="font-size:0.75rem;color:#999;margin-bottom:4px;">${p.city_name}</div>
                    <div class="metrics-grid">
                        <span class="metric-label">歪みスコア</span>
                        <span class="metric-value">${p.distortion_score}/100 (#${p.distortion_rank})</span>
                        <span class="metric-label">暗黙Cap Rate</span>
                        <span class="metric-value">${p.implied_cap_rate.toFixed(1)}%</span>
                        <span class="metric-label">平均地価</span>
                        <span class="metric-value">&yen;${Math.round(p.avg_land_price).toLocaleString()}/m2</span>
                        <span class="metric-label">地価順位</span>
                        <span class="metric-value">#${p.land_price_rank} (安い順)</span>
                        <span class="metric-label">平均賃料</span>
                        <span class="metric-value">&yen;${Math.round(p.avg_rent).toLocaleString()}/m2/月</span>
                        <span class="metric-label">賃料順位</span>
                        <span class="metric-value">#${p.rent_rank} (高い順)</span>
                        ${p.land_change_rate != null ? `
                        <span class="metric-label">地価変動</span>
                        <span class="metric-value">${(p.land_change_rate * 100).toFixed(1)}%</span>
                        ` : ''}
                        <span class="metric-label">データ品質</span>
                        <span class="metric-value">${p.data_quality}</span>
                    </div>
                    <div class="comparison-badge ${compClass}">${p.comparison}</div>
                </div>
            `, { maxWidth: 300 });

            layer.on('click', () => loadStationDetail(p.station_id));
        },
    });

    distortionLayer.addTo(map);
}

function getColorForMode(p, mode) {
    switch (mode) {
        case 'distortion':
            return p.color;
        case 'cap_rate':
            if (p.implied_cap_rate >= 8) return '#1a9641';
            if (p.implied_cap_rate >= 6) return '#66bb6a';
            if (p.implied_cap_rate >= 4) return '#fdd835';
            if (p.implied_cap_rate >= 2) return '#ff9800';
            return '#e53935';
        case 'land_price':
            if (p.avg_land_price <= 400000) return '#1a9641';
            if (p.avg_land_price <= 700000) return '#66bb6a';
            if (p.avg_land_price <= 1200000) return '#fdd835';
            if (p.avg_land_price <= 2500000) return '#ff9800';
            return '#e53935';
        case 'rent':
            if (p.avg_rent >= 5000) return '#1a9641';
            if (p.avg_rent >= 4000) return '#66bb6a';
            if (p.avg_rent >= 3000) return '#fdd835';
            if (p.avg_rent >= 2500) return '#ff9800';
            return '#e53935';
        default:
            return p.color;
    }
}

function getValueForMode(p, mode) {
    switch (mode) {
        case 'distortion': return p.distortion_score.toFixed(0);
        case 'cap_rate': return p.implied_cap_rate.toFixed(1) + '%';
        case 'land_price': return (p.avg_land_price / 10000).toFixed(0) + '万';
        case 'rent': return '&yen;' + Math.round(p.avg_rent).toLocaleString();
        default: return p.distortion_score.toFixed(0);
    }
}

function getComparisonClass(comparison) {
    const classMap = {
        '割安': 'comp-cheap',
        'やや割安': 'comp-slightly-cheap',
        '適正': 'comp-fair',
        'やや割高': 'comp-slightly-expensive',
        '割高': 'comp-expensive',
    };
    return classMap[comparison] || 'comp-fair';
}

function updateVisualization() {
    if (currentDistortionData) {
        renderDistortionMap(currentDistortionData.geojson);
    }
}

// ===== ランキング =====

function renderRanking(ranking) {
    let html = '';
    ranking.forEach((r, i) => {
        const color = r.distortion_score >= 65 ? '#1a9641' :
                      r.distortion_score >= 55 ? '#66bb6a' :
                      r.distortion_score >= 45 ? '#fdd835' :
                      r.distortion_score >= 35 ? '#ff9800' : '#e53935';
        const compClass = getComparisonClass(r.nearby_comparison);

        html += `
            <div class="distortion-item" data-name="${r.station_name}" data-line="${r.line_name || ''}"
                 onclick="focusStation('${r.station_id}', ${r.center_lat}, ${r.center_lng})">
                <span class="distortion-rank">${i + 1}</span>
                <div class="distortion-bubble" style="background:${color}">${r.distortion_score.toFixed(0)}</div>
                <div class="distortion-info">
                    <div class="distortion-name">${r.station_name} <small style="color:#78909c">${r.line_name || ''}</small></div>
                    <div class="distortion-stats">
                        <span>地価: &yen;${(r.avg_land_price / 10000).toFixed(0)}万/m2</span>
                        <span>賃料: &yen;${Math.round(r.avg_rent)}/m2</span>
                        <span>Cap: ${(r.implied_cap_rate * 100).toFixed(1)}%</span>
                        <span>${r.city_name}</span>
                    </div>
                </div>
                <span class="distortion-comparison ${compClass}">${r.nearby_comparison}</span>
            </div>`;
    });
    document.getElementById('ranking-list').innerHTML = html;
}

function filterRanking() {
    const query = document.getElementById('station-search').value.toLowerCase();
    const items = document.querySelectorAll('.distortion-item');
    items.forEach(item => {
        const name = (item.dataset.name || '').toLowerCase();
        const line = (item.dataset.line || '').toLowerCase();
        item.style.display = (name.includes(query) || line.includes(query)) ? '' : 'none';
    });
}

function focusStation(stationId, lat, lng) {
    if (lat && lng) {
        map.setView([lat, lng], 15);
    }
    loadStationDetail(stationId);
}

// ===== 駅詳細 =====

async function loadStationDetail(stationId) {
    const panel = document.getElementById('detail-panel');
    panel.style.display = 'block';
    document.getElementById('detail-content').innerHTML = '<div class="loading">読込中</div>';

    try {
        const resp = await fetch(`/api/analysis/station-detail/${stationId}`);
        const data = await resp.json();
        renderStationDetail(data);

        if (data.land_price_points && data.land_price_points.length > 0) {
            showLandPricePoints(data.land_price_points);
        }
    } catch (e) {
        document.getElementById('detail-content').textContent = 'エラー';
    }
}

function renderStationDetail(data) {
    document.getElementById('detail-title').textContent =
        `${data.station_name} ${data.line_name ? '(' + data.line_name + ')' : ''}`;

    let html = `
        <div class="detail-section">
            <h4>データ概要 <small style="color:#78909c">${data.city_name || ''}</small></h4>
            <div class="detail-grid">
                <span class="label">地価ポイント</span><span class="value">${data.land_price_count}件</span>
                <span class="label">賃料事例</span><span class="value">${data.rental_count}件</span>
                <span class="label">取引事例</span><span class="value">${data.transaction_count}件</span>
            </div>
        </div>
    `;

    // 構造別賃料テーブル
    if (data.structure_summary && data.structure_summary.length > 0) {
        html += `
            <div class="detail-section">
                <h4>構造別平均賃料</h4>
                <table class="station-table">
                    <tr><th>構造</th><th>平均m2賃料</th><th>件数</th></tr>
        `;
        data.structure_summary.forEach(s => {
            html += `<tr>
                <td>${s.structure}</td>
                <td>&yen;${Math.round(s.avg_rent).toLocaleString()}</td>
                <td>${s.count}</td>
            </tr>`;
        });
        html += '</table></div>';
    }

    // 地価ポイント上位
    if (data.land_price_points && data.land_price_points.length > 0) {
        const sorted = [...data.land_price_points].sort((a, b) => a.price - b.price);
        const top5cheap = sorted.slice(0, 5);
        const top5expensive = sorted.slice(-5).reverse();

        html += `
            <div class="detail-section">
                <h4>地価ポイント（安い順 TOP5）</h4>
                <table class="station-table">
                    <tr><th>住所</th><th>m2単価</th><th>用途</th></tr>
        `;
        top5cheap.forEach(p => {
            html += `<tr>
                <td style="max-width:130px;overflow:hidden;text-overflow:ellipsis">${p.address}</td>
                <td>&yen;${p.price.toLocaleString()}</td>
                <td>${p.use_zone || '-'}</td>
            </tr>`;
        });
        html += '</table></div>';

        if (top5expensive.length > 0 && top5expensive[0].price !== top5cheap[0].price) {
            html += `
                <div class="detail-section">
                    <h4>地価ポイント（高い順 TOP5）</h4>
                    <table class="station-table">
                        <tr><th>住所</th><th>m2単価</th><th>用途</th></tr>
            `;
            top5expensive.forEach(p => {
                html += `<tr>
                    <td style="max-width:130px;overflow:hidden;text-overflow:ellipsis">${p.address}</td>
                    <td>&yen;${p.price.toLocaleString()}</td>
                    <td>${p.use_zone || '-'}</td>
                </tr>`;
            });
            html += '</table></div>';
        }
    }

    document.getElementById('detail-content').innerHTML = html;
}

function showLandPricePoints(points) {
    if (landPriceDetailLayer) map.removeLayer(landPriceDetailLayer);

    const markers = points.map(p => {
        const color = p.price <= 400000 ? '#2196f3' :
                      p.price <= 800000 ? '#4caf50' :
                      p.price <= 1500000 ? '#ff9800' :
                      p.price <= 3000000 ? '#f44336' : '#9c27b0';

        const marker = L.circleMarker([p.lat, p.lng], {
            radius: 5,
            fillColor: color,
            color: '#fff',
            weight: 1,
            fillOpacity: 0.8,
        });

        let popupContent = `
            <div class="popup-detail">
                <strong>&yen;${p.price.toLocaleString()}/m2</strong><br>
                ${p.address}<br>
                用途: ${p.use_zone || '-'}
        `;
        if (p.station) {
            popupContent += `<br>最寄駅: ${p.station}`;
            if (p.station_dist) popupContent += ` (徒歩${p.station_dist}分)`;
        }
        if (p.change_rate != null) {
            const sign = p.change_rate >= 0 ? '+' : '';
            popupContent += `<br>前年比: ${sign}${(p.change_rate * 100).toFixed(1)}%`;
        }
        popupContent += '</div>';
        marker.bindPopup(popupContent);
        return marker;
    });

    landPriceDetailLayer = L.layerGroup(markers);
    landPriceDetailLayer.addTo(map);
}

// ===== バッチ処理 =====

async function runBatch() {
    const btn = document.getElementById('btn-batch');
    const pref = document.getElementById('pref-select').value;
    btn.disabled = true;
    btn.textContent = '更新中...';

    try {
        const resp = await fetch('/api/batch/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prefectures: [pref] }),
        });
        const data = await resp.json();

        if (data.status === 'completed') {
            loadDbStats();
            runAnalysis();
            await loadBatchLogs();
        }
    } catch (e) {
        console.error('バッチエラー:', e);
    } finally {
        btn.disabled = false;
        btn.textContent = 'データ更新(バッチ)';
    }
}

async function loadBatchLogs() {
    try {
        const resp = await fetch('/api/batch/logs?limit=10');
        const data = await resp.json();
        const panel = document.getElementById('batch-panel');
        panel.style.display = 'block';

        let html = '';
        data.logs.forEach(log => {
            const statusColor = log.status === 'completed' ? '#66bb6a' :
                                log.status === 'error' ? '#ef5350' : '#ffa726';
            html += `
                <div style="font-size:0.72rem;padding:3px 0;border-bottom:1px solid #1a2744;">
                    <span style="color:${statusColor}">[${log.status}]</span>
                    ${log.batch_type} | ${log.records_inserted || 0}件保存
                    <span style="color:#546e7a">${log.started_at || ''}</span>
                </div>`;
        });
        document.getElementById('batch-logs').innerHTML = html;
    } catch (e) {
        console.error('バッチログ取得エラー:', e);
    }
}
