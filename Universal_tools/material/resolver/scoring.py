import os
from typing import List, Optional

from .rules import AMBIGUOUS_DELTA_THRESHOLD, CHANNEL_ALIASES, SUFFIX_PATTERNS
from .types import ChannelCandidate


def _tokens(s: str) -> List[str]:
    out = []
    cur = []
    for ch in s.lower():
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return out


def _mat_token_match(material_name: str, file_path: str) -> float:
    stem = os.path.splitext(os.path.basename(file_path))[0].lower()
    mat_tokens = _tokens(material_name)
    if not mat_tokens:
        return 0.0

    hits = 0
    for t in mat_tokens:
        if t and t in stem:
            hits += 1
    if hits == 0:
        return 0.0
    return min(1.0, hits / max(1, len(mat_tokens)))


def _channel_match(channel: str, file_path: str) -> float:
    stem = os.path.splitext(os.path.basename(file_path))[0].lower()
    alias_hits = any(a in stem for a in CHANNEL_ALIASES.get(channel, []))
    suffix_hits = any(stem.endswith(suf) for suf in SUFFIX_PATTERNS.get(channel, []))
    if suffix_hits:
        return 1.0
    if alias_hits:
        return 0.7
    return 0.0


def _proximity_bonus(file_path: str, context_dir: str) -> float:
    if not context_dir:
        return 0.0
    try:
        common = os.path.commonpath([os.path.abspath(file_path), os.path.abspath(context_dir)])
        if common and common == os.path.abspath(context_dir):
            return 0.3
    except Exception:
        return 0.0
    return 0.0


def score_candidate(material_name: str, channel: str, file_path: str, context: dict) -> float:
    base = 0.0
    base += 0.45 * _mat_token_match(material_name, file_path)
    base += 0.45 * _channel_match(channel, file_path)
    base += 0.10 * _proximity_bonus(file_path, context.get("context_dir", ""))
    if context.get("source") == "metadata":
        base += 0.15
    return min(1.0, base)


def pick_best_candidate(candidates: List[ChannelCandidate]) -> Optional[ChannelCandidate]:
    if not candidates:
        return None
    cands = sorted(candidates, key=lambda c: c.score, reverse=True)
    if len(cands) >= 2:
        delta = cands[0].score - cands[1].score
        if delta < AMBIGUOUS_DELTA_THRESHOLD:
            cands[0].meta["ambiguous"] = "1"
    return cands[0]
