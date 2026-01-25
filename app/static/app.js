// DOM Elements
const scrapeForm = document.getElementById('scrapeForm');
const urlsInput = document.getElementById('urls');
const maxPostsInput = document.getElementById('maxPosts');
const includeReactionsCheckbox = document.getElementById('includeReactions');
const includeCommentsCheckbox = document.getElementById('includeComments');
const submitBtn = document.getElementById('submitBtn');
const costEstimate = document.getElementById('costEstimate');

const progressSection = document.getElementById('progressSection');
const progressFill = document.getElementById('progressFill');
const statusTitle = document.getElementById('statusTitle');
const statusText = document.getElementById('statusText');

const successSection = document.getElementById('successSection');
const downloadLink = document.getElementById('downloadLink');
const newScrapeBtn = document.getElementById('newScrapeBtn');

const errorSection = document.getElementById('errorSection');
const errorMessage = document.getElementById('errorMessage');
const retryBtn = document.getElementById('retryBtn');

// Cost per item in USD
const COST_PER_ITEM = 0.002;

// Update cost estimate whenever inputs change
function updateCostEstimate() {
    const urls = urlsInput.value.split('\n').filter(u => u.trim().length > 0);
    const maxPosts = parseInt(maxPostsInput.value) || 0;
    const includeReactions = includeReactionsCheckbox.checked;
    const includeComments = includeCommentsCheckbox.checked;

    const urlCount = urls.length;
    let postCount = urlCount * maxPosts;
    let additionalItems = 0;

    // Each post with reactions/comments adds roughly 1 item per option
    if (includeReactions) additionalItems += postCount;
    if (includeComments) additionalItems += postCount;

    const totalItems = postCount + additionalItems;
    const cost = totalItems * COST_PER_ITEM;

    const costValue = costEstimate.querySelector('.cost-value');
    const costDetail = costEstimate.querySelector('.cost-detail');

    costValue.textContent = `$${cost.toFixed(2)}`;
    costDetail.textContent = `(${totalItems} items)`;
}

// Add event listeners for cost calculation
urlsInput.addEventListener('input', updateCostEstimate);
maxPostsInput.addEventListener('input', updateCostEstimate);
includeReactionsCheckbox.addEventListener('change', updateCostEstimate);
includeCommentsCheckbox.addEventListener('change', updateCostEstimate);

// Initial cost calculation
updateCostEstimate();

// Show/hide sections
function showForm() {
    scrapeForm.classList.remove('hidden');
    progressSection.classList.add('hidden');
    successSection.classList.add('hidden');
    errorSection.classList.add('hidden');
    submitBtn.disabled = false;
    submitBtn.querySelector('.btn-text').textContent = 'Start Scraping';
}

function showProgress() {
    scrapeForm.classList.add('hidden');
    progressSection.classList.remove('hidden');
    successSection.classList.add('hidden');
    errorSection.classList.add('hidden');
    progressFill.style.width = '10%';
}

function showSuccess(downloadUrl) {
    scrapeForm.classList.add('hidden');
    progressSection.classList.add('hidden');
    successSection.classList.remove('hidden');
    errorSection.classList.add('hidden');
    downloadLink.href = downloadUrl;
}

function showError(message) {
    scrapeForm.classList.add('hidden');
    progressSection.classList.add('hidden');
    successSection.classList.add('hidden');
    errorSection.classList.remove('hidden');
    errorMessage.textContent = message;
}

function updateProgress(status, message) {
    statusTitle.textContent = status === 'RUNNING' ? 'Scraping in progress...' : 'Processing...';
    statusText.textContent = message || 'Please wait while we fetch your data...';

    // Animate progress bar
    if (status === 'PENDING') {
        progressFill.style.width = '20%';
    } else if (status === 'RUNNING') {
        progressFill.style.width = '60%';
    }
}

// Poll for job status
async function pollJobStatus(jobId, attempt = 0) {
    const maxAttempts = 120; // 10 minutes with backoff
    const baseDelay = 2000;  // 2 seconds

    if (attempt >= maxAttempts) {
        showError('Job timed out. Please try again with fewer URLs or posts.');
        return;
    }

    try {
        const response = await fetch(`/api/jobs/${jobId}`);

        if (!response.ok) {
            throw new Error('Failed to check job status');
        }

        const job = await response.json();

        if (job.status === 'SUCCEEDED') {
            progressFill.style.width = '100%';
            setTimeout(() => {
                showSuccess(job.download_url);
            }, 500);
        } else if (job.status === 'FAILED') {
            showError(job.error || 'Job failed. Please try again.');
        } else {
            updateProgress(job.status, `Status: ${job.status}`);

            // Exponential backoff: 2s, 3s, 4.5s... capped at 10s
            const delay = Math.min(baseDelay * Math.pow(1.5, Math.min(attempt, 5)), 10000);
            setTimeout(() => pollJobStatus(jobId, attempt + 1), delay);
        }
    } catch (error) {
        console.error('Polling error:', error);
        showError('Connection error. Please check your network and try again.');
    }
}

// Form submission
scrapeForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const urls = urlsInput.value
        .split('\n')
        .map(u => u.trim())
        .filter(u => u.length > 0);

    if (urls.length === 0) {
        showError('Please enter at least one LinkedIn URL.');
        return;
    }

    // Validate URLs
    const invalidUrls = urls.filter(u => !u.toLowerCase().includes('linkedin.com'));
    if (invalidUrls.length > 0) {
        showError(`Invalid LinkedIn URL: ${invalidUrls[0]}`);
        return;
    }

    const payload = {
        urls: urls,
        max_posts: parseInt(maxPostsInput.value) || 10,
        include_reactions: includeReactionsCheckbox.checked,
        max_reactions: 5,
        include_comments: includeCommentsCheckbox.checked,
        max_comments: 5
    };

    // Disable submit button and show loading state
    submitBtn.disabled = true;
    submitBtn.querySelector('.btn-text').textContent = 'Starting...';

    try {
        showProgress();
        statusText.textContent = 'Starting scrape job...';

        const response = await fetch('/api/scrape', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || 'Failed to start scrape job');
        }

        const data = await response.json();

        if (data.status === 'SUCCEEDED' && data.download_url) {
            // Sync job completed immediately
            progressFill.style.width = '100%';
            setTimeout(() => {
                showSuccess(data.download_url);
            }, 500);
        } else {
            // Async job - start polling
            updateProgress('PENDING', 'Job queued, starting soon...');
            pollJobStatus(data.job_id);
        }
    } catch (error) {
        console.error('Submit error:', error);
        showError(error.message || 'Failed to start scrape job. Please try again.');
    }
});

// New scrape button
newScrapeBtn.addEventListener('click', () => {
    showForm();
});

// Retry button
retryBtn.addEventListener('click', () => {
    showForm();
});
