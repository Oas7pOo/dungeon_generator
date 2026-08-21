#!/usr/bin/env python3
"""
Test script to verify basic stairwell functionality
Tests three conditions:
1. n_floors=2 且非 slab 时：每层存在一个 type=="stairwell" 的房间
2. stairs_export 在 floor0 至少 1 条（connects_to==1）
3. windows 不落在 stairwell 房间的轮廓边上
"""

import json
from src.generators.dwellings_core.house import generate_house_export


def make_grid(w, h):
    """Create a simple grid area"""
    return [(x, y) for x in range(w) for y in range(h)]


def test_stairwell_rooms():
    """Test that each floor has a stairwell room when n_floors=2 and not slab"""
    print("=== Testing stairwell rooms ===")
    
    # Test with 2 floors, non-slab
    area = make_grid(10, 10)
    
    result = generate_house_export(
        seed=12345,
        tags=[],
        area_cells=area,
        footprint_mode="full",
        n_floors=2,
    )
    
    floors = result.get("floors", [])
    assert len(floors) == 2, f"Expected 2 floors, got {len(floors)}"
    
    for floor_i, floor in enumerate(floors):
        rooms = floor.get("rooms", [])
        stairwell_rooms = [r for r in rooms if r.get("type") == "stairwell"]
        assert len(stairwell_rooms) >= 1, f"Floor {floor_i} missing stairwell room"
        print(f"✅ Floor {floor_i} has {len(stairwell_rooms)} stairwell room(s)")
    
    return True


def test_stairs_export_connects():
    """Test that stairs_export on floor0 has at least 1 entry with connects_to==1"""
    print("\n=== Testing stairs_export connections ===")
    
    area = make_grid(10, 10)
    
    result = generate_house_export(
        seed=12345,
        tags=[],
        area_cells=area,
        footprint_mode="full",
        n_floors=2,
    )
    
    floors = result.get("floors", [])
    assert len(floors) >= 1, f"Expected at least 1 floor, got {len(floors)}"
    
    # Check floor0 stairs_export
    floor0 = floors[0]
    stairs_export = floor0.get("stairs", [])
    assert len(stairs_export) >= 1, f"Floor 0 missing stairs_export entries"
    
    connects_to_1 = [s for s in stairs_export if s.get("connects_to") == 1]
    assert len(connects_to_1) >= 1, f"Floor 0 stairs_export missing connects_to==1"
    
    print(f"✅ Floor 0 has {len(connects_to_1)} stairs connecting to floor 1")
    return True


def test_windows_not_on_stairwell_edges():
    """Test that windows don't fall on stairwell room contour edges"""
    print("\n=== Testing windows not on stairwell edges ===")
    
    area = make_grid(10, 10)
    
    result = generate_house_export(
        seed=12345,
        tags=[],
        area_cells=area,
        footprint_mode="full",
        n_floors=2,
    )
    
    floors = result.get("floors", [])
    assert len(floors) >= 1, f"Expected at least 1 floor, got {len(floors)}"
    
    floor0 = floors[0]
    rooms = floor0.get("rooms", [])
    windows = floor0.get("windows", [])
    
    # Find stairwell room
    stairwell_room = None
    for room in rooms:
        if room.get("type") == "stairwell":
            stairwell_room = room
            break
    
    assert stairwell_room is not None, "No stairwell room found"
    
    # Build cell to room mapping for floor0
    room_by_cell = {}
    for room in rooms:
        rid = room.get("id")
        cells = room.get("cells", [])
        for cell in cells:
            room_by_cell[tuple(cell)] = rid
    
    # Check each window edge - should not be on stairwell room edges
    stairwell_rid = stairwell_room.get("id")
    window_count = len(windows)
    stairwell_window_count = 0
    
    for window in windows:
        edge_key = window.get("edge_key")
        if not edge_key:
            continue
        
        # Determine which room this window edge belongs to
        # For a window edge, one of the adjacent cells should be in the area
        typ, x, y = edge_key
        
        # Check adjacent cells based on edge type
        if typ == "H":
            # Horizontal edge: check (x,y-1) and (x,y) cells
            cell1 = (x, y-1)
            cell2 = (x, y)
        else:  # V
            # Vertical edge: check (x-1,y) and (x,y) cells
            cell1 = (x-1, y)
            cell2 = (x, y)
        
        # Get room IDs for adjacent cells
        rid1 = room_by_cell.get(cell1)
        rid2 = room_by_cell.get(cell2)
        
        # If either cell is in stairwell, this window is on stairwell edge
        if rid1 == stairwell_rid or rid2 == stairwell_rid:
            stairwell_window_count += 1
    
    assert stairwell_window_count == 0, f"Found {stairwell_window_count} windows on stairwell edges (expected 0)"
    print(f"✅ No windows ({window_count} total) found on stairwell edges")
    return True


def test_stairs_basic():
    """Main test function"""
    print("Testing basic stairwell functionality...")
    
    # Run all tests
    results = []
    results.append("stairwell rooms: " + ("✅" if test_stairwell_rooms() else "❌"))
    results.append("stairs_export connects: " + ("✅" if test_stairs_export_connects() else "❌"))
    results.append("windows not on stairwell edges: " + ("✅" if test_windows_not_on_stairwell_edges() else "❌"))
    
    # Print summary
    print("\n=== Summary ===")
    for result in results:
        print(result)
    
    # Count passes
    passed = sum(1 for r in results if "✅" in r)
    if passed == 3:
        print("\n✅ All tests passed! Basic stairwell functionality is working correctly.")
        return True
    else:
        print(f"\n❌ {passed}/3 tests passed. Some issues found.")
        return False


if __name__ == "__main__":
    test_stairs_basic()
