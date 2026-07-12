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
