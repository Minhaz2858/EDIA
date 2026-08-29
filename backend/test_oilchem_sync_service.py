import unittest

from backend.src.service.oilchem_sync_service import (
    extract_latest_naphtha_article_url,
    parse_brent_public_page,
    parse_japan_naphtha_article,
    parse_regional_market_price,
)


class OilchemSyncParserTests(unittest.TestCase):
    def test_parse_brent_public_page_reads_market_date_and_price(self):
        html = """
        <div>国际原油价格 Market Price 2026-04-01 9:00</div>
        <div>布伦特期货 101.16</div>
        """

        price_date, price = parse_brent_public_page(html)

        self.assertEqual(price_date, "2026-04-01")
        self.assertEqual(price, 101.16)

    def test_parse_regional_market_price_reads_east_china_value(self):
        html = """
        <div>裂解C5市场价格 Market Price 2026-04-01 14:00</div>
        <div>华中 7350 0|0.00%</div>
        <div>华南 7248 0|0.00%</div>
        <div>华东 7300 0|0.00%</div>
        <div>广告招商</div>
        """

        price_date, price = parse_regional_market_price(html, "裂解C5市场价格", "华东")

        self.assertEqual(price_date, "2026-04-01")
        self.assertEqual(price, 7300.0)

    def test_extract_latest_naphtha_article_url_prefers_first_matching_link(self):
        html = """
        <a href="https://www.oilchem.net/26-0402-08-db5f8fa1587a686c.html">[石脑油]：4月1日国际石脑油价格快讯</a>
        <a href="https://www.oilchem.net/26-0401-08-bbc920a380ba5404.html">[石脑油]：3月31日国际石脑油价格快讯</a>
        """

        url = extract_latest_naphtha_article_url(html)

        self.assertEqual(url, "https://www.oilchem.net/26-0402-08-db5f8fa1587a686c.html")

    def test_parse_japan_naphtha_article_reads_article_date_and_price(self):
        html = """
        <div>发布时间：2026-04-02 08:50</div>
        <h1>[石脑油]：4月1日国际石脑油价格快讯</h1>
        <table>
          <tr><td>日本石脑油</td><td>702.50</td></tr>
        </table>
        """

        price_date, price = parse_japan_naphtha_article(html)

        self.assertEqual(price_date, "2026-04-01")
        self.assertEqual(price, 702.5)


if __name__ == "__main__":
    unittest.main()