# 在 生成地图.py 中，生成地图后，绘制地图
# 其中应该以图表形式生成虚线的地图格子，建筑区以半透明红色表示，并标注建筑区名称，表名应该为图表名，
# 读取地图"地牢测试"，并绘制地图

import 生成地图
import sqlite3
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import json
import os
import platform
from matplotlib.colors import to_rgba
import math
from matplotlib.font_manager import FontProperties

# 设置中文字体支持
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun', 'KaiTi', 'FangSong', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题

# 根据操作系统选择合适的中文字体
system = platform.system()
if system == 'Windows':
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei'] + plt.rcParams['font.sans-serif']
elif system == 'Darwin':  # macOS
    plt.rcParams['font.sans-serif'] = ['PingFang SC', 'STHeiti'] + plt.rcParams['font.sans-serif']
elif system == 'Linux':
    plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'WenQuanYi Zen Hei'] + plt.rcParams['font.sans-serif']

# 尝试设置全局字体
try:
    # 创建一个通用的中文字体对象
    chinese_font = FontProperties(family=plt.rcParams['font.sans-serif'][0])
except:
    print("警告: 无法加载默认中文字体，文本可能无法正确显示")
    chinese_font = None

# 从dungeon.db中读取地图"地牢测试"
conn = sqlite3.connect('dungeon.db')
cursor = conn.cursor()
cursor.execute("SELECT * FROM map WHERE name = '地牢测试'")
地图 = cursor.fetchone()
conn.close()

