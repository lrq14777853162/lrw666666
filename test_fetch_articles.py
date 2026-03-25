#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for fetch_articles.py — uses mocked HTTP responses."""

import json
import math
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import fetch_articles


def _make_response(articles: list, total: int) -> MagicMock:
    """Build a mock requests.Response whose .json() returns a standard payload."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"total": total, "list": articles}
    return mock_resp


class TestFetchPage(unittest.TestCase):
    """fetch_page() should return the parsed JSON on success."""

    def test_returns_parsed_json(self):
        payload = {"total": 5, "list": [{"title": "A"}]}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = payload

        with patch("requests.Session.get", return_value=mock_resp):
            session = __import__("requests").Session()
            result = fetch_articles.fetch_page(session, page_no=1, page_size=10)

        self.assertEqual(result, payload)

    def test_retries_on_failure_then_succeeds(self):
        import requests as req

        good_resp = MagicMock()
        good_resp.raise_for_status = MagicMock()
        good_resp.json.return_value = {"total": 1, "list": [{"title": "X"}]}

        side_effects = [req.exceptions.ConnectionError("fail"), good_resp]

        with patch("requests.Session.get", side_effect=side_effects):
            with patch("time.sleep"):  # speed up retries
                session = req.Session()
                result = fetch_articles.fetch_page(session, page_no=1, page_size=10)

        self.assertEqual(result["total"], 1)

    def test_returns_empty_dict_after_all_retries_fail(self):
        import requests as req

        with patch(
            "requests.Session.get",
            side_effect=req.exceptions.ConnectionError("fail"),
        ):
            with patch("time.sleep"):
                session = req.Session()
                result = fetch_articles.fetch_page(session, page_no=1, page_size=10)

        self.assertEqual(result, {})


class TestFetchAll(unittest.TestCase):
    """fetch_all() should paginate and collect every article."""

    def _mock_get(self, page_size: int, total: int, articles_factory=None):
        """
        Return a side_effect callable that simulates server-side pagination.
        `articles_factory(page_no)` produces the article list for a given page.
        """
        if articles_factory is None:
            def articles_factory(page_no):
                start = (page_no - 1) * page_size
                end = min(start + page_size, total)
                return [{"title": f"Article {i + 1}"} for i in range(start, end)]

        def side_effect(url, params, headers, timeout):
            page_no = int(params.get("pageNo", 1))
            articles = articles_factory(page_no)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"total": total, "list": articles}
            return resp

        return side_effect

    def test_single_page(self):
        total = 5
        page_size = 10
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            output_path = tmp.name
        try:
            with patch("requests.Session.get", side_effect=self._mock_get(page_size, total)):
                with patch("time.sleep"):
                    result = fetch_articles.fetch_all(
                        page_size=page_size, delay=0, output_file=output_path
                    )
            self.assertEqual(len(result), total)
        finally:
            os.unlink(output_path)

    def test_multiple_pages(self):
        total = 25
        page_size = 10
        expected_pages = math.ceil(total / page_size)  # 3

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            output_path = tmp.name
        try:
            with patch("requests.Session.get", side_effect=self._mock_get(page_size, total)):
                with patch("time.sleep"):
                    result = fetch_articles.fetch_all(
                        page_size=page_size, delay=0, output_file=output_path
                    )

            self.assertEqual(len(result), total)
            # Verify titles are correct (Articles 1–25)
            titles = [a["title"] for a in result]
            self.assertEqual(titles[0], "Article 1")
            self.assertEqual(titles[-1], f"Article {total}")
        finally:
            os.unlink(output_path)

    def test_exact_page_boundary(self):
        """total is an exact multiple of page_size."""
        total = 20
        page_size = 10
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            output_path = tmp.name
        try:
            with patch("requests.Session.get", side_effect=self._mock_get(page_size, total)):
                with patch("time.sleep"):
                    result = fetch_articles.fetch_all(
                        page_size=page_size, delay=0, output_file=output_path
                    )
            self.assertEqual(len(result), total)
        finally:
            os.unlink(output_path)

    def test_output_file_written(self):
        total = 3
        page_size = 10
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            output_path = tmp.name
        try:
            with patch("requests.Session.get", side_effect=self._mock_get(page_size, total)):
                with patch("time.sleep"):
                    fetch_articles.fetch_all(
                        page_size=page_size, delay=0, output_file=output_path
                    )

            self.assertTrue(os.path.exists(output_path))
            with open(output_path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(len(data), total)
        finally:
            os.unlink(output_path)

    def test_skips_failed_page_and_continues(self):
        """A failed intermediate page should be skipped; others collected."""
        import requests as req

        total = 30
        page_size = 10
        # page 2 raises an error; pages 1 and 3 succeed
        call_count = [0]

        def side_effect(url, params, headers, timeout):
            call_count[0] += 1
            page_no = int(params.get("pageNo", 1))
            if page_no == 2:
                raise req.exceptions.ConnectionError("boom")
            start = (page_no - 1) * page_size
            end = min(start + page_size, total)
            articles = [{"title": f"Article {i + 1}"} for i in range(start, end)]
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"total": total, "list": articles}
            return resp

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            output_path = tmp.name
        try:
            with patch("requests.Session.get", side_effect=side_effect):
                with patch("time.sleep"):
                    result = fetch_articles.fetch_all(
                        page_size=page_size, delay=0, output_file=output_path
                    )

            # Pages 1 and 3 have 10 articles each; page 2 skipped → 20 articles
            self.assertEqual(len(result), 20)
        finally:
            os.unlink(output_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
