#  修改 dwelling 生成器的核心逻辑

##  1. 修改 shape.py 中的 contour2area 实现

**文件**: `refactored/src/generators/dwellings_core/shape.py`

**修改内容**:
- 删除从第 246 行开始的所有内容，包括当前的 `contour2area` 函数和相关辅助函数
- 在 `find_edge_by_start` 函数后添加新的 `contour2area` 实现，使用从外部进行 flood fill 的方法
- 新实现不依赖 clockwise 判断，更稳定可靠

##  2. 拆分 footprint.py 中的 notch 逻辑

**文件**: `refactored/src/generators/dwellings_core/footprint.py`

**修改内容**:
- 在 `_missing_dirs` 函数后添加两个新函数：
  - `get_notch_cut`：生成 notch 要移除的 cells
  - `apply_notch_if_valid`：验证并应用 notch
- 修改 `irregularize_area_by_notches` 函数，将原来的 notch 生成和应用逻辑替换为调用这两个新函数

##  3. 在 house.py 中添加轮廓回填归一化

**文件**: `refactored/src/generators/dwellings_core/house.py`

**修改内容**:
- 在 `irregularize_area_by_notches` 调用后添加轮廓回填归一化逻辑：
  - 导入 `outline_edges` 和 `contour2area` 函数
  - 使用 `outline_edges` 生成轮廓
  - 使用 `contour2area` 从轮廓生成新的 area cells，确保形状稳定

##  验收标准

1. **shape.py 修改后**：运行测试代码，确保矩形区域的轮廓转换正确
2. **footprint.py 修改后**：使用不同 seed 多次运行，确认 footprint 不再总是矩形
3. **house.py 修改后**：生成的 dwelling 形状更加稳定，便于后续处理

这些修改将使 dwelling 生成器的核心逻辑更加稳定和可靠，特别是在处理不规则和复杂轮廓时。