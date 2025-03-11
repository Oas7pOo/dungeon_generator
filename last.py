import numpy as np
import random
import json
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

class GridMap:
    def __init__(self, layers: int, rows: int, cols: int, room_params: list):
        self.layers = layers
        self.rows = rows
        self.cols = cols
        self.room_params = room_params
        self.grid = np.zeros((layers, rows, cols), dtype=int)
        self.grid_status = np.zeros((layers, rows, cols), dtype=int)
        self.placed_rooms = [[] for _ in range(layers)]
        self.EMPTY = 0
        self.WALL = 1
        self.SPACE = 2
        self.generate_rooms()

    def generate_room_size(self, layer):
        rm, rx, _, dist = self.room_params[layer]
        for _ in range(100):
            if dist == 'uniform':
                h = random.randint(rm[0], rx[0])
                w = random.randint(rm[1], rx[1])
            elif dist == 'exponential':
                h = int(np.random.exponential(scale=(rx[0] - rm[0]) / 3) + rm[0])
                w = int(np.random.exponential(scale=(rx[1] - rm[1]) / 3) + rm[1])
            else:
                raise ValueError()
            if h > 0 and w > 0:
                ratio = w / h
                if random(0,1) > 0.4 or (ratio < 2 and ratio > 0.5):
                    return h, w
        return rm[0], rm[1]

    def find_valid_position(self, layer, h, w):
        for row in range(self.rows - h):
            for col in range(self.cols - w):
                t = row - 1
                l = col - 1
                b = row + h
                r = col + w
                if t < 0 or l < 0 or b >= self.rows or r >= self.cols:
                    continue
                box = self.grid[layer, t:b+1, l:r+1]
                if np.any(box != self.EMPTY):
                    continue
                return row, col
        return None

    def place_room(self, layer, row, col, h, w):
        t = row - 1
        l = col - 1
        b = row + h
        r = col + w
        for rr in range(row, row + h):
            for cc in range(col, col + w):
                self.grid[layer, rr, cc] = self.SPACE
                self.grid_status[layer, rr, cc] = self.SPACE
        for x in range(l, r + 1):
            self.grid[layer, t, x] = self.WALL
            self.grid_status[layer, t, x] = self.WALL
        for x in range(l, r + 1):
            self.grid[layer, b, x] = self.WALL
            self.grid_status[layer, b, x] = self.WALL
        for y in range(t, b + 1):
            self.grid[layer, y, l] = self.WALL
            self.grid_status[layer, y, l] = self.WALL
        for y in range(t, b + 1):
            self.grid[layer, y, r] = self.WALL
            self.grid_status[layer, y, r] = self.WALL
        self.placed_rooms[layer].append((row, col, h, w))

    def generate_rooms(self):
        for layer in range(self.layers):
            rm, rx, rc, _ = self.room_params[layer]
            tries = 100
            c = 0
            while True:
                c += 1
                self.grid[layer].fill(self.EMPTY)
                self.grid_status[layer].fill(self.EMPTY)
                self.placed_rooms[layer].clear()
                ok = True
                for _r in range(rc):
                    done = False
                    for _a in range(4):
                        h, w = self.generate_room_size(layer)
                        if h > self.rows or w > self.cols:
                            continue
                        row = random.randint(0, self.rows - h)
                        col = random.randint(0, self.cols - w)
                        t = row - 1
                        l = col - 1
                        b = row + h
                        r = col + w
                        if t < 0 or l < 0 or b >= self.rows or r >= self.cols:
                            continue
                        box = self.grid[layer, t:b+1, l:r+1]
                        if np.any(box != self.EMPTY):
                            continue
                        self.place_room(layer, row, col, h, w)
                        done = True
                        break
                    if not done:
                        pos = self.find_valid_position(layer, h, w)
                        if pos:
                            self.place_room(layer, pos[0], pos[1], h, w)
                        else:
                            ok = False
                            break
                if ok or c >= tries:
                    break

    def export_to_js(self, filename="grid_map.js"):
        d = {
            "layers": self.layers,
            "rows": self.rows,
            "cols": self.cols,
            "grid": self.grid.tolist(),
            "rooms": []
        }
        for layer in range(self.layers):
            for i, (row, col, h, w) in enumerate(self.placed_rooms[layer]):
                d["rooms"].append({
                    "layer": layer,
                    "room_id": i + 1,
                    "top_left": [row, col],
                    "bottom_right": [row + h - 1, col + w - 1]
                })
        with open(filename, "w") as f:
            f.write("const gridMap = {}".format(json.dumps(d, indent=4)))

    def save_layer_images(self, path):
        cmap = mcolors.ListedColormap(['green','dimgray','white'])
        bounds = [-0.5, 0.5, 1.5, 2.5]
        norm = mcolors.BoundaryNorm(bounds, cmap.N)
        for layer in range(self.layers):
            fig, ax = plt.subplots()
            ax.imshow(self.grid[layer], cmap=cmap, norm=norm, interpolation='none')
            ax.set_xticks(np.arange(-0.5, self.cols, 1))
            ax.set_yticks(np.arange(-0.5, self.rows, 1))
            ax.set_xticklabels([])
            ax.set_yticklabels([])
            ax.set_xlim(-0.5, self.cols-0.5)
            ax.set_ylim(self.rows-0.5, -0.5)
            ax.grid(True, color='gray', linestyle='dashed', linewidth=0.5)
            fig.set_size_inches(10,10)
            fig.savefig(f'{path}/grid_layer_{layer}.pdf')
            plt.close(fig)

    def display_layer(self, layer: int):
        if 0 <= layer < self.layers:
            print(self.grid[layer])
        else:
            raise IndexError()


if __name__ == "__main__":
    layers = 2
    rows, cols = 100, 100
    room_params = [
        ((5, 10), (30, 40), 20, 'exponential'), #可以设置为平均uniform，不建议
        ((5, 10), (30, 40), 20, 'exponential')
    ]
    grid_map = GridMap(layers, rows, cols, room_params)
    grid_map.display_layer(0)
    grid_map.export_to_js("./grid_map.js")
    grid_map.save_layer_images("./1/")
