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
import logging

from streamlink.exceptions import PluginError
from streamlink.plugin import Plugin, pluginargument, pluginmatcher

from soop_grid.bootstrap import Config
from soop_grid.protocol import GridError
from soop_grid.stream import OfficialHLSFallback, SoopAutoStream, SoopGridStream


log = logging.getLogger("streamlink.plugins.soop_grid")


def official_hls_streams(session, url, account, password, stream_password, purge_credentials=False):
    # Keep HLS behavior in Streamlink's maintained SOOP plugin. This sideloaded
    # plugin uses a separate ID and priority, so the built-in `soop` remains
    # loaded and is delegated to through the public plugin/session APIs.
    session.plugins.load_builtin()
    plugin_class = session.plugins.get_loaded().get("soop")
    if plugin_class is None:
        return {}
    plugin = plugin_class(
        session,
        url,
        options={
            "username": account,
            "password": password,
            "stream-password": stream_password,
            "purge-credentials": purge_credentials,
        },
    )
    return plugin.streams()


@pluginmatcher(
    re.compile(
        r"https?://play\.(?:sooplive\.com|sooplive\.co\.kr|afreecatv\.com)/(?P<channel>[A-Za-z0-9_-]+)(?:/(?P<bno>\d+))?/?(?:[?#].*)?$"
    ),
    priority=30,
)
@pluginargument(
    "transport",
    choices=("auto", "grid", "hls"),
    default="auto",
    argument_name="soop-grid-transport",
    help="Transport: Grid, official SOOP HLS, or Grid-first with initial HLS fallback.",
)
@pluginargument(
    "grid-account",
    sensitive=True,
    argument_name="soop-grid-account",
    help="Legacy alias for --soop-grid-username.",
)
@pluginargument(
    "grid-username",
    sensitive=True,
    argument_name="soop-grid-username",
    help="SOOP account ID for Native Grid and HLS fallback.",
)
@pluginargument(
    "grid-password",
    sensitive=True,
    argument_name="soop-grid-password",
    help="SOOP account password for Native Grid and HLS fallback.",
)
@pluginargument(
    "grid-require-adult",
    action="store_true",
    argument_name="soop-grid-require-adult",
    help="Require the authenticated live API adult check.",
)
@pluginargument(
    "grid-duration",
    type=int,
    argument_name="soop-grid-duration",
    help="Optional Grid capture limit in seconds; omit to record until stopped.",
)
@pluginargument(
    "grid-max-bytes",
    type=int,
    argument_name="soop-grid-max-bytes",
    help="Optional maximum Grid wire bytes; omit for no lifetime byte cap.",
)
@pluginargument(
    "grid-diagnostics",
    argument_name="soop-grid-diagnostics",
    help="Create a new private directory for redacted diagnostics and atomic summary.",
)
@pluginargument(
    "grid-stream-password",
    sensitive=True,
    argument_name="soop-grid-stream-password",
    help="Broadcast password for Native Grid and HLS fallback.",
)
@pluginargument(
    "grid-purge-credentials",
    action="store_true",
    argument_name="soop-grid-purge-credentials",
    help="Ask the built-in SOOP plugin to purge its saved credentials in HLS mode.",
)
class SoopGridPlugin(Plugin):
    def _get_streams(self):
        transport = self.get_option("transport") or "auto"
        account_option = self.get_option("grid-username")
        legacy_account_option = self.get_option("grid-account")
        if account_option is not None and legacy_account_option is not None:
            raise PluginError("Specify only one of --soop-grid-username and --soop-grid-account")
        account = (
            account_option
            if account_option is not None
            else legacy_account_option
            if legacy_account_option is not None
            else os.environ.get("SOOP_ACCOUNT_ID", "")
        )
        account_password = self.get_option("grid-password")
        if account_password is None:
            account_password = os.environ.pop("SOOP_ACCOUNT_PASSWORD", "")
        else:
            os.environ.pop("SOOP_ACCOUNT_PASSWORD", None)
        stream_password = self.get_option("grid-stream-password")
        if stream_password is None:
            stream_password = os.environ.pop("SOOP_STREAM_PASSWORD", "")
        else:
            os.environ.pop("SOOP_STREAM_PASSWORD", None)
        purge_credentials = bool(self.get_option("grid-purge-credentials"))
        if bool(account) != bool(account_password) or stream_password and not account:
            raise PluginError("account_credentials_required")
        if transport == "hls":
            try:
                hls_streams = official_hls_streams(
                    self.session,
                    self.url,
                    account,
                    account_password,
                    stream_password,
                    purge_credentials,
                )
            except Exception:
                raise PluginError("Official SOOP HLS stream enumeration failed") from None
            if not hls_streams:
                raise PluginError("The official SOOP plugin returned no HLS streams")
            yield from hls_streams.items()
            return

        config = Config(
            broadcaster=self.match["channel"],
            bno=self.match["bno"],
            account=account,
            account_password=account_password,
            stream_password=stream_password,
            require_adult=bool(self.get_option("grid-require-adult")),
            duration=self.get_option("grid-duration"),
            max_bytes=self.get_option("grid-max-bytes"),
            capture_dir=os.environ.get("SOOP_GRID_CAPTURE_DIR") or None,
            diagnostics_dir=self.get_option("grid-diagnostics")
            or os.environ.get("SOOP_GRID_DIAGNOSTICS_DIR")
            or None,
            referer=self.url,
            user_agent=self.session.http.headers.get("User-Agent"),
        )
        try:
            config.validate()
        except GridError as exc:
            config.clear()
            raise PluginError(str(exc)) from None
        grid = SoopGridStream(self.session, config)
        if transport == "auto":
            fallback = OfficialHLSFallback(
                self.session,
                self.url,
                account,
                account_password,
                stream_password,
                purge_credentials=purge_credentials,
            )
            yield "original", SoopAutoStream(self.session, grid, fallback)
        else:
            yield "original", grid


__plugin__ = SoopGridPlugin
