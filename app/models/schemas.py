from pydantic import BaseModel, field_validator
from typing import List, Optional


class ScrapeRequest(BaseModel):
    """Request model for starting a scrape job."""

    urls: List[str]
    max_posts: int = 10
    include_reactions: bool = False
    max_reactions: int = 5
    include_comments: bool = False
    max_comments: int = 5

    @field_validator('urls')
    @classmethod
    def validate_linkedin_urls(cls, v):
        if not v:
            raise ValueError('At least one URL is required')

        for url in v:
            url_lower = url.lower()
            if 'linkedin.com' not in url_lower:
                raise ValueError(f'Invalid LinkedIn URL: {url}')
            if not (url_lower.startswith('http://') or url_lower.startswith('https://')):
                raise ValueError(f'URL must start with http:// or https://: {url}')
        return v

    @field_validator('max_posts')
    @classmethod
    def validate_max_posts(cls, v):
        if v < 1:
            raise ValueError('max_posts must be at least 1')
        if v > 100:
            raise ValueError('max_posts cannot exceed 100')
        return v


class ScrapeResponse(BaseModel):
    """Response model for a scrape job submission."""

    job_id: str
    status: str
    message: str
    download_url: Optional[str] = None


class JobStatusResponse(BaseModel):
    """Response model for job status polling."""

    job_id: str
    status: str  # PENDING, RUNNING, SUCCEEDED, FAILED
    progress: Optional[dict] = None
    download_url: Optional[str] = None
    error: Optional[str] = None
    dataset_id: Optional[str] = None
