/**
 * 駅単位エリア歪み分析 - フロントエンドJS
 */

let map;
let distortionLayer = null;
let landPriceDetailLayer = null;
let currentDistortionData = null;
let currentStationDetail = null;
let currentCompetitionData = null;

function initAnalysisMap(center, zoom) {
    map = L.map('map').setView(center, zoom);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 19,
    }).addTo(map);

    document.getElementById('btn-analyze').addEventListener('click', runAnalysis);
    document.getElementById('btn-batch').addEventListener('click', runBatch);
    document.getElementById('view-mode').addEventListener('change', updateVisualization);
    const simBtn = document.getElementById('btn-simulate');
    if (simBtn) simBtn.addEventListener('click', runSimpleSimulation);

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
        currentStationDetail = data;
        renderStationDetail(data);
        await loadAdvancedStationAnalytics(data);

        if (data.land_price_points && data.land_price_points.length > 0) {
            showLandPricePoints(data.land_price_points);
        }
    } catch (e) {
        document.getElementById('detail-content').textContent = 'エラー';
    }
}

async function loadAdvancedStationAnalytics(stationDetail) {
    const panel = document.getElementById('advanced-panel');
    if (!panel || !stationDetail) return;
    panel.style.display = 'block';
    document.getElementById('demand-metrics').innerHTML = '<span class="label">読込中</span><span class="value">...</span>';
    document.getElementById('price-trend-chart').innerHTML = '<div class="loading">推移読込中</div>';
    document.getElementById('price-trend-summary').textContent = '';

    const pref = document.getElementById('pref-select').value;
    const stationName = stationDetail.station_name || '';
    if (!stationName) return;

    try {
        const resp = await fetch(`/api/analysis/competition?station=${encodeURIComponent(stationName)}&prefecture_code=${pref}`);
        const data = await resp.json();
        currentCompetitionData = data;
        renderDemandMetrics(data.station_stats || {});
        renderPriceTrendChart(data.price_trend || {});
        applySimulationDefaults(stationDetail, data);
    } catch (e) {
        document.getElementById('demand-metrics').innerHTML = '<span class="label">エラー</span><span class="value">読込失敗</span>';
        document.getElementById('price-trend-chart').innerHTML = '<span style="color:#ef5350;">推移データ取得エラー</span>';
    }
}

function renderDemandMetrics(stats) {
    const el = document.getElementById('demand-metrics');
    if (!el) return;
    const fmtInt = (v) => (v == null ? 'N/A' : Number(v).toLocaleString());
    const fmtPct = (v) => (v == null ? 'N/A' : `${(Number(v) * 100).toFixed(1)}%`);
    const fmtYld = (v) => (v == null ? 'N/A' : `${(Number(v) * 100).toFixed(2)}%`);
    el.innerHTML = `
        <span class="label">駅名</span><span class="value">${stats.station_name || 'N/A'}</span>
        <span class="label">乗降客数/日</span><span class="value">${fmtInt(stats.passengers_daily)}</span>
        <span class="label">空室率</span><span class="value">${fmtPct(stats.vacancy_rate)}</span>
        <span class="label">平均地価</span><span class="value">${fmtInt(stats.avg_land_price_sqm)} 円/㎡</span>
        <span class="label">平均賃料</span><span class="value">${fmtInt(stats.avg_rent_per_sqm)} 円/㎡</span>
        <span class="label">暗黙利回り</span><span class="value">${fmtYld(stats.implied_yield)}</span>
    `;
}

function renderPriceTrendChart(priceTrend) {
    const chartEl = document.getElementById('price-trend-chart');
    const summaryEl = document.getElementById('price-trend-summary');
    if (!chartEl || !summaryEl) return;
    const entries = Object.entries(priceTrend || {})
        .map(([year, row]) => ({ year: Number(year), avg: Number(row.avg || 0), count: Number(row.count || 0) }))
        .filter(x => x.year > 0 && x.avg > 0)
        .sort((a, b) => a.year - b.year);
    if (!entries.length) {
        chartEl.innerHTML = '<span style="color:#78909c;">推移データがありません</span>';
        summaryEl.textContent = '';
        return;
    }
    const min = Math.min(...entries.map(e => e.avg));
    const max = Math.max(...entries.map(e => e.avg));
    const range = Math.max(1, max - min);
    const bars = entries.map(e => {
        const ratio = (e.avg - min) / range;
        const h = Math.round(28 + ratio * 80);
        return `<div class="trend-bar-wrap">
            <div class="trend-bar" style="height:${h}px;" title="${e.year}: ${Math.round(e.avg).toLocaleString()} 円/㎡"></div>
            <div class="trend-year">${e.year}</div>
        </div>`;
    }).join('');
    chartEl.innerHTML = `<div class="trend-bars">${bars}</div>`;
    const first = entries[0];
    const last = entries[entries.length - 1];
    const change = first.avg > 0 ? ((last.avg / first.avg) - 1) : 0;
    const sign = change >= 0 ? '+' : '';
    summaryEl.textContent = `${first.year}→${last.year}: ${Math.round(first.avg).toLocaleString()} → ${Math.round(last.avg).toLocaleString()} 円/㎡ (${sign}${(change * 100).toFixed(1)}%)`;
}

