"""市区町村マスタデータ（UI用）"""

# 都道府県 → 市区町村リスト
CITY_MASTER = {
    "13": [
        {"code": "", "name": "全域"},
        {"code": "13101", "name": "千代田区"},
        {"code": "13102", "name": "中央区"},
        {"code": "13103", "name": "港区"},
        {"code": "13104", "name": "新宿区"},
        {"code": "13105", "name": "文京区"},
        {"code": "13106", "name": "台東区"},
        {"code": "13107", "name": "墨田区"},
        {"code": "13108", "name": "江東区"},
        {"code": "13109", "name": "品川区"},
        {"code": "13110", "name": "目黒区"},
        {"code": "13111", "name": "大田区"},
        {"code": "13112", "name": "世田谷区"},
        {"code": "13113", "name": "渋谷区"},
        {"code": "13114", "name": "中野区"},
        {"code": "13115", "name": "杉並区"},
        {"code": "13116", "name": "豊島区"},
        {"code": "13117", "name": "北区"},
        {"code": "13118", "name": "荒川区"},
        {"code": "13119", "name": "板橋区"},
        {"code": "13120", "name": "練馬区"},
        {"code": "13121", "name": "足立区"},
        {"code": "13122", "name": "葛飾区"},
        {"code": "13123", "name": "江戸川区"},
    ],
    "14": [
        {"code": "", "name": "全域"},
        {"code": "14100", "name": "横浜市"},
        {"code": "14130", "name": "川崎市"},
        {"code": "14150", "name": "相模原市"},
        {"code": "14201", "name": "横須賀市"},
        {"code": "14204", "name": "藤沢市"},
        {"code": "14205", "name": "小田原市"},
        {"code": "14211", "name": "鎌倉市"},
    ],
    "11": [
        {"code": "", "name": "全域"},
        {"code": "11100", "name": "さいたま市"},
        {"code": "11201", "name": "川越市"},
        {"code": "11203", "name": "川口市"},
        {"code": "11210", "name": "所沢市"},
        {"code": "11214", "name": "越谷市"},
    ],
    "12": [
        {"code": "", "name": "全域"},
        {"code": "12100", "name": "千葉市"},
        {"code": "12202", "name": "船橋市"},
        {"code": "12204", "name": "松戸市"},
        {"code": "12207", "name": "柏市"},
        {"code": "12203", "name": "市川市"},
    ],
    "27": [
        {"code": "", "name": "全域"},
        {"code": "27100", "name": "大阪市"},
        {"code": "27140", "name": "堺市"},
        {"code": "27202", "name": "豊中市"},
        {"code": "27203", "name": "吹田市"},
    ],
    "23": [
        {"code": "", "name": "全域"},
        {"code": "23100", "name": "名古屋市"},
        {"code": "23202", "name": "岡崎市"},
        {"code": "23201", "name": "豊橋市"},
    ],
    "40": [
        {"code": "", "name": "全域"},
        {"code": "40130", "name": "福岡市"},
        {"code": "40100", "name": "北九州市"},
        {"code": "40203", "name": "久留米市"},
    ],
}

# ===== JSONファイルからの市区町村拡張読み込み =====
def _load_extended_cities():
    import json
    from pathlib import Path
    json_path = Path(__file__).resolve().parent.parent / "output" / "all_cities.json"
    if not json_path.exists():
        return
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for pref, cities in raw.items():
            existing_codes = {c["code"] for c in CITY_MASTER.get(pref, [])}
            if pref not in CITY_MASTER:
                CITY_MASTER[pref] = [{"code": "", "name": "全域"}]
            for c in cities:
                if c["code"] not in existing_codes:
                    CITY_MASTER[pref].append(c)
                    existing_codes.add(c["code"])
    except Exception:
        pass

_load_extended_cities()

CITY_NAME_MAP = {}
for pref, cities in CITY_MASTER.items():
    for c in cities:
        if c["code"]:
            CITY_NAME_MAP[c["code"]] = c["name"]
