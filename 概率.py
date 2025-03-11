import numpy as np
import matplotlib.pyplot as plt

# 定义 rm 和 rx（最小和最大尺寸）
rm = [10, 10]
rx = [200, 200]

# 生成随机数
samples = [int(np.random.exponential(scale=(rx[0] - rm[0]) / 4) + rm[0]) for _ in range(10000)]

# 画出直方图
plt.figure(figsize=(8, 5))
plt.hist(samples, bins=30, edgecolor="black", alpha=0.7, density=True)
plt.xlabel("随机生成的值")
plt.ylabel("频率")
plt.title("指数分布随机数直方图")
plt.show()