def 绘制地图(地图名, 层索引=1, 展示网格=True, 展示建筑区=True, 展示建筑区名=True, 展示房间=True, 图像大小=(10, 10)):
    """
    绘制地牢地图及其建筑区和房间
    
    参数:
        地图名: 要绘制的地图名称，用于从数据库中获取地图信息
        层索引: 要显示的层级，默认为1
        展示网格: 是否显示网格线
        展示建筑区: 是否显示建筑区
        展示建筑区名: 是否显示建筑区名称
        展示房间: 是否显示房间
        图像大小: 图像尺寸，默认为(10, 10)英寸
        
    返回:
        matplotlib图表对象
    """
    # 连接数据库
    conn = sqlite3.connect('dungeon.db')
    cursor = conn.cursor()
    
    # 获取地图基本信息
    cursor.execute("SELECT width, height, layers FROM map WHERE name = ?", (地图名,))
    地图信息 = cursor.fetchone()
    
    if not 地图信息:
        print(f"错误: 找不到名为 '{地图名}' 的地图")
        return None
    
    地图宽度, 地图高度, 总层数 = 地图信息
    
    # 验证层索引有效
    if 层索引 < 1 or 层索引 > 总层数:
        print(f"错误: 无效的层索引 {层索引}，有效范围是 1-{总层数}")
        return None
    
    # 设置颜色常量
    建筑区颜色 = (1.0, 0.4, 0.4, 0.5)  # 半透明红色
    房间矢量颜色 = (0.4, 0.4, 1.0, 0.3)  # 半透明蓝色
    房间外墙颜色 = (0.3, 0.3, 0.3, 1.0)  # 不透明深灰色
    房间内部颜色 = (0.8, 0.8, 0.8, 1.0)  # 不透明浅灰色
    房间内墙颜色 = (0.7, 0.2, 0.2, 0.8)  # 不透明红棕色
    
    # 创建图表
    fig, ax = plt.subplots(figsize=图像大小)
    ax.set_xlim(0, 地图宽度)
    ax.set_ylim(0, 地图高度)
    ax.set_aspect('equal')  # 确保X和Y轴比例相同
    
    # 设置标题
    ax.set_title(f"{地图名} - 层级 {层索引}", fontproperties=chinese_font)
    
    # 显示网格
    if 展示网格:
        # 主网格线（每10个单位）
        ax.grid(True, which='major', linestyle='-', linewidth=0.5, color='#cccccc')
        # 次网格线（每个单位）
        ax.grid(True, which='minor', linestyle=':', linewidth=0.2, color='#dddddd')
        ax.set_xticks(np.arange(0, 地图宽度 + 1, 10))
        ax.set_xticks(np.arange(0, 地图宽度 + 1, 1), minor=True)
        ax.set_yticks(np.arange(0, 地图高度 + 1, 10))
        ax.set_yticks(np.arange(0, 地图高度 + 1, 1), minor=True)
    
    # 显示边界
    ax.axhline(y=0, color='k', linestyle='-', linewidth=2)
    ax.axhline(y=地图高度, color='k', linestyle='-', linewidth=2)
    ax.axvline(x=0, color='k', linestyle='-', linewidth=2)
    ax.axvline(x=地图宽度, color='k', linestyle='-', linewidth=2)
    
    # 如果需要显示建筑区
    if 展示建筑区:
        # 获取当前层的建筑区 - 包括单层和跨层建筑区
        cursor.execute('''
        SELECT name, position, type, corner, size, area FROM building_areas 
        WHERE map_name = ? AND layer = ?
        ''', (地图名, 层索引))
        
        建筑区列表 = cursor.fetchall()
        已处理建筑区 = set()  # 跟踪已处理的建筑区
        
        # 处理每个建筑区
        for 建筑区 in 建筑区列表:
            名称, 位置_str, 类型, 角点_str, 大小_str, 面积 = 建筑区
            已处理建筑区.add(名称)
            
            try:
                # 解析JSON数据
                位置 = json.loads(位置_str) if 位置_str else None
                大小数据 = json.loads(大小_str) if 大小_str else {}
                
                # 将面积添加到大小数据中
                if 面积 is not None:
                    大小数据["area"] = 面积
                
                # 绘制建筑区形状
                绘制建筑区形状(ax, 类型, 位置, 角点_str, 大小数据, 名称, 建筑区颜色, 展示建筑区名)
                
            except Exception as e:
                print(f"处理建筑区 '{名称}' 时出错: {e}")
        
        # 获取跨层建筑区 - 通过size字段中的min_layer和max_layer判断
        cursor.execute('''
        SELECT name, position, type, corner, size, area FROM building_areas 
        WHERE map_name = ? AND layer != ?
        ''', (地图名, 层索引))
        
        额外建筑区列表 = cursor.fetchall()
        
        # 处理每个可能的跨层建筑区
        for 建筑区 in 额外建筑区列表:
            名称, 位置_str, 类型, 角点_str, 大小_str, 面积 = 建筑区
            
            # 跳过已处理的建筑区
            if 名称 in 已处理建筑区:
                continue
                
            try:
                # 解析大小数据JSON
                大小数据 = json.loads(大小_str) if 大小_str else {}
                
                # 将面积添加到大小数据中
                if 面积 is not None:
                    大小数据["area"] = 面积
                
                # 检查是否是跨层建筑区，并且包含当前层
                if ("is_multi_layer" in 大小数据 and 大小数据["is_multi_layer"] and
                    "min_layer" in 大小数据 and "max_layer" in 大小数据):
                    min_layer = 大小数据["min_layer"]
                    max_layer = 大小数据["max_layer"]
                    
                    # 如果当前层在跨层范围内，则绘制该建筑区
                    if min_layer <= 层索引 <= max_layer:
                        位置 = json.loads(位置_str) if 位置_str else None
                        绘制建筑区形状(ax, 类型, 位置, 角点_str, 大小数据, 名称, 建筑区颜色, 展示建筑区名)
                        已处理建筑区.add(名称)
                
            except Exception as e:
                print(f"处理可能的跨层建筑区 '{名称}' 时出错: {e}")
    
    # 如果需要显示房间
    if 展示房间:
        print(f"\n正在处理 {地图名} 的第 {层索引} 层房间...")
        
        # 获取当前层的房间，确保包含内部墙格子列表
        查询 = '''
        SELECT name, wall_grid_list, space_grid_list, inner_wall_grid_list, vector_params, layer_name 
        FROM room 
        WHERE map_name = ? AND (
            layer_name = ? OR 
            layer_name LIKE ? OR 
            layer_name LIKE ? OR 
            layer_name LIKE ? OR
            (json_extract(vector_params, '$.is_multi_layer') = 1 AND 
             json_extract(vector_params, '$.min_layer') <= ? AND 
             json_extract(vector_params, '$.max_layer') >= ?)
        )
        '''
        参数 = (地图名, str(层索引), f"{层索引}-%", f"%-{层索引}", f"%-{层索引}-%", 层索引, 层索引)
        
        print(f"执行查询: {查询}")
        print(f"查询参数: {参数}")
        
        cursor.execute(查询, 参数)
        房间列表 = cursor.fetchall()
        
        print(f"找到 {len(房间列表)} 个房间")
        
        for 房间 in 房间列表:
            名称, 墙格子_str, 空间格子_str, 内墙格子_str, 矢量参数_str, 层名称 = 房间
            print(f"\n处理房间: {名称} (层级: {层名称})")
            
            try:
                # 解析JSON数据
                墙格子列表 = json.loads(墙格子_str)
                空间格子列表 = json.loads(空间格子_str)
                内墙格子列表 = json.loads(内墙格子_str) if 内墙格子_str else []
                矢量参数 = json.loads(矢量参数_str)
                
                print(f"墙格子数量: {len(墙格子列表)}")
                print(f"空间格子数量: {len(空间格子列表)}")
                print(f"内墙格子数量: {len(内墙格子列表)}")
                print(f"矢量参数: {矢量参数}")
                
                # 创建房间颜色配置
                房间颜色配置 = {
                    '内部颜色': 房间内部颜色,
                    '外墙颜色': 房间外墙颜色,
                    '内墙颜色': 房间内墙颜色,
                    '矢量颜色': 房间矢量颜色
                }
                
                # 使用绘制房间形状函数
                绘制房间形状(ax, 房间, 房间颜色配置)
                
            except Exception as e:
                print(f"处理房间 '{名称}' 时出错: {e}")
                import traceback
                print(f"错误堆栈: {traceback.format_exc()}")
                
        # 强制更新图形
        plt.draw()
    
    # 设置轴标签
    ax.set_xlabel('X 坐标', fontproperties=chinese_font)
    ax.set_ylabel('Y 坐标', fontproperties=chinese_font)
    
    # 添加图例
    图例元素 = []
    if 展示建筑区:
        图例元素.append(patches.Patch(color=建筑区颜色, label='建筑区'))
    if 展示房间:
        图例元素.extend([
            patches.Patch(color=房间矢量颜色, label='房间矢量'),
            patches.Patch(color=房间外墙颜色, label='房间外墙'),
            patches.Patch(color=房间内墙颜色, label='房间内墙'),
            patches.Patch(color=房间内部颜色, label='房间内部')
        ])
    if 图例元素:
        legend = ax.legend(handles=图例元素, loc='upper right')
        # 对图例中的每个文本应用中文字体
        for text in legend.get_texts():
            text.set_fontproperties(chinese_font)
    
    # 调整布局
    plt.tight_layout()
    
    # 关闭数据库连接
    conn.close()
    
    return fig

