import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.routers.sensor import router as sensor_router, start_sensor_tasks, stop_sensor_tasks
from app.routers.roof import router as roof_router, init_roof, close_roof

app = FastAPI(title="IRIV Combined API (Sensor + Roof Control)", version="1.0.0")

# Static UI (realtime charts)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routers
app.include_router(sensor_router)
app.include_router(roof_router)

@app.get("/")
def root():
    return FileResponse("app/static/index.html")

@app.on_event("startup")
async def _startup():
    # Start background polling + initialize GPIO
    await start_sensor_tasks()
    init_roof()

@app.on_event("shutdown")
async def _shutdown():
    await stop_sensor_tasks()
    close_roof()
