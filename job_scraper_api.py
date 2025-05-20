from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from jobspy import scrape_jobs
from typing import List
import pandas as pd
import os
import traceback
import time
import datetime

app = FastAPI()

# Request model
class JobRequest(BaseModel):
    site_name: List[str] = Field(..., example=["indeed", "linkedin"])
    search_term: str = Field(..., example="Python Developer")
    google_search_term: str = Field(..., example="Python jobs remote")
    location: str = Field(..., example="India")
    results_wanted: int = Field(..., gt=0, example=20)
    hours_old: int = Field(..., ge=0, le=720, example=24)
    country_indeed: str = Field(..., example="IN")

# Optional: cache to store previous request results in memory
CACHE = {}

@app.post("/jobs")
async def scrape_jobs_api(request: JobRequest):
    cache_key = f"{request.site_name}_{request.search_term}_{request.location}_{request.hours_old}"
    if cache_key in CACHE:
        return JSONResponse(content={"jobs": CACHE[cache_key], "cached": True})

    try:
        start_time = time.time()

        # Scrape job data
        jobs_df = scrape_jobs(
            site_name=request.site_name,
            search_term=request.search_term,
            google_search_term=request.google_search_term,
            location=request.location,
            results_wanted=request.results_wanted,
            hours_old=request.hours_old,
            country_indeed=request.country_indeed,
        )

        # Convert to list of dicts with date-safe serialization
        jobs_list = jobs_df.replace({pd.NA: None, float('nan'): None}).to_dict(orient="records")

        # Safely convert datetime/date values to ISO strings
        for job in jobs_list:
            for key, value in job.items():
                if isinstance(value, pd.Timestamp):
                    job[key] = value.isoformat() if pd.notnull(value) else None
                elif isinstance(value, (datetime.date, datetime.datetime)):
                    job[key] = value.isoformat()

        # Cache result
        CACHE[cache_key] = jobs_list

        elapsed = round(time.time() - start_time, 2)
        return JSONResponse(content={"jobs": jobs_list, "cached": False, "time_taken": elapsed})

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "trace": traceback.format_exc()}
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("job_scraper_api:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
