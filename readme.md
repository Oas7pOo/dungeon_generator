# 地牢地图生成器

一个功能强大的地牢地图生成系统，支持多层地图生成、多种建筑区类型、房间生成和可视化展示。

## 项目结构

```
dungenMap/
├── original/              # 原始代码（未重构）
├── refactored/            # 重构后的代码
│   ├── src/               # 核心代码
│   │   ├── core/          # 核心生成逻辑
│   │   │   ├── building_area_generator.py  # 建筑区生成器
│   │   │   └── room_generator.py           # 房间生成器
│   │   ├── db/            # 数据库管理
│   │   │   └── database.py                # 数据库管理器
│   │   └── visualization/ # 可视化
│   │       └── map_visualizer.py          # 地图可视化器
│   ├── test_specific_areas.py      # 特定区域测试脚本
│   ├── test_multiple_areas.py      # 多个区域测试脚本
│   └── dungeon.db                  # SQLite数据库
├── venv/                  # Python虚拟环境
└── readme.md              # 项目说明文档
```

## 功能特性

### 地图生成
- 支持多层地牢地图生成
- 多种建筑区类型：
  - 矩形建筑区（普通和旋转）
  - 圆形建筑区（圆塔）
  - 智能碰撞检测，避免建筑区重叠
- 支持跨层建筑区
- 基于概率分布的尺寸生成（均匀分布和指数分布）

### 房间生成
- 在建筑区内自动生成房间
- 支持迷宫房间生成
- 房间墙壁自动识别
- 支持多种房间形状（矩形、圆形等）
- 房间与建筑区关联管理

### 数据管理
- 使用SQLite数据库存储地图数据
- 支持建筑区和房间属性持久化
- 多层地图信息管理
- 房间与建筑区的关联查询

### 可视化输出
- 生成PDF格式的地图文件
- 生成PNG格式的地图预览
- 分层输出，每层一个文件
- 支持多层PDF合并输出
- 可显示建筑区、房间和墙壁

## 核心组件

### 建筑区生成器

#### 1. 矩形建筑区生成器
- 生成普通矩形建筑区
- 支持旋转矩形（可指定角度范围）
- 智能放置算法，寻找最佳位置
- 支持跨层建筑区生成

#### 2. 圆塔建筑区生成器
- 生成圆形建筑区
- 可指定半径范围
- 自动避开障碍物
- 支持跨层圆塔生成

### 房间生成器
- 在建筑区内生成房间
- 支持迷宫房间生成
- 自动识别房间墙壁
- 支持多层房间生成

### 地图可视化器
- 绘制地图网格
- 显示建筑区和建筑区名称
- 显示房间和房间墙壁
- 生成PDF和PNG格式输出

## 使用方法

### 1. 基本地图生成

```python
# 导入必要的模块
from src.core.building_area_generator import RectangleBuildingAreaGenerator, CircleBuildingAreaGenerator
from src.core.room_generator import RoomGenerator
from src.db.database import DatabaseManager
from src.visualization.map_visualizer import MapVisualizer

# 初始化数据库
 db_manager = DatabaseManager()

# 插入地图数据
 db_manager.execute(
     "INSERT INTO map (name, width, height) VALUES (?, ?, ?)",
     ("测试地图", 100, 100)
 )

# 生成圆塔
 circle_gen = CircleBuildingAreaGenerator("三层塔", "测试地图", 1, db_manager)
 circle_gen.create_building_area(
     name="三层圆塔",
     layer=(1, 3),  # 跨1-3层
     radius_range=(5, 5),  # 固定半径5
     max_attempts=200
 )

# 生成旋转矩形
 rect_gen = RectangleBuildingAreaGenerator("旋转矩形", "测试地图", 1, db_manager)
 rect_gen.create_building_area(
     name="旋转矩形房间",
     layer=(1, 2),  # 1-2层
     rect_size=[(10, 15), (10, 15)],  # 固定10x15大小
     angle=[45],  # 固定45度旋转
     max_attempts=200
 )

# 生成房间
 room_gen = RoomGenerator(db_manager)
 room_gen.generate_and_save_rooms("测试地图")

# 绘制并保存地图
 visualizer = MapVisualizer(db_manager)
 for layer in [1, 2, 3]:
     fig = visualizer.draw_map("测试地图", layer_index=layer, show_grid=True, 
                              show_building_areas=True, show_area_names=True)
     visualizer.save_map(fig, f"测试地图_层{layer}", formats=['png', 'pdf'])
```

