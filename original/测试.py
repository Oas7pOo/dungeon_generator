房间大小范围 = ((10, 10), (20, 20))

if isinstance(房间大小范围, tuple) and len(房间大小范围) == 2:
            # 标准格式：((min_width, min_height), (max_width, max_height))
            min_size, max_size = 房间大小范围
            rm = [min_size[0], min_size[1]]
            rx = [max_size[0], max_size[1]]

print(rm, rx)