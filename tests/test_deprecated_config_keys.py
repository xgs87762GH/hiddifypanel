"""Retired settings stay readable for old rows/backups but are not active config keys."""

import unittest

from hiddifypanel.models.config_enum import (
    DEPRECATED_CONFIG_KEYS,
    NON_DB_CONFIG_KEYS,
    ConfigEnum,
    config_enum_members,
)


class DeprecatedConfigKeysTests(unittest.TestCase):
    def test_retired_keys_are_not_active_members(self):
        names = {member.name for member in config_enum_members()}
        self.assertFalse(names & DEPRECATED_CONFIG_KEYS)
        self.assertFalse(names & NON_DB_CONFIG_KEYS)

    def test_current_settings_remain_active(self):
        names = {member.name for member in config_enum_members()}
        self.assertIn("telegram_adtag", names)
        self.assertIn("ssr_fakedomain", names)

    def test_retired_keys_still_resolve_for_old_rows(self):
        for name in DEPRECATED_CONFIG_KEYS:
            self.assertIsInstance(ConfigEnum[name], ConfigEnum)

    def test_not_found_is_a_sentinel_not_a_setting(self):
        self.assertIs(ConfigEnum["does-not-exist"], ConfigEnum.not_found)
        self.assertNotIn("not_found", {member.name for member in config_enum_members()})


if __name__ == "__main__":
    unittest.main()
