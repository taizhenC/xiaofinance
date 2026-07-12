import hashlib
import re
import time
import unicodedata

CN_UNITS = {"万": 10_000, "亿": 100_000_000, "w": 10_000, "k": 1_000}

_COUNT_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*(万|亿|w|k)?\+?$")


def now_ms() -> int:
    return int(time.time() * 1000)


def normalize_ts(value) -> int | None:
    """Epoch timestamp → ms. XHS gives 13-digit ms, but defend against 10-digit seconds."""
    if value is None:
        return None
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v < 10**12:
        v *= 1000
    return v


def parse_cn_count(value) -> int:
    """'1.2万'→12000, '10+'→10, '3856'→3856, ''/None/'点赞'→0."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower().replace(",", "")
    if not s:
        return 0
    m = _COUNT_RE.match(s)
    if not m:
        return 0
    num = float(m.group(1))
    unit = CN_UNITS.get(m.group(2) or "", 1)
    return int(num * unit)


def norm_text(s: str) -> str:
    """Full-width → half-width (NFKC), lowercase latin. Keeps case-insensitive matching sane."""
    return unicodedata.normalize("NFKC", s or "")


_TOPIC_TAG_RE = re.compile(r"#[^#\n]{0,40}\[话题\]#")
_TAG_MARK_RE = re.compile(r"\[话题\]#")


def note_text(title: str | None, desc: str | None) -> str:
    """Title + desc as one line; XHS descs often repeat the title verbatim at
    the start, which would double it in quotes and LLM input."""
    title = " ".join((title or "").split())
    desc = " ".join((desc or "").split())
    if title and desc.startswith(title):
        return desc
    return f"{title} {desc}".strip()


def clean_tags(s: str) -> str:
    """'#闪迪[话题]#' → '#闪迪' — keeps the topic signal, drops the markup."""
    return " ".join(_TAG_MARK_RE.sub(" ", s or "").split())


def strip_hashtags(s: str) -> str:
    """Remove whole #xxx[话题]# spans — for checking whether a term appears in
    the prose rather than only in the tag block."""
    return _TOPIC_TAG_RE.sub(" ", s or "")


_KEEP_RE = re.compile(r"[^0-9a-z一-鿿]+")


def norm_for_hash(s: str) -> str:
    return _KEEP_RE.sub("", norm_text(s).lower())


# Matches both forms a hashtag reaches us in: raw '#美股[话题]#' and clean_tags' '#美股'.
_HASHTAG_RE = re.compile(r"#[^#\n]{0,40}\[话题\]#|#[^\s#]{1,30}")
_HANDLE_RE = re.compile(r"@[^\s@]{1,20}")
_EMOJI_TAG_RE = re.compile(r"\[[^\[\]\n]{1,12}\]")  # [笑哭R], [加油R]
_URL_RE = re.compile(r"https?://\S+")
_NON_PROSE_RE = re.compile(r"[^0-9a-zA-Z一-鿿]+")

# 问一问 is XHS's own AI assistant. "@问一问 为什么海力士进不了纳指" is a question put to
# a bot — it looks like a quote but states no view, so it is worth nothing as evidence.
BOT_HANDLES = ("问一问",)

# An image post whose desc is nothing but #话题# tags reduces to a bare title: its argument
# lives in the picture, which we cannot read. "Is That True？" scores 10.
MIN_NOTE_SUBSTANCE = 15
# "有", "同意", "👍" — agreement with no argument attached.
MIN_COMMENT_SUBSTANCE = 6
# A headline quote has to stand on its own. "我咋感觉股价到头了" (18) is a fine bearish datum
# for the model to count and a poor thing to show as what the crowd is saying.
QUOTE_MIN_SUBSTANCE = 20


def substance(s: str) -> int:
    """How much a reader (or the LLM) actually gets out of a piece of text: its length
    once hashtags, @handles, emoji tags, URLs and punctuation come out.

    A CJK character counts double, because 一个汉字 carries roughly what an English word
    does — without that, "Is That True？" (10) and "海力士暴涨13%，美股创新高！" (12) look
    like the same amount of information, and the second one is a whole claim."""
    s = _URL_RE.sub(" ", s or "")
    s = _HASHTAG_RE.sub(" ", s)
    s = _HANDLE_RE.sub(" ", s)
    s = _EMOJI_TAG_RE.sub(" ", s)
    prose = _NON_PROSE_RE.sub("", norm_text(s))
    return len(prose) + sum(1 for ch in prose if "一" <= ch <= "鿿")


def is_bot_prompt(s: str) -> bool:
    return any(f"@{h}" in (s or "") for h in BOT_HANDLES)


def simhash64(text: str) -> int:
    s = norm_for_hash(text)
    if not s:
        return 0
    grams = [s[i : i + 2] for i in range(len(s) - 1)] or [s]
    v = [0] * 64
    for g in grams:
        h = int.from_bytes(hashlib.blake2b(g.encode("utf-8"), digest_size=8).digest(), "big")
        for b in range(64):
            v[b] += 1 if (h >> b) & 1 else -1
    return sum(1 << b for b in range(64) if v[b] > 0)


def hamming64(a: int, b: int) -> int:
    return ((a ^ b) & 0xFFFFFFFFFFFFFFFF).bit_count()


def to_signed64(v: int) -> int:
    """SQLite INTEGER is signed 64-bit; wrap unsigned simhash for storage."""
    return v - (1 << 64) if v >= (1 << 63) else v


def from_signed64(v: int) -> int:
    return v & 0xFFFFFFFFFFFFFFFF


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()
