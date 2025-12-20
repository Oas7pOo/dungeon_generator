# Test script to verify no terrace doors are generated with no_terrace tag
from src.generators.dwellings_core.house import generate_house_export

# Test that no terrace doors are generated with no_terrace tag
print("=== Testing No Terrace Doors with no_terrace Tag ===")

# Create a simple 20x20 area
area_cells = [(x, y) for x in range(20) for y in range(20)]

result = generate_house_export(
    seed=42,
    tags=["no_terrace"],
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
print(f"Result: {'✓ No terrace doors (correct)' if terrace_door_count == 0 else '✗ Terrace doors generated (wrong)'}")
