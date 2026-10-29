import unittest
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from jinja2.runtime import Namespace

from hiddifypanel.proxy_v3.context_vars.version import TemplateVersion


class TemplateLoader(FileSystemLoader):
    def get_source(self, environment, template):
        return super().get_source(environment, template if template.endswith(".j2") else template + ".j2")


class SkipProxy(Exception):
    pass


def skip_proxy(reason=""):
    raise SkipProxy(str(reason))


class SublinkUriAllowInsecureTests(unittest.TestCase):
    """Xray >= 26.1.1 fails the whole config on `allowInsecure` (hiddify/Hiddify-Manager#5438)."""

    PIN = "a" * 64
    TEMPLATE = "sublink/uri/security/tls_http"

    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "hiddifypanel/proxy_v3/proxy_templates"
        self.env = Environment(loader=TemplateLoader(root), undefined=StrictUndefined)
        self.env.globals["skip"] = skip_proxy
        self.domain = SimpleNamespace(
            sni="server.example",
            is_reality=lambda: False,
            allow_insecure=True,
            extra=SimpleNamespace(ech_config=None),
            ech=False,
            echinfo="",
            mode=SimpleNamespace(is_cdn=lambda: False),
            cert=SimpleNamespace(pinnedPeerCertSha256=[self.PIN]),
        )
        self.ctx = SimpleNamespace(
            # transport is read by tls_http.j2 for the xhttp+CDN uTLS hello; tests use plain TLS.
            proxy=SimpleNamespace(
                domain=self.domain,
                transport="",
                tls_layer=SimpleNamespace(alpns=lambda: ["h2", "http/1.1"]),
            ),
            hconfig=SimpleNamespace(utls="none", tls_fragment_enable=False),
            platform=SimpleNamespace(xray=SimpleNamespace(version=TemplateVersion(""))),
        )

    def render(self, xray_version):
        self.ctx.platform.xray.version = TemplateVersion(xray_version)
        qp = Namespace()
        self.env.get_template(self.TEMPLATE).render(
            ctx=self.ctx, domain=self.domain, qp=qp, tls_layer=self.ctx.proxy.tls_layer
        )
        return qp

    def test_legacy_xray_keeps_allow_insecure(self):
        for version in ("1.8.6", "25.12.31", "26.1.0"):
            with self.subTest(version=version):
                qp = self.render(version)
                self.assertEqual(qp.allowInsecure, "true")
                self.assertEqual(qp.insecure, "true")
                self.assertFalse(hasattr(qp, "pcs"))

    def test_modern_xray_uses_pinned_peer_cert(self):
        for version in ("26.1.1", "26.3.27"):
            with self.subTest(version=version):
                qp = self.render(version)
                self.assertEqual(qp.pcs, self.PIN)
                self.assertFalse(hasattr(qp, "allowInsecure"))
                self.assertFalse(hasattr(qp, "insecure"))

    def test_unknown_core_version_is_treated_as_modern(self):
        qp = self.render("")
        self.assertEqual(qp.pcs, self.PIN)
        self.assertFalse(hasattr(qp, "allowInsecure"))

    def test_insecure_domain_without_a_pin_is_skipped(self):
        self.domain.cert.pinnedPeerCertSha256 = []
        with self.assertRaisesRegex(SkipProxy, "without a pinned peer certificate"):
            self.render("26.3.27")

    def test_legacy_core_still_works_without_a_pin(self):
        self.domain.cert.pinnedPeerCertSha256 = []
        self.assertEqual(self.render("25.12.31").allowInsecure, "true")

    def test_trusted_domain_emits_no_insecurity(self):
        self.domain.allow_insecure = False
        for version in ("1.8.6", "26.3.27"):
            with self.subTest(version=version):
                qp = self.render(version)
                self.assertFalse(hasattr(qp, "allowInsecure"))
                self.assertFalse(hasattr(qp, "pcs"))


if __name__ == "__main__":
    unittest.main()