function applySimulationDefaults(stationDetail, competitionData) {
    const stats = (competitionData && competitionData.station_stats) || {};
    const avgRentSqm = Number(stats.avg_rent_per_sqm || 0);
    const vacancyRate = Number(stats.vacancy_rate || 0.08);
    const avgLand = Number(stats.avg_land_price_sqm || 0);
    // デフォルト前提: 延床200㎡
    if (avgLand > 0) {
        const estPriceMan = Math.max(1500, Math.round((avgLand * 120) / 10000));
        const priceEl = document.getElementById('sim-price-man');
        if (priceEl && (!priceEl.value || Number(priceEl.value) <= 0)) priceEl.value = estPriceMan;
    }
    if (avgRentSqm > 0) {
        const estMonthlyMan = Math.max(10, Math.round((avgRentSqm * 200 * (1 - vacancyRate)) / 10000 / 12));
        const rentEl = document.getElementById('sim-rent-man');
        if (rentEl && (!rentEl.value || Number(rentEl.value) <= 0)) rentEl.value = estMonthlyMan;
    }
}

function runSimpleSimulation() {
    const priceMan = Number(document.getElementById('sim-price-man')?.value || 0);
    const rentMan = Number(document.getElementById('sim-rent-man')?.value || 0);
    const equityPct = Number(document.getElementById('sim-equity-pct')?.value || 20) / 100;
    const ratePct = Number(document.getElementById('sim-rate-pct')?.value || 2.0) / 100;
    const termYears = Number(document.getElementById('sim-term-year')?.value || 30);
    const holdYears = Number(document.getElementById('sim-hold-year')?.value || 10);
    const summaryEl = document.getElementById('sim-summary');
    const chartEl = document.getElementById('sim-chart');
    const tableEl = document.getElementById('sim-table');
    if (!summaryEl || !chartEl || !tableEl) return;

    if (priceMan <= 0 || rentMan <= 0 || termYears <= 0 || holdYears <= 0) {
        summaryEl.innerHTML = '<span style="color:#ef5350;">入力値を確認してください</span>';
        return;
    }

    const stats = (currentCompetitionData && currentCompetitionData.station_stats) || {};
    const vacancyRate = Number(stats.vacancy_rate ?? 0.08);
    const opexRate = 0.24;
    const rentGrowth = 0.005;

    const purchase = priceMan * 10000;
    const monthlyRent = rentMan * 10000;
    const loanAmount = Math.max(0, Math.round(purchase * (1 - equityPct)));
    const annualDebt = calcAnnualDebtService(loanAmount, ratePct, termYears);
    let balance = loanAmount;

    const rows = [];
    let cumulative = 0;
    for (let y = 1; y <= holdYears; y++) {
        const gross = monthlyRent * 12 * Math.pow(1 + rentGrowth, y - 1);
        const effective = gross * (1 - vacancyRate);
        const noi = effective * (1 - opexRate);
        const interest = balance * ratePct;
        const principal = Math.max(0, Math.min(balance, annualDebt - interest));
        balance = Math.max(0, balance - principal);
        const cf = noi - annualDebt;
        cumulative += cf;
        rows.push({ year: y, gross, noi, debt: annualDebt, cf, balance });
    }

    const year1 = rows[0];
    const last = rows[rows.length - 1];
    const ccr = purchase > 0 ? (year1.cf / purchase) : 0;
    const dscr = annualDebt > 0 ? (year1.noi / annualDebt) : 0;
    summaryEl.innerHTML = `
        <div class="detail-grid">
            <span class="label">初年度CF</span><span class="value">${Math.round(year1.cf).toLocaleString()} 円</span>
            <span class="label">累計CF(${holdYears}年)</span><span class="value">${Math.round(cumulative).toLocaleString()} 円</span>
            <span class="label">CCR</span><span class="value">${(ccr * 100).toFixed(2)}%</span>
            <span class="label">DSCR</span><span class="value">${dscr.toFixed(2)}</span>
            <span class="label">期末ローン残高</span><span class="value">${Math.round(last.balance).toLocaleString()} 円</span>
        </div>
    `;

    const cfMax = Math.max(...rows.map(r => Math.abs(r.cf)), 1);
    chartEl.innerHTML = `<div class="trend-bars">` + rows.map(r => {
        const h = Math.round(20 + (Math.abs(r.cf) / cfMax) * 88);
        const cls = r.cf >= 0 ? 'trend-bar-positive' : 'trend-bar-negative';
        return `<div class="trend-bar-wrap">
            <div class="trend-bar ${cls}" style="height:${h}px;" title="Y${r.year}: CF ${Math.round(r.cf).toLocaleString()} 円"></div>
            <div class="trend-year">Y${r.year}</div>
        </div>`;
    }).join('') + `</div>`;

    tableEl.innerHTML = `
        <table class="station-table">
            <tr>
                <th>年</th><th>満室賃料</th><th>NOI</th><th>返済額</th><th>税前CF</th><th>ローン残高</th>
            </tr>
            ${rows.map(r => `<tr>
                <td>${r.year}</td>
                <td>${Math.round(r.gross).toLocaleString()}</td>
                <td>${Math.round(r.noi).toLocaleString()}</td>
                <td>${Math.round(r.debt).toLocaleString()}</td>
                <td style="color:${r.cf >= 0 ? '#66bb6a' : '#ef5350'}">${Math.round(r.cf).toLocaleString()}</td>
                <td>${Math.round(r.balance).toLocaleString()}</td>
            </tr>`).join('')}
        </table>
    `;
}

function calcAnnualDebtService(loanAmount, rate, termYears) {
    if (loanAmount <= 0) return 0;
    const mRate = rate / 12;
    const n = termYears * 12;
    if (mRate <= 0) return loanAmount / Math.max(1, termYears);
    const monthly = loanAmount * (mRate * Math.pow(1 + mRate, n)) / (Math.pow(1 + mRate, n) - 1);
    return monthly * 12;
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
