import numpy as np
import random
import json
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

class Cell:
    def __init__(self, x, y, 类型="外部", 温度 = 0, 建筑区 = 0, 房间区 = 0, 物品 = 0, 生态 = 0):
        self.x = x  # 行索引
        self.y = y  # 列索引
        self.类型 = 类型  # 外部, 墙壁, 室内等
        self.建筑区 = 建筑区
        self.房间区 = 房间区

    def __repr__(self):
        return f"Cell({self.x}{self.y}{self.类型}{self.建筑区}{self.房间区})"

class 物品:
    def __init__(self):
    #房间里面的物品，生成石柱，石笋等等

class 房间:
    def __init__(self):
        self.建筑的格子 = [] # 存储室内格子(row, col)
    #和用浊刻进行建造洞穴
    #特殊房间建筑
    #（选择稀疏建筑还是稠密建筑，或者独立房间，稀疏建筑房间更少抱团，更多依靠道路链接，稠密建筑则由房间分割成）
    #采用递归分割（Recursive Splitting）和模块化拼接（Modular Assembly）的方法
    #每种房间的物品可以不一样
    #递归分割（类似BSP二分数），形成多个小房间基于房间之间的链接生成门
    #（门为在某个边缘点画一个范围圆，而圆如果只经过一个联通的外部区域则可以生成并将该外部区域变为门类型，否则证明该门经过多个房间，需要重新生成）
    #（门可能使用路径查找A*算法）    楼梯/走廊用网格路径链接


class 建筑区:
    def __init__(self, 左上角x, 左上角y, 宽度, 高度, 层, 规模):
        self.左上角x = 左上角x  # 行索引
        self.左上角y = 左上角y  # 列索引
        self.宽度 = 宽度
        self.高度 = 高度
        self.层 = 层
        self.规模 = 规模
#柏林噪声分布矩形，每个矩形里面确定house


class 生态:
    def __init__(self):
    #我的世界温度系统和柏林噪声

class 层:
    def __init__(self, 行数: int, 列数: int, 高度: float):
        self.行数 = 行数  # 地图行数
        self.列数 = 列数  # 地图列数
        self.高度 = 高度
        # 使用二维列表存储Cell对象
        self.网格 = [[Cell(x, y) for x in range(行数)] for y in range(列数)]
        self.houses = []

class 地图:
    def __init__(self):
        self


class 渲染:
    def __init__(self):
        self