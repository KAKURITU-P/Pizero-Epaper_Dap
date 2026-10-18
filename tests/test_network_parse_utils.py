import unittest

from network_parse_utils import (
    is_probably_invalid_ssid,
    parse_bluetoothctl_devices,
    parse_nmcli_connection_show,
    parse_wpa_cli_list_networks,
    resolve_bt_display_name,
)


class ParseUtilsTest(unittest.TestCase):
    def test_parse_bluetoothctl_devices_filters_invalid_mac(self):
        output = """Device AA:BB:CC:DD:EE:FF Headset\nDevice INVALID Foo\n[NEW] Device 11:22:33:44:55:66 Speaker\n"""
        self.assertEqual(
            parse_bluetoothctl_devices(output),
            [("AA:BB:CC:DD:EE:FF", "Headset")],
        )

    def test_resolve_bt_display_name_fallback(self):
        self.assertEqual(
            resolve_bt_display_name("AA:BB:CC:DD:EE:FF", alias=" not available ", name="", listed_name="  My BT  "),
            "My BT",
        )
        self.assertEqual(
            resolve_bt_display_name("AA:BB:CC:DD:EE:FF", alias=" ", name="unknown", listed_name=""),
            "AA:BB:CC:DD:EE:FF",
        )

    def test_parse_nmcli_connection_show_with_escaped_colon(self):
        output = "Cafe\\:2G:802-11-wireless\nEthernet 1:802-3-ethernet\n"
        self.assertEqual(
            parse_nmcli_connection_show(output),
            [("Cafe:2G", "802-11-wireless"), ("Ethernet 1", "802-3-ethernet")],
        )

    def test_parse_wpa_cli_list_networks(self):
        output = """Selected interface 'wlan0'\nnetwork id / ssid / bssid / flags\n0\tHome WiFi\tany\t[CURRENT]\n1\tCafe:2G\t00:11:22:33:44:55\t\n"""
        parsed = parse_wpa_cli_list_networks(output)
        self.assertEqual(parsed[0]["id"], "0")
        self.assertEqual(parsed[0]["ssid"], "Home WiFi")
        self.assertEqual(parsed[1]["ssid"], "Cafe:2G")

    def test_invalid_ssid_filter(self):
        self.assertTrue(is_probably_invalid_ssid("wlan0"))
        self.assertTrue(is_probably_invalid_ssid("network id"))
        self.assertFalse(is_probably_invalid_ssid("My Home WiFi"))


if __name__ == "__main__":
    unittest.main()
