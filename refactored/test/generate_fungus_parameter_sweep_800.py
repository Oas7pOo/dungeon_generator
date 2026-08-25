#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""固定 800x800 / 60 建筑，对真菌维护强度 0.1--0.9 生成九份 PDF。"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import sys
from typing import Any, Dict, Iterable, List

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import (BuildingAreaSpec, ConnectionSpec, DatabaseManager, DecorationSpec,
                 InteriorSpec, MapGenerator, MapSpec, RoadStyleGenerator)


SEED = 20260824
MAP_W = MAP_H = 800
ROOM_COUNT = 60
STRENGTHS = tuple(round(i / 10.0, 1) for i in range(1, 10))
OUT = os.path.join(os.path.dirname(__file__), "output", "pdf", "fungus_parameter_sweep_800")


def _remove_sqlite_family(path: str) -> None:
    """覆盖测试数据库前移除 SQLite 主文件及中断运行留下的 WAL/SHM。"""
    for candidate in (path, path + "-wal", path + "-shm"):
        if os.path.exists(candidate):
            os.remove(candidate)


def _load_buildings(db: DatabaseManager, map_id: int) -> List[Dict[str, Any]]:
    rows = db.fetch_all(
        "SELECT id, name, geom_json, tiles_json, other_json FROM room "
        "WHERE map_id = ? AND room_type='building' ORDER BY id", (map_id,)) or []
    buildings = []
    for row in rows:
        geom = json.loads(row["geom_json"])
        other = json.loads(row["other_json"] or "{}")
        buildings.append({
            "id": int(row["id"]),
            "name": str(row["name"]),
            "kind": str(other.get("kind") or "rect"),
            "center": [float(geom["center"][0]), float(geom["center"][1])],
            "geom": geom,
        })
    return buildings


def _building_signature(db: DatabaseManager, map_id: int) -> str:
    rows = db.fetch_all(
        "SELECT id, name, geom_json, tiles_json, other_json FROM room "
        "WHERE map_id = ? AND room_type='building' ORDER BY id", (map_id,)) or []
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _create_fixed_base(output_dir: str) -> Dict[str, Any]:
    os.makedirs(output_dir, exist_ok=True)
    base_db = os.path.join(output_dir, "fixed_buildings_base.db")
    _remove_sqlite_family(base_db)
    random.seed(SEED)
    np.random.seed(SEED)
    db = DatabaseManager(base_db)
    try:
        spec = MapSpec(
            name="800x800真菌参数对比-固定60建筑", width=MAP_W, height=MAP_H,
            layers=1, seed=SEED,
            areas=[BuildingAreaSpec(
                generator="rectangle", count=1, name_prefix="地图",
                kwargs={"rect_size": [(MAP_W, MAP_H), (MAP_W, MAP_H)], "max_attempts": 20})],
            interior=InteriorSpec(mode="basic"), connection=ConnectionSpec(mode="none"),
            decoration=DecorationSpec(kind="none"),
        )
        map_id = int(MapGenerator(db).generate(spec)["map_id"])
        area_id = int(db.fetch_one(
            "SELECT id FROM building_area WHERE map_id = ?", (map_id,))["id"])
        db.execute("DELETE FROM item WHERE map_id = ?", (map_id,))
        db.execute("DELETE FROM room WHERE map_id = ?", (map_id,))

        gen = RoadStyleGenerator(db, map_id, seed=SEED)
        gen.map_w, gen.map_h = MAP_W, MAP_H
        kinds = ["circle", "rotated"] + ["rect"] * (ROOM_COUNT - 2)
        buildings = gen.place_buildings(
            area_id, (0, 0, MAP_W, MAP_H), n=ROOM_COUNT, gap=10,
            kinds=kinds, rng=random.Random(SEED), min_size=(5, 5), max_size=(60, 80),
            size_dist="exponential")
        if len(buildings) != ROOM_COUNT:
            raise RuntimeError(f"固定布局只放置了 {len(buildings)}/{ROOM_COUNT} 个建筑")
        geom_types = [building["geom"]["type"] for building in buildings]
        if geom_types.count("circle") != 1 or geom_types.count("rotated_rectangle") != 1:
            raise RuntimeError(f"特殊建筑数量错误: {geom_types}")
        signature = _building_signature(db, map_id)
        areas = []
        for row in db.fetch_all(
                "SELECT tiles_json FROM room WHERE map_id=? AND room_type='building' ORDER BY id",
                (map_id,)) or []:
            areas.append(len(json.loads(row["tiles_json"]).get("space") or []))
        # sum(i<j)(A_i+A_j) = (N-1) * sum(A_i)。倍率 1.0 对应生成器自动值。
        automatic_maintenance_cost = ((ROOM_COUNT - 1) * sum(areas) /
                                      math.sqrt(MAP_W * MAP_H))
        return {
            "base_db": base_db,
            "map_id": map_id,
            "area_id": area_id,
            "signature": signature,
            "automatic_maintenance_cost": automatic_maintenance_cost,
        }
    finally:
        db.close()


