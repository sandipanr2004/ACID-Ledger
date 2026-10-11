"""Merkle tree over journal entry hashes (SHA-256).

Leaves are the hex-encoded hash of each journal entry. Odd levels duplicate
their last node, the same convention QLDB uses for its journal Merkle trees.
"""

import hashlib


def _b(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str)


def _h(left_hex: str, right_hex: str) -> str:
    return hashlib.sha256(_b(left_hex) + _b(right_hex)).hexdigest()


def empty_root() -> str:
    """Deterministic root for an empty journal."""
    return hashlib.sha256(b"").hexdigest()


def merkle_root(entry_hashes):
    """Compute the Merkle root over a list of hex entry hashes."""
    level = list(entry_hashes)
    if not level:
        return empty_root()
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [_h(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def build_proof(entry_hashes, index):
    """Build an inclusion proof for entry `index`.

    Returns {"root": hex, "index": int, "leaf": hex,
             "path": [{"side": "left"|"right", "hash": hex}, ...]}
    """
    entry_hashes = list(entry_hashes)
    if not entry_hashes:
        raise ValueError("cannot build proof for an empty journal")
    if not 0 <= index < len(entry_hashes):
        raise IndexError("entry index out of range")

    leaf = entry_hashes[index]
    level = list(entry_hashes)
    i = index
    path = []
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        sib = i ^ 1
        if sib < i:
            path.append({"side": "left", "hash": level[sib]})
        else:
            path.append({"side": "right", "hash": level[sib]})
        level = [_h(level[j], level[j + 1]) for j in range(0, len(level), 2)]
        i //= 2
    return {"root": level[0], "index": index, "leaf": leaf, "path": path}


def verify_proof(leaf_hex, proof):
    """Verify an inclusion proof produced by build_proof against its root."""
    digest = _b(leaf_hex)
    for step in proof["path"]:
        sibling = _b(step["hash"])
        if step["side"] == "left":
            digest = hashlib.sha256(sibling + digest).digest()
        elif step["side"] == "right":
            digest = hashlib.sha256(digest + sibling).digest()
        else:
            return False
    return digest.hex() == proof["root"] and leaf_hex == proof["leaf"]
