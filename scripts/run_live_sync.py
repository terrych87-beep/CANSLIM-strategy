#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ASCII entry for live data sync (used by startup bat)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.live_data_sync import sync_live_data


if __name__ == '__main__':
    result = sync_live_data(ROOT, fail_soft=False)
    if not result.get('ok'):
        sys.exit(1)
