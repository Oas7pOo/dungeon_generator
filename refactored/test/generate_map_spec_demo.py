#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 0 重构演示：用 MapSpec / MapGenerator 生成主题地图并输出 PDF/PNG。

输出目录：refactored/test/output/map_spec_demo/
- 每个 preset 一张地图（独立 demo.db）
- 每个 preset 每层输出 1 个 PDF + 1 个 PNG
- 控制台打印每个 preset 的配方（spec）与生成统计
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import DatabaseManager, MapGenerator, MapVisualizer, PRESETS

OUT = os.path.join(os.path.dirname(__file__), "output", "map_spec_demo")
SEED = 20251220


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    db_path = os.path.join(OUT, "demo.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    db = DatabaseManager(db_path)
    gen = MapGenerator(db)
    vis = MapVisualizer(db)

    for key, factory in PRESETS.items():
        spec = factory(seed=SEED)
        print("=" * 60)
        print(f"[{key}] 需求设定（配方）：")
        print(spec.to_json())
        print(f"[{key}] 生成统计：")
        res = gen.generate(spec)
        print(res)
        if res.get("warnings"):
            print(f"[{key}] 警告（规划中功能，已跳过）：")
            for w in res["warnings"]:
                print("   -", w)

        # 每层输出 PDF + PNG
        for L in range(1, int(spec.layers) + 1):
            fig = vis.draw_map(
                res["map_id"],
                layer_index=L,
                fig_size=(12, 12),
                show_grid=True,
                show_building_areas=True,
                show_area_names=True,
                show_room_names=True,
            )
            if fig is not None:
                vis.save_map(fig, f"{key}_seed{SEED}_层{L}", formats=["pdf", "png"], output_dir=OUT)

    db.close()
    print("=" * 60)
    print("DONE -> 输出目录:", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
