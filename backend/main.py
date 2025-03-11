from fastapi import FastAPI
from pydantic import BaseModel
from typing import List

from map_logic import MapManager

app = FastAPI()
map_manager = MapManager()

class MapConfig(BaseModel):
    width: int
    height: int
    layers: int

@app.post("/initialize")
async def initialize_map(config: MapConfig):
    map_manager.initialize_map(config.width, config.height, config.layers)
    return {"message": "地图初始化成功", "map": map_manager.get_map_info()}

@app.get("/map-info")
async def get_map_info():
    return map_manager.get_map_info()
