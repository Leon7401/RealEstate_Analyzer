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
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any
from contextlib import contextmanager

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
            self._migrate_add_column(conn, "properties", "station_id", "TEXT")
            self._migrate_add_column(conn, "land_listings", "duplicate_of_id", "INTEGER")
            self._migrate_add_column(conn, "land_listings", "normalized_address", "TEXT")
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
                    conn.execute("""
                        INSERT INTO rental_comps
                            (address, rent_monthly, area_sqm, rent_per_sqm,
                             layout, structure, built_year, floor, floors_total,
                             management_fee, latitude, longitude,
                             nearest_station, station_distance_min, station_id,
                             city_code, source)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(address, rent_monthly, area_sqm)
                        DO UPDATE SET
                            rent_per_sqm=excluded.rent_per_sqm,
                            station_id=excluded.station_id,
                            fetched_at=datetime('now','localtime')
                    """, (
                        r.get("address", ""), r.get("rent_monthly", 0),
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
            conn.execute("""
                INSERT INTO properties
                    (id, name, address, prefecture_code, city_code,
                     latitude, longitude, asking_price, land_area,
                     building_area, structure, built_year, building_age,
                     units, current_rent_annual, gross_yield,
                     nearest_station, station_distance_min, station_id,
                     source, source_url, data_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    asking_price=excluded.asking_price,
                    current_rent_annual=excluded.current_rent_annual,
                    gross_yield=excluded.gross_yield,
                    station_id=excluded.station_id,
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
            ))
        return True

    def get_properties(
        self, city_code: str = "", station_id: str = "", limit: int = 200,
    ) -> List[Dict]:
        with self._conn() as conn:
            sql = "SELECT * FROM properties WHERE 1=1"
            params = []
            if station_id:
                sql += " AND station_id=?"
                params.append(station_id)
            elif city_code:
                sql += " AND city_code=?"
                params.append(city_code)
            sql += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

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

    # ===== 土地物件 =====

    def upsert_land_listings(self, records: List[Dict]) -> int:
        """土地物件を一括upsert"""
        inserted = 0
        with self._conn() as conn:
            for r in records:
                try:
                    conn.execute("""
                        INSERT INTO land_listings
                            (address, railway_line, station, walk_minutes,
                             land_price, land_area_sqm,
                             building_coverage_ratio, floor_area_ratio,
                             zoning, quasi_fireproof, two_way_road, north_road,
                             source, source_url, maisoku_pdf_path,
                             analysis_status, memo, latitude, longitude)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                            updated_at=datetime('now','localtime')
                    """, (
                        r.get("address", ""),
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
                    ))
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass
        return inserted

    def get_land_listings(
        self, station: str = "", min_price: int = None,
        max_price: int = None, min_area: float = None,
        status: str = "", limit: int = 500, offset: int = 0,
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
            row = conn.execute("SELECT COUNT(*) as cnt FROM land_listings WHERE duplicate_of_id IS NULL").fetchone()
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
                     walk_max, max_pages)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                config.get("name", ""), config.get("source", "SUUMO"),
                json.dumps(config.get("prefecture_codes", [])),
                json.dumps(config.get("area_codes", [])),
                config.get("price_min"), config.get("price_max"),
                config.get("area_min"), config.get("area_max"),
                config.get("walk_max"), config.get("max_pages", 5),
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
        import re

        with self._conn() as conn:
            # Get all non-duplicate listings
            rows = conn.execute("""
                SELECT id, address, land_price, land_area_sqm, source
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

            marked = 0
            seen = {}  # norm_addr -> canonical_id

            for l in listings:
                key = l['norm']
                if not key:
                    continue

                if key in seen:
                    # Exact address match - mark as duplicate
                    conn.execute(
                        "UPDATE land_listings SET duplicate_of_id=? WHERE id=?",
                        (seen[key], l['id'])
                    )
                    marked += 1
                else:
                    # Check for near-matches (same normalized address prefix + similar price)
                    for existing_key, existing_id in seen.items():
                        if len(key) > 5 and len(existing_key) > 5:
                            # Same first 80% of address
                            min_len = min(len(key), len(existing_key))
                            prefix_len = int(min_len * 0.8)
                            if key[:prefix_len] == existing_key[:prefix_len]:
                                # Check if price is within 10%
                                existing = next((x for x in listings if x['id'] == existing_id), None)
                                if existing and l.get('land_price') and existing.get('land_price'):
                                    price_diff = abs(l['land_price'] - existing['land_price'])
                                    avg_price = (l['land_price'] + existing['land_price']) / 2
                                    if avg_price > 0 and price_diff / avg_price < 0.1:
                                        conn.execute(
                                            "UPDATE land_listings SET duplicate_of_id=? WHERE id=?",
                                            (existing_id, l['id'])
                                        )
                                        marked += 1
                                        break

                    if key not in seen:
                        seen[key] = l['id']

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
