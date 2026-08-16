"""Coordinator that owns the soundbar connection and merged state."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
from .protocol import LGSoundbarClient

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

# How long to wait after requesting state for the bar's responses to arrive.
RESPONSE_GRACE = 1.5


class LGSoundbarCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Maintains one connection per soundbar and a merged field cache.

    The soundbar both answers ``get`` requests and *pushes* updates whenever
    something changes (app, remote, front panel), so state stays live between
    polls. The periodic poll is only a safety-net resync.

    The cache is keyed by message type (``{"EQ_VIEW_INFO": {...},
    "SETTING_VIEW_INFO": {...}, ...}``), NOT a single flat namespace. The bar
    itself reuses field names across message types for unrelated things --
    confirmed on an H7: ``i_bass_min``/``i_bass_max`` mean the EQ tone range
    in EQ_VIEW_INFO (-6..6) but the physical bass channel's level range in
    SETTING_VIEW_INFO (-15..12), and ``b_smart_mixer`` appears in both
    SPK_LIST_VIEW_INFO and SETTING_VIEW_INFO and has been observed to
    genuinely disagree between the two mid-transition. A flat merge lets
    whichever response lands last silently clobber the other message's
    value under the same key -- this is what caused the Bass tone entity to
    flap between two different, both "valid", ranges every poll cycle.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        host: str,
        name: str,
        unique_id: str,
    ) -> None:
        scan = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=name,
            update_interval=timedelta(seconds=scan),
        )
        self.host = host
        self.device_name = name
        self.unique_id = unique_id
        self._client: LGSoundbarClient | None = None
        self._cache: dict[str, dict[str, Any]] = {}

    def get(self, message: str, key: str, default: Any = None) -> Any:
        """Look up a field within its own message namespace.

        Always use this (or ``self.data.get(message, {})``) instead of
        reaching into a flat merged dict -- see the class docstring for why.
        """
        return (self.data or {}).get(message, {}).get(key, default)

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        if self._client is None:
            self._client = LGSoundbarClient(
                self.host,
                on_message=self._on_message_threadsafe,
                on_availability=self._on_availability_threadsafe,
            )
            # Give the background socket a moment to come up so the first poll
            # returns data instead of waiting a whole interval.
            for _ in range(20):
                if self._client.available:
                    break
                await asyncio.sleep(0.25)

        try:
            await self.hass.async_add_executor_job(self._client.request_all)
        except ConnectionError as err:
            raise UpdateFailed(f"Soundbar {self.host} unreachable: {err}") from err

        # Let the push callback merge the responses that come back.
        await asyncio.sleep(RESPONSE_GRACE)

        if not self._cache:
            raise UpdateFailed(f"No response from soundbar {self.host}")
        return {msg: dict(fields) for msg, fields in self._cache.items()}

    # -- thread-safe bridges from the client's listener thread ---------------

    def _on_message_threadsafe(self, message: dict) -> None:
        self.hass.loop.call_soon_threadsafe(self._handle_message, message)

    def _on_availability_threadsafe(self, available: bool) -> None:
        self.hass.loop.call_soon_threadsafe(self._handle_availability, available)

    @callback
    def _handle_message(self, message: dict) -> None:
        msg_type = message.get("msg")
        data = message.get("data")
        if not msg_type or not isinstance(data, dict):
            return
        self._cache.setdefault(msg_type, {}).update(data)
        self.async_set_updated_data(
            {msg: dict(fields) for msg, fields in self._cache.items()}
        )

    @callback
    def _handle_availability(self, available: bool) -> None:
        if not available:
            return
        # On (re)connect, ask for a fresh snapshot.
        if self._client is not None:
            self.hass.async_add_executor_job(self._client.request_all)

    # -- control -------------------------------------------------------------

    async def async_set_key(self, message: str, key: str, value: Any) -> None:
        """Write a single field and optimistically reflect it.

        The bar echoes the real value back via a push shortly after, so any
        device-side clamping/scaling self-corrects.
        """
        if self._client is None:
            raise UpdateFailed("Soundbar not connected")
        await self.hass.async_add_executor_job(self._client.set, message, {key: value})
        self._cache.setdefault(message, {})[key] = value
        self.async_set_updated_data(
            {msg: dict(fields) for msg, fields in self._cache.items()}
        )

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        if self._client is not None:
            await self.hass.async_add_executor_job(self._client.close)
            self._client = None
