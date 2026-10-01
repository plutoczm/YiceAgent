const titles={overview:'工作台总览',text2sql:'自然语言转 SQL',review:'SQL 风险审查',warehouse:'数仓设计助手',knowledge:'工程知识检索'};
const knowledge=[
 {topic:'实时计算',title:'Flink Watermark 与迟到数据',body:'Watermark 表示事件时间进度。结合允许迟到时间与 Side Output，避免乱序数据被静默丢弃。'},
 {topic:'实时计算',title:'Kafka 消费一致性',body:'幂等 Producer、事务写入与 Checkpoint 协同可构建端到端 Exactly-once 处理语义。'},
 {topic:'性能',title:'Spark 数据倾斜治理',body:'先定位高频 Key，再选择预聚合、加盐、广播 Join 或 AQE 倾斜分区优化。'},
 {topic:'性能',title:'ClickHouse 排序键设计',body:'ORDER BY 决定物理排序与稀疏索引效果，应优先放置高频过滤且基数适中的列。'},
 {topic:'数仓',title:'维度建模的粒度声明',body:'事实表设计先明确一行代表什么，再确定维度外键、可加性事实和退化维度。'},
 {topic:'数仓',title:'DWD 与 DWS 的职责边界',body:'DWD 沉淀业务明细事实，DWS 面向主题聚合并复用公共指标，ADS 服务具体应用。'},
 {topic:'湖仓',title:'Medallion 分层实践',body:'Bronze 保留原始事实，Silver 完成清洗建模，Gold 交付面向业务的聚合数据产品。'},
 {topic:'湖仓',title:'Iceberg 表维护',body:'定期合并小文件、过期快照并清理孤儿文件，同时保留满足审计要求的时间旅行窗口。'},
 {topic:'性能',title:'SQL 分区裁剪',body:'避免在分区字段外层套函数；使用原始字段范围条件，让优化器在扫描前完成分区裁剪。'}
];
function showView(id){document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===id));document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.view===id));document.querySelector('#pageTitle').textContent=titles[id];history.replaceState(null,'','#'+id);window.scrollTo({top:0,behavior:'smooth'});}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>showView(b.dataset.view)));
document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',()=>showView(b.dataset.go)));
const initial=location.hash.slice(1);if(titles[initial])showView(initial);

document.querySelector('#sqlForm').addEventListener('submit',e=>{
 e.preventDefault();
 const dialect=document.querySelector('#dialect').value;
 const q=document.querySelector('#question').value;
 const dateFn=dialect==='MySQL'?'CURRENT_DATE - INTERVAL 30 DAY':'date_sub(current_date(), 30)';
 const sql=[
  '-- '+dialect+' · '+q,
  'WITH order_base AS (',
  '  SELECT order_id, channel, order_status, created_at',
  '  FROM orders',
  '  WHERE created_at >= '+dateFn,
  '),',
  'pay_summary AS (',
  '  SELECT order_id,',
  "         SUM(CASE WHEN pay_status = 'SUCCESS' THEN pay_amount ELSE 0 END) AS pay_amount,",
  "         MAX(CASE WHEN pay_status = 'SUCCESS' THEN 1 ELSE 0 END) AS is_paid",
  '  FROM payments',
  '  GROUP BY order_id',
  ')',
  'SELECT o.channel,',
  '       ROUND(SUM(COALESCE(p.pay_amount, 0)), 2) AS pay_amount,',
  '       SUM(COALESCE(p.is_paid, 0)) AS pay_order_count,',
  '       ROUND(SUM(COALESCE(p.is_paid, 0)) / NULLIF(COUNT(DISTINCT o.order_id), 0), 4) AS pay_conversion_rate',
  'FROM order_base o',
  'LEFT JOIN pay_summary p ON o.order_id = p.order_id',
  'GROUP BY o.channel',
  'ORDER BY pay_amount DESC;'
 ].join('\n');
 document.querySelector('#sqlOutput').textContent=sql;
 document.querySelector('#sqlNote').textContent='已按 '+dialect+' 生成：包含日期裁剪、支付预聚合、空值处理与除零保护。';
});

