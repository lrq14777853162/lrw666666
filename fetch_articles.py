#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
贵港社保局文章检索脚本
通过关键词"社保缴纳基数"翻页拉取所有文章内容
API: http://rsj.gxgg.gov.cn/irs-common-search/search
"""

import json
import math
import time
import argparse
import sys
import requests


# ── API 基础参数 ──────────────────────────────────────────────────────────────
BASE_URL = "http://rsj.gxgg.gov.cn/irs-common-search/search"

DEFAULT_PARAMS = {
    "code": "188466ca6d5",
    "configCode": "",
    "sign": "9cc99c9d-94aa-44b4-aa79-41227a5385d7",
    "searchWord": "社保 缴纳基数",
    "orderBy": "related",
    "searchBy": "all",
    "appendixType": "",
    "granularity": "ALL",
    "isSearchForced": "0",
    "pageSize": "10",
    "isAdvancedSearch": "",
    "isDefaultAdvanced": "",
    "advancedFilters": "",
    "dataTypeId": "14881",
}

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "http://rsj.gxgg.gov.cn/",
}


def fetch_page(session: requests.Session, page_no: int, page_size: int) -> dict:
    """获取指定页的搜索结果，失败时自动重试最多 3 次。"""
    params = {**DEFAULT_PARAMS, "pageNo": str(page_no), "pageSize": str(page_size)}
    for attempt in range(1, 4):
        try:
            resp = session.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.JSONDecodeError as exc:
            print(f"  [警告] 第 {page_no} 页 JSON 解析失败（尝试 {attempt}/3）: {exc}",
                  file=sys.stderr)
        except requests.exceptions.RequestException as exc:
            print(f"  [警告] 第 {page_no} 页请求失败（尝试 {attempt}/3）: {exc}",
                  file=sys.stderr)
        if attempt < 3:
            time.sleep(2 * attempt)
    return {}


def fetch_all(page_size: int = 10, delay: float = 1.0,
              output_file: str = "articles.json") -> list:
    """
    翻页拉取全部文章。

    Parameters
    ----------
    page_size  : 每页条数，默认 10
    delay      : 翻页间隔（秒），默认 1.0
    output_file: 结果保存路径，默认 articles.json

    Returns
    -------
    所有文章列表
    """
    all_articles: list = []
    session = requests.Session()

    print(f"[*] 开始检索，关键词：{DEFAULT_PARAMS['searchWord']}")
    print(f"[*] API: {BASE_URL}")

    # ── 第 1 页：探测总数 ────────────────────────────────────────────────────
    print("[*] 正在获取第 1 页…")
    data = fetch_page(session, 1, page_size)
    if not data:
        print("[错误] 无法获取第 1 页数据，程序退出。", file=sys.stderr)
        return []

    # 尝试从响应中提取总条数和文章列表（兼容多种字段名）
    total = (
        data.get("total")
        or data.get("totalCount")
        or data.get("data", {}).get("total")
        or data.get("data", {}).get("totalCount")
        or 0
    )
    articles_page = (
        data.get("list")
        or data.get("data", {}).get("list")
        or data.get("rows")
        or data.get("data", {}).get("rows")
        or []
    )

    if not articles_page:
        print("[提示] 第 1 页未返回文章列表，完整响应：")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return []

    total = int(total)
    all_articles.extend(articles_page)
    print(f"[*] 总条数：{total}，每页：{page_size}")
    print(f"[*] 第 1 页获取 {len(articles_page)} 条")

    # ── 后续页 ───────────────────────────────────────────────────────────────
    total_pages = math.ceil(total / page_size) if total > 0 else 1

    for page_no in range(2, total_pages + 1):
        print(f"[*] 正在获取第 {page_no}/{total_pages} 页…")
        time.sleep(delay)
        data = fetch_page(session, page_no, page_size)
        if not data:
            print(f"  [警告] 第 {page_no} 页获取失败，跳过。", file=sys.stderr)
            continue

        articles_page = (
            data.get("list")
            or data.get("data", {}).get("list")
            or data.get("rows")
            or data.get("data", {}).get("rows")
            or []
        )
        if not articles_page:
            print(f"  [提示] 第 {page_no} 页返回空列表，停止翻页。")
            break
        all_articles.extend(articles_page)
        print(f"  获取 {len(articles_page)} 条，累计 {len(all_articles)} 条")

    # ── 保存结果 ─────────────────────────────────────────────────────────────
    print(f"\n[*] 全部完成，共获取 {len(all_articles)} 条文章。")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_articles, f, ensure_ascii=False, indent=2)
    print(f"[*] 结果已保存至：{output_file}")

    return all_articles


def main():
    parser = argparse.ArgumentParser(
        description="贵港社保局文章检索——翻页拉取所有内容"
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=10,
        help="每页条数（默认 10，最大建议 50）",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="翻页间隔秒数（默认 1.0）",
    )
    parser.add_argument(
        "--output",
        default="articles.json",
        help="结果保存文件名（默认 articles.json）",
    )
    args = parser.parse_args()

    articles = fetch_all(
        page_size=args.page_size,
        delay=args.delay,
        output_file=args.output,
    )
    if articles:
        print(f"\n前 3 条标题预览：")
        for i, art in enumerate(articles[:3], 1):
            title = (
                art.get("title")
                or art.get("name")
                or art.get("articleTitle")
                or str(art)[:80]
            )
            print(f"  {i}. {title}")


if __name__ == "__main__":
    main()
