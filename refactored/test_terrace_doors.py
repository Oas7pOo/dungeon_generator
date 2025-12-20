# Test script to verify terrace doors generation
from src.generators.dwellings_core.house import generate_house_export

# Test that terrace doors are generated
print("=== Testing Terrace Doors Generation ===")

# Create a simple 20x20 area
area_cells = [(x, y) for x in range(20) for y in range(20)]

result = generate_house_export(
    seed=42,
    tags=[],
    area_cells=area_cells,
    n_floors=2
)

# Check for terrace doors
terrace_door_count = 0
for floor in result['floors']:
    if floor['floor_index'] > 0:  # Only check upper floors
        print(f"\nFloor {floor['floor_index']}:")
        print(f"  Area cells: {len(floor['area_cells'])}")
        print(f"  Terrace cells: {len(floor['terrace_cells'])}")
        print(f"  Total rooms: {len(floor['rooms'])}")
        print(f"  Total doors: {len(floor['doors'])}")
        
        # Count terrace doors
        floor_terrace_doors = 0
        for door in floor['doors']:
            if door.get('door_type') == 'TERRACE':
                floor_terrace_doors += 1
                terrace_door_count += 1
        print(f"  Terrace doors: {floor_terrace_doors}")

print(f"\n=== Final Results ===")
print(f"Total terrace doors across all floors: {terrace_door_count}")
print(f"Result: {'✓ Terrace doors generated' if terrace_door_count > 0 else '✗ No terrace doors'}")
