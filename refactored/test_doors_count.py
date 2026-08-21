#!/usr/bin/env python3
"""
Test script to verify door generation with larger footprint (12x12)
"""

from src.generators.dwellings_core.house import generate_house_export

def make_grid(w, h):
    """Create a grid of cells with given width and height"""
    return [(x, y) for y in range(h) for x in range(w)]

def test_doors_count():
    print("Testing door generation with 12x12 grid...")
    
    # Create 12x12 grid
    area = make_grid(12, 12)
    
    # Generate house with seed 12345, medium size, no terrace
    res = generate_house_export(
        seed=12345,
        tags=["size_medium", "no_terrace"],
        area_cells=area,
        footprint_mode="full",
        n_floors=1,
    )
    
    # Get parity information
    p = res["_parity"]
    
    print(f"overall_hash: {p['overall_hash']}")
    print(f"floor0 rooms_indoor: {p['floors'][0]['rooms_indoor']}")
    print(f"floor0 doors_open: {p['floors'][0]['doors_open']}")
    print(f"floor0 door_pairs: {p['floors'][0]['door_pairs']}")
    
    # Verify expected conditions
    if p['floors'][0]['rooms_indoor'] <= 1:
        print("❌ ERROR: rooms_indoor should be greater than 1")
    else:
        print("✅ rooms_indoor is greater than 1")
    
    if p['floors'][0]['doors_open'] <= 0:
        print("❌ ERROR: doors_open should be greater than 0")
    else:
        print("✅ doors_open is greater than 0")
    
    return p['floors'][0]

if __name__ == "__main__":
    test_doors_count()
