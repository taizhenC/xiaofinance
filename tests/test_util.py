from infinance.util import hamming64, normalize_ts, parse_cn_count, simhash64


def test_parse_cn_count():
    assert parse_cn_count("1.2万") == 12000
    assert parse_cn_count("10+") == 10
    assert parse_cn_count("") == 0
    assert parse_cn_count(None) == 0
    assert parse_cn_count("3856") == 3856
    assert parse_cn_count("1.5亿") == 150_000_000
    assert parse_cn_count("点赞") == 0
    assert parse_cn_count(42) == 42
    assert parse_cn_count("1,234") == 1234


def test_normalize_ts():
    assert normalize_ts(1720000000) == 1720000000000
    assert normalize_ts(1720000000000) == 1720000000000
    assert normalize_ts("1720000000") == 1720000000000
    assert normalize_ts(None) is None
    assert normalize_ts(0) is None
    assert normalize_ts("garbage") is None


def test_simhash_near_duplicates_cluster():
    a = "英伟达财报炸裂，盘后大涨8%，AI芯片需求依然强劲，继续持有不动摇"
    b = "英伟达财报炸裂，盘后大涨8%！AI芯片需求依然强劲，继续持有不动摇！！"
    c = "今天去迪士尼玩了一天，太开心了，推荐大家去"
    assert hamming64(simhash64(a), simhash64(b)) <= 6
    assert hamming64(simhash64(a), simhash64(c)) > 6
