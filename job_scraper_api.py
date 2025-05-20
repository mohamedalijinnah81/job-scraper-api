from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field
from jobspy import scrape_jobs
from typing import List, Optional, Dict, Any
import pandas as pd
import os
import time
import asyncio
import logging
from functools import lru_cache
import uvicorn
import json
from contextlib import asynccontextmanager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("job_scraper_api")

# Simple in-memory cache for recent requests
CACHE = {}
CACHE_TTL = 600  # Cache time-to-live in seconds (10 minutes)

# Lifespan context manager for startup/shutdown events
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize any resources
    logger.info("Starting job scraper API")
    yield
    # Shutdown: Clean up resources
    logger.info("Shutting down job scraper API")

# Initialize FastAPI app with lifespan
app = FastAPI(
    title="Job Scraper API",
    description="API for scraping job listings from various sites",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware to allow cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Modify as needed for security
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add GZip compression middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Define request model with validation
class JobRequest(BaseModel):
    site_name: List[str] = Field(..., description="List of job sites to scrape")
    search_term: str = Field(..., description="Job search term")
    google_search_term: str = Field("", description="Google search term (optional)")
    location: str = Field("", description="Job location (optional)")
    results_wanted: int = Field(10, ge=1, le=100, description="Number of results wanted (1-100)")
    hours_old: int = Field(24, ge=1, description="Maximum age of job listings in hours")
    country_indeed: str = Field("usa", description="Country code for Indeed")

# Define response model
class JobResponse(BaseModel):
    jobs: List[Dict[str, Any]]
    count: int
    elapsed_time: float
    cached: bool = False

# Cache key generator
def get_cache_key(request: JobRequest) -> str:
    """Generate a unique cache key based on request parameters"""
    return json.dumps({
        "site_name": sorted(request.site_name),
        "search_term": request.search_term,
        "google_search_term": request.google_search_term,
        "location": request.location,
        "results_wanted": request.results_wanted,
        "hours_old": request.hours_old,
        "country_indeed": request.country_indeed
    }, sort_keys=True)

# Cache check and cleanup
def get_from_cache(key: str) -> Optional[dict]:
    """Try to get results from cache, return None if not found or expired"""
    if key in CACHE:
        data, timestamp = CACHE[key]
        if time.time() - timestamp < CACHE_TTL:
            return data
        # Clean up expired entry
        del CACHE[key]
    return None

# LRU cached function to scrape jobs
@lru_cache(maxsize=32)
def cached_scrape_jobs(
    site_names_tuple: tuple,
    search_term: str,
    google_search_term: str,
    location: str,
    results_wanted: int,
    hours_old: int,
    country_indeed: str
):
    """Cached version of scrape_jobs that works with immutable arguments"""
    start_time = time.time()
    
    # Convert tuple back to list for the scrape_jobs function
    site_names = list(site_names_tuple)
    
    try:
        jobs_df = scrape_jobs(
            site_name=site_names,
            search_term=search_term,
            google_search_term=google_search_term,
            location=location,
            results_wanted=results_wanted,
            hours_old=hours_old,
            country_indeed=country_indeed,
        )
        
        # Clean DataFrame
        jobs_list = jobs_df.replace({pd.NA: None, float('nan'): None}).to_dict(orient="records")
        
        elapsed_time = time.time() - start_time
        
        return {
            "jobs": jobs_list,
            "count": len(jobs_list),
            "elapsed_time": elapsed_time,
            "cached": False
        }
    except Exception as e:
        logger.error(f"Error scraping jobs: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error scraping jobs: {str(e)}")

# Background job processing task
async def process_job_request(request: JobRequest):
    """Process a job request in the background"""
    # This is where you could implement more complex background processing if needed
    pass

@app.post("/jobs", response_model=JobResponse)
async def scrape_jobs_api(request: JobRequest, background_tasks: BackgroundTasks, response: Response):
    """
    Scrape jobs based on the provided parameters
    
    Returns job listings matching the search criteria
    """
    start_time = time.time()
    
    # Try to get from cache first
    cache_key = get_cache_key(request)
    cached_result = get_from_cache(cache_key)
    
    if cached_result:
        cached_result["cached"] = True
        cached_result["elapsed_time"] = time.time() - start_time
        return JobResponse(**cached_result)
    
    # Background task for any post-processing
    background_tasks.add_task(process_job_request, request)
    
    try:
        # Convert the mutable list to an immutable tuple for the lru_cache
        site_names_tuple = tuple(request.site_name)
        
        # Call the cached scrape function
        result = cached_scrape_jobs(
            site_names_tuple=site_names_tuple,
            search_term=request.search_term,
            google_search_term=request.google_search_term,
            location=request.location,
            results_wanted=request.results_wanted,
            hours_old=request.hours_old,
            country_indeed=request.country_indeed,
        )
        
        # Update elapsed time
        result["elapsed_time"] = time.time() - start_time
        
        # Store in cache
        CACHE[cache_key] = (result, time.time())
        
        # Set Cache-Control header
        response.headers["Cache-Control"] = f"max-age={CACHE_TTL}"
        
        return JobResponse(**result)
        
    except Exception as e:
        logger.error(f"Error in API request: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    return {"status": "ok", "timestamp": time.time()}

# Periodic cache cleanup
@app.on_event("startup")
async def setup_cache_cleanup():
    async def cleanup_cache():
        while True:
            try:
                current_time = time.time()
                keys_to_delete = []
                
                for key, (_, timestamp) in CACHE.items():
                    if current_time - timestamp > CACHE_TTL:
                        keys_to_delete.append(key)
                
                for key in keys_to_delete:
                    del CACHE[key]
                
                logger.info(f"Cache cleanup: removed {len(keys_to_delete)} expired entries")
            except Exception as e:
                logger.error(f"Error during cache cleanup: {str(e)}")
            
            # Run cleanup every 5 minutes
            await asyncio.sleep(300)
    
    # Start the background task
    asyncio.create_task(cleanup_cache())

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "job_scraper_api:app", 
        host="0.0.0.0", 
        port=port,
        workers=int(os.getenv("WORKERS", 1)),  # Adjust based on available CPU cores
        log_level="info"
    )