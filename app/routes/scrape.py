from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
import io
import uuid
import pandas as pd
from typing import Dict
from datetime import datetime, timedelta

from app.models.schemas import ScrapeRequest, ScrapeResponse, JobStatusResponse
from app.services.apify_service import ApifyService
from app.config import get_settings

router = APIRouter()

# In-memory job storage (for MVP; use Redis for production scaling)
jobs: Dict[str, dict] = {}

# Job TTL cleanup threshold
JOB_TTL_HOURS = 1


def cleanup_old_jobs():
    """Remove jobs older than TTL."""
    now = datetime.now()
    expired = [
        job_id for job_id, job in jobs.items()
        if now - job.get("created_at", now) > timedelta(hours=JOB_TTL_HOURS)
    ]
    for job_id in expired:
        del jobs[job_id]


def generate_job_id() -> str:
    """Generate a unique job ID."""
    return str(uuid.uuid4())[:8]


def run_scrape_job(job_id: str, apify_input: dict):
    """Background task to run the scrape job and poll for completion."""
    settings = get_settings()
    service = ApifyService(settings.APIFY_API_TOKEN)

    try:
        # Update status to RUNNING
        jobs[job_id]["status"] = "RUNNING"

        # Start the Actor run
        run_data = service.start_run(settings.APIFY_ACTOR_ID, apify_input)
        run_id = run_data["id"]
        jobs[job_id]["run_id"] = run_id

        # Poll for completion
        final_data = service.wait_for_run(run_id, max_wait=600)

        if final_data["status"] == "SUCCEEDED":
            jobs[job_id]["status"] = "SUCCEEDED"
            jobs[job_id]["dataset_id"] = final_data["defaultDatasetId"]
            jobs[job_id]["download_url"] = f"/api/jobs/{job_id}/download"
        else:
            jobs[job_id]["status"] = "FAILED"
            jobs[job_id]["error"] = f"Actor run {final_data['status']}"

    except Exception as e:
        jobs[job_id]["status"] = "FAILED"
        jobs[job_id]["error"] = str(e)


@router.post("/api/scrape", response_model=ScrapeResponse)
async def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks):
    """
    Start a LinkedIn scrape job.

    For small jobs (<=3 URLs, <=10 posts), attempts sync execution.
    For larger jobs, runs async with polling.
    """
    # Cleanup old jobs periodically
    cleanup_old_jobs()

    settings = get_settings()

    # Build Apify input
    apify_input = {
        "targetUrls": request.urls,
        "maxPosts": request.max_posts,
        "scrapeReactions": request.include_reactions,
        "maxReactions": request.max_reactions if request.include_reactions else 0,
        "scrapeComments": request.include_comments,
        "maxComments": request.max_comments if request.include_comments else 0,
    }

    # Determine if this is a quick job
    is_quick_job = len(request.urls) <= 3 and request.max_posts <= 10

    job_id = generate_job_id()

    if is_quick_job:
        # Try sync execution for quick jobs
        service = ApifyService(settings.APIFY_API_TOKEN)
        try:
            # Attempt sync run (up to 5 minutes)
            result = service.run_sync(settings.APIFY_ACTOR_ID, apify_input, timeout=300)

            if result is not None:
                # Store the result for download
                jobs[job_id] = {
                    "status": "SUCCEEDED",
                    "created_at": datetime.now(),
                    "sync_data": result,
                    "download_url": f"/api/jobs/{job_id}/download"
                }
                return ScrapeResponse(
                    job_id=job_id,
                    status="SUCCEEDED",
                    message="Scrape completed successfully",
                    download_url=f"/api/jobs/{job_id}/download"
                )
        except Exception:
            # Fall through to async execution
            pass

    # Async execution for larger jobs or if sync failed
    jobs[job_id] = {
        "status": "PENDING",
        "created_at": datetime.now(),
        "apify_input": apify_input
    }

    background_tasks.add_task(run_scrape_job, job_id, apify_input)

    return ScrapeResponse(
        job_id=job_id,
        status="PENDING",
        message="Scrape job started. Poll /api/jobs/{job_id} for status."
    )


@router.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get the current status of a scrape job."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]

    return JobStatusResponse(
        job_id=job_id,
        status=job["status"],
        download_url=job.get("download_url"),
        error=job.get("error"),
        dataset_id=job.get("dataset_id")
    )


@router.get("/api/jobs/{job_id}/download")
async def download_csv(job_id: str):
    """Download the scrape results as a CSV file."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]

    if job["status"] != "SUCCEEDED":
        raise HTTPException(
            status_code=400,
            detail=f"Job not ready. Current status: {job['status']}"
        )

    settings = get_settings()

    # Check if we have sync data (from quick job)
    if "sync_data" in job:
        # Convert JSON to CSV using pandas for proper flattening
        data = job["sync_data"]

        if not data:
            raise HTTPException(status_code=404, detail="No data available")

        if isinstance(data, list) and len(data) > 0:
            # Use pandas to handle nested data - it will flatten automatically
            df = pd.json_normalize(data)
            csv_bytes = df.to_csv(index=False).encode('utf-8')
        else:
            csv_bytes = b"No data"

    else:
        # Fetch from Apify dataset
        dataset_id = job.get("dataset_id")
        if not dataset_id:
            raise HTTPException(status_code=404, detail="No dataset available")

        service = ApifyService(settings.APIFY_API_TOKEN)
        csv_bytes = service.get_dataset_items(dataset_id, format="csv")

    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=linkedin_posts_{job_id}.csv"
        }
    )
