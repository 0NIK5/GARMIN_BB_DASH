import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .database import engine, Base, ensure_profile_name_column
from .api import router as api_router

ensure_profile_name_column(engine)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Garmin Recovery Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))
app.include_router(api_router)
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

@app.get("/health")
def health_check():
    return {"status": "ok"}
