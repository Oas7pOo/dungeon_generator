class MapManager:
    def __init__(self):
        self.map_width = 0
        self.map_height = 0
        self.layers = []

    def initialize_map(self, width, height, layers):
        self.map_width = width
        self.map_height = height
        self.layers = [{"id": i, "width": width, "height": height} for i in range(layers)]

    def get_map_info(self):
        return {
            "width": self.map_width,
            "height": self.map_height,
            "layers": self.layers
        }
