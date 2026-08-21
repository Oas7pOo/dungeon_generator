# Implementation Plan: Add _parity field to generate_house_export()

## Step 1: Add necessary imports to house.py
- Add `import hashlib` and `import json` to the existing import section

## Step 2: Implement utility functions in house.py
Add these three functions before `validate_plan_export()`:
- `_sha1_json(obj: Any) -> str`: Generates a stable sha1 hash for an object
- `_sig_doors(doors_export: List[Dict[str, Any]]) -> List[Tuple]`: Generates stable signatures for doors
- `_sig_edge_keys(edge_keys: List[Any]) -> List[Tuple]`: Generates stable signatures for windows/stairs

## Step 3: Create parity container in generate_house_export()
- Add a `parity` dictionary at the beginning of the function, right after `floors = []`
- Include seed, tags, and floors list in the parity container

## Step 4: Add floor parity after each floor generation
- Insert parity recording code before `validate_plan_export(plan_export)`
- Record various statistics for each floor (area_cells, terrace_cells, rooms, doors, windows, stairs, inner walls)
- Generate stable hashes for doors, windows, stairs, and the entire floor
- Append floor parity to the parity container

## Step 5: Add overall hash and return _parity
- Calculate `overall_hash` by combining all floor hashes
- Add `_parity` field to the returned dictionary

## Step 6: Print or save parity in dwellings_house_generator.py
- After calling `generate_house_export()`, extract and print the parity information
- Remove `_parity` from house_export before writing to DB

This implementation will add the requested _parity field without changing the core generation logic, allowing for easy comparison between different runs.