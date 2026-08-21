#!/usr/bin/env python3
"""
Simple test script to verify parity consistency with basic parameters
"""

from src.generators.dwellings_core.house import generate_house_export

# Simple test parameters
seed = 12345
tags = ["size_small", "no_terrace"]
area_cells = [(x, y) for x in range(10) for y in range(10)]
n_floors = 1

# Run twice with the same parameters
print("Running generate_house_export() twice with the same parameters...")

# First run
house1 = generate_house_export(
    seed=seed,
    tags=tags,
    area_cells=area_cells,
    footprint_mode="full",  # Use full mode to avoid missing function
    n_floors=n_floors
)

# Second run
house2 = generate_house_export(
    seed=seed,
    tags=tags,
    area_cells=area_cells,
    footprint_mode="full",  # Use full mode to avoid missing function
    n_floors=n_floors
)

# Check if parity exists
if "_parity" not in house1 or "_parity" not in house2:
    print("ERROR: _parity field not found in one or both results")
    print(f"House 1 keys: {list(house1.keys())}")
    print(f"House 2 keys: {list(house2.keys())}")
    exit(1)

# Compare parity information
parity1 = house1["_parity"]
parity2 = house2["_parity"]

print(f"\nOverall hash - Run 1: {parity1['overall_hash']}")
print(f"Overall hash - Run 2: {parity2['overall_hash']}")

if parity1["overall_hash"] == parity2["overall_hash"]:
    print("✅ Overall hash matches!")
else:
    print("❌ Overall hash mismatch!")

# Compare floor-level information
print(f"\nComparing {len(parity1['floors'])} floors...")
all_floors_match = True

for i in range(len(parity1['floors'])):
    floor1 = parity1['floors'][i]
    floor2 = parity2['floors'][i]
    
    print(f"\nFloor {i}:")
    print(f"  Rooms indoor: {floor1['rooms_indoor']} vs {floor2['rooms_indoor']} - {'✅' if floor1['rooms_indoor'] == floor2['rooms_indoor'] else '❌'}")
    print(f"  Rooms total: {floor1['rooms_total']} vs {floor2['rooms_total']} - {'✅' if floor1['rooms_total'] == floor2['rooms_total'] else '❌'}")
    print(f"  Doors open: {floor1['doors_open']} vs {floor2['doors_open']} - {'✅' if floor1['doors_open'] == floor2['doors_open'] else '❌'}")
    print(f"  Inner wall edges: {floor1['inner_wall_edges']} vs {floor2['inner_wall_edges']} - {'✅' if floor1['inner_wall_edges'] == floor2['inner_wall_edges'] else '❌'}")
    print(f"  Floor hash: {floor1['hash']['floor']} vs {floor2['hash']['floor']} - {'✅' if floor1['hash']['floor'] == floor2['hash']['floor'] else '❌'}")
    
    # Check all counts
    counts_match = all(
        floor1[k] == floor2[k] for k in ['rooms_indoor', 'rooms_total', 'doors_open', 'windows', 'stairs', 'inner_wall_edges']
    )
    
    # Check all hashes
    hashes_match = all(
        floor1['hash'][k] == floor2['hash'][k] for k in ['doors', 'windows', 'stairs', 'floor']
    )
    
    if counts_match and hashes_match:
        print(f"  ✅ Floor {i} matches completely!")
    else:
        print(f"  ❌ Floor {i} mismatch!")
        all_floors_match = False

if all_floors_match:
    print("\n✅ ALL CHECKS PASSED: Parity is consistent!")
    print("   Same input produces same output with identical parity hashes and counts.")
else:
    print("\n❌ SOME CHECKS FAILED: Parity is inconsistent!")
