from src.generators.dwellings_core.specs import Specs

def test_specs_style_switches():
    s = Specs.from_tags(["mechanical", "organic"])
    assert s.prefer_corners is True
    assert s.prefer_walls is True

def test_specs_no_nooks_depends_on_hallways():
    s1 = Specs.from_tags([])                 # 不含 hallways
    assert s1.no_nooks is True
    s2 = Specs.from_tags(["hallways"])       # 含 hallways
    assert s2.no_nooks is False

def test_specs_window_mode():
    assert Specs.from_tags(["blank"]).window_mode == "blank"
    assert Specs.from_tags(["transparent"]).window_mode == "transparent"
    assert Specs.from_tags([]).window_mode == "normal"

def test_specs_stairs_mode():
    assert Specs.from_tags(["spiral"]).stairs_mode == "spiral"
    assert Specs.from_tags([]).stairs_mode == "stairwell"

def test_specs_terrace_flag():
    assert Specs.from_tags([]).allow_terrace is True
    assert Specs.from_tags(["no_terrace"]).allow_terrace is False

def test_specs_size_hint_and_infer():
    s = Specs.from_tags(["small"])
    assert s.infer_size_class(9999) == "small"   # hint 优先
    s2 = Specs.from_tags([])
    assert s2.infer_size_class(100) == "small"
    assert s2.infer_size_class(300) == "medium"
    assert s2.infer_size_class(1000) == "large"