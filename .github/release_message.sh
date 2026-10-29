#!/usr/bin/env bash
# 发布说明统一由 .github/release_notes/<tag>.md（curated notes）提供，见 workflow 的
# "Use curated notes when present" 步骤。不再对官方 PyPI 取区间——本 fork 不发 PyPI、
# 版本号也对不上官方，gitchangelog 必然失败并打印 "Error in change log"。
current=$(cat hiddifypanel/VERSION)
: "$current"