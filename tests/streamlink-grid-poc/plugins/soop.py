# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""
$description Experimental native TCP Grid receiver for authorized SOOP live streams.
$url play.sooplive.com
$url play.sooplive.co.kr
$url play.afreecatv.com
$type live
"""

import os
import re

from streamlink.exceptions import PluginError
from streamlink.plugin import Plugin, pluginargument, pluginmatcher

from soop_grid.bootstrap import Config
from soop_grid.protocol import GridError
from soop_grid.stream import SoopGridStream


@pluginmatcher(
    re.compile(
        r"https?://play\.(?:sooplive\.com|sooplive\.co\.kr|afreecatv\.com)/(?P<channel>[A-Za-z0-9_-]+)(?:/(?P<bno>\d+))?/?(?:[?#].*)?$"
    )
)
@pluginargument(
    "grid-account", sensitive=True, help="Account ID; password comes only from SOOP_ACCOUNT_PASSWORD."
)
@pluginargument(
    "grid-require-adult", action="store_true", help="Require the authenticated live API adult check."
)
@pluginargument(
    "grid-duration", type=int, default=35, help="Bounded Grid session duration in seconds (1..60)."
)
class SoopGrid(Plugin):
    def _get_streams(self):
        config = Config(
            broadcaster=self.match["channel"],
            bno=self.match["bno"],
            account=self.get_option("grid-account") or os.environ.get("SOOP_ACCOUNT_ID", ""),
            account_password=os.environ.pop("SOOP_ACCOUNT_PASSWORD", ""),
            stream_password=os.environ.pop("SOOP_STREAM_PASSWORD", ""),
            require_adult=bool(self.get_option("grid-require-adult")),
            duration=int(
                self.get_option("grid-duration") if self.get_option("grid-duration") is not None else 35
            ),
            capture_dir=os.environ.get("SOOP_GRID_CAPTURE_DIR") or None,
        )
        try:
            config.validate()
        except GridError as exc:
            config.clear()
            raise PluginError(str(exc)) from None
        yield "original", SoopGridStream(self.session, config)


__plugin__ = SoopGrid
