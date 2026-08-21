#!/usr/bin/env python3
"""
Test script to verify parity implementation works correctly
"""
import json
from src.generators.dwellings_core.house import generate_house_export

# Test with a simple area
area_cells = [(x, y) for x in range(10) for y in range(10)]

# Generate house with parity
house_export = generate_house_export(
    seed=12345,
    tags=["size_small"],
    area_cells=area_cells,
    n_floors=2,
)

# Print parity information if it exists
if "_parity" in house_export:
    print("=== Parity Information ===")
    parity = house_export["_parity"]
    print(f"Seed: {parity['seed']}")
    print(f"Tags: {parity['tags']}")
    print(f"Overall Hash: {parity['overall_hash']}")
    print(f"\nFloors: {len(parity['floors'])}")
    
    for i, floor in enumerate(parity['floors']):
        print(f"\n--- Floor {i} ---")
        print(f"Rooms (indoor/total): {floor['rooms_indoor']}/{floor['rooms_total']}")
        print(f"Doors (candidates/open): {floor['door_pairs']}/{floor['doors_open']}")
        print(f"Windows: {floor['windows']}")
        print(f"Stairs: {floor['stairs']}")
        print(f"Inner Walls: {floor['inner_wall_edges']} edges in {floor['inner_wall_chains']} chains")
        print(f"Hashes: door={floor['hash']['doors']}, window={floor['hash']['windows']}, stair={floor['hash']['stairs']}, floor={floor['hash']['floor']}")
else:
    print("ERROR: _parity field not found in house_export")
    print(f"Available keys: {list(house_export.keys())}")

# Test consistency by generating twice and comparing hashes
print("\n=== Consistency Test ===")
house_export2 = generate_house_export(
    seed=12345,
    tags=["size_small"],
    area_cells=area_cells,
    n_floors=2,
)

if house_export["_parity"]["overall_hash"] == house_export2["_parity"]["overall_hash"]:
    print("✅ Consistency test passed: Same seed produces same overall hash")
    print(f"   Hash: {house_export['_parity']['overall_hash']}")
else:
    print("❌ Consistency test failed: Same seed produces different hashes")
    print(f"   Hash 1: {house_export['_parity']['overall_hash']}")
    print(f"   Hash 2: {house_export2['_parity']['overall_hash']}")
