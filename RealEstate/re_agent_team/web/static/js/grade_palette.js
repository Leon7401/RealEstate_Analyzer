/**
 * 投資グレード色パレット（Python contracts/palette.py と同一ソース）
 * 地図・一覧・結果パネル・ランキングはすべてここを参照する。
 */
window.GRADE_PALETTE = Object.freeze({
    S: '#1a9641',
    A: '#4dac26',
    B: '#b8e186',
    C: '#fdb863',
    D: '#e66101',
    F: '#d7191c',
});

/** 資産性グレード（投資グレードと視覚的に分離） */
window.ASSET_GRADE_PALETTE = Object.freeze({
    S: '#0277bd',
    A: '#0288d1',
    B: '#4fc3f7',
    C: '#81d4fa',
    D: '#b0bec5',
    F: '#78909c',
});

window.normalizeGrade = function normalizeGrade(grade) {
    const g = String(grade || '').trim().toUpperCase();
    return ['S', 'A', 'B', 'C', 'D', 'F'].includes(g) ? g : '';
};

window.gradeColor = function gradeColor(grade, opts) {
    const asset = !!(opts && opts.asset);
    const fallback = (opts && opts.fallback) || '#546e7a';
    const g = window.normalizeGrade(grade);
    const palette = asset ? window.ASSET_GRADE_PALETTE : window.GRADE_PALETTE;
    return palette[g] || fallback;
};

/** 投資判定グレードのみ（資産性グレードは混ぜない） */
window.resolvePropertyGrade = function resolvePropertyGrade(p) {
    if (!p) return '';
    return window.normalizeGrade(
        (p._selected && p._selected.grade) ||
        p._analysis_grade ||
        p.grade ||
        p.judge_grade
    );
};

window.resolvePropertyScore = function resolvePropertyScore(p) {
    if (!p) return 0;
    if (p._selected && p._selected.score != null) return Number(p._selected.score) || 0;
    if (p._analysis_score != null) return Number(p._analysis_score) || 0;
    return Number(p.score) || 0;
};

window.applySelectedJudgment = function applySelectedJudgment(p, selected) {
    if (!p || !selected) return;
    p._selected = {
        grade: window.normalizeGrade(selected.grade) || selected.grade || '',
        score: Number(selected.score || selected.overall_score || 0) || 0,
        recommendation: selected.recommendation || '',
        scenario: selected.scenario || '',
        confidence: selected.confidence,
        gross_yield: selected.gross_yield,
        net_yield: selected.net_yield,
    };
    if (p._selected.grade) {
        p._analysis_grade = p._selected.grade;
        p.grade = p._selected.grade;
    }
    p._analysis_score = p._selected.score;
    if (p._selected.recommendation) p._analysis_recommendation = p._selected.recommendation;
    if (p._selected.scenario) p._analysis_scenario = p._selected.scenario;
    if (p._selected.confidence != null) p._analysis_confidence = p._selected.confidence;
    if (p._selected.gross_yield != null) p.gross_yield = p._selected.gross_yield;
    if (p._selected.net_yield != null) p.net_yield = p._selected.net_yield;
};
