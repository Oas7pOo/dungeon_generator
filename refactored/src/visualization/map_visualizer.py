import os
import json
import platform
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.font_manager import FontProperties
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import ListedColormap

from ..db.database import DatabaseManager


class MapVisualizer:
    """
    地图可视化管理器（高性能版）
    - 房间/墙/门/内墙：使用 imshow + 掩码（mask）渲染，避免逐格 Rectangle
    - 提供 5 个渲染层：building_areas / room_grid / room_vector / item_grid / item_vector
    - 允许用户控制：每层是否显示 + 叠放顺序
    """

    # 5 个层的 key（你在参数里用这些字符串）
    L_BUILDING = "building_areas"
    L_ROOM_GRID = "room_grid"
    L_ROOM_VECTOR = "room_vector"
    L_ITEM_GRID = "item_grid"
    L_ITEM_VECTOR = "item_vector"

    ALL_LAYERS = (L_BUILDING, L_ROOM_GRID, L_ROOM_VECTOR, L_ITEM_GRID, L_ITEM_VECTOR)

    # 文字标签统一画在最高层（高于所有掩码/多边形），避免被像素层压住
    TOP_TEXT_ZORDER = 1000

    # 多层"分层视图"PDF 的页定义（save_multi_view_pdf 使用）
    # 三种展示层按"堆叠"设计：矢量叠在建筑区上，像素叠在矢量上（底 -> 顶）
    MULTI_VIEW_ORDER = [
        ("建筑区展示层",
         dict(show_building_areas=True, show_room_grid=False, show_room_vector=False,
              show_item_grid=False, show_item_vector=False,
              layer_order=[L_BUILDING])),
        ("矢量展示层",
         dict(show_building_areas=True, show_room_grid=False, show_room_vector=True,
              show_item_grid=False, show_item_vector=False,
              layer_order=[L_BUILDING, L_ROOM_VECTOR])),
        ("像素展示层",
         dict(show_building_areas=True, show_room_grid=True, show_room_vector=True,
              show_item_grid=True, show_item_vector=False,
              layer_order=[L_BUILDING, L_ROOM_VECTOR, L_ROOM_GRID, L_ITEM_GRID])),
    ]

    def __init__(self, db_manager=None):
        self.db_manager = db_manager or DatabaseManager()
        self._setup_chinese_font()

    # ---------------- font ----------------

    def _setup_chinese_font(self):
        plt.rcParams["font.sans-serif"] = [
            "SimHei", "Microsoft YaHei", "SimSun", "KaiTi", "FangSong",
            "Arial Unicode MS", "DejaVu Sans"
        ]
        plt.rcParams["axes.unicode_minus"] = False

        system = platform.system()
        if system == "Windows":
            plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"] + plt.rcParams["font.sans-serif"]
        elif system == "Darwin":
            plt.rcParams["font.sans-serif"] = ["PingFang SC", "STHeiti"] + plt.rcParams["font.sans-serif"]
        elif system == "Linux":
            plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "WenQuanYi Zen Hei"] + plt.rcParams["font.sans-serif"]

        try:
            self.chinese_font = FontProperties(family=plt.rcParams["font.sans-serif"][0])
        except Exception as e:
            print(f"警告: 无法加载默认中文字体，文本可能无法正确显示: {e}")
            self.chinese_font = None

    # ---------------- db fetch ----------------

    def get_map_info(self, map_id):
        """
        通过 map_id 获取地图信息
        """
        result = self.db_manager.fetch_one(
            "SELECT name, width, height FROM map WHERE id = ?",
            (int(map_id),)
        )
        if result:
            return {"name": result["name"], "width": int(result["width"]), "height": int(result["height"])}
        return None

    def _fetch_rooms(self, map_id, layer_index):
        query = """
        SELECT name, tiles_json, geom_json
        FROM room
        WHERE map_id = ? AND layer_start <= ? AND layer_end >= ?
        """
        rows = self.db_manager.fetch_all(query, (int(map_id), layer_index, layer_index))

        rooms = []
        for row in rows:
            name = row["name"]
            tiles_json = row["tiles_json"]
            geom_json = row.get("geom_json")

            try:
                tiles = json.loads(tiles_json) if tiles_json else {}
            except Exception:
                tiles = {}

            try:
                vec = json.loads(geom_json) if geom_json else {}
            except Exception:
                vec = {}

            rooms.append({
                "name": name,
                "wall": tiles.get("wall", []),
                "space": tiles.get("space", []),
                "inner_wall": tiles.get("inner_wall", []),
                "door": [],           # 门仍从 item 表读
                "vector": vec,        # ✅ 关键
            })
        return rooms


    def _fetch_door_tiles(self, map_id, layer_index):
        query = """
        SELECT tiles_json
        FROM item
        WHERE map_id = ? AND item_type = 'door'
        AND layer_start <= ? AND layer_end >= ?
        """
        rows = self.db_manager.fetch_all(query, (int(map_id), layer_index, layer_index))

        door_tiles = []
        for row in rows:
            try:
                tj = json.loads(row["tiles_json"]) if row["tiles_json"] else {}
                wt = tj.get("wall_tiles", [])
                if isinstance(wt, list):
                    door_tiles.extend(wt)
            except Exception as e:
                print(f"解析 door tiles_json 出错: {e}")
        return door_tiles


    def _fetch_building_areas(self, map_id, layer_index):
        query = """
        SELECT name, geom_type, center_x, center_y, radius, geom_json, size_json
        FROM building_area
        WHERE map_id = ? AND layer_start <= ? AND layer_end >= ?
        """
        rows = self.db_manager.fetch_all(query, (int(map_id), layer_index, layer_index))

        building_areas = []
        for row in rows:
            name = row["name"]
            geom_type = row["geom_type"]
            cx, cy = row.get("center_x"), row.get("center_y")
            radius = row.get("radius")
            geom_json = row.get("geom_json")
            size_json = row.get("size_json")

            if geom_type == "circle":
                position = [float(cx or 0.0), float(cy or 0.0)]
                corner_str = str(float(radius or 0.0))  # 给 _draw_building_areas 当半径
                building_areas.append((name, json.dumps(position), "circle", corner_str, "[]"))
            elif geom_type == "rectangle" and not geom_json:
                # 轴对齐矩形不存 geom_json：用 center + size_json 重建 corners
                try:
                    size = json.loads(size_json) if size_json else {}
                    w = float(size.get("width") or 0)
                    h = float(size.get("height") or 0)
                    if w > 0 and h > 0 and cx is not None and cy is not None:
                        hw, hh = w / 2.0, h / 2.0
                        fx, fy = float(cx), float(cy)
                        corner_str = json.dumps([
                            [fx - hw, fy - hh],
                            [fx + hw, fy - hh],
                            [fx + hw, fy + hh],
                            [fx - hw, fy + hh],
                        ])
                    else:
                        corner_str = "[]"
                except Exception:
                    corner_str = "[]"
                building_areas.append((name, json.dumps([0, 0]), "rectangle", corner_str, "[]"))
            else:
                # geom_json 里就是 corners: [[x,y],...]
                corner_str = geom_json or "[]"
                building_areas.append((name, json.dumps([0, 0]), geom_type, corner_str, "[]"))

        return building_areas


    # ---------------- masks + imshow helpers ----------------

    @staticmethod
    def _stamp_mask(mask, pts, W, H):
        """
        mask: HxW uint8
        pts: [[x,y], ...]
        """
        if not pts:
            return
        try:
            import numpy as np
        except Exception as e:
            raise RuntimeError("需要 numpy 才能使用 imshow 掩码渲染。请先 pip install numpy") from e

        arr = np.asarray(pts, dtype=np.int32)
        if arr.ndim != 2 or arr.shape[1] != 2:
            return

        xs = arr[:, 0]
        ys = arr[:, 1]
        m = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
        xs = xs[m]
        ys = ys[m]
        mask[ys, xs] = 1

    @staticmethod
    def _imshow_mask(ax, mask, rgba, W, H, zorder):
        """
        用两色 ListedColormap 显示 mask：
        0 -> 透明
        1 -> rgba
        """
        cmap = ListedColormap([(0, 0, 0, 0), rgba])
        ax.imshow(
            mask,
            origin="lower",
            interpolation="nearest",
            cmap=cmap,
            extent=(0, W, 0, H),
            zorder=zorder
        )

    # ---------------- draw pieces ----------------

    def _draw_building_areas(self, ax, building_areas, area_color, show_area_names, zorder):
        # 轮廓线宽按地图尺度自适应（1200x1200 下 1pt 几乎不可见）
        try:
            x0, x1 = ax.get_xlim()
            lw = max(1.0, (max(abs(x0), abs(x1)) / 400.0))
        except Exception:
            lw = 1.0

        for area in building_areas:
            name, position_str, area_type, corner_str, size_str = area

            try:
                position = json.loads(position_str)
            except Exception:
                position = eval(position_str) if position_str else None

            if area_type == "circle":
                try:
                    radius = float(corner_str)
                    if position and radius > 0:
                        circle = patches.Circle(
                            position, radius,
                            facecolor=area_color,
                            edgecolor="red",
                            linewidth=lw,
                            zorder=zorder
                        )
                        ax.add_patch(circle)
                        if show_area_names:
                            ax.text(
                                position[0], position[1], name,
                                ha="center", va="center",
                                fontsize=8,
                                zorder=self.TOP_TEXT_ZORDER,
                                **({"fontproperties": self.chinese_font} if self.chinese_font else {})
                            )
                except Exception as e:
                    print(f"绘制圆形建筑区 '{name}' 时出错: {e}")
            else:
                try:
                    corners = json.loads(corner_str)
                    if corners and isinstance(corners, list) and len(corners) >= 3:
                        polygon = patches.Polygon(
                            corners,
                            facecolor=area_color,
                            edgecolor="red",
                            linewidth=lw,
                            zorder=zorder
                        )
                        ax.add_patch(polygon)

                        if show_area_names:
                            cx = sum(p[0] for p in corners) / len(corners)
                            cy = sum(p[1] for p in corners) / len(corners)
                            ax.text(
                                cx, cy, name,
                                ha="center", va="center",
                                fontsize=8,
                                zorder=self.TOP_TEXT_ZORDER,
                                **({"fontproperties": self.chinese_font} if self.chinese_font else {})
                            )
                except Exception as e:
                    print(f"绘制建筑区 '{name}' 时出错: {e}")

    def _draw_room_vectors(self, ax, rooms, room_vector_color, show_room_names, zorder):
        for r in rooms:
            name = r["name"]
            vec = r["vector"] or {}
            room_type = vec.get("type", "unknown")
            center = vec.get("center", [0, 0])

            try:
                if room_type == "road" and isinstance(vec.get("path"), list) and len(vec["path"]) >= 2:
                    # 道路矢量：沿拐点（waypoints）画简洁折线（跟着路的形状，非 bbox 矩形）
                    px = [p[0] + 0.5 for p in vec["path"]]
                    py = [p[1] + 0.5 for p in vec["path"]]
                    lw = float(vec.get("width", 5)) * 0.8
                    ax.plot(px, py, color="blue", linewidth=lw, solid_capstyle="round",
                            zorder=zorder)
                    center = vec.get("center", [px[len(px) // 2], py[len(py) // 2]])

                elif room_type == "circle":
                    radius = vec.get("radius", 0)
                    if radius > 0:
                        circle = patches.Circle(
                            center,
                            radius=radius,
                            facecolor=room_vector_color,
                            edgecolor="blue",
                            linewidth=1.0,
                            zorder=zorder
                        )
                        ax.add_patch(circle)

                elif "corners" in vec and isinstance(vec["corners"], list) and len(vec["corners"]) >= 3:
                    corners = vec["corners"]
                    poly = patches.Polygon(
                        corners,
                        closed=True,
                        facecolor=room_vector_color,
                        edgecolor="blue",
                        linewidth=1.0,
                        zorder=zorder
                    )
                    ax.add_patch(poly)

                    if "center" not in vec:
                        center = [
                            sum(p[0] for p in corners) / len(corners),
                            sum(p[1] for p in corners) / len(corners),
                        ]
                elif "bbox" in vec and isinstance(vec["bbox"], list) and len(vec["bbox"]) == 4:
                    x1, y1, x2, y2 = vec["bbox"]
                    rect = patches.Rectangle(
                        (x1, y1), x2 - x1, y2 - y1,
                        facecolor=room_vector_color,
                        edgecolor="blue",
                        linewidth=1.0,
                        zorder=zorder
                    )
                    ax.add_patch(rect)
                    if "center" in vec:
                        center = vec["center"]
                    else:
                        center = [(x1 + x2) / 2.0, (y1 + y2) / 2.0]

                if show_room_names:
                    ax.text(
                        center[0], center[1], name,
                        ha="center", va="center",
                        color="blue",
                        fontsize=8,
                        zorder=self.TOP_TEXT_ZORDER,
                        bbox=dict(facecolor="white", edgecolor="none", alpha=0.7),
                        **({"fontproperties": self.chinese_font} if self.chinese_font else {})
                    )
            except Exception as e:
                print(f"绘制房间矢量 '{name}' 出错: {e}")

    def _draw_item_vectors_from_doors(self, ax, door_mask, W, H, zorder):
        """
        物品矢量层（当前用 door_mask 做示例）：
        - 用 scatter 标出门格子中心点（比画一堆 Rectangle 轻得多）
        """
        try:
            import numpy as np
        except Exception as e:
            raise RuntimeError("需要 numpy 才能使用 item_vector 渲染。请先 pip install numpy") from e

        ys, xs = (door_mask > 0).nonzero()
        if xs.size == 0:
            return

        # 点太多时可采样，避免 PDF 体积爆炸
        max_points = 20000
        if xs.size > max_points:
            idx = np.random.choice(xs.size, max_points, replace=False)
            xs = xs[idx]
            ys = ys[idx]

        ax.scatter(
            xs + 0.5, ys + 0.5,
            s=6,
            marker="x",
            linewidths=0.6,
            zorder=zorder
        )

    # ---------------- public draw API ----------------

    def draw_map(
        self,
        map_id,
        layer_index=1,
        fig_size=(10, 10),
        show_grid=True,
        show_area_names=True,
        show_room_names=True,
        # 五层可控开关
        show_building_areas=True,
        show_room_grid=True,
        show_room_vector=True,
        show_item_grid=True,
        show_item_vector=False,
        # 叠放顺序（从底到顶）
        layer_order=None,
        # 网格细分控制（避免 1000x1000 时 minor tick 太重）
        grid_major_step=10,
        grid_minor_step=None,
    ):
        """
        绘制单张地图（单层级）
        - layer_order: e.g. ["building_areas","room_vector","room_grid","item_vector","item_grid"]
        """

        map_info = self.get_map_info(map_id)
        if not map_info:
            print(f"错误: 找不到 map_id 为 {map_id} 的地图")
            return None

        W = map_info["width"]
        H = map_info["height"]

        # 默认叠放顺序：建筑区 -> 房间格子 -> 房间矢量 -> 物品格子 -> 物品矢量
        if layer_order is None:
            layer_order = [self.L_BUILDING, self.L_ROOM_GRID, self.L_ROOM_VECTOR, self.L_ITEM_GRID, self.L_ITEM_VECTOR]

        # 清洗 order：只保留合法层，并确保五层都可被放进去（你想要缺省也行）
        cleaned = []
        for k in layer_order:
            if k in self.ALL_LAYERS and k not in cleaned:
                cleaned.append(k)
        layer_order = cleaned

        enabled = {
            self.L_BUILDING: bool(show_building_areas),
            self.L_ROOM_GRID: bool(show_room_grid),
            self.L_ROOM_VECTOR: bool(show_room_vector),
            self.L_ITEM_GRID: bool(show_item_grid),
            self.L_ITEM_VECTOR: bool(show_item_vector),
        }

        # 颜色（保持你原来的风格）
        area_color = (1.0, 0.4, 0.4, 0.5)          # 建筑区 半透明红
        room_vector_color = (0.4, 0.4, 1.0, 0.3)   # 房间矢量 半透明蓝
        room_outer_wall_color = (0.3, 0.3, 0.3, 1.0)
        room_inner_color = (0.8, 0.8, 0.8, 1.0)
        room_inner_wall_color = (0.7, 0.2, 0.2, 0.8)
        door_color = (1.0, 1.0, 0.0, 1.0)          # 门/物品格子 黄

        # 图
        fig, ax = plt.subplots(figsize=fig_size)
        ax.set_xlim(0, W)
        ax.set_ylim(0, H)
        ax.set_aspect("equal")

        # 使用 map_info["name"] 作为标题
        map_name = map_info["name"]
        if self.chinese_font:
            ax.set_title(f"{map_name} - 层级 {layer_index}", fontproperties=self.chinese_font)
        else:
            ax.set_title(f"{map_name} - 层级 {layer_index}")

        # 网格
        if show_grid:
            ax.grid(True, which="major", linestyle="-", linewidth=0.5, color="#cccccc")
            ax.set_xticks(range(0, W + 1, max(1, int(grid_major_step))))
            ax.set_yticks(range(0, H + 1, max(1, int(grid_major_step))))

            if grid_minor_step is None:
                # 默认：大图不画 minor，避免 tick 数量爆炸
                grid_minor_step = 1 if max(W, H) <= 300 else 0

            if grid_minor_step and grid_minor_step > 0:
                ax.grid(True, which="minor", linestyle=":", linewidth=0.2, color="#f0f0f0")
                ax.set_xticks(range(0, W + 1, int(grid_minor_step)), minor=True)
                ax.set_yticks(range(0, H + 1, int(grid_minor_step)), minor=True)

        # 预取数据（按需）
        rooms = None
        building_areas = None

        needs_rooms = enabled[self.L_ROOM_GRID] or enabled[self.L_ROOM_VECTOR] or enabled[self.L_ITEM_GRID] or enabled[self.L_ITEM_VECTOR]
        if needs_rooms:
            rooms = self._fetch_rooms(map_id, layer_index)

        if enabled[self.L_BUILDING]:
            building_areas = self._fetch_building_areas(map_id, layer_index)

        # 掩码（按需）
        floor_mask = wall_mask = inner_wall_mask = door_mask = None
        if enabled[self.L_ROOM_GRID] or enabled[self.L_ITEM_GRID] or enabled[self.L_ITEM_VECTOR]:
            try:
                import numpy as np
            except Exception as e:
                raise RuntimeError("需要 numpy 才能使用 imshow 掩码渲染。请先 pip install numpy") from e

            floor_mask = np.zeros((H, W), dtype=np.uint8)
            wall_mask = np.zeros((H, W), dtype=np.uint8)
            inner_wall_mask = np.zeros((H, W), dtype=np.uint8)
            door_mask = np.zeros((H, W), dtype=np.uint8)

            for r in rooms or []:
                # 房间格子层需要：space/wall/inner_wall
                if enabled[self.L_ROOM_GRID]:
                    self._stamp_mask(floor_mask, r["space"], W, H)
                    self._stamp_mask(wall_mask, r["wall"], W, H)
                    self._stamp_mask(inner_wall_mask, r["inner_wall"], W, H)

                # ✅ 从 item 表把 door 的 wall_tiles 盖进 door_mask
                if enabled[self.L_ITEM_GRID] or enabled[self.L_ITEM_VECTOR]:
                    door_tiles = self._fetch_door_tiles(map_id, layer_index)
                    self._stamp_mask(door_mask, door_tiles, W, H)


        # 逐层渲染（按 layer_order 决定叠放）
        base_z = 10
        for i, layer_key in enumerate(layer_order):
            if not enabled.get(layer_key, False):
                continue
            z = base_z + i

            if layer_key == self.L_BUILDING:
                self._draw_building_areas(ax, building_areas or [], area_color, show_area_names, zorder=z)

            elif layer_key == self.L_ROOM_GRID:
                # 地板 -> 外墙 -> 内墙（同一层内固定顺序）
                self._imshow_mask(ax, floor_mask, room_inner_color, W, H, zorder=z + 0.0)
                self._imshow_mask(ax, wall_mask, room_outer_wall_color, W, H, zorder=z + 0.1)
                self._imshow_mask(ax, inner_wall_mask, room_inner_wall_color, W, H, zorder=z + 0.2)

            elif layer_key == self.L_ROOM_VECTOR:
                self._draw_room_vectors(ax, rooms or [], room_vector_color, show_room_names, zorder=z)

            elif layer_key == self.L_ITEM_GRID:
                # 门格子（物品格子层）
                self._imshow_mask(ax, door_mask, door_color, W, H, zorder=z)

            elif layer_key == self.L_ITEM_VECTOR:
                # 门矢量（物品矢量层）：用 scatter 标点
                self._draw_item_vectors_from_doors(ax, door_mask, W, H, zorder=z)

        return fig

    # ---------------- save helpers ----------------

    def save_map(self, fig, filename, formats=None, output_dir="地图输出", dpi=150, tight_bbox=False):
        """
        保存地图图像到文件
        - 默认 tight_bbox=False：避免 bbox_inches='tight' 在大量元素时巨慢
        """
        if formats is None:
            formats = ["png"]

        os.makedirs(output_dir, exist_ok=True)

        for fmt in formats:
            save_path = os.path.join(output_dir, f"{filename}.{fmt}")
            try:
                if tight_bbox:
                    fig.savefig(save_path, dpi=dpi, bbox_inches="tight", format=fmt)
                else:
                    fig.savefig(save_path, dpi=dpi, format=fmt)
                print(f"✅ 成功保存地图到: {save_path}")
            except Exception as e:
                print(f"❌ 保存地图到 {save_path} 时出错: {e}")

        plt.close(fig)

    def save_map_by_layer(
        self,
        map_id,
        layers=None,
        output_dir="地图输出",
        formats=("png", "pdf"),
        fig_size=(10, 10),
        dpi=150,
        tight_bbox=False,
        # draw_map 参数透传
        **draw_kwargs
    ):
        """
        通过 map_id 保存每层地图
        """
        if layers is None:
            max_layer = self.db_manager.fetch_one(
                "SELECT MAX(layer_end) FROM building_area WHERE map_id = ?",
                (int(map_id),)
            )
            max_layer = max_layer["layer_end"] if max_layer and max_layer["layer_end"] else 3
            layers = range(1, max_layer + 1)

        for layer in layers:
            fig = self.draw_map(map_id, layer_index=layer, fig_size=fig_size, **draw_kwargs)
            if fig:
                self.save_map(
                    fig,
                    f"map{map_id}_层{layer}",
                    formats=list(formats),
                    output_dir=output_dir,
                    dpi=dpi,
                    tight_bbox=tight_bbox
                )

    def save_multi_layer_pdf(
        self,
        map_id,
        layers=None,
        output_dir="地图输出",
        fig_size=(10, 10),
        filename=None,
        dpi=150,
        tight_bbox=False,
        # draw_map 参数透传
        **draw_kwargs
    ):
        """
        多层级合并 PDF：每页一个 layer_index
        - 注意：tight_bbox=True 会更慢，默认关
        """
        if layers is None:
            max_layer = self.db_manager.fetch_one(
                "SELECT MAX(layer_end) FROM building_area WHERE map_id = ?",
                (int(map_id),)
            )
            max_layer = max_layer["layer_end"] if max_layer and max_layer["layer_end"] else 3
            layers = range(1, max_layer + 1)

        os.makedirs(output_dir, exist_ok=True)

        safe_name = (filename or f"map{map_id}").replace(" ", "_")
        pdf_path = os.path.join(output_dir, f"{safe_name}_多层.pdf")

        with PdfPages(pdf_path) as pdf:
            for layer in layers:
                fig = self.draw_map(map_id, layer_index=layer, fig_size=fig_size, **draw_kwargs)
                if fig:
                    if tight_bbox:
                        pdf.savefig(fig, dpi=dpi, bbox_inches="tight")
                    else:
                        pdf.savefig(fig, dpi=dpi)
                    plt.close(fig)

        print(f"✅ 成功保存多层PDF到: {pdf_path}")
        return pdf_path

    def save_combined_pdf(
        self,
        map_id,
        layers=None,
        output_dir="地图输出",
        fig_size=(10, 10),
        filename=None,
        dpi=150,
        tight_bbox=False,
        page_specs=None,
        # draw_map 参数透传（作为每个 page_spec 的默认值）
        **default_draw_kwargs
    ):
        """
        组合 PDF（强力版）：
        - 你可以用 page_specs 定义“页的顺序”和“每页用哪些层、层的叠放顺序”。
        """
        if layers is None:
            max_layer = self.db_manager.fetch_one(
                "SELECT MAX(layer_end) FROM building_area WHERE map_id = ?",
                (int(map_id),)
            )
            max_layer = max_layer["layer_end"] if max_layer and max_layer["layer_end"] else 3
            layers = range(1, max_layer + 1)

        os.makedirs(output_dir, exist_ok=True)
        safe_filename = (filename or f"map{map_id}").replace(" ", "_")
        pdf_path = os.path.join(output_dir, f"{safe_filename}.pdf")

        # 默认：只输出一组“当前 draw_kwargs”的页面（每层级一页）
        if page_specs is None:
            page_specs = [
                {"title": "地图", "draw_kwargs": dict(default_draw_kwargs)}
            ]

        with PdfPages(pdf_path) as pdf:
            for spec in page_specs:
                title = spec.get("title", "地图")
                draw_kwargs = dict(default_draw_kwargs)
                draw_kwargs.update(spec.get("draw_kwargs", {}))

                for layer in layers:
                    fig = self.draw_map(map_id, layer_index=layer, fig_size=fig_size, **draw_kwargs)
                    if fig:
                        # 使用地图名称作为标题
                        map_info = self.get_map_info(map_id)
                        map_name = map_info["name"] if map_info else f"map{map_id}"
                        
                        if self.chinese_font:
                            plt.title(f"{map_name} - 层级 {layer}（{title}）", fontproperties=self.chinese_font)
                        else:
                            plt.title(f"{map_name} - 层级 {layer}（{title}）")

                        if tight_bbox:
                            pdf.savefig(fig, dpi=dpi, bbox_inches="tight")
                        else:
                            pdf.savefig(fig, dpi=dpi)

                        plt.close(fig)

        print(f"✅ 成功保存组合PDF到: {pdf_path}")
        return pdf_path

    def save_multi_view_pdf(
        self,
        map_id,
        layers=None,
        output_dir="地图输出",
        fig_size=(10, 10),
        filename=None,
        dpi=150,
        tight_bbox=False,
        views=None,
        # draw_map 参数透传（作为每页默认值，页面级配置覆盖）
        **default_draw_kwargs
    ):
        """
        多层"分层视图"PDF（每层 3 页，页序 = 层外层、视图内层）：

            层1: 第1页 建筑区展示层 / 第2页 矢量展示层 / 第3页 像素展示层
            层2: 第4页 建筑区展示层 / 第5页 矢量展示层 / 第6页 像素展示层
            ...

        三种展示层按"堆叠"设计（从底到顶）：
            - 建筑区展示层：只画建筑区轮廓
            - 矢量展示层  ：建筑区 + 房间矢量
            - 像素展示层  ：建筑区 + 矢量 + 房间像素格子 + 门格子（完整堆叠）

        输出：{filename}_多层视图.pdf（单文件多页，多层放在同一个 PDF 里）
        """
        if layers is None:
            max_layer = self.db_manager.fetch_one(
                "SELECT MAX(layer_end) FROM building_area WHERE map_id = ?",
                (int(map_id),)
            )
            max_layer = max_layer["layer_end"] if max_layer and max_layer["layer_end"] else 1
            layers = range(1, int(max_layer) + 1)

        views = views or self.MULTI_VIEW_ORDER
        os.makedirs(output_dir, exist_ok=True)
        safe_name = (filename or f"map{map_id}").replace(" ", "_")
        pdf_path = os.path.join(output_dir, f"{safe_name}_多层视图.pdf")

        with PdfPages(pdf_path) as pdf:
            for layer in layers:
                for view_title, view_kw in views:
                    draw_kwargs = dict(default_draw_kwargs)
                    draw_kwargs.update(view_kw)

                    fig = self.draw_map(int(map_id), layer_index=int(layer), fig_size=fig_size, **draw_kwargs)
                    if fig:
                        map_info = self.get_map_info(map_id)
                        map_name = map_info["name"] if map_info else f"map{map_id}"
                        if self.chinese_font:
                            plt.title(f"{map_name} - 层{layer} - {view_title}", fontproperties=self.chinese_font)
                        else:
                            plt.title(f"{map_name} - 层{layer} - {view_title}")

                        if tight_bbox:
                            pdf.savefig(fig, dpi=dpi, bbox_inches="tight")
                        else:
                            pdf.savefig(fig, dpi=dpi)
                        plt.close(fig)

        print(f"✅ 成功保存多层视图PDF到: {pdf_path}")
        return pdf_path

    def close(self):
        self.db_manager.close()