def generate(output_dir: str = OUT, strengths: Iterable[float] = STRENGTHS,
             max_iterations: int = 48, max_attempts: int = 110) -> List[Dict[str, Any]]:
    base = _create_fixed_base(output_dir)
    results: List[Dict[str, Any]] = []
    expected_doors = None
    for raw_strength in strengths:
        strength = round(float(raw_strength), 1)
        label = f"{strength:.1f}"
        run_dir = os.path.join(output_dir, f"strength_{label}")
        os.makedirs(run_dir, exist_ok=True)
        db_path = os.path.join(run_dir, f"fungus_strength_{label}.db")
        _remove_sqlite_family(db_path)
        shutil.copy2(base["base_db"], db_path)
        db = DatabaseManager(db_path)
        try:
            map_id = int(base["map_id"])
            if _building_signature(db, map_id) != base["signature"]:
                raise RuntimeError(f"参数 {label} 的固定建筑签名发生变化")
            db.execute("UPDATE map SET name = ? WHERE id = ?",
                       (f"800x800真菌-维护强度{label}-固定60建筑", map_id))
            buildings = _load_buildings(db, map_id)
            gen = RoadStyleGenerator(db, map_id, seed=SEED)
            gen.map_w, gen.map_h = MAP_W, MAP_H
            gen.build_obstacles(buildings, clear_zone=0)
            center = (MAP_W / 2.0, MAP_H / 2.0)
            doors = [gen.make_door(
                building, blocked=gen.obstacles, width=5,
                preferred_direction=(center[0] - building["center"][0],
                                     center[1] - building["center"][1]))
                for building in buildings]
            if any(door is None for door in doors):
                raise RuntimeError(f"参数 {label} 只有 {sum(d is not None for d in doors)}/60 个门")
            door_signature = tuple(
                (door["building_id"], tuple(door["door_mid"]), tuple(door["dir"]))
                for door in doors if door is not None)
            if expected_doors is None:
                expected_doors = door_signature
            elif door_signature != expected_doors:
                raise RuntimeError(f"参数 {label} 的门位置与其它参数不一致")

            maintenance_cost = float(base["automatic_maintenance_cost"]) * strength
            roads, area_map = gen.connect_fungus_v3(
                {"area_id": base["area_id"], "buildings": buildings}, doors,
                maintenance_cost=maintenance_cost, min_road_width=5,
                optimization_cell_size=5, max_iterations=max_iterations,
                max_attempts=max_attempts, erosion_batch_size=0,
                perimeter_weight=0.005, nucleation_interval=4,
                detour_factor=2.0, boundary_rounding=0, building_clearance=1,
                late_nucleation_rounds=12, hole_growth_steps=8,
                hole_growth_batch=0, late_min_solid_depth=3)
            gen.finalize_road_walls()
            components = gen.compute_components(buildings)
            if roads != 1 or not area_map or len(components) != 1:
                raise RuntimeError(
                    f"参数 {label} 路网失败: roads={roads}, cells={len(area_map)}, components={len(components)}")
            row = db.fetch_one(
                "SELECT geom_json, other_json FROM room WHERE map_id=? AND room_type='road'", (map_id,))
            geom = json.loads(row["geom_json"])
            meta = json.loads(row["other_json"])
            if geom.get("style") != "真菌":
                raise RuntimeError(f"参数 {label} 得到错误 style: {geom.get('style')}")
            pdf_path = gen.render_pdf(
                f"fungus_strength_{label}_800_fixed60", run_dir, fig_size=(16, 16))
            result = {
                "strength": strength,
                "maintenance_cost": maintenance_cost,
                "db": os.path.abspath(db_path),
                "pdf": os.path.abspath(pdf_path),
                "building_signature": base["signature"],
                "buildings": len(buildings),
                "circle_buildings": sum(b["geom"]["type"] == "circle" for b in buildings),
                "rotated_buildings": sum(b["geom"]["type"] == "rotated_rectangle" for b in buildings),
                "doors": len(doors),
                "components": len(components),
                "road_area": meta.get("road_area"),
                "travel_cost": meta.get("travel_cost"),
                "objective": meta.get("objective"),
                "iterations": meta.get("erosion_iterations"),
                "attempts": meta.get("erosion_attempts"),
            }
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            db.close()

    manifest = os.path.join(output_dir, "manifest.json")
    with open(manifest, "w", encoding="utf-8") as stream:
        json.dump(results, stream, ensure_ascii=False, indent=2)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strengths", nargs="*", type=float, default=list(STRENGTHS))
    parser.add_argument("--output-dir", default=OUT)
    parser.add_argument("--max-iterations", type=int, default=48)
    parser.add_argument("--max-attempts", type=int, default=110)
    args = parser.parse_args()
    results = generate(args.output_dir, args.strengths, args.max_iterations, args.max_attempts)
    print(f"生成完成: {len(results)} 份 PDF，目录 {os.path.abspath(args.output_dir)}")


if __name__ == "__main__":
    main()
