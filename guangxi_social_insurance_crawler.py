#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
广西各市2025年社保缴纳基数和比例爬虫
数据来源：广西壮族自治区人力资源和社会保障厅及各市人社局官网
官方文件：桂人社发〔2025〕42号
"""

import csv
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

# 尝试导入 pandas 和 openpyxl（可选）
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

# ─────────────────────── 日志配置 ───────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────── 数据结构 ───────────────────────
@dataclass
class InsuranceRate:
    """单险种缴费比例"""
    company_rate: float   # 单位缴费比例（%）
    personal_rate: float  # 个人缴费比例（%）
    note: str = ""        # 备注


@dataclass
class CityInsuranceData:
    """一个城市的社保缴纳基数与比例"""
    city: str                        # 城市名称
    year: int = 2025                 # 年度
    reference_base: float = 0.0      # 参考工资基数（元/月），即全口径城镇单位就业人员月均工资
    base_upper_limit: float = 0.0    # 缴费基数上限（元/月）
    base_lower_limit: float = 0.0    # 缴费基数下限（元/月）
    pension: Optional[InsuranceRate] = None           # 基本养老保险
    medical: Optional[InsuranceRate] = None           # 基本医疗保险
    unemployment: Optional[InsuranceRate] = None      # 失业保险
    work_injury: Optional[InsuranceRate] = None       # 工伤保险（单位平均费率）
    maternity: Optional[InsuranceRate] = None         # 生育保险
    source_url: str = ""             # 数据来源URL
    remark: str = ""                 # 备注

    def to_dict(self) -> dict:
        return {
            "城市": self.city,
            "年度": self.year,
            "参考工资基数(元/月)": self.reference_base,
            "缴费基数上限(元/月)": self.base_upper_limit,
            "缴费基数下限(元/月)": self.base_lower_limit,
            # 养老
            "养老-单位比例(%)": self.pension.company_rate if self.pension else "",
            "养老-个人比例(%)": self.pension.personal_rate if self.pension else "",
            # 医疗
            "医疗-单位比例(%)": self.medical.company_rate if self.medical else "",
            "医疗-个人比例(%)": self.medical.personal_rate if self.medical else "",
            # 失业
            "失业-单位比例(%)": self.unemployment.company_rate if self.unemployment else "",
            "失业-个人比例(%)": self.unemployment.personal_rate if self.unemployment else "",
            # 工伤（个人不缴）
            "工伤-单位比例(%)": self.work_injury.company_rate if self.work_injury else "",
            "工伤-备注": self.work_injury.note if self.work_injury else "",
            # 生育（个人不缴）
            "生育-单位比例(%)": self.maternity.company_rate if self.maternity else "",
            "生育-备注": self.maternity.note if self.maternity else "",
            "数据来源": self.source_url,
            "备注": self.remark,
        }


# ─────────────────────── 静态兜底数据 ───────────────────────
# 数据来源：桂人社发〔2025〕42号（2025年10月起执行）
# 参考文件：http://rst.gxzf.gov.cn/zwgk/xxgkzcfg/fgfxlm/t25983863.shtml
#
# 广西全区执行统一标准，各设区市无单独差异
# 工伤保险按行业风险类别划分：一类0.2%、二类0.4%、三类0.7%（此处取平均参考值0.4%）

_PROVINCE_SOURCE = "http://rst.gxzf.gov.cn/zwgk/xxgkzcfg/fgfxlm/t25983863.shtml"

CITIES_CONFIG: Dict[str, Dict] = {
    "南宁市":  {"url": "http://rsj.nanning.gov.cn/"},
    "柳州市":  {"url": "http://rsj.liuzhou.gov.cn/zwgk/fdzdgknr/zcwj/shbx/202509/t20250928_3672970.shtml"},
    "桂林市":  {"url": "http://rsj.guilin.gov.cn/"},
    "梧州市":  {"url": "http://rsj.wuzhou.gov.cn/"},
    "北海市":  {"url": "http://rsj.beihai.gov.cn/"},
    "防城港市": {"url": "http://rsj.fcgs.gov.cn/"},
    "钦州市":  {"url": "http://rsj.qinzhou.gov.cn/"},
    "贵港市":  {"url": "http://rsj.gxgg.gov.cn/"},
    "玉林市":  {"url": "http://rsj.yulin.gov.cn/"},
    "百色市":  {"url": "http://rsj.baise.gov.cn/"},
    "贺州市":  {"url": "http://rsj.gxhz.gov.cn/"},
    "河池市":  {"url": "http://rsj.hechi.gov.cn/"},
    "来宾市":  {"url": "http://rsj.laibin.gov.cn/"},
    "崇左市":  {"url": "http://rsj.chongzuo.gov.cn/"},
}

# 广西2025年度统一社保缴费基数与比例（桂人社发〔2025〕42号）
PROVINCE_STANDARD = {
    "reference_base": 6905.0,
    "base_upper_limit": 20715.0,   # 6905 × 300%
    "base_lower_limit": 4143.0,    # 6905 × 60%
    "pension":       InsuranceRate(company_rate=16.0,  personal_rate=8.0),
    "medical":       InsuranceRate(company_rate=8.0,   personal_rate=2.0,
                                   note="含生育保险，部分地区单独列明"),
    "unemployment":  InsuranceRate(company_rate=0.5,   personal_rate=0.5),
    "work_injury":   InsuranceRate(company_rate=0.4,   personal_rate=0.0,
                                   note="行业差别费率：一类0.2%、二类0.4%、三类0.7%，取参考均值"),
    "maternity":     InsuranceRate(company_rate=0.5,   personal_rate=0.0,
                                   note="部分城市已与医疗保险合并征收"),
}


def _build_city_from_province(city: str, city_url: str) -> CityInsuranceData:
    """用省级统一标准填充某城市数据"""
    s = PROVINCE_STANDARD
    return CityInsuranceData(
        city=city,
        year=2025,
        reference_base=s["reference_base"],
        base_upper_limit=s["base_upper_limit"],
        base_lower_limit=s["base_lower_limit"],
        pension=s["pension"],
        medical=s["medical"],
        unemployment=s["unemployment"],
        work_injury=s["work_injury"],
        maternity=s["maternity"],
        source_url=city_url,
        remark="执行广西壮族自治区2025年度统一标准（桂人社发〔2025〕42号）",
    )


# ─────────────────────── 爬虫核心 ───────────────────────
class GuangxiSocialInsuranceCrawler:
    """广西各市2025年社保缴纳基数和比例爬虫"""

    # 搜索关键词（用于在页面内查找缴费基数通知链接）
    _SEARCH_KEYWORDS = ["2025", "缴费基数", "缴费比例", "养老保险", "失业保险"]

    def __init__(self, timeout: int = 10, delay: float = 1.0):
        """
        :param timeout: HTTP 请求超时时间（秒）
        :param delay:   两次请求之间的间隔（秒），避免对目标站点造成压力
        """
        self.timeout = timeout
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })

    # ── 公共入口 ──────────────────────────────────────────
    def crawl_all(self) -> List[CityInsuranceData]:
        """爬取广西全部14个设区市的社保数据，无法爬取时使用省级统一标准兜底"""
        results: List[CityInsuranceData] = []

        # 优先爬取省级人社厅公告，获取统一标准
        province_data = self._crawl_province()

        for city, cfg in CITIES_CONFIG.items():
            logger.info("正在处理：%s", city)
            city_data = self._crawl_city(city, cfg["url"], province_data)
            results.append(city_data)
            time.sleep(self.delay)

        return results

    # ── 省级数据爬取 ──────────────────────────────────────
    def _crawl_province(self) -> Optional[dict]:
        """爬取广西人社厅官网，获取2025年度社保缴费基数与比例公告"""
        urls_to_try = [
            _PROVINCE_SOURCE,
            "http://rst.gxzf.gov.cn/zwgk/xxgkzcfg/fgfxlm/",
        ]
        for url in urls_to_try:
            data = self._try_extract_base_ratio(url)
            if data:
                logger.info("成功从省级官网获取数据：%s", url)
                return data

        logger.warning("省级官网爬取失败，将使用内置静态数据")
        return None

    # ── 城市级数据爬取 ────────────────────────────────────
    def _crawl_city(
        self,
        city: str,
        city_url: str,
        province_data: Optional[dict],
    ) -> CityInsuranceData:
        """爬取单个城市的社保数据"""
        extracted = self._try_extract_base_ratio(city_url)
        if extracted:
            logger.info("  ✔ %s 从城市官网获取数据成功", city)
            return self._build_from_extracted(city, city_url, extracted)

        # 城市官网未能提取到数据 → 用省级标准兜底
        logger.info("  ⚠ %s 未能从官网提取到数据，使用省级统一标准", city)
        if province_data:
            return self._build_from_extracted(city, city_url, province_data)
        return _build_city_from_province(city, city_url)

    # ── 页面解析 ──────────────────────────────────────────
    def _try_extract_base_ratio(self, url: str) -> Optional[dict]:
        """
        访问给定 URL，尝试解析2025年社保缴费基数及各险种比例。
        解析失败则返回 None。
        """
        html = self._fetch(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator="\n")

        # 检查页面是否包含足够的关键字
        if not all(kw in text for kw in ["2025", "缴费"]):
            # 尝试在列表页中找具体公告链接
            detail_url = self._find_notice_link(soup, url)
            if detail_url:
                logger.debug("  → 跟进公告链接：%s", detail_url)
                html = self._fetch(detail_url)
                if html:
                    soup = BeautifulSoup(html, "html.parser")
                    text = soup.get_text(separator="\n")
                else:
                    return None

        return self._parse_base_ratio(text)

    def _find_notice_link(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """在列表页中寻找2025年缴费基数公告的链接"""
        keywords = ["2025", "缴费基数", "缴费比例"]
        for a in soup.find_all("a", href=True):
            link_text = a.get_text(strip=True)
            if any(kw in link_text for kw in keywords):
                href = a["href"]
                if href.startswith("http"):
                    return href
                from urllib.parse import urljoin
                return urljoin(base_url, href)
        return None

    def _parse_base_ratio(self, text: str) -> Optional[dict]:
        """
        从页面纯文本中解析缴费基数和比例。
        使用正则表达式匹配常见格式。
        """
        result: dict = {}

        # ── 缴费基数 ──────────────────────────────────────
        # 匹配 "月均工资XXXX元" / "基数XXXX元" / "6905" 等
        base_patterns = [
            r"月平均工资[为是]?(\d+\.?\d*)\s*元",
            r"缴费基数.*?(\d{4,5}\.?\d*)\s*元",
            r"参考基数[为是]?(\d{4,5}\.?\d*)\s*元",
        ]
        for pat in base_patterns:
            m = re.search(pat, text)
            if m:
                result["reference_base"] = float(m.group(1))
                break

        # 上下限
        upper_patterns = [
            r"缴费基数上限[为是]?(\d{4,6}\.?\d*)\s*元",
            r"(\d{4,6}\.?\d*)\s*元.*?上限",
            r"最高[缴费]?基数[为是]?(\d{4,6}\.?\d*)\s*元",
        ]
        for pat in upper_patterns:
            m = re.search(pat, text)
            if m:
                result["base_upper_limit"] = float(m.group(1))
                break

        lower_patterns = [
            r"缴费基数下限[为是]?(\d{4,5}\.?\d*)\s*元",
            r"最低[缴费]?基数[为是]?(\d{4,5}\.?\d*)\s*元",
            r"(\d{4,5}\.?\d*)\s*元.*?下限",
        ]
        for pat in lower_patterns:
            m = re.search(pat, text)
            if m:
                result["base_lower_limit"] = float(m.group(1))
                break

        # ── 养老保险 ──────────────────────────────────────
        pension_company_pat = [
            r"养老保险.*?单位.*?(\d+\.?\d*)\s*%",
            r"基本养老.*?用人单位.*?(\d+\.?\d*)\s*%",
        ]
        pension_personal_pat = [
            r"养老保险.*?个人.*?(\d+\.?\d*)\s*%",
            r"基本养老.*?职工.*?(\d+\.?\d*)\s*%",
        ]
        pension = self._parse_insurance_rate(
            text, pension_company_pat, pension_personal_pat, PROVINCE_STANDARD["pension"]
        )
        if pension:
            result["pension"] = pension

        # ── 医疗保险 ──────────────────────────────────────
        medical_company_pat = [
            r"医疗保险.*?单位.*?(\d+\.?\d*)\s*%",
            r"基本医疗.*?用人单位.*?(\d+\.?\d*)\s*%",
        ]
        medical_personal_pat = [
            r"医疗保险.*?个人.*?(\d+\.?\d*)\s*%",
            r"基本医疗.*?职工.*?(\d+\.?\d*)\s*%",
        ]
        medical = self._parse_insurance_rate(
            text, medical_company_pat, medical_personal_pat, PROVINCE_STANDARD["medical"]
        )
        if medical:
            result["medical"] = medical

        # ── 失业保险 ──────────────────────────────────────
        unemp_company_pat = [r"失业保险.*?单位.*?(\d+\.?\d*)\s*%"]
        unemp_personal_pat = [r"失业保险.*?个人.*?(\d+\.?\d*)\s*%"]
        unemployment = self._parse_insurance_rate(
            text, unemp_company_pat, unemp_personal_pat, PROVINCE_STANDARD["unemployment"]
        )
        if unemployment:
            result["unemployment"] = unemployment

        # 只要解析到基数或任意一个险种，视为有效
        if result:
            return result
        return None

    @staticmethod
    def _parse_insurance_rate(
        text: str,
        company_patterns: list,
        personal_patterns: list,
        fallback: InsuranceRate,
    ) -> Optional[InsuranceRate]:
        """
        从文本中提取单位和个人缴费比例，任意一方匹配即返回结果，
        未匹配的一方使用 fallback 中的默认值。无任何匹配时返回 None。
        """
        company_str = GuangxiSocialInsuranceCrawler._extract_first_match(text, company_patterns)
        personal_str = GuangxiSocialInsuranceCrawler._extract_first_match(text, personal_patterns)
        if company_str is None and personal_str is None:
            return None
        return InsuranceRate(
            company_rate=float(company_str) if company_str else fallback.company_rate,
            personal_rate=float(personal_str) if personal_str else fallback.personal_rate,
        )

    @staticmethod
    def _extract_first_match(text: str, patterns: list) -> Optional[str]:
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _build_from_extracted(city: str, url: str, extracted: dict) -> CityInsuranceData:
        """将解析结果与省级标准合并，构造 CityInsuranceData"""
        s = PROVINCE_STANDARD
        return CityInsuranceData(
            city=city,
            year=2025,
            reference_base=extracted.get("reference_base", s["reference_base"]),
            base_upper_limit=extracted.get("base_upper_limit", s["base_upper_limit"]),
            base_lower_limit=extracted.get("base_lower_limit", s["base_lower_limit"]),
            pension=extracted.get("pension", s["pension"]),
            medical=extracted.get("medical", s["medical"]),
            unemployment=extracted.get("unemployment", s["unemployment"]),
            work_injury=extracted.get("work_injury", s["work_injury"]),
            maternity=extracted.get("maternity", s["maternity"]),
            source_url=url,
            remark="执行广西壮族自治区2025年度统一标准（桂人社发〔2025〕42号）",
        )

    # ── HTTP 工具 ─────────────────────────────────────────
    def _fetch(self, url: str) -> Optional[str]:
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except requests.RequestException as exc:
            logger.debug("请求失败 [%s]: %s", url, exc)
            return None


# ─────────────────────── 输出工具 ───────────────────────
def save_to_csv(data: List[CityInsuranceData], filepath: str) -> None:
    """将数据保存为 CSV 文件"""
    if not data:
        logger.warning("无数据，跳过 CSV 导出")
        return
    rows = [d.to_dict() for d in data]
    fieldnames = list(rows[0].keys())
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("CSV 已保存至：%s", filepath)


def save_to_excel(data: List[CityInsuranceData], filepath: str) -> None:
    """将数据保存为 Excel 文件（需要 pandas + openpyxl）"""
    if not PANDAS_AVAILABLE:
        logger.warning("未安装 pandas，跳过 Excel 导出")
        return
    if not data:
        logger.warning("无数据，跳过 Excel 导出")
        return
    rows = [d.to_dict() for d in data]
    df = pd.DataFrame(rows)
    df.to_excel(filepath, index=False)
    logger.info("Excel 已保存至：%s", filepath)


def save_to_json(data: List[CityInsuranceData], filepath: str) -> None:
    """将数据保存为 JSON 文件"""
    rows = [d.to_dict() for d in data]
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    logger.info("JSON 已保存至：%s", filepath)


def print_summary(data: List[CityInsuranceData]) -> None:
    """在控制台打印汇总表格"""
    if not data:
        print("暂无数据")
        return

    header = (
        f"{'城市':<8} {'基数下限':>10} {'基数上限':>10} "
        f"{'养老(单/个)':>12} {'医疗(单/个)':>12} "
        f"{'失业(单/个)':>12} {'工伤(单)':>10} {'生育(单)':>10}"
    )
    sep = "─" * len(header)
    print(f"\n广西各市2025年社保缴费基数与比例汇总")
    print(sep)
    print(header)
    print(sep)
    for d in data:
        pension_str = (
            f"{d.pension.company_rate}%/{d.pension.personal_rate}%"
            if d.pension else "N/A"
        )
        medical_str = (
            f"{d.medical.company_rate}%/{d.medical.personal_rate}%"
            if d.medical else "N/A"
        )
        unemp_str = (
            f"{d.unemployment.company_rate}%/{d.unemployment.personal_rate}%"
            if d.unemployment else "N/A"
        )
        injury_str = (
            f"{d.work_injury.company_rate}%"
            if d.work_injury else "N/A"
        )
        maternity_str = (
            f"{d.maternity.company_rate}%"
            if d.maternity else "N/A"
        )
        print(
            f"{d.city:<8} {d.base_lower_limit:>10,.0f} {d.base_upper_limit:>10,.0f} "
            f"{pension_str:>12} {medical_str:>12} "
            f"{unemp_str:>12} {injury_str:>10} {maternity_str:>10}"
        )
    print(sep)
    print(f"注：工伤保险单位费率按行业划分（一类0.2%、二类0.4%、三类0.7%），上表取参考均值0.4%")
    print(f"    生育保险部分城市已与基本医疗保险合并征收")
    print(f"    数据来源：桂人社发〔2025〕42号，广西壮族自治区人力资源和社会保障厅\n")


# ─────────────────────── 主程序 ───────────────────────
def main():
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 60)
    print("广西各市2025年社保缴纳基数和比例爬虫")
    print("=" * 60)
    print(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"输出目录：{output_dir}/\n")

    crawler = GuangxiSocialInsuranceCrawler(timeout=10, delay=1.0)
    data = crawler.crawl_all()

    # 控制台输出
    print_summary(data)

    # 保存文件
    csv_path = os.path.join(output_dir, f"guangxi_social_insurance_2025_{timestamp}.csv")
    json_path = os.path.join(output_dir, f"guangxi_social_insurance_2025_{timestamp}.json")
    xlsx_path = os.path.join(output_dir, f"guangxi_social_insurance_2025_{timestamp}.xlsx")

    save_to_csv(data, csv_path)
    save_to_json(data, json_path)
    save_to_excel(data, xlsx_path)

    print(f"\n完成！共处理 {len(data)} 个城市的数据。")
    print(f"文件已保存至 {output_dir}/ 目录下。")


if __name__ == "__main__":
    main()
