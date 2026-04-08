import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .database import engine, Base
from .api import router as api_router

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Garmin Recovery Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)

@app.get("/health")
def health_check():
    return {"status": "ok"}
