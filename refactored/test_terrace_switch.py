# Test script to verify terrace switch functionality
from src.generators.dwellings_core.house import generate_house_export

# Test 1: Default case - should generate terraces
def test_default_terrace():
    print("=== Test 1: Default case (with terrace) ===")
    # Create a simple 20x20 area
    area_cells = [(x, y) for x in range(20) for y in range(20)]
    
    result = generate_house_export(
        seed=42,
        tags=[],
        area_cells=area_cells,
        n_floors=2
    )
    
    # Check if terrace_cells exist in upper floors
    has_terrace = False
    for floor in result['floors']:
        if floor['floor_index'] > 0 and floor['terrace_cells']:
            has_terrace = True
            print(f"Floor {floor['floor_index']}: {len(floor['terrace_cells'])} terrace cells")
            print(f"Floor {floor['floor_index']}: {len(floor['rooms'])} total rooms")
    
    print(f"Result: {'✓ Terraces generated' if has_terrace else '✗ No terraces'}")
    return has_terrace

# Test 2: no_terrace tag - should NOT generate terraces
def test_no_terrace():
    print("\n=== Test 2: no_terrace tag (without terrace) ===")
    # Create a simple 20x20 area
    area_cells = [(x, y) for x in range(20) for y in range(20)]
    
    result = generate_house_export(
        seed=42,
        tags=["no_terrace"],
        area_cells=area_cells,
        n_floors=2
    )
    
    # Check if terrace_cells exist in upper floors
    has_terrace = False
    for floor in result['floors']:
        if floor['floor_index'] > 0 and floor['terrace_cells']:
            has_terrace = True
            print(f"Floor {floor['floor_index']}: {len(floor['terrace_cells'])} terrace cells")
            print(f"Floor {floor['floor_index']}: {len(floor['rooms'])} total rooms")
    
    print(f"Result: {'✓ No terraces (correct)' if not has_terrace else '✗ Terraces generated (wrong)'}")
    return not has_terrace

# Run tests
if __name__ == "__main__":
    test1_result = test_default_terrace()
    test2_result = test_no_terrace()
    
    print("\n" + "="*50)
    if test1_result and test2_result:
        print("🎉 All tests passed! Terrace switch functionality is working correctly.")
    else:
        print("❌ Some tests failed!")
