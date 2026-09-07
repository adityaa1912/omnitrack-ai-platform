"""Build/release metadata helpers shared by CI workflows and tests.

Image tags are immutable: every commit produces ``sha-<full>`` (and
``sha-<short>`` for readability), tags produce ``v<version>``. ``latest`` is
never pushed by the release workflow.
"""

from __future__ import annotations

import re

TAG_REF = re.compile(r"^refs/tags/(v\d+\.\d+\.\d+)$")


def short_sha(sha: str, length: int = 12) -> str:
    return sha[:length]


def compute_image_tags(ref: str, sha: str) -> list[str]:
    """Tags for an image built from ``ref`` (git ref) at commit ``sha``.

    Always includes the immutable ``sha-<full>`` tag. Branch refs add the
    branch name (slashes replaced); ``main`` additionally gets ``main``.
    Version tags (``refs/tags/vX.Y.Z``) map to ``vX.Y.Z``.
    """
    tags = [f"sha-{sha}"]
    if ref.startswith("refs/heads/"):
        branch = ref[len("refs/heads/"):]
        tags.append(branch.replace("/", "-"))
    elif ref.startswith("refs/tags/"):
        tag = ref[len("refs/tags/"):]
        if TAG_REF.match(ref):
            tags.append(tag)
    else:
        tags.append(ref.replace("/", "-"))
    return tags


def is_release_ref(ref: str) -> bool:
    return bool(TAG_REF.match(ref))


def is_pushable_ref(ref: str) -> bool:
    return ref == "refs/heads/main" or is_release_ref(ref)


def image_ref(registry: str, name: str, tag: str) -> str:
    return f"{registry}/{name}:{tag}"
