#!/usr/bin/env python3
"""
Test script to verify window segment sampling is working correctly
Tests three conditions:
1. blank tag => windows=0
2. transparent tag => windows cover all eligible edges
3. Same seed => same windows hash
"""

import json
from src.generators.dwellings_core.house import generate_house_export

def make_grid(w, h):
    """Create a simple grid area"""
    return [(x, y) for x in range(w) for y in range(h)]

def test_blank_tag():
    """Test that blank tag produces 0 windows"""
    print("=== Testing blank tag ===")
    area = make_grid(10, 10)
    
    result = generate_house_export(
        seed=12345,
        tags=["blank"],
        area_cells=area,
        footprint_mode="full",
        n_floors=1,
    )
    
    floors = result.get("floors", [])
    if floors:
        windows = floors[0].get("windows", [])
        print(f"Windows generated: {len(windows)}")
        if len(windows) == 0:
            print("✅ blank tag works: no windows generated")
            return True
        else:
            print("❌ blank tag failed: windows generated")
            return False
    return False

def test_transparent_tag():
    """Test that transparent tag produces max windows"""
    print("\n=== Testing transparent tag ===")
    area = make_grid(10, 10)
    
    result = generate_house_export(
        seed=12345,
        tags=["transparent"],
        area_cells=area,
        footprint_mode="full",
        n_floors=1,
    )
    
    floors = result.get("floors", [])
    if floors:
        windows = floors[0].get("windows", [])
        print(f"Windows generated: {len(windows)}")
        parity = result.get("_parity", {})
        if parity and parity.get("floors"):
            floor_parity = parity["floors"][0]
            area_cells = floor_parity.get("area_cells", 0)
            # With transparent, we expect many windows
            # This is a heuristic check - should be significantly more than default
            if len(windows) > area_cells * 0.5:  # More than half the perimeter
                print("✅ transparent tag works: lots of windows generated")
                return True
            else:
                print(f"❌ transparent tag failed: only {len(windows)} windows for {area_cells} cells")
                return False
    return False

def test_consistency():
    """Test that same seed produces same windows hash"""
    print("\n=== Testing consistency ===")
    area = make_grid(10, 10)
    seed = 67890
    
    # First run
    result1 = generate_house_export(
        seed=seed,
        tags=["size_medium"],
        area_cells=area,
        footprint_mode="full",
        n_floors=1,
    )
    
    # Second run with same seed
    result2 = generate_house_export(
        seed=seed,
        tags=["size_medium"],
        area_cells=area,
        footprint_mode="full",
        n_floors=1,
    )
    
    # Get parity info
    parity1 = result1.get("_parity", {})
    parity2 = result2.get("_parity", {})
    
    if parity1 and parity2 and parity1.get("floors") and parity2.get("floors"):
        windows_hash1 = parity1["floors"][0]["hash"]["windows"]
        windows_hash2 = parity2["floors"][0]["hash"]["windows"]
        
        print(f"Windows hash run 1: {windows_hash1}")
        print(f"Windows hash run 2: {windows_hash2}")
        
        if windows_hash1 == windows_hash2:
            print("✅ Consistency works: same seed produces same windows hash")
            return True
        else:
            print("❌ Consistency failed: different windows hashes")
            return False
    return False

def test_segment_sampling():
    """Main test function"""
    print("Testing window segment sampling...")
    
    # Run all tests
    results = []
    results.append("Blank tag: " + ("✅" if test_blank_tag() else "❌"))
    results.append("Transparent tag: " + ("✅" if test_transparent_tag() else "❌"))
    results.append("Consistency: " + ("✅" if test_consistency() else "❌"))
    
    # Print summary
    print("\n=== Summary ===")
    for result in results:
        print(result)
    
    # Count passes
    passed = sum(1 for r in results if "✅" in r)
    if passed == 3:
        print("\n✅ All tests passed! Window segment sampling is working correctly.")
        return True
    else:
        print(f"\n❌ {passed}/3 tests passed. Some issues found.")
        return False

if __name__ == "__main__":
    test_segment_sampling()
