import streamlit as st
import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta

# ==========================================
# 1. 模拟数据库与配置 (Mock Data Layer)
# ==========================================

# 模拟：县城美妆常用热词库
KEYWORDS = ["积雪草泥膜", "早C晚A套装", "冷感防晒喷雾", "纯欲风唇泥", "水杨酸棉片", "美白身体乳", "一次性洗脸巾", "蓬松头发喷雾"]

# 模拟：供应链数据库 (支持小单快反的供应商)
SUPPLIERS = [
    {"id": 101, "name": "广州XX生物科技 (实力商家)", "moq": 3, "mixed_batch": True, "shipping_speed": "24h", "link": "https://1688.com/example1"},
    {"id": 102, "name": "义乌XX彩妆供应链", "moq": 12, "mixed_batch": True, "shipping_speed": "48h", "link": "https://1688.com/example2"},
    {"id": 103, "name": "上海XX品牌云仓", "moq": 1, "mixed_batch": False, "shipping_speed": "12h", "link": "https://1688.com/example3"},
]

# ==========================================
# 2. 双引擎核心逻辑 (Core Engines)
# ==========================================

class TrendDiscoveryEngine:
    """趋势发现引擎：捕捉社交媒体与电商数据"""
    
    def fetch_social_data(self, region):
        """模拟：获取特定区域（如湖南）的抖音/小红书声量数据"""
        data = []
        for kw in KEYWORDS:
            # 模拟算法：生成随机声量，如果关键词带有'泥膜'或'唇泥'则给予更高热度
            base_vol = random.randint(1000, 50000)
            if "泥膜" in kw or "唇泥" in kw:
                base_vol *= 1.5
            
            # 渗透率逻辑：一线城市火了，县城正在涨
            growth_rate = random.uniform(-0.1, 0.8) 
            
            data.append({
                "product_name": kw,
                "social_volume": int(base_vol),
                "growth_rate": round(growth_rate, 2),
                "platform": random.choice(["抖音", "小红书", "全网"]),
                "region_match": region
            })
        return pd.DataFrame(data)

class LocalMotionEngine:
    """本地动销引擎：捕捉批发与物流数据"""
    
    def fetch_wholesale_data(self, product_list):
        """模拟：分析该产品在周边省市的批发流通量"""
        data = []
        for prod in product_list:
            # 模拟逻辑：如果批发量巨大但零售声量还没爆，就是'前置机会'
            wholesale_idx = random.randint(50, 100) # 批发指数
            local_stock_pressure = random.randint(0, 1) # 0=缺货, 1=饱和
            
            data.append({
                "product_name": prod,
                "wholesale_index": wholesale_idx,
                "is_saturated": "饱和" if local_stock_pressure == 1 else "蓝海"
            })
        return pd.DataFrame(data)

class RecommendationSystem:
    """智能匹配系统：结合趋势与供应链"""
    
    def generate_recommendations(self, trend_df, local_df, budget):
        # 1. 合并数据
        merged = pd.merge(trend_df, local_df, on="product_name")
        
        # 2. 核心算法：计算“推荐指数”
        # 逻辑：增长快 + 本地未饱和 + 批发指数高 = 爆款
        merged['score'] = (merged['social_volume'] / 1000) * (1 + merged['growth_rate']) + (merged['wholesale_index'] / 2)
        merged.loc[merged['is_saturated'] == '饱和', 'score'] *= 0.5 # 饱和市场降权
        
        # 3. 匹配供应链
        results = []
        for _, row in merged.sort_values(by='score', ascending=False).iterrows():
            # 简单匹配逻辑：随机匹配一个供应商
            supplier = random.choice(SUPPLIERS)
            
            # 估算进货建议
            est_cost = random.randint(15, 60) # 预估进货价
            suggested_qty = int(budget * 0.2 / est_cost) # 建议用预算的20%测试单品
            if suggested_qty < supplier['moq']:
                suggested_qty = supplier['moq']
                
            results.append({
                "产品名称": row['product_name'],
                "推荐指数": round(row['score'], 1),
                "趋势标签": "🔥 爆款急升" if row['growth_rate'] > 0.5 else "📈 稳步增长",
                "本地状态": row['is_saturated'],
                "建议进货价": f"¥{est_cost}",
                "建议零售价": f"¥{int(est_cost * 2.2)}", # 确保县城店主有50%+毛利
                "推荐货源": supplier['name'],
                "起订量": supplier['moq'],
                "一键采购": supplier['link']
            })
        
        return pd.DataFrame(results)

