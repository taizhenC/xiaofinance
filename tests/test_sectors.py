import json

from app.config import settings
from app.mentions import load_stock_dict
from app.scoring import sector_breakdown


def test_sector_breakdown_shares_and_leaders():
    stats = {
        "NVDA": {"score": 30.0, "mentions": 6, "focused_mentions": 4},
        "MU": {"score": 10.0, "mentions": 3, "focused_mentions": 1},
        "JPM": {"score": 10.0, "mentions": 2, "focused_mentions": 0},
        "ZZZ": {"score": 5.0, "mentions": 1, "focused_mentions": 1},
        "QUIET": {"score": 0.0, "mentions": 0, "focused_mentions": 0},
    }
    sectors = {"NVDA": "半导体", "MU": "半导体", "JPM": "金融"}

    out = sector_breakdown(stats, sectors)
    by = {s["sector"]: s for s in out}

    assert [s["sector"] for s in out] == ["半导体", "金融", "其他"]  # ordered by score
    assert by["半导体"]["share"] == 72.7  # 40 of 55
    assert by["半导体"]["tickers"] == 2
    assert by["半导体"]["leader"]["ticker"] == "NVDA"
    assert by["其他"]["leader"]["ticker"] == "ZZZ"  # an unmapped ticker still surfaces
    assert "QUIET" not in {s["leader"]["ticker"] for s in out}  # no mentions, no row


def test_every_dict_entry_has_a_sector():
    stocks = load_stock_dict()["stocks"]
    missing = [s["ticker"] for s in stocks if not s.get("sector")]
    assert not missing, f"tickers with no sector: {missing}"


def test_sector_labels_stay_a_closed_set():
    # A typo'd sector silently becomes its own slice of the composition bar.
    known = {
        "半导体", "科技", "中概", "金融", "医药", "消费", "汽车", "能源电力",
        "工业军工", "加密", "旅游航空", "题材", "ETF指数",
    }
    raw = json.loads(settings.STOCK_DICT_PATH.read_text(encoding="utf-8"))
    used = {s["sector"] for s in raw["stocks"]}
    assert used <= known, f"unknown sector labels: {used - known}"
