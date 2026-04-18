"""Deterministic friendly names for cluster nodes.

Maps `gke-nav-gpu-cluster-t4-pool-8ksx` → `hopeless-bear` (same node → same
alias, always). Makes demos memorable: "NVSentinel just cordoned hopeless-bear."

Word lists are curated for terminal-friendly length (≤8 chars each), positive/
neutral affect (no slurs / politically loaded words), and phonetic distinctness.
"""
from __future__ import annotations

import hashlib

# 64 adjectives × 64 animals = 4096 unique aliases — plenty for a cluster demo.
# Curated for: short, pronounceable, non-offensive, vivid.
_ADJECTIVES = [
    "hopeful", "hopeless", "restless", "curious", "sleepy", "grumpy", "cheerful",
    "stormy", "sunny", "misty", "cosmic", "stellar", "lunar", "swift", "steady",
    "brave", "bashful", "cunning", "clever", "dreamy", "eager", "earnest",
    "fabled", "festive", "fluffy", "frosty", "gentle", "giddy", "glowing",
    "hardy", "humble", "icy", "jolly", "kindly", "lazy", "lively", "lucid",
    "merry", "mighty", "mellow", "nimble", "noisy", "plucky", "prancy",
    "quirky", "radiant", "rowdy", "rustic", "scruffy", "shiny", "silly",
    "sleek", "snug", "somber", "spiffy", "spry", "stoic", "tangy", "tidy",
    "unruly", "velvet", "wistful", "witty", "zesty",
]

_ANIMALS = [
    "bear", "otter", "wolf", "fox", "lynx", "panda", "moose", "badger",
    "beaver", "bison", "bobcat", "buffalo", "camel", "caribou", "cheetah",
    "chipmunk", "coyote", "dingo", "dolphin", "eagle", "elk", "ferret",
    "finch", "gazelle", "gecko", "gerbil", "gibbon", "goat", "goose",
    "hamster", "hare", "hawk", "hedgehog", "heron", "ibex", "iguana",
    "jackal", "jaguar", "koala", "lemur", "leopard", "llama", "magpie",
    "marmot", "mongoose", "mule", "narwhal", "ocelot", "orca", "osprey",
    "panther", "pelican", "penguin", "raccoon", "raven", "robin", "salmon",
    "seal", "sloth", "sparrow", "stag", "swan", "tapir", "tiger", "walrus",
]


def friendly_name(real_name: str) -> str:
    """Deterministic adj-animal alias for a node / cluster / resource name.

    >>> friendly_name("gke-nav-gpu-cluster-t4-pool-8ksx")
    'prancy-raven'
    >>> friendly_name("gke-nav-gpu-cluster-t4-pool-8ksx")
    'prancy-raven'
    """
    if not real_name:
        return "unknown-beast"
    # SHA-256 → stable across Python versions (unlike hash())
    digest = hashlib.sha256(real_name.encode()).digest()
    adj_idx = int.from_bytes(digest[:4], "big") % len(_ADJECTIVES)
    ani_idx = int.from_bytes(digest[4:8], "big") % len(_ANIMALS)
    return f"{_ADJECTIVES[adj_idx]}-{_ANIMALS[ani_idx]}"


def short(real_name: str) -> str:
    """Last hyphen-separated token of a k8s node name, for parenthetical display."""
    if not real_name:
        return ""
    return real_name.rsplit("-", 1)[-1]
