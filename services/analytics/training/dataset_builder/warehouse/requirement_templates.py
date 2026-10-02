"""数仓设计需求模板。

组合式生成：领域 x 业务对象 x 场景修饰 x 指标短语，覆盖 _detect_domain
支持的三个领域（ecommerce / advertising / user_behavior），中英文变体。
"""

from __future__ import annotations

_DOMAINS: dict[str, list[str]] = {
    "ecommerce": [
        "电商",
        "跨境电商",
        "零售",
        "在线商超",
    ],
    "advertising": [
        "广告投放",
        "程序化广告",
        "信息流广告",
        "广告联盟",
    ],
    "user_behavior": [
        "用户行为",
        "埋点",
        "用户增长",
        "移动应用",
    ],
}

_OBJECTS: dict[str, list[str]] = {
    "ecommerce": ["订单", "支付", "商品", "库存", "会员", "售后"],
    "advertising": ["广告活动", "素材", "投放计划", "渠道"],
    "user_behavior": ["事件明细", "会话", "转化漏斗", "留痕日志"],
}

_INDICATORS: dict[str, list[str]] = {
    "ecommerce": ["GMV", "转化率", "复购率", "ARPU", "客单价"],
    "advertising": ["曝光", "点击", "CTR", "CPC", "ROAS"],
    "user_behavior": ["DAU", "留存率", "使用时长", "漏斗转化"],
}

_SUFFIXES = [
    "设计{domain}的{obj}数仓，关注指标{ind}",
    "为{domain}平台设计分层数仓，聚焦{obj}分析，指标包括{ind}",
    "设计{domain}数仓，指标{ind}，聚焦{obj}，ODS 保留原始日志，ADS 输出看板",
    "基于维度建模设计{domain}数仓，包含事实表与维度表，指标包括{ind}",
    "design a {domain} data warehouse for {obj} analytics with metrics {ind}",
    "为{domain}业务构建数仓，{obj}为核心业务对象，度量指标{ind}",
]


def generate_requirements() -> list[str]:
    requirements: list[str] = []
    for domain in ("ecommerce", "advertising", "user_behavior"):
        objects = _OBJECTS[domain]
        indicators = _INDICATORS[domain]
        for domain_label in _DOMAINS[domain]:
            for obj in objects:
                for ind in indicators:
                    for suffix in _SUFFIXES:
                        requirements.append(
                            suffix.format(domain=domain_label, obj=obj, ind=ind)
                        )
    # 组合公式应保证唯一；去重兜底，避免模板退化产生重复需求。
    return list(dict.fromkeys(requirements))


DEFAULT_RECOMMENDATION_LANGUAGES = ("zh-CN", "en")
