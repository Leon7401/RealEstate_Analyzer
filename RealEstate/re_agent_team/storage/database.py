"""
SQLite データベース層 - 全データの永続化

テーブル:
  stations         : 駅マスタ
  land_prices      : 公示地価・基準地価
  transactions     : 不動産取引価格情報
  rental_comps     : 賃貸比較事例
  properties       : 分析対象物件
  judgments        : 投資判定結果
  batch_logs       : バッチ処理ログ
  area_metrics     : エリア歪み分析結果（レガシー・市区町村単位）
  station_metrics  : 駅単位の歪み分析結果
"""
import sqlite3
import json
import threading
import re
import math
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

from config.settings import DB_PATH


class Database:
    """SQLiteデータベース管理"""

    _local = threading.local()

    def __init__(self, db_path: str = None):
        self.db_path = str(db_path or DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        """スレッドセーフな接続管理"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            self._local.conn.execute("PRAGMA temp_store=MEMORY")
        try:
            yield self._local.conn
            self._local.conn.commit()
        except Exception:
            self._local.conn.rollback()
            raise

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript("""
                -- 駅マスタ
                CREATE TABLE IF NOT EXISTS stations (
                    station_id TEXT PRIMARY KEY,
                    station_name TEXT NOT NULL,
                    line_name TEXT,
                    prefecture_code TEXT NOT NULL,
                    city_code TEXT,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS land_prices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL,
                    price_per_sqm INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    land_use_zone TEXT,
                    acreage REAL,
                    nearest_station TEXT,
                    station_distance_min INTEGER,
                    station_id TEXT,
                    price_change_rate REAL,
                    price_type TEXT DEFAULT '公示地価',
                    prefecture_code TEXT NOT NULL,
                    city_code TEXT NOT NULL,
                    fetched_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(address, year, price_type)
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL,
                    transaction_price INTEGER NOT NULL,
                    price_per_sqm REAL,
                    transaction_date TEXT,
                    land_area REAL,
                    building_area REAL,
                    land_shape TEXT,
                    land_use_zone TEXT,
                    structure TEXT,
                    built_year INTEGER,
                    use_type TEXT,
                    property_type TEXT DEFAULT '土地',
                    latitude REAL,
                    longitude REAL,
                    nearest_station TEXT,
                    station_distance_min INTEGER,
                    station_id TEXT,
                    prefecture_code TEXT NOT NULL,
                    city_code TEXT NOT NULL,
                    fetched_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(address, transaction_price, transaction_date)
                );

                CREATE TABLE IF NOT EXISTS rental_comps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    building_name TEXT,
                    address TEXT NOT NULL,
                    rent_monthly INTEGER NOT NULL,
                    area_sqm REAL NOT NULL,
                    rent_per_sqm REAL NOT NULL,
                    layout TEXT,
                    structure TEXT,
                    built_year INTEGER,
                    floor INTEGER,
                    floors_total INTEGER,
                    management_fee INTEGER,
                    latitude REAL,
                    longitude REAL,
                    nearest_station TEXT,
                    station_distance_min INTEGER,
                    station_id TEXT,
                    city_code TEXT,
                    source TEXT,
                    fetched_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(address, rent_monthly, area_sqm)
                );

                CREATE TABLE IF NOT EXISTS properties (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    address TEXT NOT NULL,
                    prefecture_code TEXT NOT NULL,
                    city_code TEXT NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    asking_price INTEGER,
                    land_area REAL,
                    building_area REAL,
                    structure TEXT,
                    built_year INTEGER,
                    building_age INTEGER,
                    units INTEGER,
                    current_rent_annual INTEGER,
                    gross_yield REAL,
                    nearest_station TEXT,
                    station_distance_min INTEGER,
                    station_id TEXT,
                    source TEXT,
                    source_url TEXT,
                    data_json TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    updated_at TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS judgments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    property_id TEXT NOT NULL,
                    grade TEXT NOT NULL,
                    recommendation TEXT,
                    overall_score REAL,
                    confidence REAL,
                    key_metrics_json TEXT,
                    full_result_json TEXT,
                    judged_at TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS property_analysis_cache (
                    analysis_key TEXT PRIMARY KEY,
                    property_id TEXT,
                    property_type TEXT DEFAULT 'property',
                    name TEXT,
                    address TEXT,
                    grade TEXT,
                    score REAL,
                    scenario TEXT,
                    selected_json TEXT,
                    as_is_json TEXT,
                    rebuild_json TEXT,
                    analyzed_at TEXT DEFAULT (datetime('now','localtime')),
                    updated_at TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS batch_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_type TEXT NOT NULL,
                    prefecture_code TEXT,
                    city_code TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    records_fetched INTEGER DEFAULT 0,
                    records_inserted INTEGER DEFAULT 0,
                    error_message TEXT,
                    started_at TEXT DEFAULT (datetime('now','localtime')),
                    finished_at TEXT
                );

                -- レガシー: 市区町村単位メトリクス
                CREATE TABLE IF NOT EXISTS area_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    city_code TEXT NOT NULL,
                    city_name TEXT,
                    prefecture_code TEXT NOT NULL,
                    year INTEGER,
                    avg_land_price_sqm REAL,
                    median_land_price_sqm REAL,
                    land_price_change_rate REAL,
                    avg_rent_per_sqm REAL,
                    median_rent_per_sqm REAL,
                    implied_yield REAL,
                    yield_gap REAL,
                    distortion_score REAL,
                    sample_count_land INTEGER DEFAULT 0,
                    sample_count_rent INTEGER DEFAULT 0,
                    sample_count_tx INTEGER DEFAULT 0,
                    center_lat REAL,
                    center_lng REAL,
                    computed_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(city_code, year)
                );

                -- 駅単位メトリクス（メイン分析テーブル）
                CREATE TABLE IF NOT EXISTS station_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    station_id TEXT NOT NULL,
                    station_name TEXT,
                    line_name TEXT,
                    prefecture_code TEXT NOT NULL,
                    city_code TEXT,
                    year INTEGER,
                    avg_land_price_sqm REAL,
                    median_land_price_sqm REAL,
                    land_price_change_rate REAL,
                    avg_rent_per_sqm REAL,
                    median_rent_per_sqm REAL,
                    implied_yield REAL,
                    yield_gap REAL,
                    distortion_score REAL,
                    sample_count_land INTEGER DEFAULT 0,
                    sample_count_rent INTEGER DEFAULT 0,
                    sample_count_tx INTEGER DEFAULT 0,
                    center_lat REAL,
                    center_lng REAL,
                    computed_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(station_id, year)
                );

                -- 土地物件（新築用地）
                CREATE TABLE IF NOT EXISTS land_listings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL,
                    railway_line TEXT,
                    station TEXT,
                    walk_minutes INTEGER,
                    land_price INTEGER,
                    land_area_sqm REAL,
                    building_coverage_ratio REAL,
                    floor_area_ratio REAL,
                    zoning TEXT,
                    quasi_fireproof INTEGER DEFAULT 0,
                    two_way_road INTEGER DEFAULT 0,
                    north_road INTEGER DEFAULT 0,
                    source TEXT,
                    source_url TEXT,
                    maisoku_pdf_path TEXT,
                    analysis_status TEXT DEFAULT 'pending',
                    memo TEXT,
                    latitude REAL,
                    longitude REAL,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    updated_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(address, land_price, source)
                );

                -- 建築プラン
                CREATE TABLE IF NOT EXISTS building_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    land_listing_id INTEGER NOT NULL,
                    structure_type TEXT NOT NULL,
                    floors INTEGER NOT NULL,
                    unit_size_sqm REAL NOT NULL,
                    max_footprint_sqm REAL,
                    max_total_floor_area_sqm REAL,
                    actual_total_floor_area_sqm REAL,
                    common_area_ratio REAL,
                    effective_floor_area_sqm REAL,
                    max_units INTEGER,
                    estimated_rent_per_sqm REAL,
                    estimated_monthly_rent_per_unit INTEGER,
                    estimated_annual_income INTEGER,
                    estimated_construction_cost INTEGER,
                    total_investment INTEGER,
                    estimated_yield REAL,
                    UNIQUE(land_listing_id, structure_type, floors, unit_size_sqm),
                    FOREIGN KEY (land_listing_id) REFERENCES land_listings(id)
                );

                -- 土地投資判定結果
                CREATE TABLE IF NOT EXISTS land_judgments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    land_listing_id INTEGER NOT NULL,
                    building_plan_id INTEGER,
                    grade TEXT NOT NULL,
                    recommendation TEXT,
                    overall_score REAL,
                    confidence REAL,
                    key_metrics_json TEXT,
                    full_result_json TEXT,
                    judged_at TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (land_listing_id) REFERENCES land_listings(id)
                );

                -- スクレイピング検索条件
                CREATE TABLE IF NOT EXISTS scrape_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'SUUMO',
                    prefecture_codes TEXT,
                    area_codes TEXT,
                    price_min INTEGER,
                    price_max INTEGER,
                    area_min REAL,
                    area_max REAL,
                    walk_max INTEGER,
                    max_pages INTEGER DEFAULT 5,
                    run_interval_hours INTEGER DEFAULT 24,
                    is_active INTEGER DEFAULT 1,
                    last_run_at TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );

                -- インデックス
                CREATE INDEX IF NOT EXISTS idx_ll_station ON land_listings(station);
                CREATE INDEX IF NOT EXISTS idx_ll_status ON land_listings(analysis_status);
                CREATE INDEX IF NOT EXISTS idx_ll_price ON land_listings(land_price);
                CREATE INDEX IF NOT EXISTS idx_bp_listing ON building_plans(land_listing_id);
                CREATE INDEX IF NOT EXISTS idx_bp_yield ON building_plans(estimated_yield DESC);
                CREATE INDEX IF NOT EXISTS idx_lj_listing ON land_judgments(land_listing_id);
                CREATE INDEX IF NOT EXISTS idx_lp_city ON land_prices(city_code);
                CREATE INDEX IF NOT EXISTS idx_lp_year ON land_prices(year);
                CREATE INDEX IF NOT EXISTS idx_lp_station_id ON land_prices(station_id);
                CREATE INDEX IF NOT EXISTS idx_tx_city ON transactions(city_code);
                CREATE INDEX IF NOT EXISTS idx_tx_station_id ON transactions(station_id);
                CREATE INDEX IF NOT EXISTS idx_rc_city ON rental_comps(city_code);
                CREATE INDEX IF NOT EXISTS idx_rc_station ON rental_comps(nearest_station);
                CREATE INDEX IF NOT EXISTS idx_rc_station_id ON rental_comps(station_id);
                CREATE INDEX IF NOT EXISTS idx_prop_city ON properties(city_code);
                CREATE INDEX IF NOT EXISTS idx_prop_station_id ON properties(station_id);
                CREATE INDEX IF NOT EXISTS idx_am_city ON area_metrics(city_code);
                CREATE INDEX IF NOT EXISTS idx_am_distortion ON area_metrics(distortion_score DESC);
                CREATE INDEX IF NOT EXISTS idx_sm_station ON station_metrics(station_id);
                CREATE INDEX IF NOT EXISTS idx_sm_pref ON station_metrics(prefecture_code);
                CREATE INDEX IF NOT EXISTS idx_sm_distortion ON station_metrics(distortion_score DESC);

                CREATE INDEX IF NOT EXISTS idx_ll_source ON land_listings(source);
                CREATE INDEX IF NOT EXISTS idx_ll_created ON land_listings(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ll_coords ON land_listings(latitude, longitude);
                CREATE INDEX IF NOT EXISTS idx_ll_zoning ON land_listings(zoning);
                CREATE INDEX IF NOT EXISTS idx_bp_structure ON building_plans(structure_type, floors);
                CREATE INDEX IF NOT EXISTS idx_lj_grade ON land_judgments(grade);
                CREATE INDEX IF NOT EXISTS idx_pac_property_id ON property_analysis_cache(property_id);
                CREATE INDEX IF NOT EXISTS idx_pac_grade ON property_analysis_cache(grade);
                CREATE INDEX IF NOT EXISTS idx_pac_score ON property_analysis_cache(score DESC);

                -- 土地資産性スコア
                CREATE TABLE IF NOT EXISTS land_asset_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    land_listing_id INTEGER NOT NULL UNIQUE,
                    overall_score REAL DEFAULT 0,
                    grade TEXT DEFAULT '?',
                    summary TEXT,
                    road_score REAL DEFAULT 0,
                    road_info_json TEXT,
                    hazard_score REAL DEFAULT 0,
                    hazard_info_json TEXT,
                    elevation_score REAL DEFAULT 0,
                    elevation_info_json TEXT,
                    lot_shape_score REAL DEFAULT 0,
                    lot_shape_info_json TEXT,
                    population_score REAL DEFAULT 0,
                    population_info_json TEXT,
                    station_distance_score REAL DEFAULT 0,
                    scored_at TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (land_listing_id) REFERENCES land_listings(id)
                );
                CREATE INDEX IF NOT EXISTS idx_las_listing ON land_asset_scores(land_listing_id);
                CREATE INDEX IF NOT EXISTS idx_las_grade ON land_asset_scores(grade);
                CREATE INDEX IF NOT EXISTS idx_las_score ON land_asset_scores(overall_score DESC);
            """)
            # マイグレーション: 既存テーブルにstation_id列を追加
            self._migrate_add_column(conn, "land_prices", "station_id", "TEXT")
            self._migrate_add_column(conn, "transactions", "station_id", "TEXT")
            self._migrate_add_column(conn, "rental_comps", "station_id", "TEXT")
            self._migrate_add_column(conn, "rental_comps", "building_name", "TEXT")
            self._migrate_add_column(conn, "properties", "station_id", "TEXT")
            self._migrate_add_column(conn, "properties", "listing_status", "TEXT DEFAULT 'active'")
            self._migrate_add_column(conn, "properties", "last_seen_at", "TEXT")
            self._migrate_add_column(conn, "properties", "last_verified_at", "TEXT")
            self._migrate_add_column(conn, "properties", "delisted_confirmed_at", "TEXT")
            self._migrate_add_column(conn, "properties", "verify_fail_count", "INTEGER DEFAULT 0")
            self._migrate_add_column(conn, "properties", "last_verified_http_status", "INTEGER")
            self._migrate_add_column(conn, "properties", "verify_note", "TEXT")
            self._migrate_add_column(conn, "land_listings", "duplicate_of_id", "INTEGER")
            self._migrate_add_column(conn, "land_listings", "normalized_address", "TEXT")
            self._migrate_add_column(conn, "land_listings", "listing_status", "TEXT DEFAULT 'active'")
            self._migrate_add_column(conn, "land_listings", "last_seen_at", "TEXT")
            self._migrate_add_column(conn, "land_listings", "last_verified_at", "TEXT")
            self._migrate_add_column(conn, "land_listings", "delisted_confirmed_at", "TEXT")
            self._migrate_add_column(conn, "land_listings", "verify_fail_count", "INTEGER DEFAULT 0")
            self._migrate_add_column(conn, "land_listings", "last_verified_http_status", "INTEGER")
            self._migrate_add_column(conn, "land_listings", "verify_note", "TEXT")
            self._migrate_add_column(conn, "scrape_configs", "run_interval_hours", "INTEGER DEFAULT 24")
            # 駅メトリクス拡張: 乗降客数・人口・空室率
            self._migrate_add_column(conn, "station_metrics", "passengers_daily", "INTEGER")
            self._migrate_add_column(conn, "station_metrics", "passengers_change_rate", "REAL")
            self._migrate_add_column(conn, "station_metrics", "population_500m", "INTEGER")
            self._migrate_add_column(conn, "station_metrics", "vacancy_rate", "REAL")
            self._migrate_add_column(conn, "station_metrics", "avg_age_years", "REAL")
            self._migrate_add_column(conn, "station_metrics", "new_supply_units", "INTEGER")
            # 建築プラン拡張: ベストプランフラグ
            self._migrate_add_column(conn, "building_plans", "is_best_plan", "INTEGER DEFAULT 0")
            self._migrate_add_column(conn, "building_plans", "rank_score", "REAL")
            # 判定結果拡張: 多角評価
            self._migrate_add_column(conn, "judgments", "hold_sell_roi", "REAL")
            self._migrate_add_column(conn, "judgments", "hold_sell_total_return", "INTEGER")
            # 土地物件拡張: 接道・地形・規制
            self._migrate_add_column(conn, "land_listings", "road_width_m", "REAL")
            self._migrate_add_column(conn, "land_listings", "road_legal_type", "TEXT")
            self._migrate_add_column(conn, "land_listings", "frontage_m", "REAL")
            self._migrate_add_column(conn, "land_listings", "depth_m", "REAL")
            self._migrate_add_column(conn, "land_listings", "has_retaining_wall", "INTEGER DEFAULT 0")
            self._migrate_add_column(conn, "land_listings", "has_step_retaining_wall", "INTEGER DEFAULT 0")
            self._migrate_add_column(conn, "land_listings", "land_shape", "TEXT")
            self._migrate_add_column(conn, "land_listings", "setback_required", "INTEGER DEFAULT 0")
            self._migrate_add_column(conn, "land_listings", "setback_area_sqm", "REAL")
            self._migrate_add_column(conn, "land_listings", "corner_lot", "INTEGER DEFAULT 0")
            self._migrate_add_column(conn, "land_listings", "height_limit_m", "REAL")
            self._migrate_add_column(conn, "land_listings", "height_district", "TEXT")
            # 建築プラン拡張: 設備・規制
            self._migrate_add_column(conn, "building_plans", "equipment_grade", "TEXT DEFAULT 'premium'")
            self._migrate_add_column(conn, "building_plans", "equipment_premium_factor", "REAL DEFAULT 1.05")
            self._migrate_add_column(conn, "building_plans", "setback_cost_premium", "INTEGER DEFAULT 0")
            self._migrate_add_column(conn, "building_plans", "volume_reduction_ratio", "REAL DEFAULT 0")
            self._migrate_add_column(conn, "building_plans", "ward_ordinance_compliant", "INTEGER DEFAULT 1")
            self._migrate_add_column(conn, "building_plans", "ward_ordinance_note", "TEXT")
            # 人口メッシュ座標カラム
            self._migrate_add_column(conn, "api_population_mesh", "center_lat", "REAL")
            self._migrate_add_column(conn, "api_population_mesh", "center_lng", "REAL")
            # メッシュ用途地域・道路
            self._migrate_add_column(conn, "mesh_250m", "zoning", "TEXT")
            self._migrate_add_column(conn, "mesh_250m", "coverage_ratio", "TEXT")
            self._migrate_add_column(conn, "mesh_250m", "floor_area_ratio", "TEXT")
            self._migrate_add_column(conn, "mesh_250m", "front_road", "TEXT")
            self._migrate_add_column(conn, "mesh_250m", "road_width_m", "REAL")

            # 250mメッシュ統合テーブル
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS mesh_250m (
                    mesh_id TEXT PRIMARY KEY,
                    center_lat REAL NOT NULL,
                    center_lng REAL NOT NULL,
                    -- 地価
                    avg_land_price_sqm REAL,
                    land_price_count INTEGER DEFAULT 0,
                    -- 賃料
                    avg_rent_sqm REAL,
                    rent_count INTEGER DEFAULT 0,
                    -- 取引
                    avg_tx_price_sqm REAL,
                    tx_count INTEGER DEFAULT 0,
                    -- 人口
                    pop_current REAL,
                    pop_future REAL,
                    pop_change_rate REAL,
                    -- 施設
                    school_count INTEGER DEFAULT 0,
                    medical_count INTEGER DEFAULT 0,
                    childcare_count INTEGER DEFAULT 0,
                    -- 最寄駅
                    nearest_station TEXT,
                    station_dist_km REAL,
                    -- 集計時刻
                    computed_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE INDEX IF NOT EXISTS idx_m250_coords ON mesh_250m(center_lat, center_lng);
            """)
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_apm_coords ON api_population_mesh(center_lat, center_lng)")
            except Exception:
                pass
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_prop_listing_status ON properties(listing_status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_prop_verified_at ON properties(last_verified_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ll_listing_status2 ON land_listings(listing_status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ll_verified_at ON land_listings(last_verified_at)")
            except Exception:
                pass

            # API公示地価キャッシュテーブル
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS api_land_prices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    point_id TEXT,
                    place_name TEXT,
                    price_per_sqm INTEGER NOT NULL,
                    year INTEGER,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    zoning TEXT,
                    station TEXT,
                    change_rate REAL,
                    coverage TEXT,
                    far TEXT,
                    fire_prevention TEXT,
                    land_price_type INTEGER DEFAULT 0,
                    prefecture_code TEXT,
                    city_code TEXT,
                    fetched_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(point_id, year)
                );
                CREATE INDEX IF NOT EXISTS idx_alp_coords ON api_land_prices(latitude, longitude);
                CREATE INDEX IF NOT EXISTS idx_alp_year ON api_land_prices(year);
                CREATE INDEX IF NOT EXISTS idx_alp_pref ON api_land_prices(prefecture_code);

                CREATE TABLE IF NOT EXISTS api_population_mesh (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mesh_id TEXT NOT NULL,
                    pop_current REAL,
                    pop_future REAL,
                    change_rate REAL,
                    center_lat REAL,
                    center_lng REAL,
                    geometry_json TEXT,
                    fetched_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(mesh_id)
                );
                CREATE INDEX IF NOT EXISTS idx_apm_mesh ON api_population_mesh(mesh_id);

                CREATE TABLE IF NOT EXISTS api_facilities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    category TEXT NOT NULL,
                    address TEXT,
                    latitude REAL,
                    longitude REAL,
                    extra_json TEXT,
                    fetched_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(name, category, latitude, longitude)
                );
                CREATE INDEX IF NOT EXISTS idx_af_cat ON api_facilities(category);
                CREATE INDEX IF NOT EXISTS idx_af_coords ON api_facilities(latitude, longitude);
            """)

    def _migrate_add_column(self, conn, table: str, column: str, col_type: str):
        """既存テーブルにカラムを安全に追加"""
        try:
            cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except Exception:
            pass

    # ===== 駅マスタ =====

    def populate_stations(self, stations: List[Dict]) -> int:
        """駅マスタを一括投入"""
        inserted = 0
        with self._conn() as conn:
            for s in stations:
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO stations
                            (station_id, station_name, line_name,
                             prefecture_code, city_code, latitude, longitude)
                        VALUES (?,?,?,?,?,?,?)
                    """, (
                        s["station_id"], s["name"], s.get("line", ""),
                        s["pref"], s.get("city_code", ""),
                        s["lat"], s["lon"],
                    ))
                    inserted += 1
                except Exception:
                    pass
        return inserted

    def get_stations(self, prefecture_code: str = "") -> List[Dict]:
        with self._conn() as conn:
            if prefecture_code:
                rows = conn.execute(
                    "SELECT * FROM stations WHERE prefecture_code=? ORDER BY station_name",
                    (prefecture_code,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM stations ORDER BY station_name").fetchall()
            return [dict(r) for r in rows]

    # ===== 公示地価 =====

    def upsert_land_prices(self, records: List[Dict]) -> int:
        """公示地価を一括upsert"""
        inserted = 0
        with self._conn() as conn:
            for r in records:
                try:
                    conn.execute("""
                        INSERT INTO land_prices
                            (address, price_per_sqm, year, latitude, longitude,
                             land_use_zone, acreage, nearest_station,
                             station_distance_min, station_id,
                             price_change_rate, price_type,
                             prefecture_code, city_code)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(address, year, price_type) DO UPDATE SET
                            price_per_sqm=excluded.price_per_sqm,
                            latitude=excluded.latitude,
                            longitude=excluded.longitude,
                            station_id=excluded.station_id,
                            price_change_rate=excluded.price_change_rate,
                            fetched_at=datetime('now','localtime')
                    """, (
                        r.get("address", ""), r.get("price_per_sqm", 0),
                        r.get("year", 0), r.get("latitude"), r.get("longitude"),
                        r.get("land_use_zone"), r.get("acreage"),
                        r.get("nearest_station"), r.get("station_distance_min"),
                        r.get("station_id"),
                        r.get("price_change_rate"), r.get("price_type", "公示地価"),
                        r.get("prefecture_code", ""), r.get("city_code", ""),
                    ))
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass
        return inserted

    def get_land_prices(
        self, city_code: str = "", prefecture_code: str = "",
        station_id: str = "", year: int = None, limit: int = 5000,
    ) -> List[Dict]:
        with self._conn() as conn:
            sql = "SELECT * FROM land_prices WHERE 1=1"
            params = []
            if station_id:
                sql += " AND station_id=?"
                params.append(station_id)
            elif city_code:
                sql += " AND city_code=?"
                params.append(city_code)
            elif prefecture_code:
                sql += " AND prefecture_code=?"
                params.append(prefecture_code)
            if year:
                sql += " AND year=?"
                params.append(year)
            sql += " ORDER BY price_per_sqm DESC LIMIT ?"
            params.append(limit)
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # ===== 取引事例 =====

    def upsert_transactions(self, records: List[Dict]) -> int:
        inserted = 0
        with self._conn() as conn:
            for r in records:
                try:
                    conn.execute("""
                        INSERT INTO transactions
                            (address, transaction_price, price_per_sqm,
                             transaction_date, land_area, building_area,
                             land_shape, land_use_zone, structure, built_year,
                             use_type, property_type, latitude, longitude,
                             nearest_station, station_distance_min, station_id,
                             prefecture_code, city_code)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(address, transaction_price, transaction_date)
                        DO NOTHING
                    """, (
                        r.get("address", ""), r.get("transaction_price", 0),
                        r.get("price_per_sqm"), r.get("transaction_date"),
                        r.get("land_area"), r.get("building_area"),
                        r.get("land_shape"), r.get("land_use_zone"),
                        r.get("structure"), r.get("built_year"),
                        r.get("use"), r.get("property_type", "土地"),
                        r.get("latitude"), r.get("longitude"),
                        r.get("nearest_station"), r.get("station_distance_min"),
                        r.get("station_id"),
                        r.get("prefecture_code", ""), r.get("city_code", ""),
                    ))
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass
        return inserted

    def get_transactions(
        self, city_code: str = "", prefecture_code: str = "",
        station_id: str = "", limit: int = 5000,
    ) -> List[Dict]:
        with self._conn() as conn:
            sql = "SELECT * FROM transactions WHERE 1=1"
            params = []
            if station_id:
                sql += " AND station_id=?"
                params.append(station_id)
            elif city_code:
                sql += " AND city_code=?"
                params.append(city_code)
            elif prefecture_code:
                sql += " AND prefecture_code=?"
                params.append(prefecture_code)
            sql += " ORDER BY transaction_date DESC LIMIT ?"
            params.append(limit)
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # ===== 賃料事例 =====

    def upsert_rental_comps(self, records: List[Dict]) -> int:
        inserted = 0
        with self._conn() as conn:
            for r in records:
                try:
                    # スクレイピング由来の駅名を実在駅へ正規化
                    try:
                        from data.station_master import resolve_station_id, STATION_MAP
                        sid = resolve_station_id(
                            nearest_station_text=r.get("nearest_station"),
                            lat=r.get("latitude"),
                            lon=r.get("longitude"),
                            pref_code=(str(r.get("city_code") or "")[:2] if r.get("city_code") else None),
                        )
                        if sid:
                            r["station_id"] = sid
                            if sid in STATION_MAP:
                                r["nearest_station"] = STATION_MAP[sid]["name"]
                    except Exception:
                        pass

                    conn.execute("""
                        INSERT INTO rental_comps
                            (building_name, address, rent_monthly, area_sqm, rent_per_sqm,
                             layout, structure, built_year, floor, floors_total,
                             management_fee, latitude, longitude,
                             nearest_station, station_distance_min, station_id,
                             city_code, source)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(address, rent_monthly, area_sqm)
                        DO UPDATE SET
                            building_name=COALESCE(excluded.building_name, rental_comps.building_name),
                            rent_per_sqm=excluded.rent_per_sqm,
                            floor=COALESCE(excluded.floor, rental_comps.floor),
                            floors_total=COALESCE(excluded.floors_total, rental_comps.floors_total),
                            station_id=excluded.station_id,
                            fetched_at=datetime('now','localtime')
                    """, (
                        r.get("building_name"), r.get("address", ""), r.get("rent_monthly", 0),
                        r.get("area_sqm", 0), r.get("rent_per_sqm", 0),
                        r.get("layout"), r.get("structure"),
                        r.get("built_year"), r.get("floor"),
                        r.get("floors_total"), r.get("management_fee"),
                        r.get("latitude"), r.get("longitude"),
                        r.get("nearest_station"), r.get("station_distance_min"),
                        r.get("station_id"),
                        r.get("city_code"), r.get("source"),
                    ))
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass
        return inserted

    def merge_duplicate_rental_comps(
        self,
        dry_run: bool = False,
        min_group_size: int = 2,
        max_groups: int = 5000,
    ) -> Dict[str, Any]:
        """
        賃料事例の重複統合。
        主キー:
          1) 住所+賃料+面積+階数
          2) 建物名+階数+賃料帯+面積（同一物件名/同一階の取りこぼし吸収）
        """
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute("""
                SELECT *
                FROM rental_comps
                ORDER BY fetched_at DESC, id DESC
            """).fetchall()]

            def _norm(s: Any) -> str:
                v = str(s or "").strip().replace("　", " ")
                return re.sub(r"\s+", "", v)

            def _norm_addr(s: Any) -> str:
                v = _norm(s)
                v = v.replace("丁目", "-").replace("番地", "-").replace("番", "-").replace("号", "")
                return re.sub(r"-{2,}", "-", v).strip("-")

            seen: Dict[str, int] = {}
            groups: Dict[int, List[int]] = {}

            for r in rows:
                rid = int(r.get("id") or 0)
                if rid <= 0:
                    continue
                addr = _norm_addr(r.get("address"))
                bname = _norm(r.get("building_name"))
                station = _norm(r.get("nearest_station")).replace("駅", "")
                floor = r.get("floor")
                city = _norm(r.get("city_code"))
                try:
                    rent = int(float(r.get("rent_monthly"))) if r.get("rent_monthly") is not None else None
                except (TypeError, ValueError):
                    rent = None
                try:
                    area = round(float(r.get("area_sqm")), 1) if r.get("area_sqm") is not None else None
                except (TypeError, ValueError):
                    area = None

                keys = []
                if addr and rent is not None and area is not None:
                    keys.append(f"a:{addr}|{rent}|{area}|{floor}")
                if bname and floor is not None and area is not None:
                    rent_bucket = int(rent / 500) if rent is not None else -1
                    keys.append(f"b:{city}|{bname}|{floor}|{rent_bucket}|{area}|{station}")

                canonical_id = None
                for k in keys:
                    if k in seen:
                        canonical_id = seen[k]
                        break
                if canonical_id:
                    groups.setdefault(canonical_id, []).append(rid)
                else:
                    groups.setdefault(rid, [])
                    for k in keys:
                        seen[k] = rid

            grouped = [(cid, dupes) for cid, dupes in groups.items() if len(dupes) >= max(1, min_group_size - 1)]
            grouped = grouped[:max_groups]
            merged_records = sum(len(dupes) for _, dupes in grouped)

            if not dry_run:
                for _, dupes in grouped:
                    for did in dupes:
                        conn.execute("DELETE FROM rental_comps WHERE id = ?", (did,))

            return {
                "dry_run": dry_run,
                "group_count": len(grouped),
                "merged_records": merged_records,
                "groups": [
                    {"canonical_id": cid, "duplicate_ids": dupes, "group_size": len(dupes) + 1}
                    for cid, dupes in grouped
                ],
            }

    def get_rental_comps(
        self, city_code: str = "", station: str = "",
        station_id: str = "", limit: int = 5000,
    ) -> List[Dict]:
        with self._conn() as conn:
            sql = "SELECT * FROM rental_comps WHERE 1=1"
            params = []
            if station_id:
                sql += " AND station_id=?"
                params.append(station_id)
            elif city_code:
                sql += " AND city_code=?"
                params.append(city_code)
            if station:
                sql += " AND nearest_station LIKE ?"
                params.append(f"%{station}%")
            sql += " ORDER BY fetched_at DESC LIMIT ?"
            params.append(limit)
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # ===== 物件 =====

    def upsert_property(self, prop: Dict) -> bool:
        with self._conn() as conn:
            prop = dict(prop or {})

            # 駅名の実在判定と正規化（誤抽出をDB保存前に補正）
            try:
                from data.station_master import resolve_station_id, STATION_MAP
                sid = resolve_station_id(
                    nearest_station_text=prop.get("nearest_station"),
                    lat=prop.get("latitude"),
                    lon=prop.get("longitude"),
                    pref_code=prop.get("prefecture_code"),
                )
                if sid:
                    prop["station_id"] = sid
                    if sid in STATION_MAP:
                        prop["nearest_station"] = STATION_MAP[sid]["name"]
                else:
                    # 実在駅として解決できない駅名は座標補正誤爆の原因になるため破棄
                    if prop.get("nearest_station"):
                        prop["nearest_station"] = None
            except Exception:
                pass

            # 同一URL物件は既存IDに寄せて重複新規作成を抑止
            normalized_url = self._normalize_source_url(prop.get("source_url"))
            if normalized_url:
                existed = conn.execute("""
                    SELECT id FROM properties
                    WHERE source_url = ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                """, (normalized_url,)).fetchone()
                if existed:
                    prop["id"] = dict(existed)["id"]
                prop["source_url"] = normalized_url

            # URLが無い場合は住所+価格+面積一致で既存寄せ（保守的）
            if not prop.get("id"):
                address = self._normalize_address(prop.get("address"))
                asking = prop.get("asking_price")
                la = prop.get("land_area")
                ba = prop.get("building_area")
                if address and asking and (la or ba):
                    existed = conn.execute("""
                        SELECT id
                        FROM properties
                        WHERE address = ?
                          AND asking_price = ?
                          AND COALESCE(land_area, -1) = COALESCE(?, -1)
                          AND COALESCE(building_area, -1) = COALESCE(?, -1)
                        ORDER BY updated_at DESC
                        LIMIT 1
                    """, (address, asking, la, ba)).fetchone()
                    if existed:
                        prop["id"] = dict(existed)["id"]
                prop["address"] = address or (prop.get("address") or "")

            conn.execute("""
                INSERT INTO properties
                    (id, name, address, prefecture_code, city_code,
                     latitude, longitude, asking_price, land_area,
                     building_area, structure, built_year, building_age,
                     units, current_rent_annual, gross_yield,
                     nearest_station, station_distance_min, station_id,
                     source, source_url, data_json, listing_status, last_seen_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    asking_price=excluded.asking_price,
                    current_rent_annual=excluded.current_rent_annual,
                    gross_yield=excluded.gross_yield,
                    station_id=excluded.station_id,
                    listing_status='active',
                    delisted_confirmed_at=NULL,
                    verify_fail_count=0,
                    last_seen_at=datetime('now','localtime'),
                    data_json=excluded.data_json,
                    updated_at=datetime('now','localtime')
            """, (
                prop.get("id", ""), prop.get("name", ""),
                prop.get("address", ""), prop.get("prefecture_code", ""),
                prop.get("city_code", ""), prop.get("latitude"),
                prop.get("longitude"), prop.get("asking_price"),
                prop.get("land_area"), prop.get("building_area"),
                prop.get("structure"), prop.get("built_year"),
                prop.get("building_age"), prop.get("units"),
                prop.get("current_rent_annual"), prop.get("gross_yield"),
                prop.get("nearest_station"), prop.get("station_distance_min"),
                prop.get("station_id"),
                prop.get("source"), prop.get("source_url"),
                json.dumps(prop, ensure_ascii=False),
                "active",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))
        return True

    @staticmethod
    def _normalize_source_url(url: Any) -> str:
        if not url:
            return ""
        try:
            raw = str(url).strip()
            if not raw:
                return ""
            parts = urlsplit(raw)
            path = (parts.path or "/").rstrip("/") or "/"
            return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))
        except Exception:
            return str(url).strip()

    @staticmethod
    def _normalize_address(address: Any) -> str:
        if address is None:
            return ""
        s = str(address).strip().replace("　", " ")
        s = re.sub(r"\s+", "", s)
        # 代表的な表記ゆれを軽く吸収
        s = s.replace("丁目", "-").replace("番地", "-").replace("番", "-").replace("号", "")
        s = re.sub(r"-{2,}", "-", s).strip("-")
        return s

    @staticmethod
    def _normalize_station_name(station: Any) -> str:
        if not station:
            return ""
        s = str(station).replace("駅", "").replace("　", "").strip()
        s = re.sub(r"\s+", "", s)
        return s

    @staticmethod
    def _value_score(v: Any) -> int:
        if v is None:
            return 0
        if isinstance(v, str):
            return 1 if v.strip() else 0
        return 1

    def merge_duplicate_properties(
        self,
        dry_run: bool = False,
        min_group_size: int = 2,
        max_groups: int = 500,
    ) -> Dict[str, Any]:
        """
        重複物件を検出し統合する。
        優先キー:
          1) source_url 正規化一致
          2) 駅距離 + 価格 + 延べ床面積（+市区町村/駅名）一致
          3) 住所正規化 + 価格帯 + 面積 + 駅名一致
        """
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute("""
                SELECT *
                FROM properties
                ORDER BY updated_at DESC
            """).fetchall()]

            def _norm_name(v: Any) -> str:
                s = re.sub(r"\s+", "", str(v or ""))
                s = re.sub(r"(new|NEW|新着|登録|更新|価格改定|値下げ|限定|公開).*", "", s)
                # 号棟・棟番などの揺れを軽く吸収
                s = re.sub(r"(第?\d+号棟|[A-Z]棟|[A-Z]号棟)", "", s)
                return s.strip("-_")

            def _distance_km(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[float]:
                try:
                    lat1, lng1 = float(a.get("latitude")), float(a.get("longitude"))
                    lat2, lng2 = float(b.get("latitude")), float(b.get("longitude"))
                except (TypeError, ValueError):
                    return None
                r = 6371.0
                p1, p2 = math.radians(lat1), math.radians(lat2)
                dp = math.radians(lat2 - lat1)
                dl = math.radians(lng2 - lng1)
                x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
                return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, x))))

            def _rel_diff(x: Optional[float], y: Optional[float]) -> float:
                if x is None or y is None:
                    return 999.0
                base = max(abs(x), abs(y), 1e-9)
                return abs(x - y) / base

            # 複数キー一致で連結統合するため、Union-Findでグループ化
            id_to_prop: Dict[str, Dict[str, Any]] = {}
            parent: Dict[str, str] = {}
            rank: Dict[str, int] = {}

            def _find(x: str) -> str:
                px = parent.get(x, x)
                if px != x:
                    parent[x] = _find(px)
                return parent.get(x, x)

            def _union(a: str, b: str):
                ra, rb = _find(a), _find(b)
                if ra == rb:
                    return
                rka, rkb = rank.get(ra, 0), rank.get(rb, 0)
                if rka < rkb:
                    parent[ra] = rb
                elif rka > rkb:
                    parent[rb] = ra
                else:
                    parent[rb] = ra
                    rank[ra] = rka + 1

            key_owner: Dict[str, str] = {}
            for p in rows:
                pid = p.get("id")
                if not pid:
                    continue
                id_to_prop[pid] = p
                parent.setdefault(pid, pid)
                rank.setdefault(pid, 0)

                addr = self._normalize_address(p.get("address"))
                station = self._normalize_station_name(p.get("nearest_station"))
                land_area = round(float(p["land_area"]), 1) if p.get("land_area") else None
                bld_area = round(float(p["building_area"]), 1) if p.get("building_area") else None
                price = p.get("asking_price")
                station_dist = p.get("station_distance_min")
                city = (p.get("city_code") or p.get("prefecture_code") or "").strip()
                name = _norm_name(p.get("name"))

                keys = []
                url_key = self._normalize_source_url(p.get("source_url"))
                if url_key:
                    keys.append(f"url:{url_key}")

                # 駅距離・価格・延床面積（市区町村＋駅名で誤結合抑制）
                if price is not None and bld_area is not None and station_dist is not None and city:
                    try:
                        price_exact = int(float(price))
                        dist_exact = int(float(station_dist))
                        keys.append(f"spd:{city}|{station}|{dist_exact}|{price_exact}|{bld_area}")
                    except (TypeError, ValueError):
                        pass

                # 同価格帯・同規模・近接駅のフォールバック（URL違いの横断重複吸収）
                if price is not None and bld_area is not None and city:
                    try:
                        price_bucket = int(float(price) / 500_000)
                        keys.append(f"spb:{city}|{station}|{price_bucket}|{bld_area}")
                    except (TypeError, ValueError):
                        pass

                # 同駅・同価格帯・同面積帯・近接座標（半径~1km）フォールバック
                lat = p.get("latitude")
                lng = p.get("longitude")
                area_for_bucket = bld_area if bld_area is not None else land_area
                if (
                    station
                    and price is not None
                    and area_for_bucket is not None
                    and lat is not None
                    and lng is not None
                ):
                    try:
                        latf = float(lat)
                        lngf = float(lng)
                        price_bucket = int(float(price) / 1_000_000)   # 100万円帯
                        area_bucket = int(float(area_for_bucket) / 5)   # 5㎡帯
                        lat_bucket = round(latf, 2)                     # 約1.1km
                        lng_bucket = round(lngf, 2)
                        keys.append(
                            f"sgp:{city}|{station}|{price_bucket}|{area_bucket}|{lat_bucket}|{lng_bucket}"
                        )
                    except (TypeError, ValueError):
                        pass

                # 面積欠損時でも、同駅・同価格帯・超近接座標なら候補化（過度統合防止で座標は厳しめ）
                if (
                    station
                    and price is not None
                    and lat is not None
                    and lng is not None
                    and station_dist is not None
                ):
                    try:
                        latf = float(lat)
                        lngf = float(lng)
                        price_bucket = int(float(price) / 500_000)
                        dist_bucket = int(float(station_dist) / 2)
                        keys.append(
                            f"sgx:{city}|{station}|{price_bucket}|{round(latf, 3)}|{round(lngf, 3)}|{dist_bucket}"
                        )
                    except (TypeError, ValueError):
                        pass

                # 住所ベース
                if addr:
                    try:
                        price_bucket = int(float(price) / 500_000) if price else -1
                    except (TypeError, ValueError):
                        price_bucket = -1
                    keys.append(f"fp:{addr}|{price_bucket}|{land_area}|{bld_area}|{station}")

                # 名称ベース（住所欠落や文字化けの取りこぼし補完）
                if name and price is not None and bld_area is not None and city:
                    try:
                        price_bucket = int(float(price) / 500_000)
                        keys.append(f"np:{city}|{name[:32]}|{price_bucket}|{bld_area}")
                    except (TypeError, ValueError):
                        pass

                for k in keys:
                    owner = key_owner.get(k)
                    if owner and owner != pid:
                        _union(owner, pid)
                    else:
                        key_owner[k] = pid

            # 第2段: 同駅・同価格帯クラスタ内でスコア評価して結合（座標近接を重視）
            station_price_buckets: Dict[str, List[Dict[str, Any]]] = {}
            for p in id_to_prop.values():
                station = self._normalize_station_name(p.get("nearest_station"))
                city = (p.get("city_code") or p.get("prefecture_code") or "").strip()
                try:
                    price_bucket = int(float(p.get("asking_price")) / 500_000)
                except (TypeError, ValueError):
                    continue
                if not station:
                    continue
                station_price_buckets.setdefault(f"{city}|{station}|{price_bucket}", []).append(p)

            for plist in station_price_buckets.values():
                n = len(plist)
                if n < 2:
                    continue
                # bucketが大きすぎる場合の暴走回避
                if n > 60:
                    continue
                for i in range(n):
                    a = plist[i]
                    aid = a.get("id")
                    if not aid:
                        continue
                    for j in range(i + 1, n):
                        b = plist[j]
                        bid = b.get("id")
                        if not bid:
                            continue

                        score = 0.0
                        dkm = _distance_km(a, b)
                        if dkm is not None:
                            if dkm <= 0.2:
                                score += 3.0
                            elif dkm <= 0.5:
                                score += 1.8
                            elif dkm <= 0.8:
                                score += 1.0

                        try:
                            ad = float(a.get("station_distance_min")) if a.get("station_distance_min") is not None else None
                            bd = float(b.get("station_distance_min")) if b.get("station_distance_min") is not None else None
                        except (TypeError, ValueError):
                            ad = bd = None
                        if ad is not None and bd is not None and abs(ad - bd) <= 2:
                            score += 1.2

                        try:
                            ap = float(a.get("asking_price")) if a.get("asking_price") is not None else None
                            bp = float(b.get("asking_price")) if b.get("asking_price") is not None else None
                        except (TypeError, ValueError):
                            ap = bp = None
                        if ap is not None and bp is not None and _rel_diff(ap, bp) <= 0.02:
                            score += 1.0

                        try:
                            aba = float(a.get("building_area")) if a.get("building_area") is not None else None
                            bba = float(b.get("building_area")) if b.get("building_area") is not None else None
                        except (TypeError, ValueError):
                            aba = bba = None
                        if aba is not None and bba is not None:
                            if _rel_diff(aba, bba) <= 0.08:
                                score += 1.8
                        else:
                            try:
                                ala = float(a.get("land_area")) if a.get("land_area") is not None else None
                                bla = float(b.get("land_area")) if b.get("land_area") is not None else None
                            except (TypeError, ValueError):
                                ala = bla = None
                            if ala is not None and bla is not None and _rel_diff(ala, bla) <= 0.10:
                                score += 1.0

                        an = _norm_name(a.get("name"))
                        bn = _norm_name(b.get("name"))
                        if an and bn:
                            if an == bn and len(an) >= 6:
                                score += 2.0
                            elif (an in bn or bn in an) and max(len(an), len(bn)) >= 8:
                                score += 1.0

                        aa = self._normalize_address(a.get("address"))
                        ba = self._normalize_address(b.get("address"))
                        if aa and ba:
                            if aa == ba:
                                score += 2.5
                            elif aa[:10] == ba[:10]:
                                score += 1.0

                        # 閾値: 位置近接がある程度あるときのみ統合
                        if score >= 5.0 and (dkm is None or dkm <= 0.8):
                            _union(str(aid), str(bid))

            by_root: Dict[str, List[Dict[str, Any]]] = {}
            for pid, p in id_to_prop.items():
                root = _find(pid)
                by_root.setdefault(root, []).append(p)

            duplicate_groups = [g for g in by_root.values() if len(g) >= min_group_size][:max_groups]
            summary = {
                "dry_run": dry_run,
                "group_count": len(duplicate_groups),
                "merged_records": 0,
                "relinked_judgments": 0,
                "groups": [],
            }

            for grp in duplicate_groups:
                grp_sorted = sorted(
                    grp,
                    key=lambda x: (
                        # 情報密度が高いものを優先
                        sum(self._value_score(x.get(c)) for c in [
                            "asking_price", "land_area", "building_area", "structure",
                            "built_year", "current_rent_annual", "gross_yield",
                            "nearest_station", "source_url",
                        ]),
                        x.get("updated_at") or "",
                    ),
                    reverse=True,
                )
                canonical = dict(grp_sorted[0])
                duplicates = [dict(x) for x in grp_sorted[1:]]

                # canonicalを重複群の情報で補完
                for d in duplicates:
                    for col in [
                        "name", "address", "prefecture_code", "city_code",
                        "latitude", "longitude", "asking_price", "land_area",
                        "building_area", "structure", "built_year", "building_age",
                        "units", "current_rent_annual", "gross_yield", "nearest_station",
                        "station_distance_min", "station_id", "source", "source_url",
                    ]:
                        if not canonical.get(col) and d.get(col):
                            canonical[col] = d.get(col)

                merged_data = {}
                try:
                    merged_data = json.loads(canonical.get("data_json") or "{}")
                    if not isinstance(merged_data, dict):
                        merged_data = {}
                except Exception:
                    merged_data = {}

                merged_ids = [d.get("id") for d in duplicates if d.get("id")]
                merged_sources = sorted({
                    *(x.get("source") for x in grp if x.get("source")),
                })
                merged_urls = sorted({
                    self._normalize_source_url(x.get("source_url"))
                    for x in grp
                    if x.get("source_url")
                })
                merged_data["merged_from_ids"] = sorted({
                    *(merged_data.get("merged_from_ids", []) if isinstance(merged_data.get("merged_from_ids"), list) else []),
                    *merged_ids,
                })
                merged_data["merged_sources"] = merged_sources
                merged_data["merged_source_urls"] = merged_urls

                relinked = 0
                if not dry_run:
                    conn.execute("""
                        UPDATE properties SET
                            name=?, address=?, prefecture_code=?, city_code=?,
                            latitude=?, longitude=?, asking_price=?, land_area=?,
                            building_area=?, structure=?, built_year=?, building_age=?,
                            units=?, current_rent_annual=?, gross_yield=?,
                            nearest_station=?, station_distance_min=?, station_id=?,
                            source=?, source_url=?, data_json=?,
                            updated_at=datetime('now','localtime')
                        WHERE id=?
                    """, (
                        canonical.get("name") or "",
                        canonical.get("address") or "",
                        canonical.get("prefecture_code") or "",
                        canonical.get("city_code") or "",
                        canonical.get("latitude"),
                        canonical.get("longitude"),
                        canonical.get("asking_price"),
                        canonical.get("land_area"),
                        canonical.get("building_area"),
                        canonical.get("structure"),
                        canonical.get("built_year"),
                        canonical.get("building_age"),
                        canonical.get("units"),
                        canonical.get("current_rent_annual"),
                        canonical.get("gross_yield"),
                        canonical.get("nearest_station"),
                        canonical.get("station_distance_min"),
                        canonical.get("station_id"),
                        canonical.get("source"),
                        self._normalize_source_url(canonical.get("source_url")),
                        json.dumps(merged_data, ensure_ascii=False),
                        canonical.get("id"),
                    ))

                    for d in duplicates:
                        dup_id = d.get("id")
                        if not dup_id:
                            continue
                        cur = conn.execute("""
                            UPDATE judgments
                            SET property_id = ?
                            WHERE property_id = ?
                        """, (canonical.get("id"), dup_id))
                        relinked += cur.rowcount if cur else 0
                        conn.execute("DELETE FROM properties WHERE id = ?", (dup_id,))

                summary["merged_records"] += len(duplicates)
                summary["relinked_judgments"] += relinked
                summary["groups"].append({
                    "canonical_id": canonical.get("id"),
                    "canonical_name": canonical.get("name"),
                    "canonical_address": canonical.get("address"),
                    "duplicate_ids": merged_ids,
                    "group_size": len(grp),
                    "relinked_judgments": relinked,
                })

            return summary

    def get_properties(
        self, city_code: str = "", station_id: str = "", limit: int = 200,
        active_only: bool = True,
    ) -> List[Dict]:
        with self._conn() as conn:
            sql = "SELECT * FROM properties WHERE 1=1"
            params = []
            if active_only:
                sql += " AND COALESCE(listing_status, 'active') = 'active'"
            if station_id:
                sql += " AND station_id=?"
                params.append(station_id)
            elif city_code:
                sql += " AND city_code=?"
                params.append(city_code)
            sql += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_source_verification_targets(
        self,
        table: str,
        limit: int = 300,
        stale_hours: int = 24,
    ) -> List[Dict]:
        """掲載有無チェック対象（URLあり・掲載中・一定時間未確認）を取得"""
        if table not in {"properties", "land_listings"}:
            return []
        id_col = "id"
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT {id_col} AS id, source_url, listing_status, verify_fail_count, last_verified_at
                FROM {table}
                WHERE source_url IS NOT NULL
                  AND TRIM(source_url) <> ''
                  AND COALESCE(listing_status, 'active') = 'active'
                  AND (
                        last_verified_at IS NULL
                        OR last_verified_at <= datetime('now','localtime', ?)
                  )
                ORDER BY COALESCE(last_verified_at, '1970-01-01 00:00:00') ASC, updated_at DESC
                LIMIT ?
                """,
                (f"-{max(1, int(stale_hours))} hours", max(1, int(limit))),
            ).fetchall()
            return [dict(r) for r in rows]

    def record_source_verification_result(
        self,
        table: str,
        row_id: Any,
        is_alive: bool,
        http_status: Optional[int] = None,
        note: str = "",
        confirm_failures: int = 2,
    ):
        """掲載有無チェック結果を反映（連続失敗でdelisted化）"""
        if table not in {"properties", "land_listings"}:
            return
        with self._conn() as conn:
            existing = conn.execute(
                f"SELECT verify_fail_count FROM {table} WHERE id=? LIMIT 1",
                (row_id,),
            ).fetchone()
            if not existing:
                return
            fail_count = int((dict(existing).get("verify_fail_count") or 0))

            if is_alive:
                conn.execute(
                    f"""
                    UPDATE {table}
                    SET listing_status='active',
                        verify_fail_count=0,
                        delisted_confirmed_at=NULL,
                        last_seen_at=datetime('now','localtime'),
                        last_verified_at=datetime('now','localtime'),
                        last_verified_http_status=?,
                        verify_note=?,
                        updated_at=datetime('now','localtime')
                    WHERE id=?
                    """,
                    (http_status, note[:300] if note else None, row_id),
                )
                return

            fail_count += 1
            should_delist = fail_count >= max(1, int(confirm_failures))
            conn.execute(
                f"""
                UPDATE {table}
                SET listing_status=?,
                    verify_fail_count=?,
                    delisted_confirmed_at=CASE
                        WHEN ? THEN COALESCE(delisted_confirmed_at, datetime('now','localtime'))
                        ELSE delisted_confirmed_at
                    END,
                    last_verified_at=datetime('now','localtime'),
                    last_verified_http_status=?,
                    verify_note=?,
                    updated_at=datetime('now','localtime')
                WHERE id=?
                """,
                (
                    "delisted" if should_delist else "active",
                    fail_count,
                    1 if should_delist else 0,
                    http_status,
                    note[:300] if note else None,
                    row_id,
                ),
            )

    # ===== 判定結果 =====

    def save_judgment(self, result: Dict):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO judgments
                    (property_id, grade, recommendation, overall_score,
                     confidence, key_metrics_json, full_result_json)
                VALUES (?,?,?,?,?,?,?)
            """, (
                result.get("property_id", ""), result.get("grade", ""),
                result.get("recommendation", ""), result.get("overall_score", 0),
                result.get("confidence", 0),
                json.dumps(result.get("key_metrics", {}), ensure_ascii=False),
                json.dumps(result, ensure_ascii=False),
            ))

    def upsert_property_analysis_cache(
        self,
        analysis_key: str,
        property_id: Optional[str],
        property_type: str,
        name: str,
        address: str,
        grade: Optional[str],
        score: Optional[float],
        scenario: Optional[str],
        selected: Optional[Dict],
        as_is: Optional[Dict],
        rebuild: Optional[Dict],
    ):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO property_analysis_cache
                    (analysis_key, property_id, property_type, name, address,
                     grade, score, scenario, selected_json, as_is_json, rebuild_json,
                     analyzed_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?, datetime('now','localtime'), datetime('now','localtime'))
                ON CONFLICT(analysis_key) DO UPDATE SET
                    property_id=excluded.property_id,
                    property_type=excluded.property_type,
                    name=excluded.name,
                    address=excluded.address,
                    grade=excluded.grade,
                    score=excluded.score,
                    scenario=excluded.scenario,
                    selected_json=excluded.selected_json,
                    as_is_json=excluded.as_is_json,
                    rebuild_json=excluded.rebuild_json,
                    analyzed_at=datetime('now','localtime'),
                    updated_at=datetime('now','localtime')
                """,
                (
                    analysis_key,
                    property_id,
                    property_type or "property",
                    name or "",
                    address or "",
                    grade,
                    score,
                    scenario,
                    json.dumps(selected or {}, ensure_ascii=False),
                    json.dumps(as_is or {}, ensure_ascii=False),
                    json.dumps(rebuild or {}, ensure_ascii=False),
                ),
            )

    def get_property_analysis_cache(self, analysis_key: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM property_analysis_cache WHERE analysis_key=? LIMIT 1",
                (analysis_key,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            for k in ("selected_json", "as_is_json", "rebuild_json"):
                try:
                    d[k.replace("_json", "")] = json.loads(d.get(k) or "{}")
                except Exception:
                    d[k.replace("_json", "")] = {}
            return d

    def get_property_analysis_cache_bulk(self, analysis_keys: List[str]) -> Dict[str, Dict]:
        keys = [str(k or "").strip() for k in (analysis_keys or []) if str(k or "").strip()]
        if not keys:
            return {}
        rows = []
        with self._conn() as conn:
            chunk = 900
            for i in range(0, len(keys), chunk):
                part = keys[i:i + chunk]
                placeholders = ",".join("?" for _ in part)
                rows.extend(
                    conn.execute(
                        f"SELECT * FROM property_analysis_cache WHERE analysis_key IN ({placeholders})",
                        part,
                    ).fetchall()
                )
        out: Dict[str, Dict] = {}
        for row in rows:
            d = dict(row)
            for k in ("selected_json", "as_is_json", "rebuild_json"):
                try:
                    d[k.replace("_json", "")] = json.loads(d.get(k) or "{}")
                except Exception:
                    d[k.replace("_json", "")] = {}
            out[str(d.get("analysis_key") or "")] = d
        return out

    # ===== バッチログ =====

    def start_batch(self, batch_type: str, pref: str, city: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO batch_logs (batch_type, prefecture_code, city_code)
                VALUES (?,?,?)
            """, (batch_type, pref, city))
            return cur.lastrowid

    def finish_batch(
        self, batch_id: int, status: str,
        fetched: int = 0, inserted: int = 0, error: str = None,
    ):
        with self._conn() as conn:
            conn.execute("""
                UPDATE batch_logs SET
                    status=?, records_fetched=?, records_inserted=?,
                    error_message=?, finished_at=datetime('now','localtime')
                WHERE id=?
            """, (status, fetched, inserted, error, batch_id))

    def get_batch_logs(self, limit: int = 50) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM batch_logs ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def last_batch_time(self, batch_type: str, pref: str = "") -> Optional[str]:
        with self._conn() as conn:
            sql = """
                SELECT finished_at FROM batch_logs
                WHERE batch_type=? AND status='completed'
            """
            params = [batch_type]
            if pref:
                sql += " AND prefecture_code=?"
                params.append(pref)
            sql += " ORDER BY id DESC LIMIT 1"
            row = conn.execute(sql, params).fetchone()
            return dict(row)["finished_at"] if row else None

    # ===== エリアメトリクス（レガシー） =====

    def upsert_area_metrics(self, metrics: Dict):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO area_metrics
                    (city_code, city_name, prefecture_code, year,
                     avg_land_price_sqm, median_land_price_sqm,
                     land_price_change_rate,
                     avg_rent_per_sqm, median_rent_per_sqm,
                     implied_yield, yield_gap, distortion_score,
                     sample_count_land, sample_count_rent, sample_count_tx,
                     center_lat, center_lng)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(city_code, year) DO UPDATE SET
                    avg_land_price_sqm=excluded.avg_land_price_sqm,
                    median_land_price_sqm=excluded.median_land_price_sqm,
                    land_price_change_rate=excluded.land_price_change_rate,
                    avg_rent_per_sqm=excluded.avg_rent_per_sqm,
                    median_rent_per_sqm=excluded.median_rent_per_sqm,
                    implied_yield=excluded.implied_yield,
                    yield_gap=excluded.yield_gap,
                    distortion_score=excluded.distortion_score,
                    sample_count_land=excluded.sample_count_land,
                    sample_count_rent=excluded.sample_count_rent,
                    sample_count_tx=excluded.sample_count_tx,
                    center_lat=excluded.center_lat,
                    center_lng=excluded.center_lng,
                    computed_at=datetime('now','localtime')
            """, (
                metrics["city_code"], metrics.get("city_name", ""),
                metrics["prefecture_code"], metrics.get("year", 0),
                metrics.get("avg_land_price_sqm"), metrics.get("median_land_price_sqm"),
                metrics.get("land_price_change_rate"),
                metrics.get("avg_rent_per_sqm"), metrics.get("median_rent_per_sqm"),
                metrics.get("implied_yield"), metrics.get("yield_gap"),
                metrics.get("distortion_score"),
                metrics.get("sample_count_land", 0),
                metrics.get("sample_count_rent", 0),
                metrics.get("sample_count_tx", 0),
                metrics.get("center_lat"), metrics.get("center_lng"),
            ))

    def get_area_metrics(
        self, prefecture_code: str = "", year: int = None,
        sort_by: str = "distortion_score",
    ) -> List[Dict]:
        with self._conn() as conn:
            sql = "SELECT * FROM area_metrics WHERE 1=1"
            params = []
            if prefecture_code:
                sql += " AND prefecture_code=?"
                params.append(prefecture_code)
            if year:
                sql += " AND year=?"
                params.append(year)
            sql += f" ORDER BY {sort_by} DESC"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # ===== 駅メトリクス =====

    def upsert_station_metrics(self, metrics: Dict):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO station_metrics
                    (station_id, station_name, line_name,
                     prefecture_code, city_code, year,
                     avg_land_price_sqm, median_land_price_sqm,
                     land_price_change_rate,
                     avg_rent_per_sqm, median_rent_per_sqm,
                     implied_yield, yield_gap, distortion_score,
                     sample_count_land, sample_count_rent, sample_count_tx,
                     center_lat, center_lng)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(station_id, year) DO UPDATE SET
                    station_name=excluded.station_name,
                    line_name=excluded.line_name,
                    avg_land_price_sqm=excluded.avg_land_price_sqm,
                    median_land_price_sqm=excluded.median_land_price_sqm,
                    land_price_change_rate=excluded.land_price_change_rate,
                    avg_rent_per_sqm=excluded.avg_rent_per_sqm,
                    median_rent_per_sqm=excluded.median_rent_per_sqm,
                    implied_yield=excluded.implied_yield,
                    yield_gap=excluded.yield_gap,
                    distortion_score=excluded.distortion_score,
                    sample_count_land=excluded.sample_count_land,
                    sample_count_rent=excluded.sample_count_rent,
                    sample_count_tx=excluded.sample_count_tx,
                    center_lat=excluded.center_lat,
                    center_lng=excluded.center_lng,
                    computed_at=datetime('now','localtime')
            """, (
                metrics["station_id"], metrics.get("station_name", ""),
                metrics.get("line_name", ""),
                metrics["prefecture_code"], metrics.get("city_code", ""),
                metrics.get("year", 0),
                metrics.get("avg_land_price_sqm"),
                metrics.get("median_land_price_sqm"),
                metrics.get("land_price_change_rate"),
                metrics.get("avg_rent_per_sqm"),
                metrics.get("median_rent_per_sqm"),
                metrics.get("implied_yield"), metrics.get("yield_gap"),
                metrics.get("distortion_score"),
                metrics.get("sample_count_land", 0),
                metrics.get("sample_count_rent", 0),
                metrics.get("sample_count_tx", 0),
                metrics.get("center_lat"), metrics.get("center_lng"),
            ))

    def get_station_metrics(
        self, prefecture_code: str = "", year: int = None,
        sort_by: str = "distortion_score",
    ) -> List[Dict]:
        with self._conn() as conn:
            sql = "SELECT * FROM station_metrics WHERE 1=1"
            params = []
            if prefecture_code:
                sql += " AND prefecture_code=?"
                params.append(prefecture_code)
            if year:
                sql += " AND year=?"
                params.append(year)
            sql += f" ORDER BY {sort_by} DESC"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # ===== station_id一括更新 =====

    def assign_station_ids(self, resolver_fn) -> int:
        """station_idが未設定のレコードにstation_idを付与"""
        updated = 0
        with self._conn() as conn:
            for table in ["land_prices", "transactions", "rental_comps", "properties"]:
                # テーブルのカラム一覧を取得
                cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                has_pref = "prefecture_code" in cols
                pref_col = "prefecture_code" if has_pref else "''"
                rows = conn.execute(f"""
                    SELECT id, nearest_station, latitude, longitude, {pref_col} as prefecture_code
                    FROM {table}
                    WHERE station_id IS NULL OR station_id = ''
                """).fetchall()
                for row in rows:
                    row = dict(row)
                    sid = resolver_fn(
                        nearest_station_text=row.get("nearest_station"),
                        lat=row.get("latitude"),
                        lon=row.get("longitude"),
                        pref_code=row.get("prefecture_code"),
                    )
                    if sid:
                        conn.execute(
                            f"UPDATE {table} SET station_id=? WHERE id=?",
                            (sid, row["id"])
                        )
                        updated += 1
        return updated

    def reconcile_station_refs(self, table: str = "properties", limit: int = 5000, force_nearest: bool = False) -> int:
        """既存レコードの駅名を実在駅へ補正しstation_idを再付与"""
        if table not in {"properties", "rental_comps"}:
            return 0
        updated = 0
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                    SELECT id, name, address, nearest_station, station_distance_min, latitude, longitude, prefecture_code, city_code
                    FROM {table}
                    ORDER BY updated_at DESC
                    LIMIT ?
                """ if table == "properties" else f"""
                    SELECT id, '' AS name, address, nearest_station, station_distance_min, latitude, longitude, '' AS prefecture_code, city_code
                    FROM {table}
                    ORDER BY fetched_at DESC
                    LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            try:
                from data.station_master import resolve_station_id, STATION_MAP, find_nearest_station
                from data.geocoder import Geocoder
            except Exception:
                return 0

            def _pref_from_address(addr: Any) -> str:
                s = str(addr or "")
                if "東京都" in s:
                    return "13"
                if "神奈川県" in s:
                    return "14"
                if "埼玉県" in s:
                    return "11"
                if "千葉県" in s:
                    return "12"
                return ""

            def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
                r = 6371.0
                p1 = math.radians(lat1)
                p2 = math.radians(lat2)
                dp = math.radians(lat2 - lat1)
                dl = math.radians(lon2 - lon1)
                a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
                return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))

            geocoder = Geocoder()
            geo_cache: Dict[str, Any] = {}

            for row in rows:
                r = dict(row)
                pref_from_addr = _pref_from_address(r.get("address"))
                pref_code = (
                    pref_from_addr
                    or str(r.get("prefecture_code") or "")
                    or (str(r.get("city_code") or "")[:2] if r.get("city_code") else "")
                )

                # 0) 住所ジオコードを最優先（住所が取れている場合）
                addr_near = None
                if r.get("address"):
                    addr = str(r.get("address"))
                    if addr in geo_cache:
                        gc = geo_cache[addr]
                    else:
                        try:
                            gc = geocoder.geocode(addr)
                        except Exception:
                            gc = None
                        geo_cache[addr] = gc
                    if gc:
                        lat = float(gc[0])
                        lon = float(gc[1])
                        addr_near = find_nearest_station(
                            lat,
                            lon,
                            max_distance_km=8.0,
                            pref_code=None if force_nearest else (pref_code or None),
                        )
                        if (not addr_near) or float(addr_near.get("distance_km") or 999.0) > 20.0:
                            near_any = find_nearest_station(lat, lon, max_distance_km=8.0, pref_code=None)
                            if near_any:
                                addr_near = near_any

                # 1) 駅名単体で解決
                sid = resolve_station_id(
                    nearest_station_text=r.get("nearest_station"),
                    lat=r.get("latitude"),
                    lon=r.get("longitude"),
                    pref_code=pref_code or None,
                )

                # 2) 物件名・住所も含めて再解決（「◯◯駅」が名前に入るケース）
                station_hint = f"{r.get('name') or ''} {r.get('address') or ''}"
                sid_hint = resolve_station_id(
                    nearest_station_text=station_hint,
                    lat=r.get("latitude"),
                    lon=r.get("longitude"),
                    pref_code=pref_code or None,
                )
                if sid_hint:
                    explicit_station_in_hint = bool(
                        re.search(r"[^\s/／()（）,、]{2,}駅\s*(?:徒歩|バス|歩|約)?", station_hint)
                    )
                    if explicit_station_in_hint or not sid:
                        sid = sid_hint

                try:
                    cur_lat = float(r.get("latitude")) if r.get("latitude") is not None else None
                    cur_lon = float(r.get("longitude")) if r.get("longitude") is not None else None
                except (TypeError, ValueError):
                    cur_lat = cur_lon = None
                if not (r.get("address") and addr_near):
                    lat = cur_lat
                    lon = cur_lon

                # 3) 必要時のみ住所ジオコード -> 実在最寄駅で補正
                need_geocode = False
                if addr_near:
                    sid = addr_near.get("station_id") or sid
                if not sid:
                    need_geocode = True
                elif sid and sid in STATION_MAP and cur_lat is not None and cur_lon is not None:
                    s0 = STATION_MAP[sid]
                    d0 = _haversine_km(cur_lat, cur_lon, float(s0["lat"]), float(s0["lon"]))
                    walk0 = None
                    if table == "properties":
                        try:
                            walk0 = float(r.get("station_distance_min")) if r.get("station_distance_min") is not None else None
                        except (TypeError, ValueError):
                            walk0 = None
                    if walk0 and walk0 > 0:
                        expected = max(0.08 * walk0, 0.2)
                        need_geocode = d0 > max(2.0, expected * 4.0)
                    else:
                        need_geocode = d0 > 8.0
                elif cur_lat is None or cur_lon is None:
                    need_geocode = True

                if need_geocode and r.get("address"):
                    addr = str(r.get("address"))
                    if addr in geo_cache:
                        gc = geo_cache[addr]
                    else:
                        try:
                            gc = geocoder.geocode(addr)
                        except Exception:
                            gc = None
                        geo_cache[addr] = gc
                    if gc:
                        lat, lon = gc
                        near = find_nearest_station(
                            lat,
                            lon,
                            max_distance_km=8.0,
                            pref_code=None if force_nearest else (pref_code or None),
                        )
                        if (not near) or float(near.get("distance_km") or 999.0) > 20.0:
                            near_any = find_nearest_station(lat, lon, max_distance_km=8.0, pref_code=None)
                            if near_any:
                                near = near_any
                        if near:
                            near_sid = near.get("station_id")
                            # 既存sidがある場合は、住所ジオコードとの整合を確認して不整合なら上書き
                            if sid and sid in STATION_MAP:
                                s0 = STATION_MAP[sid]
                                d0 = _haversine_km(lat, lon, float(s0["lat"]), float(s0["lon"]))
                                if d0 > 15.0:  # 住所から15km超は別駅とみなす
                                    sid = near_sid
                            else:
                                sid = near_sid
                            # 座標が未設定か、都県と矛盾する場合はジオコード結果を反映
                            if table == "properties":
                                should_update_coord = cur_lat is None or cur_lon is None
                                if not should_update_coord:
                                    if pref_from_addr and not (
                                        (pref_from_addr == "13" and 35.45 <= cur_lat <= 35.92 and 139.45 <= cur_lon <= 139.95) or
                                        (pref_from_addr == "14" and 35.10 <= cur_lat <= 35.75 and 139.10 <= cur_lon <= 139.90) or
                                        (pref_from_addr == "11" and 35.70 <= cur_lat <= 36.35 and 138.70 <= cur_lon <= 139.95) or
                                        (pref_from_addr == "12" and 34.90 <= cur_lat <= 36.20 and 139.70 <= cur_lon <= 140.95)
                                    ):
                                        should_update_coord = True
                                if should_update_coord:
                                    cur = conn.execute(
                                        "UPDATE properties SET latitude=?, longitude=? WHERE id=?",
                                        (lat, lon, r["id"]),
                                    )
                                    updated += cur.rowcount if cur else 0

                near_final = None
                if addr_near:
                    near_final = addr_near
                if lat is not None and lon is not None:
                    near_final = find_nearest_station(
                        lat,
                        lon,
                        max_distance_km=8.0,
                        pref_code=None if force_nearest else (pref_code or None),
                    )
                    if (not near_final) or float(near_final.get("distance_km") or 999.0) > 20.0:
                        near_any = find_nearest_station(lat, lon, max_distance_km=8.0, pref_code=None)
                        if near_any:
                            near_final = near_any
                if force_nearest and near_final:
                    sid = near_final.get("station_id") or sid

                if not sid:
                    # 解決不可の駅名は誤った位置推定の原因になるためクリア
                    if r.get("nearest_station"):
                        cur = conn.execute(
                            f"UPDATE {table} SET nearest_station=NULL, station_id=NULL WHERE id=?",
                            (r["id"],),
                        )
                        updated += cur.rowcount if cur else 0
                    continue
                sname = STATION_MAP.get(sid, {}).get("name")
                station_distance_min = r.get("station_distance_min")
                if near_final and float(near_final.get("distance_km") or 0.0) > 0:
                    if force_nearest or not station_distance_min:
                        station_distance_min = max(1, min(120, int(round(float(near_final["distance_km"]) * 12.5))))
                cur = conn.execute(
                    f"UPDATE {table} SET station_id=?, nearest_station=?, station_distance_min=? WHERE id=?",
                    (sid, sname, station_distance_min, r["id"]),
                )
                updated += cur.rowcount if cur else 0
        return updated

    def reconcile_land_listing_station_refs(self, limit: int = 5000, force_nearest: bool = False) -> int:
        """
        土地物件の駅名/徒歩分数を住所ジオコードと駅マスタで補正。
        OCRや簡易パース誤読の残件を後段で一括クリーニングする。
        """
        updated = 0
        with self._conn() as conn:
            rows = conn.execute(
                """
                    SELECT id, address, station, walk_minutes, latitude, longitude
                    FROM land_listings
                    ORDER BY updated_at DESC, id DESC
                    LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            try:
                from data.station_master import resolve_station_id, STATION_MAP, find_nearest_station
                from data.geocoder import Geocoder
            except Exception:
                return 0

            def _pref_from_address(addr: Any) -> str:
                s = str(addr or "")
                if "東京都" in s:
                    return "13"
                if "神奈川県" in s:
                    return "14"
                if "埼玉県" in s:
                    return "11"
                if "千葉県" in s:
                    return "12"
                return ""

            def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
                r = 6371.0
                p1 = math.radians(lat1)
                p2 = math.radians(lat2)
                dp = math.radians(lat2 - lat1)
                dl = math.radians(lon2 - lon1)
                a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
                return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))

            geocoder = Geocoder()
            geo_cache: Dict[str, Any] = {}

            for row in rows:
                r = dict(row)
                pref_code = _pref_from_address(r.get("address"))

                lat = r.get("latitude")
                lon = r.get("longitude")
                try:
                    lat = float(lat) if lat is not None else None
                    lon = float(lon) if lon is not None else None
                except (TypeError, ValueError):
                    lat = lon = None

                # 0) 住所ジオコードを最優先（住所が取れている場合）
                addr_near = None
                if r.get("address"):
                    addr = str(r.get("address"))
                    if addr in geo_cache:
                        gc = geo_cache[addr]
                    else:
                        try:
                            gc = geocoder.geocode(addr)
                        except Exception:
                            gc = None
                        geo_cache[addr] = gc
                    if gc:
                        lat = float(gc[0])
                        lon = float(gc[1])
                        addr_near = find_nearest_station(
                            lat,
                            lon,
                            max_distance_km=8.0,
                            pref_code=None if force_nearest else (pref_code or None),
                        )
                        if (not addr_near) or float(addr_near.get("distance_km") or 999.0) > 20.0:
                            near_any = find_nearest_station(lat, lon, max_distance_km=8.0, pref_code=None)
                            if near_any:
                                addr_near = near_any

                sid = resolve_station_id(
                    nearest_station_text=r.get("station"),
                    lat=lat,
                    lon=lon,
                    pref_code=pref_code or None,
                )

                need_geocode = False
                if not sid or lat is None or lon is None:
                    need_geocode = True
                elif sid in STATION_MAP:
                    s0 = STATION_MAP[sid]
                    d0 = _haversine_km(lat, lon, float(s0["lat"]), float(s0["lon"]))
                    walk0 = r.get("walk_minutes")
                    try:
                        walk0 = float(walk0) if walk0 is not None else None
                    except (TypeError, ValueError):
                        walk0 = None
                    if walk0 and walk0 > 0:
                        expected = max(0.08 * walk0, 0.2)
                        need_geocode = d0 > max(2.0, expected * 4.0)
                    else:
                        need_geocode = d0 > 8.0

                if need_geocode and r.get("address"):
                    addr = str(r.get("address"))
                    if addr in geo_cache:
                        gc = geo_cache[addr]
                    else:
                        try:
                            gc = geocoder.geocode(addr)
                        except Exception:
                            gc = None
                        geo_cache[addr] = gc
                    if gc:
                        glat, glon = float(gc[0]), float(gc[1])
                        if lat is None or lon is None or _haversine_km(lat, lon, glat, glon) > 8.0:
                            lat, lon = glat, glon
                        sid = resolve_station_id(
                            nearest_station_text=r.get("station"),
                            lat=lat,
                            lon=lon,
                            pref_code=pref_code or None,
                        )
                if addr_near:
                    sid = addr_near.get("station_id") or sid

                near = addr_near
                if lat is not None and lon is not None:
                    near = find_nearest_station(
                        lat,
                        lon,
                        max_distance_km=8.0,
                        pref_code=None if force_nearest else (pref_code or None),
                    )
                    if (not near) or float(near.get("distance_km") or 999.0) > 20.0:
                        near_any = find_nearest_station(lat, lon, max_distance_km=8.0, pref_code=None)
                        if near_any:
                            near = near_any

                suspicious = False
                if sid and sid in STATION_MAP and lat is not None and lon is not None:
                    s0 = STATION_MAP[sid]
                    d0 = _haversine_km(lat, lon, float(s0["lat"]), float(s0["lon"]))
                    walk = r.get("walk_minutes")
                    try:
                        walk = float(walk) if walk is not None else None
                    except (TypeError, ValueError):
                        walk = None
                    if walk and walk > 0:
                        expected = max(0.08 * walk, 0.2)
                        suspicious = d0 > max(2.0, expected * 4.0)
                    else:
                        suspicious = d0 > 8.0
                elif r.get("station"):
                    suspicious = True

                new_station = r.get("station")
                new_walk = r.get("walk_minutes")
                if near and (force_nearest or not sid or suspicious):
                    new_station = near.get("name")
                    dkm2 = float(near.get("distance_km") or 0.0)
                    if dkm2 > 0:
                        new_walk = max(1, min(120, int(round(dkm2 * 12.5))))
                elif sid and sid in STATION_MAP:
                    new_station = STATION_MAP[sid]["name"]

                cur = conn.execute(
                    """
                    UPDATE land_listings
                    SET station=?, walk_minutes=?, latitude=?, longitude=?, updated_at=datetime('now','localtime')
                    WHERE id=?
                    """,
                    (new_station, new_walk, lat, lon, r["id"]),
                )
                updated += cur.rowcount if cur else 0
        return updated

    # ===== 土地物件 =====

    def upsert_land_listings(self, records: List[Dict]) -> int:
        """土地物件を一括upsert"""
        inserted = 0
        with self._conn() as conn:
            for r in records:
                try:
                    addr = r.get("address", "")
                    price = r.get("land_price")
                    source = r.get("source")
                    existed = conn.execute("""
                        SELECT id
                        FROM land_listings
                        WHERE address = ?
                          AND source = ?
                          AND ((land_price IS NULL AND ? IS NULL) OR land_price = ?)
                        LIMIT 1
                    """, (addr, source, price, price)).fetchone()
                    conn.execute("""
                        INSERT INTO land_listings
                            (address, railway_line, station, walk_minutes,
                             land_price, land_area_sqm,
                             building_coverage_ratio, floor_area_ratio,
                             zoning, quasi_fireproof, two_way_road, north_road,
                             source, source_url, maisoku_pdf_path,
                             analysis_status, memo, latitude, longitude,
                             listing_status, last_seen_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(address, land_price, source) DO UPDATE SET
                            railway_line=excluded.railway_line,
                            station=excluded.station,
                            walk_minutes=excluded.walk_minutes,
                            land_area_sqm=excluded.land_area_sqm,
                            building_coverage_ratio=excluded.building_coverage_ratio,
                            floor_area_ratio=excluded.floor_area_ratio,
                            zoning=excluded.zoning,
                            latitude=excluded.latitude,
                            longitude=excluded.longitude,
                            listing_status='active',
                            delisted_confirmed_at=NULL,
                            verify_fail_count=0,
                            last_seen_at=datetime('now','localtime'),
                            updated_at=datetime('now','localtime')
                    """, (
                        addr,
                        r.get("railway_line"), r.get("station"),
                        r.get("walk_minutes"),
                        r.get("land_price"), r.get("land_area_sqm"),
                        r.get("building_coverage_ratio"), r.get("floor_area_ratio"),
                        r.get("zoning"),
                        1 if r.get("quasi_fireproof") else 0,
                        1 if r.get("two_way_road") else 0,
                        1 if r.get("north_road") else 0,
                        r.get("source"), r.get("source_url"),
                        r.get("maisoku_pdf_path"),
                        r.get("analysis_status", "pending"),
                        r.get("memo"),
                        r.get("latitude"), r.get("longitude"),
                        "active",
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    ))
                    if not existed:
                        inserted += 1
                except sqlite3.IntegrityError:
                    pass
        return inserted

    def get_land_listings(
        self, station: str = "", min_price: int = None,
        max_price: int = None, min_area: float = None,
        status: str = "", limit: int = 500, offset: int = 0,
        active_only: bool = True,
    ) -> List[Dict]:
        with self._conn() as conn:
            sql = """SELECT ll.*,
                       las.overall_score AS asset_score,
                       las.grade AS asset_grade,
                       las.summary AS asset_summary
                    FROM land_listings ll
                    LEFT JOIN land_asset_scores las ON las.land_listing_id = ll.id
                    WHERE (ll.duplicate_of_id IS NULL)"""
            params = []
            if active_only:
                sql += " AND COALESCE(ll.listing_status, 'active') = 'active'"
            if station:
                sql += " AND ll.station LIKE ?"
                params.append(f"%{station}%")
            if min_price is not None:
                sql += " AND ll.land_price >= ?"
                params.append(min_price)
            if max_price is not None:
                sql += " AND ll.land_price <= ?"
                params.append(max_price)
            if min_area is not None:
                sql += " AND ll.land_area_sqm >= ?"
                params.append(min_area)
            if status:
                sql += " AND ll.analysis_status = ?"
                params.append(status)
            sql += " ORDER BY ll.updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def count_land_listings(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM land_listings "
                "WHERE duplicate_of_id IS NULL AND COALESCE(listing_status, 'active')='active'"
            ).fetchone()
            return dict(row)["cnt"]

    def get_land_listing_by_id(self, listing_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM land_listings WHERE id=?", (listing_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_land_listing_status(self, listing_id: int, status: str):
        with self._conn() as conn:
            conn.execute("""
                UPDATE land_listings SET analysis_status=?,
                    updated_at=datetime('now','localtime')
                WHERE id=?
            """, (status, listing_id))

    # ===== 建築プラン =====

    def upsert_building_plans(self, plans: List[Dict]) -> int:
        inserted = 0
        with self._conn() as conn:
            for p in plans:
                try:
                    conn.execute("""
                        INSERT INTO building_plans
                            (land_listing_id, structure_type, floors, unit_size_sqm,
                             max_footprint_sqm, max_total_floor_area_sqm,
                             actual_total_floor_area_sqm, common_area_ratio,
                             effective_floor_area_sqm, max_units,
                             estimated_rent_per_sqm,
                             estimated_monthly_rent_per_unit,
                             estimated_annual_income,
                             estimated_construction_cost,
                             total_investment, estimated_yield)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(land_listing_id, structure_type, floors, unit_size_sqm)
                        DO UPDATE SET
                            max_units=excluded.max_units,
                            effective_floor_area_sqm=excluded.effective_floor_area_sqm,
                            estimated_rent_per_sqm=excluded.estimated_rent_per_sqm,
                            estimated_monthly_rent_per_unit=excluded.estimated_monthly_rent_per_unit,
                            estimated_annual_income=excluded.estimated_annual_income,
                            estimated_construction_cost=excluded.estimated_construction_cost,
                            total_investment=excluded.total_investment,
                            estimated_yield=excluded.estimated_yield
                    """, (
                        p["land_listing_id"], p["structure_type"],
                        p["floors"], p["unit_size_sqm"],
                        p.get("max_footprint_sqm", 0),
                        p.get("max_total_floor_area_sqm", 0),
                        p.get("actual_total_floor_area_sqm", 0),
                        p.get("common_area_ratio", 0),
                        p.get("effective_floor_area_sqm", 0),
                        p.get("max_units", 0),
                        p.get("estimated_rent_per_sqm", 0),
                        p.get("estimated_monthly_rent_per_unit", 0),
                        p.get("estimated_annual_income", 0),
                        p.get("estimated_construction_cost", 0),
                        p.get("total_investment", 0),
                        p.get("estimated_yield", 0),
                    ))
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass
        return inserted

    def get_building_plans(self, land_listing_id: int) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM building_plans
                WHERE land_listing_id=?
                ORDER BY estimated_yield DESC
            """, (land_listing_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_best_plans(
        self, sort_by: str = "estimated_yield", limit: int = 100,
    ) -> List[Dict]:
        """土地×最高利回りプランを結合して返す"""
        allowed_sorts = {"estimated_yield", "total_investment", "max_units"}
        col = sort_by if sort_by in allowed_sorts else "estimated_yield"
        with self._conn() as conn:
            rows = conn.execute(f"""
                SELECT ll.*, bp.structure_type, bp.floors, bp.unit_size_sqm,
                       bp.max_units, bp.estimated_yield,
                       bp.estimated_annual_income, bp.estimated_construction_cost,
                       bp.total_investment,
                       las.overall_score AS asset_score,
                       las.grade AS asset_grade,
                       las.summary AS asset_summary
                FROM land_listings ll
                JOIN building_plans bp ON bp.land_listing_id = ll.id
                LEFT JOIN land_asset_scores las ON las.land_listing_id = ll.id
                WHERE ll.duplicate_of_id IS NULL
                AND ll.land_price > 0
                AND bp.estimated_yield > 0
                AND bp.id = (
                    SELECT bp2.id FROM building_plans bp2
                    WHERE bp2.land_listing_id = ll.id
                    ORDER BY bp2.{col} DESC LIMIT 1
                )
                ORDER BY bp.{col} DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ===== 土地資産性スコア =====

    def upsert_asset_score(self, data: Dict):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO land_asset_scores
                    (land_listing_id, overall_score, grade, summary,
                     road_score, road_info_json,
                     hazard_score, hazard_info_json,
                     elevation_score, elevation_info_json,
                     lot_shape_score, lot_shape_info_json,
                     population_score, population_info_json,
                     station_distance_score)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(land_listing_id) DO UPDATE SET
                    overall_score=excluded.overall_score,
                    grade=excluded.grade,
                    summary=excluded.summary,
                    road_score=excluded.road_score,
                    road_info_json=excluded.road_info_json,
                    hazard_score=excluded.hazard_score,
                    hazard_info_json=excluded.hazard_info_json,
                    elevation_score=excluded.elevation_score,
                    elevation_info_json=excluded.elevation_info_json,
                    lot_shape_score=excluded.lot_shape_score,
                    lot_shape_info_json=excluded.lot_shape_info_json,
                    population_score=excluded.population_score,
                    population_info_json=excluded.population_info_json,
                    station_distance_score=excluded.station_distance_score,
                    scored_at=datetime('now','localtime')
            """, (
                data["land_listing_id"],
                data.get("overall_score", 0),
                data.get("grade", "?"),
                data.get("summary", ""),
                data.get("road_score", 0),
                json.dumps(data.get("road_info", {}), ensure_ascii=False),
                data.get("hazard_score", 0),
                json.dumps(data.get("hazard_info", {}), ensure_ascii=False),
                data.get("elevation_score", 0),
                json.dumps(data.get("elevation_info", {}), ensure_ascii=False),
                data.get("lot_shape_score", 0),
                json.dumps(data.get("lot_shape_info", {}), ensure_ascii=False),
                data.get("population_score", 0),
                json.dumps(data.get("population_info", {}), ensure_ascii=False),
                data.get("station_distance_score", 0),
            ))

    def get_asset_score(self, land_listing_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM land_asset_scores
                WHERE land_listing_id=?
            """, (land_listing_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            for key in ("road_info_json", "hazard_info_json", "elevation_info_json",
                        "lot_shape_info_json", "population_info_json"):
                if d.get(key):
                    d[key.replace("_json", "")] = json.loads(d[key])
            return d

    def get_unscored_listings(self, limit: int = 200) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT ll.* FROM land_listings ll
                LEFT JOIN land_asset_scores las ON las.land_listing_id = ll.id
                WHERE las.id IS NULL
                AND ll.latitude IS NOT NULL AND ll.longitude IS NOT NULL
                AND ll.duplicate_of_id IS NULL
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ===== 土地投資判定 =====

    def save_land_judgment(self, result: Dict):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO land_judgments
                    (land_listing_id, building_plan_id, grade, recommendation,
                     overall_score, confidence, key_metrics_json, full_result_json)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                result.get("land_listing_id"), result.get("building_plan_id"),
                result.get("grade", ""), result.get("recommendation", ""),
                result.get("overall_score", 0), result.get("confidence", 0),
                json.dumps(result.get("key_metrics", {}), ensure_ascii=False),
                json.dumps(result, ensure_ascii=False),
            ))

    def get_land_judgment(self, land_listing_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM land_judgments
                WHERE land_listing_id=?
                ORDER BY id DESC LIMIT 1
            """, (land_listing_id,)).fetchone()
            return dict(row) if row else None

    # ===== スクレイピング設定 =====

    def save_scrape_config(self, config: Dict) -> int:
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO scrape_configs
                    (name, source, prefecture_codes, area_codes,
                     price_min, price_max, area_min, area_max,
                     walk_max, max_pages, run_interval_hours)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                config.get("name", ""), config.get("source", "SUUMO"),
                json.dumps(config.get("prefecture_codes", [])),
                json.dumps(config.get("area_codes", [])),
                config.get("price_min"), config.get("price_max"),
                config.get("area_min"), config.get("area_max"),
                config.get("walk_max"), config.get("max_pages", 5),
                config.get("run_interval_hours", 24),
            ))
            return cur.lastrowid

    def get_scrape_configs(self, active_only: bool = True) -> List[Dict]:
        with self._conn() as conn:
            sql = "SELECT * FROM scrape_configs"
            if active_only:
                sql += " WHERE is_active=1"
            sql += " ORDER BY id DESC"
            rows = conn.execute(sql).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["prefecture_codes"] = json.loads(d.get("prefecture_codes") or "[]")
                d["area_codes"] = json.loads(d.get("area_codes") or "[]")
                results.append(d)
            return results

    def update_scrape_config_last_run(self, config_id: int):
        with self._conn() as conn:
            conn.execute("""
                UPDATE scrape_configs SET last_run_at=datetime('now','localtime')
                WHERE id=?
            """, (config_id,))

    def tune_scrape_config_max_pages(self, config_id: int, discovered_count: int):
        """実績に応じてmax_pagesを自動調整し、検索条件の改善を継続"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT max_pages FROM scrape_configs WHERE id=?",
                (config_id,),
            ).fetchone()
            if not row:
                return
            current = int(dict(row).get("max_pages") or 3)
            nxt = current
            if discovered_count < 5 and current < 30:
                nxt = current + 2
            elif discovered_count > 80 and current > 3:
                nxt = current - 1
            if nxt != current:
                conn.execute(
                    "UPDATE scrape_configs SET max_pages=? WHERE id=?",
                    (nxt, config_id),
                )

    # ===== ジオコーディング =====

    def update_land_listing_coords(self, listing_id: int, lat: float, lng: float):
        with self._conn() as conn:
            conn.execute("""
                UPDATE land_listings SET latitude=?, longitude=?,
                    updated_at=datetime('now','localtime')
                WHERE id=?
            """, (lat, lng, listing_id))

    def get_ungeocoded_listings(self, limit: int = 200) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT id, address FROM land_listings
                WHERE (latitude IS NULL OR longitude IS NULL)
                AND address IS NOT NULL AND address != ''
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ===== 重複検出 =====

    def detect_duplicates(self) -> int:
        """重複土地物件を検出しマーク"""
        with self._conn() as conn:
            # Get all non-duplicate listings
            rows = conn.execute("""
                SELECT id, address, land_price, land_area_sqm, source, source_url
                FROM land_listings
                WHERE duplicate_of_id IS NULL
                ORDER BY id
            """).fetchall()

            if len(rows) < 2:
                return 0

            # Normalize addresses
            def normalize(addr):
                if not addr:
                    return ""
                s = addr.replace(" ", "").replace("\u3000", "")
                s = s.replace("/", "").replace("\uff0f", "")
                # Full-width digits to half-width
                for fw, hw in zip("\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19", "0123456789"):
                    s = s.replace(fw, hw)
                # Remove common suffixes
                s = re.sub(r'(売地|土地|更地)$', '', s)
                return s

            listings = [dict(r) for r in rows]
            for l in listings:
                l['norm'] = normalize(l['address'])

            def rel_diff(x, y) -> float:
                if x is None or y is None:
                    return 999.0
                try:
                    xf = float(x)
                    yf = float(y)
                except (TypeError, ValueError):
                    return 999.0
                base = max(abs(xf), abs(yf), 1e-9)
                return abs(xf - yf) / base

            marked = 0
            by_addr = {}  # norm_addr -> [canonical_listing...]

            for l in listings:
                key = l['norm']
                if not key:
                    continue

                candidates = by_addr.get(key, [])
                matched = None
                l_url = self._normalize_source_url(l.get("source_url"))
                for ex in candidates:
                    ex_url = self._normalize_source_url(ex.get("source_url"))
                    same_url = bool(l_url and ex_url and l_url == ex_url)
                    price_close = rel_diff(l.get("land_price"), ex.get("land_price")) <= 0.02
                    area_close = rel_diff(l.get("land_area_sqm"), ex.get("land_area_sqm")) <= 0.08
                    if same_url or (price_close and area_close):
                        matched = ex
                        break

                if matched:
                    conn.execute(
                        "UPDATE land_listings SET duplicate_of_id=? WHERE id=?",
                        (matched["id"], l["id"])
                    )
                    marked += 1
                else:
                    by_addr.setdefault(key, []).append(l)

            return marked

    # ===== 統計クエリ =====

    def get_db_stats(self) -> Dict:
        with self._conn() as conn:
            stats = {}
            for table in ["stations", "land_prices", "transactions",
                          "rental_comps", "properties", "judgments",
                          "area_metrics", "station_metrics",
                          "land_listings", "building_plans",
                          "land_judgments", "api_land_prices",
                          "api_population_mesh", "api_facilities"]:
                try:
                    row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
                    stats[table] = dict(row)["cnt"]
                except Exception:
                    stats[table] = 0
            return stats

    # ===== API公示地価キャッシュ =====

    def upsert_api_land_prices(self, records: List[Dict]) -> int:
        """公示地価APIデータをDBに保存"""
        inserted = 0
        with self._conn() as conn:
            for r in records:
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO api_land_prices
                        (point_id, place_name, price_per_sqm, year,
                         latitude, longitude, zoning, station,
                         change_rate, coverage, far, fire_prevention,
                         land_price_type, prefecture_code, city_code)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        r.get("point_id"), r.get("place_name"),
                        r.get("price_per_sqm", 0), r.get("year"),
                        r.get("latitude"), r.get("longitude"),
                        r.get("zoning"), r.get("station"),
                        r.get("change_rate"), r.get("coverage"),
                        r.get("far"), r.get("fire_prevention"),
                        r.get("land_price_type", 0),
                        r.get("prefecture_code"), r.get("city_code"),
                    ))
                    inserted += 1
                except Exception:
                    continue
        return inserted

    def get_api_land_prices(self, south: float = None, west: float = None,
                            north: float = None, east: float = None,
                            limit: int = 5000) -> List[Dict]:
        """DBから公示地価キャッシュを取得（bounds指定可）"""
        with self._conn() as conn:
            if south is not None and west is not None:
                rows = conn.execute("""
                    SELECT * FROM api_land_prices
                    WHERE latitude BETWEEN ? AND ?
                    AND longitude BETWEEN ? AND ?
                    ORDER BY price_per_sqm DESC LIMIT ?
                """, (south, north, west, east, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM api_land_prices ORDER BY price_per_sqm DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # ===== API人口メッシュキャッシュ =====

    def upsert_api_population_mesh(self, records: List[Dict]) -> int:
        inserted = 0
        with self._conn() as conn:
            # マイグレーション: center_lat/lng追加
            self._migrate_add_column(conn, "api_population_mesh", "center_lat", "REAL")
            self._migrate_add_column(conn, "api_population_mesh", "center_lng", "REAL")
            for r in records:
                try:
                    # geometry_jsonからcentroidを計算
                    clat, clng = r.get("center_lat"), r.get("center_lng")
                    if (clat is None or clng is None) and r.get("geometry_json"):
                        import json as _json
                        try:
                            g = _json.loads(r["geometry_json"])
                            coords = g.get("coordinates", [[]])[0]
                            if coords:
                                clat = sum(c[1] for c in coords) / len(coords)
                                clng = sum(c[0] for c in coords) / len(coords)
                        except Exception:
                            pass
                    conn.execute("""
                        INSERT OR REPLACE INTO api_population_mesh
                        (mesh_id, pop_current, pop_future, change_rate, center_lat, center_lng, geometry_json)
                        VALUES (?,?,?,?,?,?,?)
                    """, (
                        r.get("mesh_id"), r.get("pop_current"),
                        r.get("pop_future"), r.get("change_rate"),
                        clat, clng, r.get("geometry_json"),
                    ))
                    inserted += 1
                except Exception:
                    continue
        return inserted

    def get_api_population_mesh(self, south: float = None, west: float = None,
                                north: float = None, east: float = None,
                                limit: int = 10000) -> List[Dict]:
        """DBから人口メッシュを取得（bounds絞り込み対応）"""
        with self._conn() as conn:
            if south is not None and west is not None:
                rows = conn.execute("""
                    SELECT * FROM api_population_mesh
                    WHERE center_lat BETWEEN ? AND ?
                    AND center_lng BETWEEN ? AND ?
                    LIMIT ?
                """, (south, north, west, east, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM api_population_mesh LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ===== API施設キャッシュ =====

    def upsert_api_facilities(self, records: List[Dict]) -> int:
        inserted = 0
        with self._conn() as conn:
            for r in records:
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO api_facilities
                        (name, category, address, latitude, longitude, extra_json)
                        VALUES (?,?,?,?,?,?)
                    """, (
                        r.get("name"), r.get("category"),
                        r.get("address"), r.get("latitude"),
                        r.get("longitude"), r.get("extra_json"),
                    ))
                    inserted += 1
                except Exception:
                    continue
        return inserted

    def get_api_facilities(self, south: float = None, west: float = None,
                           north: float = None, east: float = None,
                           category: str = "", limit: int = 5000) -> List[Dict]:
        with self._conn() as conn:
            sql = "SELECT * FROM api_facilities WHERE 1=1"
            params = []
            if south is not None and west is not None:
                sql += " AND latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?"
                params.extend([south, north, west, east])
            if category:
                sql += " AND category = ?"
                params.append(category)
            sql += " LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
