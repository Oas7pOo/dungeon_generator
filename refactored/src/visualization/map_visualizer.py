import os
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.colors as mcolors
from matplotlib.font_manager import FontProperties
from ..db.database import DatabaseManager

class MapVisualizer:
    """
    地图可视化管理器，负责生成和保存地图图像
    """
    
    def __init__(self, db_manager=None):
        """
        初始化地图可视化管理器
        
        Args:
            db_manager: 数据库管理器实例
        """
        self.db_manager = db_manager or DatabaseManager()
        self._setup_chinese_font()
    
    def _setup_chinese_font(self):
        """
        设置中文字体支持
        """
        # 设置中文字体
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun', 'KaiTi', 'FangSong', 'Arial Unicode MS', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题
        
        # 根据操作系统选择合适的中文字体
        import platform
        system = platform.system()
        if system == 'Windows':
            plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei'] + plt.rcParams['font.sans-serif']
        elif system == 'Darwin':  # macOS
            plt.rcParams['font.sans-serif'] = ['PingFang SC', 'STHeiti'] + plt.rcParams['font.sans-serif']
        elif system == 'Linux':
            plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'WenQuanYi Zen Hei'] + plt.rcParams['font.sans-serif']
        
        # 创建一个通用的中文字体对象
        try:
            self.chinese_font = FontProperties(family=plt.rcParams['font.sans-serif'][0])
        except Exception as e:
            print(f"警告: 无法加载默认中文字体，文本可能无法正确显示: {e}")
            self.chinese_font = None
    
    def get_map_info(self, map_name):
        """
        获取地图基本信息
        
        Args:
            map_name: 地图名称
            
        Returns:
            地图信息字典或None
        """
        result = self.db_manager.fetch_one(
            "SELECT name, width, height FROM map WHERE name = ?",
            (map_name,)
        )
        
        if result:
            return {
                "name": result[0],
                "width": result[1],
                "height": result[2]
            }
        return None
    
    def draw_map(self, map_name, layer_index=1, show_grid=True, show_building_areas=True, 
                 show_area_names=True, show_rooms=True, fig_size=(10, 10)):
        """
        绘制地牢地图及其建筑区和房间
        
        Args:
            map_name: 要绘制的地图名称
            layer_index: 要显示的层级，默认为1
            show_grid: 是否显示网格线
            show_building_areas: 是否显示建筑区
            show_area_names: 是否显示建筑区名称
            show_rooms: 是否显示房间
            fig_size: 图像尺寸，默认为(10, 10)英寸
            
        Returns:
            matplotlib图表对象
        """
        # 获取地图信息
        map_info = self.get_map_info(map_name)
        if not map_info:
            print(f"错误: 找不到名为 '{map_name}' 的地图")
            return None
        
        map_width = map_info["width"]
        map_height = map_info["height"]
        
        # 设置颜色常量
        area_color = (1.0, 0.4, 0.4, 0.5)  # 半透明红色
        room_vector_color = (0.4, 0.4, 1.0, 0.3)  # 半透明蓝色
        room_outer_wall_color = (0.3, 0.3, 0.3, 1.0)  # 不透明深灰色
        room_inner_color = (0.8, 0.8, 0.8, 1.0)  # 不透明浅灰色
        room_inner_wall_color = (0.7, 0.2, 0.2, 0.8)  # 不透明红棕色
        
        # 创建图表
        fig, ax = plt.subplots(figsize=fig_size)
        ax.set_xlim(0, map_width)
        ax.set_ylim(0, map_height)
        ax.set_aspect('equal')  # 确保X和Y轴比例相同
        
        # 设置标题
        if self.chinese_font:
            ax.set_title(f"{map_name} - 层级 {layer_index}", fontproperties=self.chinese_font)
        else:
            ax.set_title(f"{map_name} - 层级 {layer_index}")
        
        # 显示网格
        if show_grid:
            # 主网格线（每10个单位）
            ax.grid(True, which='major', linestyle='-', linewidth=0.5, color='#cccccc')
            # 设置主网格线间隔
            ax.set_xticks(range(0, map_width + 1, 10))
            ax.set_yticks(range(0, map_height + 1, 10))
            
            # 次网格线（每个单位）
            ax.grid(True, which='minor', linestyle=':', linewidth=0.2, color='#f0f0f0')
            ax.set_xticks(range(0, map_width + 1), minor=True)
            ax.set_yticks(range(0, map_height + 1), minor=True)
        
        # 显示建筑区
        if show_building_areas:
            self._draw_building_areas(ax, map_name, layer_index, area_color, show_area_names)
        
        # 显示房间（预留）
        if show_rooms:
            self._draw_rooms(ax, map_name, layer_index, room_vector_color, 
                           room_outer_wall_color, room_inner_color, room_inner_wall_color)
        
        return fig
    
    def _draw_building_areas(self, ax, map_name, layer_index, area_color, show_area_names):
        """
        绘制建筑区
        
        Args:
            ax: matplotlib轴对象
            map_name: 地图名称
            layer_index: 层级索引
            area_color: 建筑区颜色
            show_area_names: 是否显示建筑区名称
        """
        # 获取建筑区数据，查找与指定层有交集的建筑区
        building_areas = self.db_manager.fetch_all(
            "SELECT name, position, type, corner, size FROM building_areas WHERE map_name = ? AND min_layer <= ? AND max_layer >= ?",
            (map_name, layer_index, layer_index)
        )
        
        import json
        
        for area in building_areas:
            name, position_str, area_type, corner_str, size_str = area
            
            # 解析位置、角点和大小
            try:
                position = json.loads(position_str)
            except:
                position = eval(position_str) if position_str else None
            
            if area_type == "circle":
                # 绘制圆形建筑区
                try:
                    radius = float(corner_str)
                    if position and radius > 0:
                        circle = patches.Circle(position, radius, facecolor=area_color, 
                                              edgecolor='red', linewidth=1)
                        ax.add_patch(circle)
                        
                        # 显示建筑区名称
                        if show_area_names:
                            ax.text(position[0], position[1], name, ha='center', va='center',
                                   fontsize=8, **{"fontproperties": self.chinese_font} if self.chinese_font else {})
                except Exception as e:
                    print(f"绘制圆形建筑区 '{name}' 时出错: {e}")
            else:
                # 绘制多边形建筑区
                try:
                    corners = json.loads(corner_str)
                    if corners and isinstance(corners, list) and len(corners) >= 3:
                        polygon = patches.Polygon(corners, facecolor=area_color, 
                                                edgecolor='red', linewidth=1)
                        ax.add_patch(polygon)
                        
                        # 计算中心点用于显示名称
                        if show_area_names:
                            # 计算多边形中心点
                            x_coords = [p[0] for p in corners]
                            y_coords = [p[1] for p in corners]
                            center_x = sum(x_coords) / len(x_coords)
                            center_y = sum(y_coords) / len(y_coords)
                            
                            ax.text(center_x, center_y, name, ha='center', va='center',
                                   fontsize=8, **{"fontproperties": self.chinese_font} if self.chinese_font else {})
                except Exception as e:
                    print(f"绘制建筑区 '{name}' 时出错: {e}")
    
    def _draw_rooms(self, ax, map_name, layer_index, room_vector_color, 
                   room_outer_wall_color, room_inner_color, room_inner_wall_color):
        """
        绘制房间和墙壁，使用与重构前相同的格子系统
        
        Args:
            ax: matplotlib轴对象
            map_name: 地图名称
            layer_index: 层级索引
            room_vector_color: 房间矢量颜色
            room_outer_wall_color: 房间外墙颜色
            room_inner_color: 房间内部颜色
            room_inner_wall_color: 房间内墙颜色
        """
        # 更新查询语句，使用min_layer和max_layer字段
        query = '''
        SELECT name, wall_grid_list, space_grid_list, inner_wall_grid_list, door_grid_list, vector_params 
        FROM room 
        WHERE map_name = ? AND min_layer <= ? AND max_layer >= ?
        '''
        params = (map_name, layer_index, layer_index)
        
        rooms = self.db_manager.fetch_all(query, params)
        
        import json
        
        # 定义绘制房间内部格子的辅助函数，确保与地图格子对齐
        def draw_room_space_grids(ax, space_grid_list, color):
            for x, y in space_grid_list:
                rect = patches.Rectangle(
                    (x, y), 1, 1,  # 直接使用(x, y)作为左下角坐标，确保与地图格子对齐
                    facecolor=color,
                    edgecolor='none',
                    zorder=2
                )
                ax.add_patch(rect)
        
        # 定义绘制房间外墙格子的辅助函数，确保与地图格子对齐
        def draw_room_wall_grids(ax, wall_grid_list, color):
            for x, y in wall_grid_list:
                rect = patches.Rectangle(
                    (x, y), 1, 1,  # 直接使用(x, y)作为左下角坐标，确保与地图格子对齐
                    facecolor=color,
                    edgecolor='black',
                    linewidth=0.5,
                    zorder=3
                )
                ax.add_patch(rect)
        
        # 定义绘制房间内墙格子的辅助函数，确保与地图格子对齐
        def draw_room_inner_wall_grids(ax, inner_wall_grid_list, color):
            for x, y in inner_wall_grid_list:
                rect = patches.Rectangle(
                    (x, y), 1, 1,  # 直接使用(x, y)作为左下角坐标，确保与地图格子对齐
                    facecolor=color,
                    edgecolor='black',
                    linewidth=0.5,
                    zorder=4
                )
                ax.add_patch(rect)
        
        # 定义绘制房间门格子的辅助函数，确保与地图格子对齐
        def draw_room_door_grids(ax, door_grid_list, color):
            for x, y in door_grid_list:
                rect = patches.Rectangle(
                    (x, y), 1, 1,  # 直接使用(x, y)作为左下角坐标，确保与地图格子对齐
                    facecolor=color,
                    edgecolor='black',
                    linewidth=0.5,
                    zorder=5.5  # 门应该显示在矢量图之上
                )
                ax.add_patch(rect)
        
        # 定义绘制房间矢量轮廓的辅助函数
        def draw_room_vector_outline(ax, vector_params, color):
            import math
            room_type = vector_params.get("type", "unknown")
            center = vector_params.get("center", [0, 0])
            
            # 对于圆形
            if room_type == "circle":
                radius = vector_params.get("radius", 0)
                if radius > 0:
                    circle = patches.Circle(
                        center, radius=radius,
                        facecolor=color,
                        edgecolor='blue',
                        linewidth=1.5,
                        zorder=4.5  # 矢量图显示在房间方块上层，门的下层
                    )
                    ax.add_patch(circle)
                    return center
            
            # 对于多边形，使用corners参数
            if "corners" in vector_params and len(vector_params["corners"]) >= 3:
                corners = vector_params["corners"]
                # 如果没有center，计算中心点
                if "center" not in vector_params:
                    center = [
                        sum(p[0] for p in corners) / len(corners),
                        sum(p[1] for p in corners) / len(corners)
                    ]
                
                poly = patches.Polygon(
                    corners,
                    closed=True,
                    facecolor=color,
                    edgecolor='blue',
                    linewidth=1.5,
                    zorder=4.5  # 矢量图显示在房间方块上层，门的下层
                )
                ax.add_patch(poly)
                return center
            
            # 对于矩形
            if room_type in ["rectangle", "rotated_rectangle"]:
                width = vector_params.get("width", 0)
                height = vector_params.get("height", 0)
                angle = vector_params.get("angle", 0)
                
                # 计算矩形的四个角点
                half_w = width / 2
                half_h = height / 2
                corners = [
                    [center[0] - half_w, center[1] - half_h],
                    [center[0] + half_w, center[1] - half_h],
                    [center[0] + half_w, center[1] + half_h],
                    [center[0] - half_w, center[1] + half_h]
                ]
                
                # 如果有旋转角度，旋转角点
                if angle != 0:
                    cos_a = math.cos(angle)
                    sin_a = math.sin(angle)
                    
                    for i, point in enumerate(corners):
                        x = point[0] - center[0]
                        y = point[1] - center[1]
                        rotated_x = x * cos_a - y * sin_a
                        rotated_y = x * sin_a + y * cos_a
                        corners[i] = [center[0] + rotated_x, center[1] + rotated_y]
                
                poly = patches.Polygon(
                    corners,
                    closed=True,
                    facecolor=color,
                    edgecolor='blue',
                    linewidth=1.5,
                    zorder=4.5  # 矢量图显示在房间方块上层，门的下层
                )
                ax.add_patch(poly)
                return center
            
            return center
        
        # 定义添加房间名称的辅助函数
        def add_room_name(ax, center, name):
            ax.text(center[0], center[1], name,
                   ha='center', va='center',
                   color='blue', fontsize=8,
                   zorder=5,
                   bbox=dict(facecolor='white', edgecolor='none', alpha=0.7),
                   **{"fontproperties": self.chinese_font} if self.chinese_font else {})
        
        # 处理每个房间
        for room in rooms:
            name, wall_grid_str, space_grid_str, inner_wall_grid_str, door_grid_str, vector_params_str = room
            
            try:
                # 解析JSON数据
                wall_grid_list = json.loads(wall_grid_str)
                space_grid_list = json.loads(space_grid_str)
                inner_wall_grid_list = json.loads(inner_wall_grid_str) if inner_wall_grid_str else []
                door_grid_list = json.loads(door_grid_str) if door_grid_str else []
                vector_params = json.loads(vector_params_str)
                
                # 使用与重构前相同的颜色配置
                color_config = {
                    '内部颜色': (0.8, 0.8, 0.8, 1.0),  # 不透明浅灰色
                    '外墙颜色': (0.3, 0.3, 0.3, 1.0),  # 不透明深灰色
                    '内墙颜色': (0.7, 0.2, 0.2, 0.8),  # 不透明红棕色
                    '门颜色': (1.0, 1.0, 0.0, 1.0),    # 不透明黄色，用于门的渲染
                    '矢量颜色': (0.4, 0.4, 1.0, 0.3)   # 半透明蓝色
                }
                
                # 1. 绘制房间内部格子
                draw_room_space_grids(ax, space_grid_list, color_config['内部颜色'])
                
                # 2. 绘制房间外墙格子
                draw_room_wall_grids(ax, wall_grid_list, color_config['外墙颜色'])
                
                # 3. 绘制房间内墙格子
                draw_room_inner_wall_grids(ax, inner_wall_grid_list, color_config['内墙颜色'])
                
                # 4. 绘制房间矢量轮廓，提高zorder使其显示在房间方块上层
                center = draw_room_vector_outline(ax, vector_params, color_config['矢量颜色'])
                
                # 5. 绘制房间门格子，提高zorder使其显示在矢量图上层
                draw_room_door_grids(ax, door_grid_list, color_config['门颜色'])
                
                # 6. 添加房间名称
                add_room_name(ax, center, name)
                
            except Exception as e:
                print(f"处理房间 '{name}' 时出错: {e}")
                continue
    
    def save_map(self, fig, filename, formats=None, output_dir="地图输出"):
        """
        保存地图图像到文件
        
        Args:
            fig: matplotlib图表对象
            filename: 文件名（不含扩展名）
            formats: 要保存的格式列表，如['png', 'pdf']，默认为['png']
            output_dir: 输出目录，默认为"地图输出"
        """
        if formats is None:
            formats = ['png']
        
        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
        
        # 保存到不同格式
        for fmt in formats:
            save_path = os.path.join(output_dir, f"{filename}.{fmt}")
            try:
                fig.savefig(save_path, dpi=300, bbox_inches='tight', format=fmt)
                print(f"✅ 成功保存地图到: {save_path}")
            except Exception as e:
                print(f"❌ 保存地图到 {save_path} 时出错: {e}")
        
        # 关闭图表，释放资源
        plt.close(fig)
    
    def save_map_by_layer(self, map_name, layers=None, output_dir="地图输出", 
                         formats=['png', 'pdf'], fig_size=(10, 10)):
        """
        保存指定地图和层级的图像
        
        Args:
            map_name: 地图名称
            layers: 层级列表，默认为所有层级
            output_dir: 输出目录
            formats: 保存格式列表
            fig_size: 图像尺寸
        """
        # 获取地图的所有层级
        if layers is None:
            # 使用max_layer字段获取最大层级
            max_layer = self.db_manager.fetch_one(
                "SELECT MAX(max_layer) FROM building_areas WHERE map_name = ?",
                (map_name,)
            )
            max_layer = max_layer[0] if max_layer and max_layer[0] else 3  # 默认3层
            layers = range(1, max_layer + 1)
        
        for layer in layers:
            # 绘制地图
            fig = self.draw_map(map_name, layer, fig_size=fig_size)
            if fig:
                # 保存地图
                self.save_map(
                    fig, 
                    f"{map_name}_层{layer}", 
                    formats=formats, 
                    output_dir=output_dir
                )
    
    def save_multi_layer_pdf(self, map_name, layers=None, output_dir="地图输出", 
                           fig_size=(10, 10), show_building_areas=True, 
                           show_rooms=True, show_grid=True, show_area_names=True):
        """
        将多个层级的地图保存到一个PDF文件中，每页一层
        
        Args:
            map_name: 地图名称
            layers: 层级列表，默认为所有层级
            output_dir: 输出目录
            fig_size: 图像尺寸
            show_building_areas: 是否显示建筑区
            show_rooms: 是否显示房间
            show_grid: 是否显示网格
            show_area_names: 是否显示建筑区名称
        """
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
        
        # 获取地图的所有层级
        if layers is None:
            # 使用max_layer字段获取最大层级
            max_layer = self.db_manager.fetch_one(
                "SELECT MAX(max_layer) FROM building_areas WHERE map_name = ?",
                (map_name,)
            )
            max_layer = max_layer[0] if max_layer and max_layer[0] else 3  # 默认3层
            layers = range(1, max_layer + 1)
        
        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)
        
        # 创建PDF文件，确保文件名没有空格
        safe_map_name = map_name.replace(" ", "_")
        pdf_path = os.path.join(output_dir, f"{safe_map_name}_多层.pdf")
        with PdfPages(pdf_path) as pdf:
            for layer in layers:
                # 绘制地图
                fig = self.draw_map(
                    map_name, 
                    layer, 
                    show_grid=show_grid, 
                    show_building_areas=show_building_areas, 
                    show_area_names=show_area_names, 
                    show_rooms=show_rooms, 
                    fig_size=fig_size
                )
                if fig:
                    # 添加到PDF
                    pdf.savefig(fig, dpi=300, bbox_inches='tight')
                    # 关闭图表，释放资源
                    plt.close(fig)
        
        print(f"✅ 成功保存多层PDF到: {pdf_path}")
        return pdf_path
    
    def save_combined_pdf(self, map_name, layers=None, output_dir="地图输出", 
                           fig_size=(10, 10), show_building_areas=True, 
                           show_rooms=True, show_grid=True, show_area_names=True, filename=None):
        """
        将多个层级的地图保存到一个PDF文件中，按照物品层 -> 房间层 -> 建筑区层的顺序，每页一层
        
        Args:
            map_name: 地图名称
            layers: 层级列表，默认为所有层级
            output_dir: 输出目录
            fig_size: 图像尺寸
            show_building_areas: 是否显示建筑区
            show_rooms: 是否显示房间
            show_grid: 是否显示网格
            show_area_names: 是否显示建筑区名称
            filename: 自定义文件名，默认使用map_name
        """
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
        
        # 获取地图的所有层级
        if layers is None:
            # 使用max_layer字段获取最大层级
            max_layer = self.db_manager.fetch_one(
                "SELECT MAX(max_layer) FROM building_areas WHERE map_name = ?",
                (map_name,)
            )
            max_layer = max_layer[0] if max_layer and max_layer[0] else 3  # 默认3层
            layers = range(1, max_layer + 1)
        
        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)
        
        # 创建PDF文件，确保文件名没有空格
        if filename:
            # 使用自定义文件名
            safe_filename = filename.replace(" ", "_")
        else:
            # 使用map_name作为文件名
            safe_filename = map_name.replace(" ", "_")
        
        pdf_path = os.path.join(output_dir, f"{safe_filename}.pdf")
        
        with PdfPages(pdf_path) as pdf:
            # 1. 物品层：显示建筑区 + 房间 + 物品（门）
            print(f"\n生成物品层（带门）...")
            for layer in layers:
                # 绘制地图，显示所有元素（建筑区、房间、物品）
                fig = self.draw_map(
                    map_name, 
                    layer, 
                    show_grid=show_grid, 
                    show_building_areas=show_building_areas, 
                    show_area_names=show_area_names, 
                    show_rooms=show_rooms,  # 显示房间，包含门
                    fig_size=fig_size
                )
                if fig:
                    # 修改标题，明确显示这是物品层
                    if self.chinese_font:
                        plt.title(f"{map_name} - 层级 {layer}（物品层）", fontproperties=self.chinese_font)
                    else:
                        plt.title(f"{map_name} - 层级 {layer}（物品层）")
                    # 添加到PDF
                    pdf.savefig(fig, dpi=300, bbox_inches='tight')
                    # 关闭图表，释放资源
                    plt.close(fig)
            
            # 2. 房间层：显示建筑区 + 房间（不带物品）
            print(f"\n生成房间层（带房间区）...")
            for layer in layers:
                # 绘制地图，显示建筑区和房间，但隐藏物品（门）
                fig = self.draw_map(
                    map_name, 
                    layer, 
                    show_grid=show_grid, 
                    show_building_areas=show_building_areas, 
                    show_area_names=show_area_names, 
                    show_rooms=show_rooms,  # 显示房间，但不单独突出物品
                    fig_size=fig_size
                )
                if fig:
                    # 修改标题，明确显示这是房间层
                    if self.chinese_font:
                        plt.title(f"{map_name} - 层级 {layer}（房间层）", fontproperties=self.chinese_font)
                    else:
                        plt.title(f"{map_name} - 层级 {layer}（房间层）")
                    # 添加到PDF
                    pdf.savefig(fig, dpi=300, bbox_inches='tight')
                    # 关闭图表，释放资源
                    plt.close(fig)
            
            # 3. 建筑区层：只显示建筑区
            print(f"\n生成建筑区层（仅建筑区）...")
            for layer in layers:
                # 绘制地图，只显示建筑区
                fig = self.draw_map(
                    map_name, 
                    layer, 
                    show_grid=show_grid, 
                    show_building_areas=show_building_areas, 
                    show_area_names=show_area_names, 
                    show_rooms=False,  # 不显示房间和物品
                    fig_size=fig_size
                )
                if fig:
                    # 修改标题，明确显示这是建筑区层
                    if self.chinese_font:
                        plt.title(f"{map_name} - 层级 {layer}（建筑区层）", fontproperties=self.chinese_font)
                    else:
                        plt.title(f"{map_name} - 层级 {layer}（建筑区层）")
                    # 添加到PDF
                    pdf.savefig(fig, dpi=300, bbox_inches='tight')
                    # 关闭图表，释放资源
                    plt.close(fig)
        
        print(f"✅ 成功保存组合PDF到: {pdf_path}")
        return pdf_path

    def close(self):
        """
        关闭数据库连接
        """
        self.db_manager.close()
