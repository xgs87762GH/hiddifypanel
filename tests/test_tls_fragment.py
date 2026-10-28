"""Per-domain TLS fragment via FinalMask (#5647)."""

from __future__ import annotations

import unittest

from hiddifypanel.proxy_v3 import tls_fragment as tf


class TlsFragmentResolveTest(unittest.TestCase):
    def test_direct_never_enables(self):
        r = tf.resolve(domain_mode="direct", extra={"tls_fragment": {"mode": "on"}})
        self.assertFalse(r["enable"])
        self.assertIsNone(tf.to_finalmask(r))

    def test_empty_extra_does_not_enable(self):
        r = tf.resolve(domain_mode="cdn", extra={})
        self.assertFalse(r["enable"])
        self.assertIsNone(tf.to_finalmask(r))

    def test_set_tls_fragment_writes_finalmask_tcp(self):
        out = tf.set_tls_fragment(
            {"other": 1},
            {
                "mode": "on",
                "packets": "tlshello",
                "lengths": ["3-5", "6-8", "10-20"],
                "delays": ["10-20"],
                "maxSplit": "3-6",
            },
        )
        self.assertEqual(out["tricks"]["template"], tf.TEMPLATE_TLS_FRAGMENT)
        fm = out["tricks"]["finalmask"]
        self.assertEqual(fm["tcp"][0]["type"], "fragment")
        self.assertEqual(fm["tcp"][0]["settings"]["lengths"], ["3-5", "6-8", "10-20"])
        self.assertEqual(fm["tcp"][0]["settings"]["delays"], ["10-20"])
        self.assertEqual(fm["tcp"][0]["settings"]["maxSplit"], "3-6")

        r = tf.resolve(domain_mode="cdn", extra=out)
        self.assertTrue(r["enable"])
        built = tf.to_finalmask(r)
        self.assertEqual(built["tcp"][0]["type"], "fragment")
        self.assertEqual(built["tcp"][0]["settings"]["lengths"], ["3-5", "6-8", "10-20"])

    def test_set_off_clears_fragment_finalmask(self):
        start = tf.set_tls_fragment(
            {},
            {"mode": "on", "lengths": ["10-100"], "delays": ["50-200"], "packets": "tlshello"},
        )
        out = tf.set_tls_fragment(start, {"mode": "off"})
        self.assertNotIn("finalmask", out.get("tricks", {}))
        self.assertNotEqual(out.get("tricks", {}).get("template"), tf.TEMPLATE_TLS_FRAGMENT)

    def test_worker_mode_eligible(self):
        out = tf.set_tls_fragment(
            {},
            {"mode": "on", "lengths": ["3-5", "6-8"], "delays": ["10-20"], "packets": "tlshello", "maxSplit": "3-6"},
        )
        r = tf.resolve(domain_mode="worker", extra=out)
        self.assertTrue(r["enable"])
        fm = tf.to_finalmask(r)
        self.assertEqual(fm["tcp"][0]["settings"]["lengths"], ["3-5", "6-8"])
        self.assertEqual(fm["tcp"][0]["settings"]["delays"], ["10-20"])
        self.assertEqual(fm["tcp"][0]["settings"]["maxSplit"], "3-6")


class LegacySingboxTlsFragmentTest(unittest.TestCase):
    def test_add_tls_tricks_uses_tls_fragment_bool_not_object(self):
        from hiddifypanel.hutils.proxy import singbox

        base = {"tls": {"enabled": True, "server_name": "s1.example"}}
        singbox.add_tls_tricks(
            base,
            {
                "tls_fragment_enable": True,
                "tls_fragment_size": "10-100",
                "tls_fragment_sleep": "50-200",
            },
        )
        self.assertNotIn("tls_fragment", base)
        self.assertTrue(base["tls"]["fragment"])


class TemplateSmokeTest(unittest.TestCase):
    def test_hiddify_core_tls_http_has_no_illegal_tls_fragment_block(self):
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[1]
            / "hiddifypanel"
            / "proxy_v3"
            / "proxy_templates"
            / "hiddify-core"
            / "client"
            / "tls"
            / "tls_http.j2"
        )
        text = path.read_text(encoding="utf-8")
        self.assertNotIn('"tls_fragment"', text)
        self.assertIn("finalmask", text.lower())

    def test_haproxy_in_httpmode_uses_client_clear_not_ssl_fc_redirect(self):
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[1]
            / "hiddifypanel"
            / "proxy_v3"
            / "proxy_templates"
            / "haproxy"
            / "server"
            / "fronts"
            / "in_httpmode.j2"
        )
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("if !{ ssl_fc }", text)
        self.assertIn("client_clear", text)
        self.assertIn("to_httpmode_from_tls", text)
        self.assertIn("hop_from_tls", text)


if __name__ == "__main__":
    unittest.main()
