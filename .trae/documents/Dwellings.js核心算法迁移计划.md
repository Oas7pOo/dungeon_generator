# Dwellings.js核心算法迁移计划

## 迁移目标
将Dwellings.js的核心生成算法完整迁移到现有代码库，确保生成结果与原版一致，同时适配现有数据库结构。

## 迁移步骤

### Step 1: 统一随机数生成器
**修改文件**: `refactored/src/generators/dwellings_core/rng.py`
**目标**: 将当前xorshift32替换为与Dwellings.js一致的Park-Miller LCG
**实现要点**:
- 实现`seed = (48271*seed) % 2147483647`的LCG算法
- 保持RNG类接口不变，确保现有代码无缝迁移
- 验证相同种子生成相同结果

### Step 2: 完善住宅生成流水线
**修改文件**: `refactored/src/generators/dwellings_core/house.py`
**目标**: 实现与Dwellings.js一致的生成流水线
**实现要点**:
- 修改`generate_house_export`函数，实现完整的House生成逻辑
- 添加`rollSpiral`和`rollStairwell`功能，支持多层建筑的楼梯类型选择
- 实现Spiral楼梯的完整定义：entrance/exit/landing
- 确保每层生成Plan的顺序正确

### Step 3: 实现非矩形外形生成
**修改文件**: `refactored/src/generators/dwellings_core/shape.py`
**目标**: 实现轮廓contour+area的非矩形外形生成
**实现要点**:
- 实现`contour2area`函数，将轮廓边转换为格子占地区域
- 实现`getNotch`机制，生成建筑凹口
- 添加`cloud2gridNArea`风格的裁剪与contour/area操作
- 支持从building_area生成不规则外形

### Step 4: 实现退台逻辑
**修改文件**: `refactored/src/generators/dwellings_core/house.py`
**目标**: 实现上层缩进和露台生成
**实现要点**:
- 添加`slab`标签支持，控制是否生成退台
- 实现上层缩进逻辑：删除一个房间形成退台
- 生成terrace building_area，记录在同层i中
- 确保露台在UI中可单独开关

### Step 5: 完善房间划分与连通
**修改文件**: `refactored/src/generators/dwellings_core/plan.py`
**目标**: 实现Dwellings.js的房间划分与连通逻辑
**实现要点**:
- 确保Plan构造严格按顺序：divideArea -> mergeCorridors -> connectRooms
- 优化门的分布效果，对齐connectRooms()的分布
- 实现`spawnWindowsExcluding`函数，生成符合规则的窗户

### Step 6: 优化门窗生成
**修改文件**: `refactored/src/generators/dwellings_house_generator.py`
**目标**: 使用Dwellings输出驱动门窗生成
**实现要点**:
- 修改门窗生成逻辑，使用Plan.getDoors()生成门
- 使用plan.windows edge列表生成窗
- 确保门窗正确附着在墙/格子上，不打洞

### Step 7: 完善楼梯系统
**修改文件**: `refactored/src/generators/dwellings_core/house.py`和`refactored/src/generators/dwellings_house_generator.py`
**目标**: 实现完整的楼梯系统
**实现要点**:
- 实现spiral楼梯：带entrance/exit/landing的跨层item
- 实现stairwell楼梯间：基于连通性筛选与权重选点
- 实现ladder梯子：1个cell的窄连接垂直通道
- 确保楼梯正确跨层，且能沿外侧标出来

### Step 8: 完善DB写入结构
**修改文件**: `refactored/src/generators/dwellings_house_generator.py`
**目标**: 确保DB写入支持所有新功能
**实现要点**:
- 确保每层indoor building_area的other_json.kind="indoor"
- 确保terrace building_area的other_json.kind="terrace"
- 正确写入doors/windows/stairs作为item
- 确保UI能正确显示所有新生成的元素

## 验收标准
1. 相同种子生成与Dwellings.js一致的结果
2. 生成的建筑具有不规则外形、上层缩进和露台
3. 门窗分布合理，符合住宅设计规则
4. 楼梯系统完整，包括spiral/stairwell/stair/ladder
5. 生成结果能正确写入数据库，UI能正确显示

## 优先级
1. 随机数生成器修改（基础）
2. 生成流水线完善
3. 非矩形外形生成
4. 退台逻辑实现
5. 门窗生成优化
6. 楼梯系统完善
7. DB写入结构优化

## 预期效果
通过以上修改，将现有代码库升级为完整的Dwellings.js实现，生成的建筑将具有原版的"味道"，包括不规则外形、合理的房间划分、自然的门窗分布和完整的楼梯系统。