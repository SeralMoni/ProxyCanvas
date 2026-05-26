from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

import config

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sdk.sousaku import SousakuClient


def create_sousaku_client(*, token: str | None = None, tokens: Iterable[str] | None = None) -> SousakuClient:
    """Create an isolated Sousaku client.

    Jobs must not share a client because the SDK stores the active token on the
    client instance. Giving each job its own single-token client avoids token
    cross-talk without needing a global network lock.
    """
    if token:
        tokens = [token]
    kwargs = {"save_dir": config.SOUSAKU_SAVE_DIR}
    if tokens is not None:
        kwargs["tokens"] = list(tokens)
        kwargs["auto_rotate_token"] = False
    return SousakuClient.from_config(config.SOUSAKU_CONFIG_PATH, **kwargs)