def 绘制建筑区形状(ax, 类型, 位置, 角点_str, 大小数据, 名称, 颜色, 展示建筑区名):
    """辅助函数，用于绘制不同类型的建筑区形状"""
    if 类型 == "circle":
        # 圆形建筑区
        半径 = float(角点_str)
        圆形 = patches.Circle(位置, radius=半径, fill=True, color=颜色, edgecolor='black', linewidth=1)
        ax.add_patch(圆形)
        
        # 显示名称
        if 展示建筑区名:
            层信息 = 获取层信息文本(大小数据)
            面积信息 = 获取面积信息文本(大小数据)
            显示文本 = f"{名称}\n{层信息}"
            if 面积信息:
                显示文本 += f"\n{面积信息}"
            ax.text(位置[0], 位置[1], 显示文本, ha='center', va='center', fontsize=8, 
                   fontproperties=chinese_font)
            
    elif 类型 == "rectangle":
        # 矩形建筑区
        角点 = json.loads(角点_str)
        
        # 计算左上角坐标
        left = min(p[0] for p in 角点)
        bottom = min(p[1] for p in 角点)
        width = max(p[0] for p in 角点) - left
        height = max(p[1] for p in 角点) - bottom
        
        矩形 = patches.Rectangle((left, bottom), width, height, fill=True, color=颜色, edgecolor='black', linewidth=1)
        ax.add_patch(矩形)
        
        # 显示名称
        if 展示建筑区名:
            center_x = left + width/2
            center_y = bottom + height/2
            层信息 = 获取层信息文本(大小数据)
            面积信息 = 获取面积信息文本(大小数据)
            显示文本 = f"{名称}\n{层信息}"
            if 面积信息:
                显示文本 += f"\n{面积信息}"
            ax.text(center_x, center_y, 显示文本, ha='center', va='center', fontsize=8,
                   fontproperties=chinese_font)
            
    elif 类型 == "polygon":
        # 多边形建筑区（如旋转矩形）
        顶点列表 = json.loads(角点_str)
        顶点数组 = np.array(顶点列表)
        
        多边形 = patches.Polygon(顶点数组, closed=True, fill=True, color=颜色, edgecolor='black', linewidth=1)
        ax.add_patch(多边形)
        
        # 显示名称
        if 展示建筑区名:
            # 计算多边形中心点
            center_x = np.mean([p[0] for p in 顶点列表])
            center_y = np.mean([p[1] for p in 顶点列表])
            层信息 = 获取层信息文本(大小数据)
            面积信息 = 获取面积信息文本(大小数据)
            显示文本 = f"{名称}\n{层信息}"
            if 面积信息:
                显示文本 += f"\n{面积信息}"
            ax.text(center_x, center_y, 显示文本, ha='center', va='center', fontsize=8,
                   fontproperties=chinese_font)

