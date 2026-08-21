#!/usr/bin/env python3
"""
Test script to verify RNG outputs the expected Park-Miller sequence
"""

from src.generators.dwellings_core.rng import RNG

# Test RNG with seed 1
def test_rng_output():
    print("Testing RNG with seed=1...")
    r = RNG(1)
    
    # Generate first 5 random values
    outputs = [r.random() for _ in range(5)]
    
    print("RNG output:")
    for i, val in enumerate(outputs):
        print(f"  {i+1}: {val}")
    
    # Expected values from Dwellings.js Park-Miller sequence
    expected = [
        2.2477936010098986e-05,
        0.08503244914348818,
        0.6013526053174179,
        0.8916112770753034,
        0.9679557019695433
    ]
    
    print("\nExpected values:")
    for i, val in enumerate(expected):
        print(f"  {i+1}: {val}")
    
    # Check if values are close enough (allowing for floating point precision differences)
    print("\nComparison:")
    all_close = True
    for i, (actual, exp) in enumerate(zip(outputs, expected)):
        diff = abs(actual - exp)
        is_close = diff < 1e-10  # Allow small floating point differences
        status = "✅" if is_close else "❌"
        print(f"  {i+1}: {status} (diff: {diff})")
        if not is_close:
            all_close = False
    
    if all_close:
        print("\n✅ All values match expected Park-Miller sequence!")
        return True
    else:
        print("\n❌ Some values don't match expected Park-Miller sequence!")
        return False

if __name__ == "__main__":
    test_rng_output()
