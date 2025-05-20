from fastapi import FastAPI, Request
from pydantic import BaseModel
from jobspy import scrape_jobs
from typing import List
import pandas as pd
import os

# Initialize FastAPI app
app = FastAPI()

# Define request model
class JobRequest(BaseModel):
    site_name: List[str]
    search_term: str
    google_search_term: str
    location: str
    results_wanted: int
    hours_old: int
    country_indeed: str

@app.post("/jobs")
async def scrape_jobs_api(request: JobRequest):
    try:
        # Scrape jobs using provided parameters
        jobs = scrape_jobs(
            site_name=request.site_name,
            search_term=request.search_term,
            google_search_term=request.google_search_term,
            location=request.location,
            results_wanted=request.results_wanted,
            hours_old=request.hours_old,
            country_indeed=request.country_indeed,
        )

        # Convert DataFrame to a list of dictionaries
        jobs_list = jobs.replace({pd.NA: None, float('nan'): None}).to_dict(orient="records")

        # Return the list as JSON response
        return {"jobs": jobs_list}

    except Exception as e:
        return {"error": str(e)}

# To run the app: uvicorn job_scraper_api:app --reload

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("job_scraper_api:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
