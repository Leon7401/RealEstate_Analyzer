"""
駅マスタデータ（一都三県）
- 駅名・座標・路線・所属市区町村
- 座標から最寄り駅を特定する関数
"""
import math
from typing import List, Dict, Optional, Tuple


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """2点間の距離(km)をHaversine公式で計算"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ===== 駅マスタ =====
# station_id: 一意キー（pref_name形式）
# 一都三県の主要駅（JR・地下鉄・私鉄の主要駅を網羅）

STATIONS: List[Dict] = [
    # ==============================
    # 東京都 (13)
    # ==============================
    # --- JR山手線 ---
    {"station_id": "tokyo", "name": "東京", "lat": 35.6812, "lon": 139.7671, "line": "JR山手線", "pref": "13", "city_code": "13101"},
    {"station_id": "yurakucho", "name": "有楽町", "lat": 35.6748, "lon": 139.7631, "line": "JR山手線", "pref": "13", "city_code": "13101"},
    {"station_id": "shimbashi", "name": "新橋", "lat": 35.6663, "lon": 139.7583, "line": "JR山手線", "pref": "13", "city_code": "13103"},
    {"station_id": "hamamatsucho", "name": "浜松町", "lat": 35.6554, "lon": 139.7571, "line": "JR山手線", "pref": "13", "city_code": "13103"},
    {"station_id": "tamachi", "name": "田町", "lat": 35.6459, "lon": 139.7476, "line": "JR山手線", "pref": "13", "city_code": "13103"},
    {"station_id": "takanawa_gw", "name": "高輪ゲートウェイ", "lat": 35.6399, "lon": 139.7409, "line": "JR山手線", "pref": "13", "city_code": "13103"},
    {"station_id": "shinagawa", "name": "品川", "lat": 35.6284, "lon": 139.7388, "line": "JR山手線", "pref": "13", "city_code": "13103"},
    {"station_id": "osaki", "name": "大崎", "lat": 35.6197, "lon": 139.7284, "line": "JR山手線", "pref": "13", "city_code": "13109"},
    {"station_id": "gotanda", "name": "五反田", "lat": 35.6264, "lon": 139.7234, "line": "JR山手線", "pref": "13", "city_code": "13109"},
    {"station_id": "meguro", "name": "目黒", "lat": 35.6340, "lon": 139.7158, "line": "JR山手線", "pref": "13", "city_code": "13110"},
    {"station_id": "ebisu", "name": "恵比寿", "lat": 35.6467, "lon": 139.7100, "line": "JR山手線", "pref": "13", "city_code": "13113"},
    {"station_id": "shibuya", "name": "渋谷", "lat": 35.6580, "lon": 139.7016, "line": "JR山手線", "pref": "13", "city_code": "13113"},
    {"station_id": "harajuku", "name": "原宿", "lat": 35.6702, "lon": 139.7027, "line": "JR山手線", "pref": "13", "city_code": "13113"},
    {"station_id": "yoyogi", "name": "代々木", "lat": 35.6833, "lon": 139.7020, "line": "JR山手線", "pref": "13", "city_code": "13113"},
    {"station_id": "shinjuku", "name": "新宿", "lat": 35.6896, "lon": 139.7006, "line": "JR山手線", "pref": "13", "city_code": "13104"},
    {"station_id": "shin_okubo", "name": "新大久保", "lat": 35.7012, "lon": 139.7002, "line": "JR山手線", "pref": "13", "city_code": "13104"},
    {"station_id": "takadanobaba", "name": "高田馬場", "lat": 35.7126, "lon": 139.7036, "line": "JR山手線", "pref": "13", "city_code": "13104"},
    {"station_id": "mejiro", "name": "目白", "lat": 35.7214, "lon": 139.7068, "line": "JR山手線", "pref": "13", "city_code": "13116"},
    {"station_id": "ikebukuro", "name": "池袋", "lat": 35.7295, "lon": 139.7109, "line": "JR山手線", "pref": "13", "city_code": "13116"},
    {"station_id": "otsuka", "name": "大塚", "lat": 35.7318, "lon": 139.7285, "line": "JR山手線", "pref": "13", "city_code": "13116"},
    {"station_id": "sugamo", "name": "巣鴨", "lat": 35.7337, "lon": 139.7394, "line": "JR山手線", "pref": "13", "city_code": "13116"},
    {"station_id": "komagome", "name": "駒込", "lat": 35.7365, "lon": 139.7468, "line": "JR山手線", "pref": "13", "city_code": "13116"},
    {"station_id": "tabata", "name": "田端", "lat": 35.7381, "lon": 139.7611, "line": "JR山手線", "pref": "13", "city_code": "13117"},
    {"station_id": "nishi_nippori", "name": "西日暮里", "lat": 35.7320, "lon": 139.7668, "line": "JR山手線", "pref": "13", "city_code": "13118"},
    {"station_id": "nippori", "name": "日暮里", "lat": 35.7280, "lon": 139.7707, "line": "JR山手線", "pref": "13", "city_code": "13118"},
    {"station_id": "uguisudani", "name": "鶯谷", "lat": 35.7205, "lon": 139.7783, "line": "JR山手線", "pref": "13", "city_code": "13106"},
    {"station_id": "ueno", "name": "上野", "lat": 35.7141, "lon": 139.7774, "line": "JR山手線", "pref": "13", "city_code": "13106"},
    {"station_id": "okachimachi", "name": "御徒町", "lat": 35.7074, "lon": 139.7745, "line": "JR山手線", "pref": "13", "city_code": "13106"},
    {"station_id": "akihabara", "name": "秋葉原", "lat": 35.6984, "lon": 139.7731, "line": "JR山手線", "pref": "13", "city_code": "13101"},
    {"station_id": "kanda", "name": "神田", "lat": 35.6918, "lon": 139.7709, "line": "JR山手線", "pref": "13", "city_code": "13101"},
    # --- 東京メトロ・都営（山手線内＋周辺主要駅） ---
    {"station_id": "omotesando", "name": "表参道", "lat": 35.6654, "lon": 139.7122, "line": "東京メトロ", "pref": "13", "city_code": "13103"},
    {"station_id": "roppongi", "name": "六本木", "lat": 35.6627, "lon": 139.7315, "line": "東京メトロ", "pref": "13", "city_code": "13103"},
    {"station_id": "akasaka", "name": "赤坂", "lat": 35.6726, "lon": 139.7372, "line": "東京メトロ", "pref": "13", "city_code": "13103"},
    {"station_id": "azabu_juban", "name": "麻布十番", "lat": 35.6554, "lon": 139.7371, "line": "東京メトロ", "pref": "13", "city_code": "13103"},
    {"station_id": "hiroo", "name": "広尾", "lat": 35.6512, "lon": 139.7225, "line": "東京メトロ", "pref": "13", "city_code": "13113"},
    {"station_id": "shirokanedai", "name": "白金台", "lat": 35.6396, "lon": 139.7246, "line": "東京メトロ", "pref": "13", "city_code": "13103"},
    {"station_id": "meiji_jingumae", "name": "明治神宮前", "lat": 35.6699, "lon": 139.7024, "line": "東京メトロ", "pref": "13", "city_code": "13113"},
    {"station_id": "kojimachi", "name": "麹町", "lat": 35.6856, "lon": 139.7394, "line": "東京メトロ", "pref": "13", "city_code": "13101"},
    {"station_id": "jimbocho", "name": "神保町", "lat": 35.6959, "lon": 139.7577, "line": "東京メトロ", "pref": "13", "city_code": "13101"},
    {"station_id": "kudanshita", "name": "九段下", "lat": 35.6960, "lon": 139.7510, "line": "東京メトロ", "pref": "13", "city_code": "13102"},
    {"station_id": "iidabashi", "name": "飯田橋", "lat": 35.7020, "lon": 139.7451, "line": "JR中央線", "pref": "13", "city_code": "13102"},
    {"station_id": "yotsuya", "name": "四ツ谷", "lat": 35.6862, "lon": 139.7302, "line": "JR中央線", "pref": "13", "city_code": "13104"},
    {"station_id": "yotsuya_sanchome", "name": "四谷三丁目", "lat": 35.6878, "lon": 139.7209, "line": "東京メトロ", "pref": "13", "city_code": "13104"},
    {"station_id": "kagurazaka", "name": "神楽坂", "lat": 35.7037, "lon": 139.7407, "line": "東京メトロ", "pref": "13", "city_code": "13104"},
    {"station_id": "ochanomizu", "name": "御茶ノ水", "lat": 35.6991, "lon": 139.7652, "line": "JR中央線", "pref": "13", "city_code": "13101"},
    {"station_id": "hongosanchome", "name": "本郷三丁目", "lat": 35.7072, "lon": 139.7595, "line": "東京メトロ", "pref": "13", "city_code": "13105"},
    {"station_id": "korakuen", "name": "後楽園", "lat": 35.7077, "lon": 139.7513, "line": "東京メトロ", "pref": "13", "city_code": "13105"},
    {"station_id": "myogadani", "name": "茗荷谷", "lat": 35.7178, "lon": 139.7316, "line": "東京メトロ", "pref": "13", "city_code": "13105"},
    {"station_id": "tsukishima", "name": "月島", "lat": 35.6633, "lon": 139.7831, "line": "東京メトロ", "pref": "13", "city_code": "13102"},
    {"station_id": "kachidoki", "name": "勝どき", "lat": 35.6594, "lon": 139.7764, "line": "都営大江戸線", "pref": "13", "city_code": "13102"},
    {"station_id": "nihombashi", "name": "日本橋", "lat": 35.6823, "lon": 139.7741, "line": "東京メトロ", "pref": "13", "city_code": "13102"},
    {"station_id": "ningyocho", "name": "人形町", "lat": 35.6863, "lon": 139.7827, "line": "東京メトロ", "pref": "13", "city_code": "13102"},
    {"station_id": "asakusabashi", "name": "浅草橋", "lat": 35.6977, "lon": 139.7870, "line": "JR総武線", "pref": "13", "city_code": "13106"},
    {"station_id": "asakusa", "name": "浅草", "lat": 35.7112, "lon": 139.7964, "line": "東京メトロ", "pref": "13", "city_code": "13106"},
    # --- JR中央線・総武線（東京〜三鷹） ---
    {"station_id": "nakano", "name": "中野", "lat": 35.7065, "lon": 139.6658, "line": "JR中央線", "pref": "13", "city_code": "13114"},
    {"station_id": "koenji", "name": "高円寺", "lat": 35.7053, "lon": 139.6496, "line": "JR中央線", "pref": "13", "city_code": "13115"},
    {"station_id": "asagaya", "name": "阿佐ヶ谷", "lat": 35.7048, "lon": 139.6362, "line": "JR中央線", "pref": "13", "city_code": "13115"},
    {"station_id": "ogikubo", "name": "荻窪", "lat": 35.7040, "lon": 139.6200, "line": "JR中央線", "pref": "13", "city_code": "13115"},
    {"station_id": "nishi_ogikubo", "name": "西荻窪", "lat": 35.7036, "lon": 139.5991, "line": "JR中央線", "pref": "13", "city_code": "13115"},
    {"station_id": "kichijoji", "name": "吉祥寺", "lat": 35.7030, "lon": 139.5798, "line": "JR中央線", "pref": "13", "city_code": "13204"},
    {"station_id": "mitaka", "name": "三鷹", "lat": 35.7027, "lon": 139.5607, "line": "JR中央線", "pref": "13", "city_code": "13204"},
    # --- 東急田園都市線・東横線 ---
    {"station_id": "nakameguro", "name": "中目黒", "lat": 35.6441, "lon": 139.6996, "line": "東急東横線", "pref": "13", "city_code": "13110"},
    {"station_id": "yutenji", "name": "祐天寺", "lat": 35.6385, "lon": 139.6883, "line": "東急東横線", "pref": "13", "city_code": "13110"},
    {"station_id": "gakugeidaigaku", "name": "学芸大学", "lat": 35.6327, "lon": 139.6844, "line": "東急東横線", "pref": "13", "city_code": "13110"},
    {"station_id": "toritsudaigaku", "name": "都立大学", "lat": 35.6187, "lon": 139.6790, "line": "東急東横線", "pref": "13", "city_code": "13110"},
    {"station_id": "jiyugaoka", "name": "自由が丘", "lat": 35.6073, "lon": 139.6685, "line": "東急東横線", "pref": "13", "city_code": "13110"},
    {"station_id": "sangenjaya", "name": "三軒茶屋", "lat": 35.6436, "lon": 139.6700, "line": "東急田園都市線", "pref": "13", "city_code": "13112"},
    {"station_id": "komazawa_daigaku", "name": "駒沢大学", "lat": 35.6336, "lon": 139.6615, "line": "東急田園都市線", "pref": "13", "city_code": "13112"},
    {"station_id": "sakurashinmachi", "name": "桜新町", "lat": 35.6300, "lon": 139.6454, "line": "東急田園都市線", "pref": "13", "city_code": "13112"},
    {"station_id": "yoga", "name": "用賀", "lat": 35.6262, "lon": 139.6334, "line": "東急田園都市線", "pref": "13", "city_code": "13112"},
    {"station_id": "futakotamagawa", "name": "二子玉川", "lat": 35.6110, "lon": 139.6262, "line": "東急田園都市線", "pref": "13", "city_code": "13112"},
    {"station_id": "shimokitazawa", "name": "下北沢", "lat": 35.6610, "lon": 139.6681, "line": "小田急線", "pref": "13", "city_code": "13112"},
    # --- 京王線 ---
    {"station_id": "shinsen", "name": "神泉", "lat": 35.6551, "lon": 139.6948, "line": "京王井の頭線", "pref": "13", "city_code": "13113"},
    {"station_id": "sasazuka", "name": "笹塚", "lat": 35.6732, "lon": 139.6680, "line": "京王線", "pref": "13", "city_code": "13113"},
    {"station_id": "meidaimae", "name": "明大前", "lat": 35.6691, "lon": 139.6498, "line": "京王線", "pref": "13", "city_code": "13112"},
    {"station_id": "chofu", "name": "調布", "lat": 35.6517, "lon": 139.5416, "line": "京王線", "pref": "13", "city_code": "13208"},
    # --- 小田急線 ---
    {"station_id": "kyodo", "name": "経堂", "lat": 35.6487, "lon": 139.6387, "line": "小田急線", "pref": "13", "city_code": "13112"},
    {"station_id": "seijogakuenmae", "name": "成城学園前", "lat": 35.6391, "lon": 139.5986, "line": "小田急線", "pref": "13", "city_code": "13112"},
    {"station_id": "machida", "name": "町田", "lat": 35.5422, "lon": 139.4453, "line": "小田急線", "pref": "13", "city_code": "13209"},
    # --- 東武スカイツリーライン・伊勢崎線 ---
    {"station_id": "oshiage", "name": "押上", "lat": 35.7107, "lon": 139.8129, "line": "東武スカイツリーライン", "pref": "13", "city_code": "13107"},
    {"station_id": "kita_senju", "name": "北千住", "lat": 35.7499, "lon": 139.8047, "line": "JR常磐線", "pref": "13", "city_code": "13121"},
    {"station_id": "ayase", "name": "綾瀬", "lat": 35.7629, "lon": 139.8249, "line": "東京メトロ千代田線", "pref": "13", "city_code": "13121"},
    {"station_id": "takenotsuka", "name": "竹ノ塚", "lat": 35.7943, "lon": 139.7920, "line": "東武スカイツリーライン", "pref": "13", "city_code": "13121"},
    # --- JR京浜東北線（蒲田方面） ---
    {"station_id": "kamata", "name": "蒲田", "lat": 35.5625, "lon": 139.7161, "line": "JR京浜東北線", "pref": "13", "city_code": "13111"},
    {"station_id": "omori", "name": "大森", "lat": 35.5880, "lon": 139.7281, "line": "JR京浜東北線", "pref": "13", "city_code": "13111"},
    # --- 品川区その他 ---
    {"station_id": "hatanodai", "name": "旗の台", "lat": 35.6052, "lon": 139.7027, "line": "東急大井町線", "pref": "13", "city_code": "13109"},
    {"station_id": "togoshi_ginza", "name": "戸越銀座", "lat": 35.6167, "lon": 139.7149, "line": "東急池上線", "pref": "13", "city_code": "13109"},
    # --- 江東区 ---
    {"station_id": "toyosu", "name": "豊洲", "lat": 35.6531, "lon": 139.7960, "line": "東京メトロ有楽町線", "pref": "13", "city_code": "13108"},
    {"station_id": "kameido", "name": "亀戸", "lat": 35.6969, "lon": 139.8264, "line": "JR総武線", "pref": "13", "city_code": "13108"},
    {"station_id": "monzennakacho", "name": "門前仲町", "lat": 35.6730, "lon": 139.7962, "line": "東京メトロ東西線", "pref": "13", "city_code": "13108"},
    {"station_id": "kiyosumi_shirakawa", "name": "清澄白河", "lat": 35.6810, "lon": 139.8010, "line": "東京メトロ半蔵門線", "pref": "13", "city_code": "13108"},
    # --- 墨田区 ---
    {"station_id": "kinshicho", "name": "錦糸町", "lat": 35.6961, "lon": 139.8142, "line": "JR総武線", "pref": "13", "city_code": "13107"},
    {"station_id": "ryogoku", "name": "両国", "lat": 35.6966, "lon": 139.7935, "line": "JR総武線", "pref": "13", "city_code": "13107"},
    # --- 荒川区 ---
    {"station_id": "minami_senju", "name": "南千住", "lat": 35.7371, "lon": 139.7935, "line": "JR常磐線", "pref": "13", "city_code": "13118"},
    # --- 北区 ---
    {"station_id": "akabane", "name": "赤羽", "lat": 35.7781, "lon": 139.7210, "line": "JR京浜東北線", "pref": "13", "city_code": "13117"},
    {"station_id": "oji", "name": "王子", "lat": 35.7521, "lon": 139.7381, "line": "JR京浜東北線", "pref": "13", "city_code": "13117"},
    # --- 板橋区 ---
    {"station_id": "itabashi", "name": "板橋", "lat": 35.7516, "lon": 139.7201, "line": "JR埼京線", "pref": "13", "city_code": "13119"},
    {"station_id": "narimasu", "name": "成増", "lat": 35.7795, "lon": 139.6324, "line": "東武東上線", "pref": "13", "city_code": "13119"},
    # --- 練馬区 ---
    {"station_id": "nerima", "name": "練馬", "lat": 35.7381, "lon": 139.6547, "line": "西武池袋線", "pref": "13", "city_code": "13120"},
    {"station_id": "oizumigakuen", "name": "大泉学園", "lat": 35.7549, "lon": 139.5880, "line": "西武池袋線", "pref": "13", "city_code": "13120"},
    {"station_id": "shakujii_koen", "name": "石神井公園", "lat": 35.7437, "lon": 139.6078, "line": "西武池袋線", "pref": "13", "city_code": "13120"},
    # --- 葛飾区 ---
    {"station_id": "kameari", "name": "亀有", "lat": 35.7625, "lon": 139.8474, "line": "JR常磐線", "pref": "13", "city_code": "13122"},
    {"station_id": "kanamachi", "name": "金町", "lat": 35.7698, "lon": 139.8717, "line": "JR常磐線", "pref": "13", "city_code": "13122"},
    # --- 江戸川区 ---
    {"station_id": "kasai", "name": "葛西", "lat": 35.6592, "lon": 139.8633, "line": "東京メトロ東西線", "pref": "13", "city_code": "13123"},
    {"station_id": "koiwa", "name": "小岩", "lat": 35.7332, "lon": 139.8801, "line": "JR総武線", "pref": "13", "city_code": "13123"},
    {"station_id": "mizue", "name": "瑞江", "lat": 35.6969, "lon": 139.8769, "line": "都営新宿線", "pref": "13", "city_code": "13123"},
    # --- 大田区（追加） ---
    {"station_id": "yukigaya_otsuka", "name": "雪が谷大塚", "lat": 35.5875, "lon": 139.6870, "line": "東急池上線", "pref": "13", "city_code": "13111"},
    {"station_id": "tenkubashi", "name": "天空橋", "lat": 35.5481, "lon": 139.7445, "line": "京急空港線", "pref": "13", "city_code": "13111"},

    # ==============================
    # 神奈川県 (14)
    # ==============================
    # --- 横浜市 ---
    {"station_id": "yokohama", "name": "横浜", "lat": 35.4660, "lon": 139.6226, "line": "JR東海道線", "pref": "14", "city_code": "14100"},
    {"station_id": "shin_yokohama", "name": "新横浜", "lat": 35.5065, "lon": 139.6177, "line": "JR横浜線", "pref": "14", "city_code": "14100"},
    {"station_id": "sakuragicho", "name": "桜木町", "lat": 35.4510, "lon": 139.6311, "line": "JR根岸線", "pref": "14", "city_code": "14100"},
    {"station_id": "kannai", "name": "関内", "lat": 35.4443, "lon": 139.6365, "line": "JR根岸線", "pref": "14", "city_code": "14100"},
    {"station_id": "ishikawacho", "name": "石川町", "lat": 35.4376, "lon": 139.6426, "line": "JR根岸線", "pref": "14", "city_code": "14100"},
    {"station_id": "totsuka", "name": "戸塚", "lat": 35.3994, "lon": 139.5309, "line": "JR東海道線", "pref": "14", "city_code": "14100"},
    {"station_id": "tsurumi", "name": "鶴見", "lat": 35.5051, "lon": 139.6754, "line": "JR京浜東北線", "pref": "14", "city_code": "14100"},
    {"station_id": "kohoku_nt_center", "name": "センター北", "lat": 35.5532, "lon": 139.5774, "line": "横浜市営地下鉄", "pref": "14", "city_code": "14100"},
    {"station_id": "azamino", "name": "あざみ野", "lat": 35.5681, "lon": 139.5533, "line": "東急田園都市線", "pref": "14", "city_code": "14100"},
    {"station_id": "aobadai", "name": "青葉台", "lat": 35.5430, "lon": 139.5176, "line": "東急田園都市線", "pref": "14", "city_code": "14100"},
    {"station_id": "tama_plaza", "name": "たまプラーザ", "lat": 35.5708, "lon": 139.5585, "line": "東急田園都市線", "pref": "14", "city_code": "14100"},
    {"station_id": "hiyoshi", "name": "日吉", "lat": 35.5537, "lon": 139.6473, "line": "東急東横線", "pref": "14", "city_code": "14100"},
    {"station_id": "kikuna", "name": "菊名", "lat": 35.5096, "lon": 139.6312, "line": "JR横浜線", "pref": "14", "city_code": "14100"},
    # --- 川崎市 ---
    {"station_id": "kawasaki", "name": "川崎", "lat": 35.5309, "lon": 139.7005, "line": "JR東海道線", "pref": "14", "city_code": "14130"},
    {"station_id": "musashi_kosugi", "name": "武蔵小杉", "lat": 35.5761, "lon": 139.6595, "line": "JR横須賀線", "pref": "14", "city_code": "14130"},
    {"station_id": "musashi_mizonokuchi", "name": "武蔵溝ノ口", "lat": 35.5994, "lon": 139.6103, "line": "JR南武線", "pref": "14", "city_code": "14130"},
    {"station_id": "noborito", "name": "登戸", "lat": 35.6208, "lon": 139.5706, "line": "JR南武線", "pref": "14", "city_code": "14130"},
    {"station_id": "shin_yurigaoka", "name": "新百合ヶ丘", "lat": 35.6039, "lon": 139.5075, "line": "小田急線", "pref": "14", "city_code": "14130"},
    # --- 相模原市 ---
    {"station_id": "sagamihara", "name": "相模原", "lat": 35.5825, "lon": 139.3699, "line": "JR横浜線", "pref": "14", "city_code": "14150"},
    {"station_id": "hashimoto", "name": "橋本", "lat": 35.5951, "lon": 139.3451, "line": "JR横浜線", "pref": "14", "city_code": "14150"},
    {"station_id": "sagami_ono", "name": "相模大野", "lat": 35.5315, "lon": 139.4377, "line": "小田急線", "pref": "14", "city_code": "14150"},
    # --- 横須賀・湘南 ---
    {"station_id": "yokosuka_chuo", "name": "横須賀中央", "lat": 35.2794, "lon": 139.6701, "line": "京急線", "pref": "14", "city_code": "14201"},
    {"station_id": "fujisawa", "name": "藤沢", "lat": 35.3388, "lon": 139.4870, "line": "JR東海道線", "pref": "14", "city_code": "14204"},
    {"station_id": "kamakura", "name": "鎌倉", "lat": 35.3190, "lon": 139.5503, "line": "JR横須賀線", "pref": "14", "city_code": "14211"},
    {"station_id": "ofuna", "name": "大船", "lat": 35.3507, "lon": 139.5315, "line": "JR東海道線", "pref": "14", "city_code": "14211"},
    {"station_id": "odawara", "name": "小田原", "lat": 35.2565, "lon": 139.1550, "line": "JR東海道線", "pref": "14", "city_code": "14205"},
    {"station_id": "hiratsuka", "name": "平塚", "lat": 35.3291, "lon": 139.3499, "line": "JR東海道線", "pref": "14", "city_code": "14203"},
    {"station_id": "chigasaki", "name": "茅ヶ崎", "lat": 35.3340, "lon": 139.4036, "line": "JR東海道線", "pref": "14", "city_code": "14207"},
    {"station_id": "yamato", "name": "大和", "lat": 35.4669, "lon": 139.4587, "line": "小田急江ノ島線", "pref": "14", "city_code": "14213"},
    {"station_id": "ebina", "name": "海老名", "lat": 35.4455, "lon": 139.3908, "line": "小田急線", "pref": "14", "city_code": "14215"},
    {"station_id": "tsujido", "name": "辻堂", "lat": 35.3394, "lon": 139.4464, "line": "JR東海道線", "pref": "14", "city_code": "14204"},

    # ==============================
    # 埼玉県 (11)
    # ==============================
    {"station_id": "omiya", "name": "大宮", "lat": 35.9063, "lon": 139.6232, "line": "JR高崎線", "pref": "11", "city_code": "11100"},
    {"station_id": "urawa", "name": "浦和", "lat": 35.8585, "lon": 139.6566, "line": "JR京浜東北線", "pref": "11", "city_code": "11100"},
    {"station_id": "musashi_urawa", "name": "武蔵浦和", "lat": 35.8448, "lon": 139.6352, "line": "JR武蔵野線", "pref": "11", "city_code": "11100"},
    {"station_id": "kita_urawa", "name": "北浦和", "lat": 35.8722, "lon": 139.6484, "line": "JR京浜東北線", "pref": "11", "city_code": "11100"},
    {"station_id": "minami_urawa", "name": "南浦和", "lat": 35.8453, "lon": 139.6645, "line": "JR京浜東北線", "pref": "11", "city_code": "11100"},
    {"station_id": "saitama_shintoshin", "name": "さいたま新都心", "lat": 35.8931, "lon": 139.6310, "line": "JR京浜東北線", "pref": "11", "city_code": "11100"},
    {"station_id": "kawaguchi", "name": "川口", "lat": 35.8069, "lon": 139.7210, "line": "JR京浜東北線", "pref": "11", "city_code": "11203"},
    {"station_id": "nishi_kawaguchi", "name": "西川口", "lat": 35.8215, "lon": 139.7166, "line": "JR京浜東北線", "pref": "11", "city_code": "11203"},
    {"station_id": "warabi", "name": "蕨", "lat": 35.8264, "lon": 139.6825, "line": "JR京浜東北線", "pref": "11", "city_code": "11223"},
    {"station_id": "toda_koen", "name": "戸田公園", "lat": 35.8132, "lon": 139.6730, "line": "JR埼京線", "pref": "11", "city_code": "11224"},
    {"station_id": "kawagoe", "name": "川越", "lat": 35.9077, "lon": 139.4854, "line": "JR川越線", "pref": "11", "city_code": "11201"},
    {"station_id": "hon_kawagoe", "name": "本川越", "lat": 35.9174, "lon": 139.4827, "line": "西武新宿線", "pref": "11", "city_code": "11201"},
    {"station_id": "tokorozawa", "name": "所沢", "lat": 35.7868, "lon": 139.4690, "line": "西武池袋線", "pref": "11", "city_code": "11210"},
    {"station_id": "koshigaya", "name": "越谷", "lat": 35.8910, "lon": 139.7902, "line": "東武スカイツリーライン", "pref": "11", "city_code": "11214"},
    {"station_id": "minami_koshigaya", "name": "南越谷", "lat": 35.8738, "lon": 139.7927, "line": "JR武蔵野線", "pref": "11", "city_code": "11214"},
    {"station_id": "kuki", "name": "久喜", "lat": 36.0624, "lon": 139.6648, "line": "JR宇都宮線", "pref": "11", "city_code": "11232"},
    {"station_id": "kasukabe", "name": "春日部", "lat": 35.9773, "lon": 139.7528, "line": "東武スカイツリーライン", "pref": "11", "city_code": "11214"},
    {"station_id": "ageo", "name": "上尾", "lat": 35.9762, "lon": 139.5915, "line": "JR高崎線", "pref": "11", "city_code": "11219"},
    {"station_id": "soka", "name": "草加", "lat": 35.8267, "lon": 139.8052, "line": "東武スカイツリーライン", "pref": "11", "city_code": "11221"},
    {"station_id": "misato", "name": "三郷", "lat": 35.8342, "lon": 139.8619, "line": "JR武蔵野線", "pref": "11", "city_code": "11237"},
    {"station_id": "shiki", "name": "志木", "lat": 35.8354, "lon": 139.5805, "line": "東武東上線", "pref": "11", "city_code": "11228"},
    {"station_id": "asaka", "name": "朝霞", "lat": 35.8131, "lon": 139.5970, "line": "東武東上線", "pref": "11", "city_code": "11227"},
    {"station_id": "asaka_dai", "name": "朝霞台", "lat": 35.8243, "lon": 139.5895, "line": "東武東上線", "pref": "11", "city_code": "11227"},
    {"station_id": "wako_shi", "name": "和光市", "lat": 35.7869, "lon": 139.6125, "line": "東武東上線", "pref": "11", "city_code": "11229"},
    {"station_id": "tsuruse", "name": "鶴瀬", "lat": 35.8462, "lon": 139.5311, "line": "東武東上線", "pref": "11", "city_code": "11237"},

    # ==============================
    # 千葉県 (12)
    # ==============================
    {"station_id": "chiba", "name": "千葉", "lat": 35.6131, "lon": 140.1134, "line": "JR総武線", "pref": "12", "city_code": "12100"},
    {"station_id": "kaihin_makuhari", "name": "海浜幕張", "lat": 35.6489, "lon": 140.0413, "line": "JR京葉線", "pref": "12", "city_code": "12100"},
    {"station_id": "inage", "name": "稲毛", "lat": 35.6364, "lon": 140.1064, "line": "JR総武線", "pref": "12", "city_code": "12100"},
    {"station_id": "soga", "name": "蘇我", "lat": 35.5827, "lon": 140.1276, "line": "JR京葉線", "pref": "12", "city_code": "12100"},
    {"station_id": "funabashi", "name": "船橋", "lat": 35.7015, "lon": 139.9852, "line": "JR総武線", "pref": "12", "city_code": "12202"},
    {"station_id": "nishi_funabashi", "name": "西船橋", "lat": 35.7200, "lon": 139.9596, "line": "JR総武線", "pref": "12", "city_code": "12202"},
    {"station_id": "tsudanuma", "name": "津田沼", "lat": 35.6818, "lon": 140.0237, "line": "JR総武線", "pref": "12", "city_code": "12202"},
    {"station_id": "minami_funabashi", "name": "南船橋", "lat": 35.6815, "lon": 139.9784, "line": "JR京葉線", "pref": "12", "city_code": "12202"},
    {"station_id": "matsudo", "name": "松戸", "lat": 35.7836, "lon": 139.9012, "line": "JR常磐線", "pref": "12", "city_code": "12204"},
    {"station_id": "shin_matsudo", "name": "新松戸", "lat": 35.8195, "lon": 139.9111, "line": "JR常磐線", "pref": "12", "city_code": "12204"},
    {"station_id": "kitakogane", "name": "北小金", "lat": 35.8337, "lon": 139.9155, "line": "JR常磐線", "pref": "12", "city_code": "12204"},
    {"station_id": "kashiwa", "name": "柏", "lat": 35.8601, "lon": 139.9739, "line": "JR常磐線", "pref": "12", "city_code": "12207"},
    {"station_id": "minami_kashiwa", "name": "南柏", "lat": 35.8447, "lon": 139.9552, "line": "JR常磐線", "pref": "12", "city_code": "12207"},
    {"station_id": "kashiwanoha_campus", "name": "柏の葉キャンパス", "lat": 35.8880, "lon": 139.9470, "line": "つくばエクスプレス", "pref": "12", "city_code": "12207"},
    {"station_id": "ichikawa", "name": "市川", "lat": 35.7316, "lon": 139.9082, "line": "JR総武線", "pref": "12", "city_code": "12203"},
    {"station_id": "motoyawata", "name": "本八幡", "lat": 35.7233, "lon": 139.9249, "line": "JR総武線", "pref": "12", "city_code": "12203"},
    {"station_id": "gyotoku", "name": "行徳", "lat": 35.6806, "lon": 139.9121, "line": "東京メトロ東西線", "pref": "12", "city_code": "12203"},
    {"station_id": "urayasu", "name": "浦安", "lat": 35.6635, "lon": 139.8981, "line": "東京メトロ東西線", "pref": "12", "city_code": "12227"},
    {"station_id": "shin_urayasu", "name": "新浦安", "lat": 35.6427, "lon": 139.9070, "line": "JR京葉線", "pref": "12", "city_code": "12227"},
    {"station_id": "nagareyama_otakanomori", "name": "流山おおたかの森", "lat": 35.8682, "lon": 139.9283, "line": "つくばエクスプレス", "pref": "12", "city_code": "12220"},
    {"station_id": "abiko", "name": "我孫子", "lat": 35.8659, "lon": 140.0256, "line": "JR常磐線", "pref": "12", "city_code": "12222"},
    {"station_id": "narita", "name": "成田", "lat": 35.7764, "lon": 140.3175, "line": "JR成田線", "pref": "12", "city_code": "12211"},
    {"station_id": "sakura", "name": "佐倉", "lat": 35.7237, "lon": 140.2280, "line": "JR総武本線", "pref": "12", "city_code": "12212"},
    {"station_id": "yachimata", "name": "八街", "lat": 35.6669, "lon": 140.3168, "line": "JR総武本線", "pref": "12", "city_code": "12230"},
    {"station_id": "yotsukaido", "name": "四街道", "lat": 35.6699, "lon": 140.1710, "line": "JR総武本線", "pref": "12", "city_code": "12228"},
    {"station_id": "noda_shi", "name": "野田市", "lat": 35.9544, "lon": 139.8722, "line": "東武アーバンパークライン", "pref": "12", "city_code": "12208"},
]


# ===== JSONファイルからの駅マスタ拡張読み込み =====
def _load_extended_stations() -> List[Dict]:
    """output/all_stations.json があれば読み込んでSTATIONSを拡張"""
    import json
    from pathlib import Path
    json_path = Path(__file__).resolve().parent.parent / "output" / "all_stations.json"
    if not json_path.exists():
        return []
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        extended = []
        existing_names = {s["name"] for s in STATIONS}
        for s in raw:
            if s["name"] not in existing_names:
                sid = f"{s['pref']}_{s['name']}"
                extended.append({
                    "station_id": sid,
                    "name": s["name"],
                    "lat": s["lat"],
                    "lon": s["lon"],
                    "line": s.get("line", ""),
                    "pref": s["pref"],
                    "city_code": s.get("city_code", ""),
                })
                existing_names.add(s["name"])
        return extended
    except Exception:
        return []

# ハードコード駅 + JSON拡張駅を統合
STATIONS = STATIONS + _load_extended_stations()

# ===== インデックス作成 =====
STATION_MAP: Dict[str, Dict] = {s["station_id"]: s for s in STATIONS}
STATION_NAME_MAP: Dict[str, str] = {s["station_id"]: s["name"] for s in STATIONS}

# 駅名 → station_id（都道府県内で一意になるようにする）
_NAME_TO_ID: Dict[str, List[str]] = {}
for s in STATIONS:
    _NAME_TO_ID.setdefault(s["name"], []).append(s["station_id"])


def get_stations_by_prefecture(pref_code: str) -> List[Dict]:
    """都道府県の駅一覧を返す"""
    return [s for s in STATIONS if s["pref"] == pref_code]


def find_nearest_station(
    lat: float, lon: float,
    max_distance_km: float = 2.0,
    pref_code: str = None,
) -> Optional[Dict]:
    """座標から最寄り駅を特定"""
    best = None
    best_dist = max_distance_km

    candidates = STATIONS
    if pref_code:
        candidates = [s for s in STATIONS if s["pref"] == pref_code]

    for s in candidates:
        d = _haversine(lat, lon, s["lat"], s["lon"])
        if d < best_dist:
            best_dist = d
            best = {**s, "distance_km": round(d, 3)}

    return best


def resolve_station_id(
    nearest_station_text: str = None,
    lat: float = None, lon: float = None,
    pref_code: str = None,
    max_distance_km: float = 2.0,
) -> Optional[str]:
    """
    駅名テキストや座標からstation_idを解決する。
    1. nearest_station_textで名前マッチ
    2. 失敗したら座標で最寄り検索
    """
    if nearest_station_text:
        # テキスト正規化
        name = nearest_station_text.strip()
        # 括弧内の路線名を除去: "渋谷(JR)" → "渋谷"
        for ch in ["(", "（"]:
            if ch in name:
                name = name[:name.index(ch)]
        name = name.strip()

        # 完全一致
        if name in _NAME_TO_ID:
            candidates = _NAME_TO_ID[name]
            if len(candidates) == 1:
                return candidates[0]
            # 都道府県でフィルタ
            if pref_code:
                for sid in candidates:
                    if STATION_MAP[sid]["pref"] == pref_code:
                        return sid
            # 座標で最も近い候補を選択
            if lat and lon:
                best_sid = None
                best_dist = 999
                for sid in candidates:
                    s = STATION_MAP[sid]
                    d = _haversine(lat, lon, s["lat"], s["lon"])
                    if d < best_dist:
                        best_dist = d
                        best_sid = sid
                return best_sid
            return candidates[0]

        # 部分一致
        for station_name, sids in _NAME_TO_ID.items():
            if name in station_name or station_name in name:
                if pref_code:
                    for sid in sids:
                        if STATION_MAP[sid]["pref"] == pref_code:
                            return sid
                return sids[0]

    # 座標による最寄り駅検索
    if lat and lon:
        result = find_nearest_station(lat, lon, max_distance_km, pref_code)
        if result:
            return result["station_id"]

    return None


# ===== 駅別参考地価（オフライン用） =====
# 主要駅周辺のm2あたり地価（円）
REFERENCE_LAND_PRICES_STATION: Dict[str, int] = {
    # 東京 - 都心
    "tokyo": 5_500_000, "yurakucho": 5_000_000, "shimbashi": 3_800_000,
    "shinagawa": 2_800_000, "shibuya": 3_200_000, "shinjuku": 3_000_000,
    "ikebukuro": 1_800_000, "ueno": 1_600_000, "akihabara": 2_200_000,
    "roppongi": 4_500_000, "omotesando": 4_000_000, "azabu_juban": 3_500_000,
    "hiroo": 3_000_000, "akasaka": 3_800_000, "kojimachi": 4_200_000,
    "ebisu": 2_800_000, "meguro": 2_000_000, "gotanda": 1_800_000,
    # 東京 - 山手線西側
    "harajuku": 3_500_000, "yoyogi": 2_500_000, "takadanobaba": 1_500_000,
    "mejiro": 1_400_000, "otsuka": 1_200_000, "sugamo": 1_100_000,
    "komagome": 1_200_000, "tabata": 1_000_000,
    # 東京 - 山手線東側
    "nishi_nippori": 900_000, "nippori": 850_000, "uguisudani": 1_000_000,
    "okachimachi": 1_800_000, "kanda": 2_500_000, "tamachi": 2_500_000,
    "hamamatsucho": 2_800_000,
    # 東京 - 中央線
    "nakano": 900_000, "koenji": 800_000, "asagaya": 750_000,
    "ogikubo": 700_000, "nishi_ogikubo": 650_000, "kichijoji": 800_000,
    "mitaka": 600_000,
    # 東京 - 世田谷・目黒
    "nakameguro": 1_500_000, "yutenji": 1_200_000, "gakugeidaigaku": 1_100_000,
    "jiyugaoka": 1_300_000, "sangenjaya": 1_000_000, "shimokitazawa": 1_100_000,
    "futakotamagawa": 1_000_000, "sakurashinmachi": 800_000, "yoga": 750_000,
    # 東京 - その他23区
    "kita_senju": 700_000, "kamata": 750_000, "omori": 800_000,
    "kinshicho": 850_000, "toyosu": 1_200_000, "kameido": 600_000,
    "monzennakacho": 1_100_000, "tsukishima": 1_400_000, "kachidoki": 1_300_000,
    "nihombashi": 3_000_000, "ningyocho": 1_800_000,
    "akabane": 600_000, "oji": 650_000, "nerima": 600_000,
    "itabashi": 550_000, "kasai": 500_000, "koiwa": 450_000,
    "kameari": 450_000, "kanamachi": 400_000, "mizue": 400_000,
    "takenotsuka": 400_000, "ayase": 450_000,
    "asakusa": 1_200_000, "asakusabashi": 1_000_000, "oshiage": 700_000,
    "ryogoku": 800_000, "minami_senju": 600_000,
    "narimasu": 500_000, "oizumigakuen": 450_000, "shakujii_koen": 500_000,
    # 神奈川
    "yokohama": 1_500_000, "shin_yokohama": 800_000, "sakuragicho": 1_200_000,
    "kannai": 1_000_000, "kawasaki": 1_000_000, "musashi_kosugi": 1_200_000,
    "musashi_mizonokuchi": 700_000, "tama_plaza": 650_000, "azamino": 600_000,
    "hiyoshi": 700_000, "noborito": 500_000, "shin_yurigaoka": 500_000,
    "fujisawa": 500_000, "kamakura": 550_000, "totsuka": 450_000,
    "tsurumi": 550_000, "ofuna": 400_000, "hiratsuka": 300_000,
    "chigasaki": 350_000, "sagamihara": 350_000, "hashimoto": 300_000,
    "odawara": 250_000, "yamato": 350_000, "ebina": 300_000,
    # 埼玉
    "omiya": 700_000, "urawa": 650_000, "musashi_urawa": 550_000,
    "kita_urawa": 500_000, "minami_urawa": 500_000,
    "saitama_shintoshin": 600_000,
    "kawaguchi": 550_000, "nishi_kawaguchi": 450_000,
    "warabi": 400_000, "toda_koen": 400_000,
    "kawagoe": 350_000, "tokorozawa": 400_000,
    "koshigaya": 300_000, "kasukabe": 250_000,
    "soka": 300_000, "wako_shi": 450_000,
    "shiki": 350_000, "asaka": 350_000, "asaka_dai": 350_000,
    "ageo": 250_000,
    # 千葉
    "chiba": 400_000, "kaihin_makuhari": 350_000,
    "funabashi": 500_000, "nishi_funabashi": 450_000, "tsudanuma": 400_000,
    "matsudo": 350_000, "shin_matsudo": 300_000,
    "kashiwa": 400_000, "minami_kashiwa": 350_000,
    "ichikawa": 450_000, "motoyawata": 400_000,
    "urayasu": 500_000, "shin_urayasu": 450_000,
    "nagareyama_otakanomori": 350_000,
    "gyotoku": 400_000, "abiko": 200_000,
}

# ===== 駅別参考賃料（オフライン用） =====
# m2あたり月額賃料（円）、構造別
REFERENCE_RENT_STATION: Dict[str, Dict[str, int]] = {
    # 東京都心
    "tokyo": {"RC": 6500, "SRC": 7000, "鉄骨": 5500, "木造": 4500},
    "shibuya": {"RC": 5800, "SRC": 6200, "鉄骨": 4800, "木造": 4000},
    "shinjuku": {"RC": 5200, "SRC": 5600, "鉄骨": 4400, "木造": 3600},
    "ikebukuro": {"RC": 4500, "SRC": 4800, "鉄骨": 3800, "木造": 3200},
    "shinagawa": {"RC": 5500, "SRC": 5800, "鉄骨": 4600, "木造": 3800},
    "ebisu": {"RC": 5500, "SRC": 5800, "鉄骨": 4600, "木造": 3800},
    "roppongi": {"RC": 6000, "SRC": 6500, "鉄骨": 5000, "木造": 4200},
    "omotesando": {"RC": 6000, "SRC": 6500, "鉄骨": 5000, "木造": 4200},
    "azabu_juban": {"RC": 5800, "SRC": 6200, "鉄骨": 4800, "木造": 4000},
    "hiroo": {"RC": 5500, "SRC": 5800, "鉄骨": 4600, "木造": 3800},
    "meguro": {"RC": 4800, "SRC": 5200, "鉄骨": 4000, "木造": 3400},
    "gotanda": {"RC": 4500, "SRC": 4800, "鉄骨": 3800, "木造": 3200},
    # 中央線
    "nakano": {"RC": 3800, "SRC": 4100, "鉄骨": 3200, "木造": 2800},
    "koenji": {"RC": 3600, "SRC": 3900, "鉄骨": 3000, "木造": 2600},
    "ogikubo": {"RC": 3500, "SRC": 3800, "鉄骨": 2900, "木造": 2500},
    "kichijoji": {"RC": 3800, "SRC": 4100, "鉄骨": 3200, "木造": 2800},
    # 世田谷・目黒
    "nakameguro": {"RC": 5000, "SRC": 5300, "鉄骨": 4200, "木造": 3500},
    "jiyugaoka": {"RC": 4500, "SRC": 4800, "鉄骨": 3800, "木造": 3200},
    "sangenjaya": {"RC": 4000, "SRC": 4300, "鉄骨": 3400, "木造": 2900},
    "shimokitazawa": {"RC": 4200, "SRC": 4500, "鉄骨": 3500, "木造": 3000},
    "futakotamagawa": {"RC": 4200, "SRC": 4500, "鉄骨": 3500, "木造": 3000},
    # 東京その他
    "toyosu": {"RC": 4500, "SRC": 4800, "鉄骨": 3800, "木造": 3200},
    "kinshicho": {"RC": 3800, "SRC": 4100, "鉄骨": 3200, "木造": 2800},
    "kita_senju": {"RC": 3200, "SRC": 3500, "鉄骨": 2700, "木造": 2300},
    "kamata": {"RC": 3400, "SRC": 3700, "鉄骨": 2900, "木造": 2500},
    "akabane": {"RC": 3000, "SRC": 3300, "鉄骨": 2500, "木造": 2200},
    "nerima": {"RC": 2800, "SRC": 3100, "鉄骨": 2400, "木造": 2100},
    "kasai": {"RC": 2600, "SRC": 2900, "鉄骨": 2200, "木造": 1900},
    "kameari": {"RC": 2500, "SRC": 2800, "鉄骨": 2100, "木造": 1800},
    # 神奈川
    "yokohama": {"RC": 4000, "SRC": 4300, "鉄骨": 3400, "木造": 2900},
    "musashi_kosugi": {"RC": 4200, "SRC": 4500, "鉄骨": 3500, "木造": 3000},
    "kawasaki": {"RC": 3500, "SRC": 3800, "鉄骨": 3000, "木造": 2500},
    "tama_plaza": {"RC": 3200, "SRC": 3500, "鉄骨": 2700, "木造": 2300},
    "fujisawa": {"RC": 2600, "SRC": 2900, "鉄骨": 2200, "木造": 1900},
    # 埼玉
    "omiya": {"RC": 3000, "SRC": 3300, "鉄骨": 2500, "木造": 2200},
    "urawa": {"RC": 2800, "SRC": 3100, "鉄骨": 2400, "木造": 2100},
    "kawaguchi": {"RC": 2800, "SRC": 3100, "鉄骨": 2400, "木造": 2100},
    "tokorozawa": {"RC": 2400, "SRC": 2700, "鉄骨": 2000, "木造": 1800},
    # 千葉
    "funabashi": {"RC": 2800, "SRC": 3100, "鉄骨": 2400, "木造": 2100},
    "matsudo": {"RC": 2400, "SRC": 2700, "鉄骨": 2000, "木造": 1800},
    "kashiwa": {"RC": 2500, "SRC": 2800, "鉄骨": 2100, "木造": 1900},
    "chiba": {"RC": 2400, "SRC": 2700, "鉄骨": 2000, "木造": 1800},
    "ichikawa": {"RC": 2800, "SRC": 3100, "鉄骨": 2400, "木造": 2100},
    "urayasu": {"RC": 3000, "SRC": 3300, "鉄骨": 2500, "木造": 2200},
}

# 参考賃料のデフォルト値
_DEFAULT_RENT = {"RC": 3000, "SRC": 3300, "鉄骨": 2500, "木造": 2200}


def get_reference_rent(station_id: str, structure: str = "RC") -> int:
    """駅別参考賃料を取得（m2/月）"""
    rents = REFERENCE_RENT_STATION.get(station_id, _DEFAULT_RENT)
    return rents.get(structure, rents.get("RC", 3000))


def get_reference_land_price(station_id: str) -> int:
    """駅別参考地価を取得（円/m2）"""
    return REFERENCE_LAND_PRICES_STATION.get(station_id, 500_000)
