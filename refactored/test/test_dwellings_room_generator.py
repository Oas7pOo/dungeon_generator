Step 2 把“外形”从矩形升级为“轮廓 contour + area”（不规整从这里长出来）

改动：

实现 contour2area() 对齐 JS 思路（轮廓边填充成 area）。

Dwellings

实现 getNotch() 这种从轮廓内凹的机制，作为 shape 改形的一个可选操作。

Dwellings

在你的 building_area 输入是多边形或已有格子占地时，先“裁剪成局部网格+offset”，再进入 dwellings 内核（JS 里有 cloud2gridNArea 的味道）。

Dwellings

验收：

生成的 footprint 能出现凹口、不规则边缘，而不是永远矩形

Step 3 做出“多层 + slab/退台”的核心差异（露台的灵魂）

改动：

支持 tag slab：所有楼层同形，不做退台。

Dwellings

非 slab：按 JS 逻辑尝试“删掉一个房间”形成上层缩进。

Dwellings

在写 DB 时：每层写一个 building_area(indoor)，并写 terrace building_area（footprint_(i-1) - footprint_i）到同层 i。

验收：

2 层以上时，能稳定出现上层缩进

terrace building_area 在上层存在并可渲染为可走露台

Step 4 还原 Plan 的“房间划分与连通”（门的前置条件）

改动：

Plan 构造严格按顺序：divideArea -> mergeCorridors -> connectRooms。

Dwellings

你现在用 MST 选门（dwellings_room_generator 里已经有 _collect_boundaries/_select_doors）。

dwellings_room_generator


这可以保留，但最终目标是让它对齐 connectRooms() 的分布效果（后续再微调概率/权重）。

验收：

每层 rooms 全连通

门数量看起来像“合理住宅”，不是走迷宫也不是全开放

Step 5 把“门窗”完全改成 Dwellings 输出驱动（别再随机乱撒）

改动：

doors：用 Plan.getDoors() 的 edge 数据生成 item，不再每房间随机门。

Dwellings

windows：用 spawnWindowsExcluding() 产生的 plan.windows edge 列表生成 window item。

Dwellings

你的 item 表保存本来就支持 wall_tiles 和矢量 circle。

item_generator

关键映射（不打洞的做法）：

door/window 的 edge -> 一个“墙格子坐标”

写入 item.tiles_json["wall_tiles"]，room.tiles_json 不需要删墙

验收：

门都在“房间相邻边界”上

窗都在“外墙”上，而且会避开门/楼梯附近（原版味很明显）

Step 6 楼梯系统：spiral / stairwell / stair / ladder 全部作为跨层 item

改动：

Spiral：按 JS 的 entrance/exit/landing 生成一个跨层 item。

Dwellings

Stairwell：按 JS 的连通性筛选与权重选点生成跨层 item。

Dwellings

直楼梯 stair：如果你想要“绕着走”的外置楼梯，也可以把它设计成沿外墙边界的 path，并存到 properties_json 里（渲染时按 path 画）。

Ladder：当某层连接点空间不足或顶层很小，退化为 ladder（1 cell，跨层 item）。

你现在 item_generator 已经支持按 room.other_json["dwelling_stairs"] 聚合生成一个跨层楼梯 item。

item_generator


dwellings_room_generator 也在 rooms_out 里对 stair/spiral/ladder 做了标记。

dwellings_room_generator

验收：

一个楼梯井不会生成一堆重复 item

layer_start/layer_end 正确跨越楼层

spiral 明显贴外墙，stairwell 明显在室内

Step 7 最后把“DB 写入结构”补到完全支持露台与多层

你 DB 里 building_area 已经有 other_json 字段可扩展。

Dwellings


你只需要做到：

每层 indoor building_area：other_json.kind="indoor"

每层 terrace building_area：other_json.kind="terrace"

rooms 正常写入

doors/windows/stairs 作为 item 写入

验收：

你在 UI 里能单独开关露台层