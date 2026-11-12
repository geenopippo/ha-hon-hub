"""Configuration: the email and password of the hOn account."""
from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import CONF_MOBILE_ID, DOMAIN
from .hon import HonAuthError, HonClient, HonConnectionError, HonError

EMAIL = TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username"))
PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password"))


class HonHubConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _check(self, email: str, password: str) -> str | None:
        """Try the login. Returns the error key, None if everything is fine."""
        client = HonClient(email, password, async_create_clientsession(self.hass))
        try:
            appliances = await client.appliances()
        except HonAuthError:
            return "invalid_auth"
        except HonConnectionError:
            return "cannot_connect"
        except HonError:
            return "unknown"
        return None if appliances else "no_appliances"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email, password = user_input[CONF_EMAIL].strip(), user_input[CONF_PASSWORD]
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()
            if (error := await self._check(email, password)) is None:
                return self.async_create_entry(title=email, data={
                    CONF_EMAIL: email, CONF_PASSWORD: password, CONF_MOBILE_ID: secrets.token_hex(8)})
            errors["base"] = error
        schema = vol.Schema({vol.Required(CONF_EMAIL): EMAIL, vol.Required(CONF_PASSWORD): PASSWORD})
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(schema, user_input),
                                    errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            email, password = user_input[CONF_EMAIL].strip(), user_input[CONF_PASSWORD]
            if (error := await self._check(email, password)) is None:
                return self.async_update_reload_and_abort(entry, unique_id=email.lower(), title=email, data={
                    CONF_EMAIL: email, CONF_PASSWORD: password,
                    CONF_MOBILE_ID: entry.data.get(CONF_MOBILE_ID) or secrets.token_hex(8)})
            errors["base"] = error
        schema = vol.Schema({vol.Required(CONF_EMAIL): EMAIL, vol.Required(CONF_PASSWORD): PASSWORD})
        suggested = user_input or {CONF_EMAIL: entry.data.get(CONF_EMAIL, "")}
        return self.async_show_form(step_id="reauth_confirm", errors=errors,
                                    data_schema=self.add_suggested_values_to_schema(schema, suggested))