def 绘制房间内部格子(ax, 空间格子列表, 颜色):
    """绘制房间的内部空间格子"""
    for x, y in 空间格子列表:
        rect = patches.Rectangle(
            (x-0.5, y-0.5), 1, 1,
            facecolor=颜色,
            edgecolor='none',
            zorder=2
        )
        ax.add_patch(rect)

def 绘制房间外墙格子(ax, 墙格子列表, 颜色):
    """绘制房间的外墙格子"""
    for x, y in 墙格子列表:
        rect = patches.Rectangle(
            (x-0.5, y-0.5), 1, 1,
            facecolor=颜色,
            edgecolor='black',
            linewidth=0.5,
            zorder=3
        )
        ax.add_patch(rect)

def 绘制房间内墙格子(ax, 内墙格子列表, 颜色):
    """绘制房间的内墙格子（迷宫墙壁）"""
    for x, y in 内墙格子列表:
        rect = patches.Rectangle(
            (x-0.5, y-0.5), 1, 1,
            facecolor=颜色,
            edgecolor='black',
            linewidth=0.5,
            zorder=4  # 确保内墙绘制在其他元素之上
        )
        ax.add_patch(rect)

def 绘制房间矢量轮廓(ax, 矢量参数, 颜色):
    """绘制房间的矢量轮廓，对多边形使用corners参数，对圆形使用Circle对象"""
    房间类型 = 矢量参数.get("type", "unknown")
    center = 矢量参数.get("center", [0, 0])
    
    # 对于圆形，直接使用Circle对象
    if 房间类型 == "circle":
        radius = 矢量参数.get("radius", 0)
        if radius > 0:
            circle = patches.Circle(
                center, radius=radius,
                facecolor=颜色,
                edgecolor='blue',
                linewidth=1.5,
                zorder=1
            )
            return circle, center
    
    # 对于其他房间类型，优先使用corners参数绘制
    if "corners" in 矢量参数 and len(矢量参数["corners"]) >= 3:
        corners = 矢量参数["corners"]
        # 如果未提供center，计算角点的中心
        if "center" not in 矢量参数:
            center = [
                sum(p[0] for p in corners) / len(corners),
                sum(p[1] for p in corners) / len(corners)
            ]
        
        poly = patches.Polygon(
            corners,
            closed=True,
            facecolor=颜色,
            edgecolor='blue',
            linewidth=1.5,
            zorder=1
        )
        return poly, center
    
    # 如果没有corners参数，尝试从不同类型的房间中提取或计算corners
    if 房间类型 in ["rectangle", "rotated_rectangle"]:
        # 对于矩形，根据中心点、宽高和角度计算四个角点
        width = 矢量参数.get("width", 0)
        height = 矢量参数.get("height", 0)
        angle = 矢量参数.get("angle", 0)
        
        # 计算矩形的四个角点（不旋转的情况）
        half_w = width / 2
        half_h = height / 2
        corners = [
            [center[0] - half_w, center[1] - half_h],  # 左下
            [center[0] + half_w, center[1] - half_h],  # 右下
            [center[0] + half_w, center[1] + half_h],  # 右上
            [center[0] - half_w, center[1] + half_h]   # 左上
        ]
        
        # 如果有旋转角度，旋转这些角点
        if angle != 0:
            # 旋转矩阵
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)
            
            # 旋转每个角点
            for i, point in enumerate(corners):
                x = point[0] - center[0]
                y = point[1] - center[1]
                rotated_x = x * cos_a - y * sin_a
                rotated_y = x * sin_a + y * cos_a
                corners[i] = [center[0] + rotated_x, center[1] + rotated_y]
        
        # 使用计算出的角点绘制多边形
        poly = patches.Polygon(
            corners,
            closed=True,
            facecolor=颜色,
            edgecolor='blue',
            linewidth=1.5,
            zorder=1
        )
        return poly, center
    
    # 没有找到有效的信息来绘制房间
    print(f"警告: 无法绘制房间矢量轮廓，缺少有效的角点或形状信息")
    return None

