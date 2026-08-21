#!/usr/bin/env python3
"""
Test script to verify parity consistency when running generate_house_export() twice
with the same input parameters.
"""

import json
from src.generators.dwellings_core.house import generate_house_export

# Fixed input parameters
TEST_SEED = 12345
TEST_TAGS = ["size_small", "no_terrace"]
TEST_AREA_CELLS = [
    (0, 0), (1, 0), (2, 0), (3, 0), (4, 0),
    (0, 1), (1, 1), (2, 1), (3, 1), (4, 1),
    (0, 2), (1, 2), (2, 2), (3, 2), (4, 2),
    (0, 3), (1, 3), (2, 3), (3, 3), (4, 3),
    (0, 4), (1, 4), (2, 4), (3, 4), (4, 4),
]
TEST_FLOORS = 2
TEST_FOOTPRINT_MODE = "full"

def run_test():
    print("Running test with fixed parameters...")
    print(f"Seed: {TEST_SEED}")
    print(f"Tags: {TEST_TAGS}")
    print(f"Area cells: {TEST_AREA_CELLS}")
    print(f"Floors: {TEST_FLOORS}")
    print(f"Footprint mode: {TEST_FOOTPRINT_MODE}")
    print("=" * 50)
    
    # Run twice with the same parameters
    results = []
    for i in range(2):
        print(f"Run {i+1}...")
        result = generate_house_export(
            seed=TEST_SEED,
            tags=TEST_TAGS,
            area_cells=TEST_AREA_CELLS,
            footprint_mode=TEST_FOOTPRINT_MODE,
            n_floors=TEST_FLOORS
        )
        results.append(result)
        print(f"Run {i+1} completed.")
    
    print("=" * 50)
    print("Comparing parity information...")
    
    # Extract parity from both results
    parity1 = results[0].get("_parity")
    parity2 = results[1].get("_parity")
    
    if not parity1 or not parity2:
        print("ERROR: No parity information found!")
        return False
    
    # Compare overall hash
    print(f"Overall hash - Run 1: {parity1['overall_hash']}")
    print(f"Overall hash - Run 2: {parity2['overall_hash']}")
    overall_match = parity1["overall_hash"] == parity2["overall_hash"]
    print(f"Overall hash match: {'✅' if overall_match else '❌'}")
    
    # Compare floor hashes and counts
    floor_matches = []
    for floor_i in range(len(parity1["floors"])):
        print(f"\nFloor {floor_i}:")
        f1 = parity1["floors"][floor_i]
        f2 = parity2["floors"][floor_i]
        
        # Compare hashes
        print(f"  Doors hash - Run 1: {f1['hash']['doors']}, Run 2: {f2['hash']['doors']}")
        print(f"  Windows hash - Run 1: {f1['hash']['windows']}, Run 2: {f2['hash']['windows']}")
        print(f"  Stairs hash - Run 1: {f1['hash']['stairs']}, Run 2: {f2['hash']['stairs']}")
        print(f"  Floor hash - Run 1: {f1['hash']['floor']}, Run 2: {f2['hash']['floor']}")
        
        hash_match = (f1['hash']['doors'] == f2['hash']['doors'] and
                     f1['hash']['windows'] == f2['hash']['windows'] and
                     f1['hash']['stairs'] == f2['hash']['stairs'] and
                     f1['hash']['floor'] == f2['hash']['floor'])
        print(f"  Floor hashes match: {'✅' if hash_match else '❌'}")
        
        # Compare counts
        count_keys = ["rooms_indoor", "rooms_total", "doors_open", "windows", "stairs", "inner_wall_edges"]
        count_matches = []
        for key in count_keys:
            v1 = f1[key]
            v2 = f2[key]
            match = v1 == v2
            count_matches.append(match)
            print(f"  {key} - Run 1: {v1}, Run 2: {v2} {'✅' if match else '❌'}")
        
        all_counts_match = all(count_matches)
        print(f"  All counts match: {'✅' if all_counts_match else '❌'}")
        
        floor_matches.append(hash_match and all_counts_match)
    
    all_floors_match = all(floor_matches)
    print(f"\nAll floors match: {'✅' if all_floors_match else '❌'}")
    
    # Final result
    success = overall_match and all_floors_match
    print(f"\n{'✅ TEST PASSED: Parity is consistent!' if success else '❌ TEST FAILED: Parity is inconsistent!'}")
    
    return success

if __name__ == "__main__":
    run_test()
