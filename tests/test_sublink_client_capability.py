"""客户端上下文的「哪些域名腿可以下发给客户端」单元测试。

合并官方 14.5.0b2 后，`ClientContextVar.iter_ctx_domains()` 仍然只做两类过滤：
`_client_domain_allowed()`（sub-link-only / REALITY 域名校验 / REALITY 下载腿）与
`expand_client_domains()`（IP 模式去重、SNI 展开）。hiddifypanel PR#68 提出的
「客户端能力过滤」（CDN 下 xhttp 明文或 TLS H1 腿、无 TLS 的 gRPC 应被剔除）
并没有合进 upstream/dev，因此本文件：

* 对**现有**两个过滤函数做真实断言（纯函数，不需要数据库/app context）；
* 用 `HIDDIFY_EXPECT_PR68=1` 提供一个门禁：cherry-pick PR#68 之后必须出现能力过滤函数，
  否则测试变红 —— 它是这次「合并不能修好 xhttp H1」结论的可执行证据。

反向验证（防假通过）：把 `_client_domain_allowed` 的第一行
``if domain.is_sub_link_only(): return False`` 改成 ``pass``，
本文件至少 3 个用例应变红；改回即全绿。见 ``docs/dev/testing.md`` §8。
"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from hiddifypanel.models.custom_proxy import CustomProxyMode
from hiddifypanel.proxy_v3.context_vars import ctx_client


def fake_domain(name: str = "a.example", *, sub_link_only: bool = False, reality: bool = False, download=None):
    """只提供被测函数真正读到的那几个成员，避免拖入 pydantic/DB。"""
    return SimpleNamespace(
        name=name,
        download=download,
        is_sub_link_only=lambda: sub_link_only,
        is_reality=lambda: reality,
    )


def fake_proxy(mode=CustomProxyMode.no_inbound, domains=None):
    return SimpleNamespace(mode=mode, domains=domains or [], with_domain=lambda d: ("bound", d))


class ClientDomainAllowedTests(unittest.TestCase):
    def test_sub_link_only_domain_is_dropped(self):
        self.assertFalse(ctx_client._client_domain_allowed(fake_domain(sub_link_only=True), fake_proxy()))

    def test_plain_domain_is_kept(self):
        self.assertTrue(ctx_client._client_domain_allowed(fake_domain(), fake_proxy()))

    def test_invalid_reality_domain_is_dropped(self):
        with mock.patch.object(ctx_client, "is_reality_domain_valid", return_value=False):
            self.assertFalse(ctx_client._client_domain_allowed(fake_domain(reality=True), fake_proxy()))

    def test_valid_reality_domain_is_kept(self):
        with mock.patch.object(ctx_client, "is_reality_domain_valid", return_value=True):
            self.assertTrue(ctx_client._client_domain_allowed(fake_domain(reality=True), fake_proxy()))

    def test_invalid_reality_download_leg_is_dropped(self):
        download = SimpleNamespace(is_reality=lambda: True)
        with (
            mock.patch.object(ctx_client, "is_download_upload_different", return_value=True),
            mock.patch.object(ctx_client, "is_reality_download_leg_valid", return_value=False),
        ):
            self.assertFalse(ctx_client._client_domain_allowed(fake_domain(download=download), fake_proxy()))

    def test_same_upload_download_leg_is_not_validated_twice(self):
        # 上传/下载是同一条腿时，下载腿不该被单独判掉（14.5 修过的回归）
        download = SimpleNamespace(is_reality=lambda: True)
        with (
            mock.patch.object(ctx_client, "is_download_upload_different", return_value=False),
            mock.patch.object(ctx_client, "is_reality_download_leg_valid", return_value=False) as check,
        ):
            self.assertTrue(ctx_client._client_domain_allowed(fake_domain(download=download), fake_proxy()))
            check.assert_not_called()


class ExpandClientDomainsTests(unittest.TestCase):
    def test_no_inbound_drops_sub_link_only_and_does_not_expand(self):
        domains = [fake_domain("a"), fake_domain("b", sub_link_only=True), fake_domain("c")]
        with mock.patch.object(ctx_client, "expand_sni_domain_servers") as expand:
            got = ctx_client.expand_client_domains(fake_proxy(mode=CustomProxyMode.no_inbound), domains)
        self.assertEqual(["a", "c"], [d.name for d in got])
        expand.assert_not_called()

    def test_default_mode_expands_each_surviving_domain(self):
        domains = [fake_domain("a"), fake_domain("b", sub_link_only=True)]
        with mock.patch.object(ctx_client, "expand_sni_domain_servers", side_effect=lambda d: [d, d]):
            got = ctx_client.expand_client_domains(fake_proxy(mode=CustomProxyMode.domains_l7_gateway), domains)
        self.assertEqual(["a", "a"], [d.name for d in got])

    def test_ip_mode_uses_deduplication(self):
        domains = [fake_domain("a"), fake_domain("b")]
        with (
            mock.patch.object(ctx_client, "_unique_ip_mode_domains", return_value=[domains[0]]) as dedup,
            mock.patch.object(ctx_client, "expand_sni_domain_servers") as expand,
        ):
            got = ctx_client.expand_client_domains(fake_proxy(mode=CustomProxyMode.ip), domains)
        self.assertEqual(["a"], [d.name for d in got])
        dedup.assert_called_once()
        expand.assert_not_called()


class IterCtxDomainsTests(unittest.TestCase):
    def test_filter_runs_before_expansion(self):
        good, bad, good2 = fake_domain("good"), fake_domain("bad"), fake_domain("good2")
        proxy = fake_proxy(domains=[good, bad, good2])
        ctx = ctx_client.ClientContextVar.model_construct(user=None, hconfig=None, platform=None, proxy=proxy)
        with (
            mock.patch.object(ctx_client, "_client_domain_allowed", side_effect=lambda d, p: d.name != "bad") as allowed,
            mock.patch.object(ctx_client, "expand_client_domains", side_effect=lambda p, ds: ds) as expand,
            mock.patch.object(ctx_client, "ClientContextDomainVar", side_effect=lambda **kw: kw["proxy"]),
        ):
            got = list(ctx.iter_ctx_domains())
        self.assertEqual([("bound", good), ("bound", good2)], got)
        self.assertEqual(3, allowed.call_count)
        self.assertEqual([good, good2], expand.call_args.args[1])


class Pr68CapabilityGapTests(unittest.TestCase):
    def test_capability_filter_presence_after_cherry_pick(self):
        if os.environ.get("HIDDIFY_EXPECT_PR68") != "1":
            self.skipTest(
                "PR#68 未合入 upstream/dev：CDN 下的 xhttp 明文/TLS H1 腿仍会下发。"
                "cherry-pick 后用 HIDDIFY_EXPECT_PR68=1 跑本用例"
            )
        names = ("_client_can_use", "client_can_use", "is_client_capable")
        self.assertTrue(
            any(hasattr(ctx_client, name) for name in names),
            "PR#68 已合入但 ctx_client 里找不到客户端能力过滤函数",
        )
