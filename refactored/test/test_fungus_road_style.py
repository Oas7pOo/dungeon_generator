import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from generate_fungus_road_map import generate


def test_fungus_style_fixed_map_is_connected_and_weighted():
    with tempfile.TemporaryDirectory() as directory:
        result = generate(directory, render=False)
        assert result["roads"] >= 9
        assert result["cycles"] >= 1
        assert result["components"] == 1
        assert all(5 <= width <= 10 for width in result["widths"])
