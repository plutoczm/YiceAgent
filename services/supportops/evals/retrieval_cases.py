from __future__ import annotations


def build_retrieval_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    groups = {
        "KB-REFUND-01": [
            "退款政策是什么？",
            "高金额退款为什么要人工审核？",
            "refund policy for a delivered order",
            "退款会原路退回吗，多久到账？",
            "does a high-value refund require human review",
        ],
        "KB-SHIPPING-01": [
            "我的包裹已经发货，怎么查物流？",
            "运输中的订单预计什么时候送达？",
            "where can I find package tracking after shipment",
            "shipping status and carrier information",
            "订单还在运输中，可以直接按已签收退货吗？",
        ],
        "KB-RETURN-01": [
            "退货政策怎么规定？",
            "已签收商品申请退货需要先确认吗？",
            "return policy for a delivered item",
            "can I return this item after delivery",
            "特殊品类是不是都支持无理由退货？",
        ],
        "KB-COUPON-01": [
            "优惠券为什么不能使用？",
            "coupon eligibility depends on what",
            "促销码可以和其他活动叠加吗？",
            "promo code order threshold rules",
            "优惠券的有效期和适用商品范围怎么看？",
        ],
        "KB-ADDRESS-01": [
            "订单没发货前怎么修改收货地址？",
            "已发货还能改地址吗？",
            "change shipping address before warehouse lock",
            "can I update the delivery address after shipment",
            "收货地址修改需要联系人工吗？",
        ],
        "KB-INVOICE-01": [
            "已支付订单怎么开发票？",
            "发票抬头填错了能修改吗？",
            "how do I request an invoice for a paid order",
            "billing information should match which records",
            "税号提交后还能重新开票吗？",
        ],
        "KB-WARRANTY-01": [
            "商品过了退货期还有保修吗？",
            "质量问题申请保修需要什么材料？",
            "warranty coverage after the return window",
            "what evidence is needed for a product defect warranty claim",
            "保修范围是不是由商品品类和厂商条款决定？",
        ],
        "KB-ACCOUNT-01": [
            "忘记密码应该怎么办？",
            "客服会向我要完整密码或验证码吗？",
            "how do I reset a forgotten password securely",
            "what should I do after a suspicious login",
            "账户异常登录后要修改哪些安全设置？",
        ],
        "KB-SUBSCRIPTION-01": [
            "怎么取消订阅自动续费？",
            "取消续费会自动退款吗？",
            "how do I cancel SaaS subscription renewal",
            "does cancelling auto renewal guarantee a refund",
            "订阅产生的账单退款按什么规则处理？",
        ],
        "KB-PRIVACY-01": [
            "怎么申请删除个人数据？",
            "隐私请求为什么要先验证身份？",
            "how can I request personal data deletion",
            "can support reveal another customer's data",
            "个人数据访问和更正请求怎么处理？",
        ],
    }
    for document_id, queries in groups.items():
        for index, query in enumerate(queries, 1):
            cases.append(
                {
                    "id": f"{document_id.lower()}-{index:02d}",
                    "query": query,
                    "relevant_ids": [document_id],
                    "supported": True,
                }
            )

    unsupported = [
        "帮我写一首关于月亮的诗",
        "what is the capital of Brazil",
        "解释一下量子纠缠",
        "recommend a movie for tonight",
        "how do I compile a Rust kernel module",
        "明天东京天气怎么样",
        "calculate the area of a triangle",
        "who won the football match yesterday",
        "给我推荐一道川菜",
        "translate this paragraph into French",
    ]
    for index, query in enumerate(unsupported, 1):
        cases.append(
            {
                "id": f"unsupported-{index:02d}",
                "query": query,
                "relevant_ids": [],
                "supported": False,
            }
        )
    return cases