# ==========================================
# 3. 前端可视化 (UI / Dashboard)
# ==========================================

def main():
    st.set_page_config(page_title="县域美妆智选系统 MVP", layout="wide")
    
    # --- 侧边栏：店主设置 ---
    st.sidebar.title("🏪 店铺设置")
    region = st.sidebar.selectbox("所在省份", ["湖南", "四川", "河南", "山东"])
    budget = st.sidebar.number_input("本期进货预算 (元)", min_value=1000, value=5000, step=500)
    store_type = st.sidebar.radio("店铺定位", ["大众日化", "精品美妆", "校园店"])
    
    st.sidebar.markdown("---")
    st.sidebar.info(f"当前模式：{store_type} \n\n 🎯 目标客群：{region}下沉市场")

    # --- 主界面 ---
    st.title(f"🚀 {region}县域美妆 · 趋势选品驾驶舱")
    
    # 实例化引擎
    trend_engine = TrendDiscoveryEngine()
    local_engine = LocalMotionEngine()
    recommender = RecommendationSystem()
    
    # 获取数据
    with st.spinner('正在扫描抖音同城榜 & 1688批发数据...'):
        df_trend = trend_engine.fetch_social_data(region)
        df_local = local_engine.fetch_wholesale_data(df_trend['product_name'].tolist())
        df_final = recommender.generate_recommendations(df_trend, df_local, budget)

    # --- 模块 1: 关键指标概览 ---
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("本周区域爆款词", f"{len(df_trend)} 个", "+3 新增")
    col2.metric("周边县市缺货率", "12%", "-2%")
    col3.metric("平均毛利空间", "55%", "↑ 高于电商")
    col4.metric("推荐SKU数", "5 款", "低风险")

    st.markdown("---")

    # --- 模块 2: 智能选品看板 ---
    c1, c2 = st.columns([2, 1])
    
    with c1:
        st.subheader("🔥 潜力爆款清单 (已过滤非混批货源)")
        st.dataframe(
            df_final[['产品名称', '趋势标签', '本地状态', '建议进货价', '建议零售价', '推荐货源', '一键采购']],
            use_container_width=True,
            hide_index=True
        )
        st.caption("注：'本地状态'显示'蓝海'意味着周边竞对尚未大量铺货，建议抢先引入。")

    with c2:
        st.subheader("📊 趋势雷达")
        # 简单的条形图展示热度
        chart_data = df_trend.sort_values('social_volume', ascending=False).head(5)
        st.bar_chart(chart_data, x="product_name", y="social_volume")
        st.markdown(f"**数据洞察：** \n检测到 `{chart_data.iloc[0]['product_name']}` 在 {region} 搜索量飙升，但本地实体店覆盖率低。")

    # --- 模块 3: 采购决策辅助 ---
    st.markdown("---")
    st.subheader("🛒 智能进货方案")
    
    top_pick = df_final.iloc[0]
    st.success(f"""
    **店主行动建议：**
    1. **首单尝试：** 建议采购 **{top_pick['产品名称']}**，数量 **{top_pick['起订量']*2} 件**。
    2. **预计投入：** 约 ¥{int(top_pick['建议进货价'].replace('¥','')) * top_pick['起订量']*2}。
    3. **陈列建议：** 放置在收银台或门口“新品推荐区”，搭配“网红同款”POP海报。
    """)
    
    with st.expander("点击查看供应商详细信息"):
        st.write(f"供应商：{top_pick['推荐货源']}")
        st.write("资质：1688实力商家 / 48小时发货 / 支持7天无理由")
        st.button(f"🔗 跳转至供应商 ({top_pick['推荐货源']}) 进行采购")

if __name__ == "__main__":
    main()