def 添加房间名称(ax, 中心点, 名称):
    """在房间中心添加房间名称"""
    ax.text(中心点[0], 中心点[1], 名称,
           ha='center', va='center',
           color='blue', fontsize=8,
           zorder=5,  # 确保文本显示在最上层
           bbox=dict(facecolor='white', edgecolor='none', alpha=0.7),
           fontproperties=chinese_font)

def 绘制房间形状(ax, 房间数据, 颜色配置):
    """
    绘制单个房间的所有组成部分
    
    参数:
        ax: matplotlib轴对象
        房间数据: 包含房间信息的元组 (名称, 墙格子_str, 空间格子_str, 内墙格子_str, 矢量参数_str, 层名称)
        颜色配置: 包含各部分颜色的字典 {
            '内部颜色': 颜色值,
            '外墙颜色': 颜色值,
            '内墙颜色': 颜色值,
            '矢量颜色': 颜色值
        }
    """
    try:
        名称, 墙格子_str, 空间格子_str, 内墙格子_str, 矢量参数_str, 层名称 = 房间数据
        
        # 解析JSON数据
        墙格子列表 = json.loads(墙格子_str)
        空间格子列表 = json.loads(空间格子_str)
        内墙格子列表 = json.loads(内墙格子_str) if 内墙格子_str else []
        矢量参数 = json.loads(矢量参数_str)
        
        # 绘制内部格子
        绘制房间内部格子(ax, 空间格子列表, 颜色配置['内部颜色'])
        
        # 绘制外墙格子
        绘制房间外墙格子(ax, 墙格子列表, 颜色配置['外墙颜色'])
        
        # 绘制内墙格子（如果有）
        if 内墙格子列表:
            绘制房间内墙格子(ax, 内墙格子列表, 颜色配置['内墙颜色'])
        
        # 绘制矢量轮廓
        结果 = 绘制房间矢量轮廓(ax, 矢量参数, 颜色配置['矢量颜色'])
        if 结果:
            shape, center = 结果
            ax.add_patch(shape)
            
            # 如果存在收缩后的角点，再绘制一个轮廓表示收缩后的形状
            if "contracted_corners" in 矢量参数 and len(矢量参数["contracted_corners"]) >= 3:
                收缩角点 = 矢量参数["contracted_corners"]
                收缩多边形 = patches.Polygon(
                    收缩角点,
                    closed=True,
                    facecolor='none',  # 透明填充
                    edgecolor='red',  # 红色边框
                    linewidth=2.0,
                    linestyle='--',  # 虚线
                    zorder=1.5  # 略高于原始轮廓
                )
                ax.add_patch(收缩多边形)
                
                # 添加收缩信息标签
                收缩信息 = "收缩后形状"
                ax.text(center[0], center[1] + 1, 收缩信息,
                      ha='center', va='center',
                      color='red', fontsize=6,
                      zorder=5,
                      bbox=dict(facecolor='white', edgecolor='none', alpha=0.7),
                      fontproperties=chinese_font)
            
            # 添加房间名称
            添加房间名称(ax, center, 名称)
            
            # 添加迷宫信息（如果有内墙）
            if 内墙格子列表:
                迷宫信息 = f"迷宫: {len(内墙格子列表)}墙格"
                # 在房间名称下方添加迷宫信息
                ax.text(center[0], center[1] - 1, 迷宫信息,
                      ha='center', va='center',
                      color='red', fontsize=6,
                      zorder=5,
                      bbox=dict(facecolor='white', edgecolor='none', alpha=0.7),
                      fontproperties=chinese_font)
                
            # 如果房间是多边形且被收缩过
            if 矢量参数.get("type") == "polygon" and 矢量参数.get("has_been_contracted", False):
                收缩信息 = f"已收缩: {矢量参数.get('contraction_count', 0)}次"
                ax.text(center[0], center[1] - 2, 收缩信息,
                       ha='center', va='center',
                       color='darkred', fontsize=6,
                       zorder=5,
                       bbox=dict(facecolor='white', edgecolor='none', alpha=0.7),
                       fontproperties=chinese_font)
                
                # 可选: 显示收缩点
                if "contraction_points" in 矢量参数:
                    收缩点 = 矢量参数["contraction_points"]
                    for 点 in 收缩点:
                        ax.plot(点[0], 点[1], 'ro', markersize=3, zorder=6)
            
    except Exception as e:
        print(f"绘制房间 '{名称}' 时出错: {e}")
        import traceback
        print(f"错误堆栈: {traceback.format_exc()}")