document.querySelector('#reviewForm').addEventListener('submit',e=>{
 e.preventDefault();
 const sql=document.querySelector('#reviewSql').value;
 const rules=[];
 if(/select\s+\*/i.test(sql))rules.push(['避免 SELECT *','显式列出所需字段，降低网络与序列化开销并减少 Schema 变更影响。',18]);
 if(/join\s+\w+(?:\s+\w+)?\s*(?:where|order|group|;|$)/i.test(sql)&&!/\b(on|using)\b/i.test(sql))rules.push(['JOIN 缺少关联条件','当前查询可能产生笛卡尔积，请补充 ON 或 USING 条件。',35]);
 if(/where\s+(date|year|month)\s*\(/i.test(sql))rules.push(['过滤列被函数包裹','改为原始时间字段的范围过滤，以启用索引或分区裁剪。',16]);
 if(/order\s+by/i.test(sql)&&!/limit\s+\d+/i.test(sql))rules.push(['全量排序','确认是否需要完整结果；交互查询建议增加 LIMIT。',10]);
 if(/\b(delete|update)\b/i.test(sql)&&!/\bwhere\b/i.test(sql))rules.push(['危险写操作','DELETE/UPDATE 缺少 WHERE，将影响整张表。',60]);
 const score=Math.max(0,100-rules.reduce((n,r)=>n+r[2],0));
 document.querySelector('#reviewScore').textContent=score;
 const level=score>=85?'low':score>=60?'medium':'high';
 const badge=document.querySelector('#riskBadge');badge.className='risk '+level;badge.textContent=level==='low'?'低风险':level==='medium'?'中风险':'高风险';
 document.querySelector('#findings').innerHTML=rules.length?rules.map((r,i)=>'<article class="finding"><b>'+(i+1)+'. '+r[0]+'</b><p>'+r[1]+'</p></article>').join(''):'<article class="finding"><b>未发现明显风险</b><p>查询通过当前规则集检查，仍建议结合执行计划与真实数据量验证。</p></article>';
});

function warehouseLayers(model){
 const star=[
  ['ODS','ods_trade_order / ods_trade_payment','原样接入业务库增量，保留源系统主键、操作时间与装载批次。'],
  ['DWD','dwd_trade_order_detail','按订单项粒度统一下单、支付、退款事实，补齐渠道、商品与店铺维度键。'],
  ['DIM','dim_product / dim_shop / dim_channel','采用代理键管理缓慢变化维度，保留生效与失效时间。'],
  ['DWS','dws_trade_channel_day','按日期、渠道聚合 GMV、支付订单数、支付用户数和退款金额。'],
  ['ADS','ads_trade_overview','交付经营总览、渠道排行和转化漏斗，统一指标口径。']
 ];
 const snowflake=[
  ['ODS','ods_trade_order / ods_trade_payment','原样接入业务库增量，保留源系统主键、操作时间与装载批次。'],
  ['DWD','dwd_trade_order_detail','按订单项粒度统一下单、支付、退款事实，补齐渠道、商品与店铺维度键。'],
  ['DIM','dim_product -> dim_category','维度规范化拆分：商品维度下钻到类目子维度，类目属性独立维护。'],
  ['DIM','dim_shop -> dim_region','店铺维度下钻到区域子维度（省/市/区），区域变化不污染店铺主维度。'],
  ['DIM','dim_channel','渠道维度保持独立，退化维度键直接冗余到事实表提升查询性能。'],
  ['DWS','dws_trade_channel_day','按日期、渠道聚合 GMV、支付订单数、支付用户数和退款金额。'],
  ['ADS','ads_trade_overview','交付经营总览、渠道排行和转化漏斗，统一指标口径。']
 ];
 const vault=[
  ['STG','stg_trade_order / stg_trade_payment / stg_refund','暂存层：按装载批次原样落库，记录增量水位与去重标记。'],
  ['HUB','hub_order / hub_product / hub_shop','枢纽表：业务键（自然键）唯一化，仅存键，不存可变属性。'],
  ['LINK','lnk_order_product / lnk_order_payment / lnk_order_refund','链接表：表达业务关系与多对多关联，携带关系生效时间。'],
  ['SAT','sat_order_detail / sat_shop_attr / sat_product_attr','卫星表：按时间承载可变属性历史，属性变化新增一条记录。'],
  ['PVT','pvt_trade_day','物化视图层：按需聚合 Link/Sat 产出分析视图，供指标直接查询。']
 ];
 if(model==='雪花模型') return snowflake;
 if(model==='Data Vault') return vault;
 return star;
}
document.querySelector('#warehouseForm').addEventListener('submit',e=>{
 e.preventDefault();
 const business=document.querySelector('#business').value;
 const model=document.querySelector('#modelType').value;
 const layers=warehouseLayers(model);
 document.querySelector('#warehouseOutput').innerHTML='<div class="result-note">'+model+' · '+business+'</div>'+layers.map(x=>'<article class="layer"><strong>'+x[0]+'</strong><div><h3>'+x[1]+'</h3><p>'+x[2]+'</p></div></article>').join('');
});

function renderKnowledge(){
 const q=document.querySelector('#knowledgeSearch').value.trim().toLowerCase();
 const active=document.querySelector('#topicFilter .active').dataset.topic;
 const rows=knowledge.filter(x=>(active==='all'||x.topic===active)&&(!q||(x.title+x.body+x.topic).toLowerCase().includes(q)));
 document.querySelector('#knowledgeGrid').innerHTML=rows.length?rows.map(x=>'<article class="knowledge-card"><span>'+x.topic+'</span><h3>'+x.title+'</h3><p>'+x.body+'</p></article>').join(''):'<p class="empty">没有匹配的知识条目。</p>';
}
document.querySelector('#knowledgeSearch').addEventListener('input',renderKnowledge);
document.querySelectorAll('#topicFilter button').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('#topicFilter button').forEach(x=>x.classList.remove('active'));b.classList.add('active');renderKnowledge();}));
renderKnowledge();
document.querySelectorAll('.copy').forEach(b=>b.addEventListener('click',async()=>{const el=document.querySelector('#'+b.dataset.copy);await navigator.clipboard.writeText(el.innerText);const toast=document.querySelector('#toast');toast.classList.add('show');setTimeout(()=>toast.classList.remove('show'),1400);}));