### 2. 运行测试脚本

#### 特定区域测试

```bash
cd refactored
python test_specific_areas.py
```

该脚本会生成：
- 一个三层圆塔
- 一个旋转45度的10x15矩形房间
- 15个随机建筑区
- 为所有建筑区生成房间（圆塔使用固定房间名room1）
- 为旋转矩形房间的层1生成迷宫

#### 多个区域测试

```bash
cd refactored
python test_multiple_areas.py
```

该脚本会生成：
- 多个随机分布的建筑区
- 支持指定建筑区数量、大小范围和分布类型
- 生成多层PDF和PNG输出

## 核心API

### BuildingAreaGenerator

#### create_building_area()
创建建筑区

**参数：**
- `name`: 建筑区名称
- `map_name`: 地图名称
- `layer`: 层索引或层范围元组
- `rect_size`: 矩形大小范围
- `angle`: 是否旋转或旋转角度
- `dist`: 尺寸分布类型（uniform/exponential）
- `max_attempts`: 最大尝试次数

**返回：**
成功创建的建筑区列表

### RoomGenerator

#### generate_and_save_rooms()
为所有建筑区生成并保存房间

**参数：**
- `map_name`: 地图名称

**返回：**
成功生成的房间数量

#### maze_room()
将房间转换为迷宫

**参数：**
- `room_name`: 房间名称
- `maze_width`: 迷宫通道宽度
- `complexity`: 迷宫复杂度

**返回：**
成功返回True，失败返回False

## 数据库结构

主要数据库表：

1. **map** - 存储地图基本信息
   - name: 地图名称
   - width: 地图宽度
   - height: 地图高度

2. **building_areas** - 存储建筑区信息
   - name: 建筑区名称
   - map_name: 所属地图
   - layer: 所在层
   - position: 位置坐标
   - type: 建筑区类型（rectangle/circle/polygon）
   - corner: 建筑区角点数据
   - size: 建筑区大小数据
   - area: 建筑区面积

3. **room** - 存储房间信息
   - name: 房间名称
   - map_name: 所属地图
   - layer_name: 所在层
   - building_area: 所属建筑区
   - wall_grid_list: 墙壁格子列表
   - space_grid_list: 空间格子列表
   - inner_wall_grid_list: 内部墙壁格子列表
   - room_type: 房间类型
   - vector_params: 矢量参数
   - other_params: 其他参数
   - area: 房间面积

## 地图输出

生成的地图文件会保存在以下目录：
- `refactored/specific_areas_output/` - 特定区域测试输出
- `refactored/multiple_areas_output/` - 多个区域测试输出

命名格式为：
- 特定建筑区地图_层1.pdf
- 特定建筑区地图_层1.png

## 依赖库

- numpy: 数值计算
- shapely: 地理空间几何操作
- sqlite3: 数据库操作
- matplotlib: 地图可视化
- json: JSON数据处理
- random: 随机数生成
- math: 数学计算

## 扩展开发

### 添加新的建筑区类型

1. 继承 `BuildingAreaGenerator` 基类
2. 实现 `create_building_area` 方法
3. 添加特定的形状生成逻辑
4. 实现碰撞检测

### 添加新的房间生成算法

1. 在 `RoomGenerator` 类中添加新方法
2. 实现房间生成逻辑
3. 调用 `save_room` 保存房间数据

## 注意事项

1. 确保在运行前已安装所有依赖库
2. 首次运行时，系统会自动创建数据库表
3. 生成大量建筑区时，可能需要增加尝试次数
4. 跨层建筑区会在所有指定层生成相同的建筑区
5. 房间生成会自动关联到对应的建筑区

## 许可证

MIT License

## 贡献

欢迎提交Issue和Pull Request，共同改进这个地牢地图生成器。