def 获取层信息文本(大小数据):
    """从大小数据中获取层信息文本"""
    if "is_multi_layer" in 大小数据 and 大小数据["is_multi_layer"]:
        min_layer = 大小数据.get("min_layer", "?")
        max_layer = 大小数据.get("max_layer", "?")
        return f"层{min_layer}-{max_layer}"
    elif "layer" in 大小数据:
        return f"层{大小数据['layer']}"
    else:
        return ""

def 获取面积信息文本(大小数据):
    """从大小数据中获取面积信息文本"""
    面积 = 0
    
    # 尝试从大小数据中获取面积
    if "area" in 大小数据 and 大小数据["area"] is not None:
        面积 = 大小数据["area"]
    
    # 如果有面积信息，格式化显示
    if 面积 > 0:
        # 对大数值进行四舍五入
        if 面积 >= 100:
            面积 = round(面积)
            return f"面积: {面积}平方单位"
        else:
            # 小数值保留一位小数
            面积 = round(面积, 1)
            return f"面积: {面积}平方单位"
    
    return ""

def 保存地图(fig, 文件名, 格式列表=None, 输出目录="地图输出"):
    """
    保存地图为指定格式
    
    参数:
        fig: matplotlib图表对象
        文件名: 保存的文件名（不含扩展名）
        格式列表: 要保存的格式列表，如['png', 'pdf']，默认为['png']
        输出目录: 保存文件的目录，默认为"地图输出"
    """
    if 格式列表 is None:
        格式列表 = ['png']
    
    # 确保输出目录存在
    if not os.path.exists(输出目录):
        os.makedirs(输出目录)
        print(f"创建输出目录: {输出目录}")
    
    # 完整的文件路径（不含扩展名）
    完整路径 = os.path.join(输出目录, 文件名)
    
    # 保存每种格式
    for 格式 in 格式列表:
        保存路径 = f"{完整路径}.{格式}"
        if 格式.lower() == 'png':
            fig.savefig(保存路径, dpi=300, bbox_inches='tight')
        else:
            fig.savefig(保存路径, format=格式, bbox_inches='tight')
        print(f"地图已保存为{格式.upper()}: {保存路径}")

