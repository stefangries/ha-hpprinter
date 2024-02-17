import logging

from homeassistant.core import Event
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import slugify

from ..common.consts import (
    DOMAIN,
    SIGNAL_HA_DEVICE_CREATED,
    SIGNAL_HA_DEVICE_DISCOVERED,
    UPDATE_API_INTERVAL,
)
from .ha_config_manager import HAConfigManager
from .rest_api import RestAPIv2

_LOGGER = logging.getLogger(__name__)


class HACoordinator(DataUpdateCoordinator):
    """My custom coordinator."""

    def __init__(
        self,
        hass,
        config_manager: HAConfigManager,
    ):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=config_manager.entry_title,
            update_interval=UPDATE_API_INTERVAL,
            update_method=self._async_update_data,
        )

        self._api = RestAPIv2(hass, config_manager)
        self._config_manager = config_manager
        self._devices: dict[str, DeviceInfo] = {}

        self._main_device_data: dict | None = None
        self._main_device_id: str | None = None

        self._device_handlers = {
            "Main": self.create_main_device,
            "Consumable": self.create_consumable_device,
            "Adapter": self.create_adapter_device,
        }

        self.config_entry.async_on_unload(
            async_dispatcher_connect(
                hass, SIGNAL_HA_DEVICE_DISCOVERED, self._on_device_discovered
            )
        )

    @property
    def api(self) -> RestAPIv2:
        return self._api

    @property
    def config_manager(self) -> HAConfigManager:
        return self._config_manager

    @property
    def entry_id(self) -> str:
        return self._config_manager.entry_id

    @property
    def entry_title(self) -> str:
        return self._config_manager.entry_title

    async def on_home_assistant_start(self, _event_data: Event):
        await self.initialize()

    async def on_home_assistant_stop(self, _event_data: Event):
        await self._api.terminate()

    async def initialize(self):
        _LOGGER.debug("Initializing coordinator")

        entry = self.config_manager.entry
        platforms = self.config_manager.platforms
        await self.hass.config_entries.async_forward_entry_setups(entry, platforms)

        _LOGGER.info(f"Start loading {DOMAIN} integration, Entry ID: {entry.entry_id}")

        await self._api.initialize()

        await self.async_config_entry_first_refresh()

    def create_main_device(
        self, device_key: str, device_data: dict, _device_config: dict
    ):
        self._main_device_data = device_data
        self._main_device_id = device_key

        model = device_data.get("make_and_model")
        serial_number = device_data.get("serial_number")
        manufacturer = device_data.get("manufacturer_name")

        device_identifier = (DOMAIN, self._main_device_id)

        device_info = DeviceInfo(
            identifiers={device_identifier},
            name=self.entry_title,
            model=model,
            serial_number=serial_number,
            manufacturer=manufacturer,
        )

        self._devices[device_key] = device_info

    def create_sub_unit_device(
        self, device_key: str, _device_data: dict, device_config: dict
    ):
        model = self._main_device_data.get("make_and_model")
        serial_number = self._main_device_data.get("serial_number")
        manufacturer = self._main_device_data.get("manufacturer_name")

        device_type = device_config.get("device_type")

        device_unique_id = slugify(f"{self.entry_id}.{device_key}")

        sub_unit_device_name = f"{self.entry_title} {device_type}"

        device_identifier = (DOMAIN, device_unique_id)

        device_info = DeviceInfo(
            identifiers={device_identifier},
            name=sub_unit_device_name,
            model=model,
            serial_number=serial_number,
            manufacturer=manufacturer,
            via_device=(DOMAIN, self._main_device_id),
        )

        self._devices[device_key] = device_info

    def create_consumable_device(
        self, device_key: str, device_data: dict, _device_config: dict
    ):
        printer_device_unique_id = slugify(f"{self.entry_id}.printer")

        device_name_parts = [self.entry_title]
        cartridge_type: str = device_data.get("consumable_type_enum")
        cartridge_color = device_data.get("marker_color")
        manufacturer = device_data.get("consumable_life_state_brand")
        serial_number = device_data.get("serial_number")

        model = device_data.get("consumable_selectibility_number")

        if cartridge_type == "printhead":
            device_name_parts.append(cartridge_type.capitalize())
            model = cartridge_type.capitalize()

        else:
            device_name_parts.append(cartridge_color)
            device_name_parts.append(cartridge_type.capitalize())

        device_unique_id = slugify(f"{self.entry_id}.{device_key}")

        cartridge_device_name = " ".join(device_name_parts)

        device_identifier = (DOMAIN, device_unique_id)

        device_info = DeviceInfo(
            identifiers={device_identifier},
            name=cartridge_device_name,
            model=model,
            serial_number=serial_number,
            manufacturer=manufacturer,
            via_device=(DOMAIN, printer_device_unique_id),
        )

        self._devices[device_key] = device_info

    def create_adapter_device(
        self, device_key: str, device_data: dict, device_config: dict
    ):
        serial_number = self._main_device_data.get("serial_number")
        manufacturer = self._main_device_data.get("manufacturer_name")

        adapter_name = device_data.get("hardware_config_name").upper()
        model = (
            device_data.get("hardware_config_device_connectivity_port_type")
            .replace("Embedded", "")
            .upper()
        )

        device_type = device_config.get("device_type")

        device_unique_id = slugify(f"{self.entry_id}.{device_key}")

        adapter_device_name = f"{self.entry_title} {device_type} {adapter_name}"

        device_identifier = (DOMAIN, device_unique_id)

        device_info = DeviceInfo(
            identifiers={device_identifier},
            name=adapter_device_name,
            model=model,
            serial_number=serial_number,
            manufacturer=manufacturer,
            via_device=(DOMAIN, self._main_device_id),
        )

        self._devices[device_key] = device_info

    def get_device(self, device_key: str) -> DeviceInfo | None:
        result = self._devices.get(device_key)

        return result

    def get_device_data(self, device_key: str):
        data = self._api.data.get(device_key, {})

        return data

    def get_device_value(self, device_key: str, key: str | None):
        data = self.get_device_data(device_key)

        if key and data is not None:
            return data.get(key)

        return data

    async def get_debug_data(self) -> dict:
        await self._api.update_full()

        data = {
            "rawData": self._api.raw_data,
            "devicesData": self._api.data,
            "devicesConfig": self._api.data_config,
        }

        return data

    async def _async_update_data(self):
        try:
            await self._api.update()

            return self._api.data

        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}")

    async def _on_device_discovered(
        self, entry_id: str, device_key: str, device_data: dict, device_config: dict
    ):
        if entry_id != self.config_entry.entry_id:
            return

        handlers = [
            device_prefix
            for device_prefix in self._device_handlers
            if device_key.startswith(device_prefix)
        ]

        if handlers:
            handler_key = handlers[0]
            handler = self._device_handlers[handler_key]

            handler(device_key, device_data, device_config)

        else:
            self.create_sub_unit_device(device_key, device_data, device_config)

        async_dispatcher_send(
            self.hass,
            SIGNAL_HA_DEVICE_CREATED,
            self.entry_id,
            device_key,
            device_data,
            device_config,
        )

        self.hass.create_task(self.async_request_refresh())