def 绘制多层地图(地图名, 层列表=None, 展示网格=True, 展示建筑区=True, 展示建筑区名=True, 展示房间=True):
    """
    绘制地牢地图的多个层
    
    参数:
        地图名: 要绘制的地图名称
        层列表: 要显示的层索引列表，如果为None则显示所有层
        展示网格: 是否显示网格线
        展示建筑区: 是否显示建筑区
        展示建筑区名: 是否显示建筑区名称
        展示房间: 是否显示房间
    返回:
        图表对象列表
    """
    # 连接数据库
    conn = sqlite3.connect('dungeon.db')
    cursor = conn.cursor()
    
    # 获取地图层数
    cursor.execute("SELECT layers FROM map WHERE name = ?", (地图名,))
    结果 = cursor.fetchone()
    
    if not 结果:
        print(f"错误: 找不到名为 '{地图名}' 的地图")
        # 列出所有可用的地图
        cursor.execute("SELECT name FROM map")
        可用地图 = cursor.fetchall()
        print(f"可用的地图: {[m[0] for m in 可用地图]}")
        conn.close()
        return []
    
    总层数 = 结果[0]
    print(f"地图 '{地图名}' 共有 {总层数} 层")
    
    # 检查是否存在房间数据
    cursor.execute("SELECT COUNT(*) FROM room WHERE map_name = ?", (地图名,))
    房间数量 = cursor.fetchone()[0]
    print(f"地图 '{地图名}' 中共有 {房间数量} 个房间")
    
    if 房间数量 > 0:
        # 显示所有房间的信息
        cursor.execute("SELECT name, layer_name FROM room WHERE map_name = ?", (地图名,))
        房间信息 = cursor.fetchall()
        for 房间名, 层名称 in 房间信息:
            print(f"房间: {房间名}, 层级: {层名称}")
    
    conn.close()
    
    # 如果没有指定层列表，则绘制所有层
    if 层列表 is None:
        层列表 = range(1, 总层数 + 1)  # 从1开始到总层数
    
    # 绘制每一层
    图表列表 = []
    for 层索引 in 层列表:
        if 层索引 < 1 or 层索引 > 总层数:
            print(f"警告: 跳过无效的层索引 {层索引}")
            continue
        
        print(f"\n开始绘制 {地图名} 第 {层索引} 层...")
        fig = 绘制地图(地图名, 层索引, 展示网格, 展示建筑区, 展示建筑区名, 展示房间)
        if fig:
            图表列表.append((层索引, fig))
            plt.show()
        else:
            print(f"绘制 {地图名} 第 {层索引} 层失败")
    
    return 图表列表

def 保存多层地图(图表列表, 地图名, 格式列表=None, 输出目录="地图输出"):
    """
    保存多层地图
    
    参数:
        图表列表: 由绘制多层地图函数返回的图表对象列表
        地图名: 地图名称（用于生成文件名）
        格式列表: 要保存的格式列表
        输出目录: 保存文件的目录
    """
    for 层索引, fig in 图表列表:
        文件名 = f"{地图名}_层{层索引}"
        保存地图(fig, 文件名, 格式列表, 输出目录)

# 添加一个新的函数来专门可视化收缩后的房间
def 绘制收缩房间对比(地图名, 房间名, 层索引=1):
    """
    绘制房间收缩前后的对比图
    
    参数:
        地图名: 地图名称
        房间名: 房间名称
        层索引: 层级索引
    """
    # 连接数据库
    conn = sqlite3.connect('dungeon.db')
    cursor = conn.cursor()
    
    # 获取房间数据
    cursor.execute('''
    SELECT wall_grid_list, space_grid_list, inner_wall_grid_list, vector_params, layer_name 
    FROM room 
    WHERE map_name = ? AND name = ?
    ''', (地图名, 房间名))
    
    房间数据 = cursor.fetchone()
    
    if not 房间数据:
        print(f"错误: 找不到名为 '{房间名}' 的房间")
        conn.close()
        return None
    
    墙格子_str, 空间格子_str, 内墙格子_str, 矢量参数_str, 层名称 = 房间数据
    
    # 解析JSON数据
    矢量参数 = json.loads(矢量参数_str)
    
    # 检查房间是否被收缩过
    if not 矢量参数.get("has_been_contracted", False) and "contracted_corners" not in 矢量参数:
        print(f"警告: 房间 '{房间名}' 尚未被收缩过")
        conn.close()
        return None
    
    # 创建对比图
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.set_title(f"{房间名} - 收缩前后对比", fontproperties=chinese_font)
    
    # 使用常规方法绘制房间
    房间数据完整 = (房间名,) + 房间数据
    
    # 设置颜色常量
    房间矢量颜色 = (0.4, 0.4, 1.0, 0.3)  # 原始形状: 半透明蓝色
    收缩矢量颜色 = (1.0, 0.4, 0.4, 0.3)  # 收缩后形状: 半透明红色
    房间外墙颜色 = (0.3, 0.3, 0.3, 1.0)  # 不透明深灰色
    房间内部颜色 = (0.8, 0.8, 0.8, 1.0)  # 不透明浅灰色
    房间内墙颜色 = (0.7, 0.2, 0.2, 0.8)  # 不透明红棕色
    
    # 创建颜色配置
    房间颜色配置 = {
        '内部颜色': 房间内部颜色,
        '外墙颜色': 房间外墙颜色,
        '内墙颜色': 房间内墙颜色,
        '矢量颜色': 房间矢量颜色
    }
    
    # 绘制房间
    绘制房间形状(ax, 房间数据完整, 房间颜色配置)
    
    # 设置轴标签
    ax.set_xlabel('X 坐标', fontproperties=chinese_font)
    ax.set_ylabel('Y 坐标', fontproperties=chinese_font)
    
    # 设置图例
    图例元素 = [
        patches.Patch(color=房间矢量颜色, label='原始形状'),
        patches.Patch(color=收缩矢量颜色, label='收缩后形状'),
        patches.Patch(color=房间外墙颜色, label='房间外墙'),
        patches.Patch(color=房间内墙颜色, label='房间内墙')
    ]
    legend = ax.legend(handles=图例元素, loc='upper right')
    # 对图例中的每个文本应用中文字体
    for text in legend.get_texts():
        text.set_fontproperties(chinese_font)
    
    # 关闭数据库连接
    conn.close()
    
    # 返回图表
    return fig

# 在主程序中添加测试
if __name__ == "__main__":
    # 为测试输出增加中文支持
    print("\n===== 测试地图绘制功能 =====")
    
    # 设置要使用的地图名称
    测试地图名 = "地牢测试"
    print(f"\n正在使用地图: {测试地图名}")
    
    # 测试1：显示所有元素
    print("\n==== 测试1：显示所有元素 ====")
    图表列表 = 绘制多层地图(测试地图名, 层列表=[1, 2])
    
    # 测试2：只显示房间
    print("\n==== 测试2：只显示房间 ====")
    图表列表 = 绘制多层地图(测试地图名, 
                      层列表=[1, 2],
                      展示网格=False,
                      展示建筑区=False,
                      展示建筑区名=False,
                      展示房间=True)
    
    # 测试3：只显示建筑区
    print("\n==== 测试3：只显示建筑区 ====")
    图表列表 = 绘制多层地图(测试地图名,
                      层列表=[1, 2],
                      展示网格=False,
                      展示建筑区=True,
                      展示建筑区名=True,
                      展示房间=False)
    
    # 测试4：显示网格和房间
    print("\n==== 测试4：显示网格和房间 ====")
    图表列表 = 绘制多层地图(测试地图名,
                      层列表=[1, 2],
                      展示网格=True,
                      展示建筑区=False,
                      展示建筑区名=False,
                      展示房间=True)
    




