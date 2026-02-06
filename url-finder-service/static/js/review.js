function getScopeQueryFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const jobId = params.get('job_id');
    if (jobId) return `?job_id=${encodeURIComponent(jobId)}`;
    return '';
}

function showReviewStatus(message, type) {
    const statusDiv = document.getElementById('review-status');
    if (!statusDiv) return;
    statusDiv.textContent = message;
    statusDiv.className = `status ${type}`;
    setTimeout(() => {
        statusDiv.textContent = '';
        statusDiv.className = 'status';
    }, 5000);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}

async function loadReviewStores() {
    const reviewList = document.getElementById('review-stores-list');
    if (!reviewList) return;

    try {
        reviewList.innerHTML = '<p style="color: #666;">Loading stores needing review...</p>';
        const response = await fetch('/api/stores/review' + getScopeQueryFromUrl());
        if (!response.ok) {
            throw new Error('Failed to load review stores');
        }

        const stores = await response.json();
        if (!stores.length) {
            reviewList.innerHTML = '<p style="color: #666; padding: 20px; text-align: center;">No stores need review at this time.</p>';
            return;
        }

        let html = '<div style="display: flex; flex-direction: column; gap: 15px;">';
        stores.forEach(store => {
            const candidateUrls = store.candidate_urls || [];
            const confidence = store.url_confidence ? (store.url_confidence * 100).toFixed(1) : 'N/A';
            const provider = store.url_finding_provider || 'unknown';

            html += `
                <div style="border: 1px solid #ddd; border-radius: 4px; padding: 15px; background: white;">
                    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 10px; gap: 10px; flex-wrap: wrap;">
                        <div>
                            <h4 style="margin: 0 0 5px 0; font-size: 16px;">${escapeHtml(store.store_name || 'Unknown Store')}</h4>
                            <p style="margin: 0; font-size: 12px; color: #666;">
                                Confidence: ${confidence}% | Provider: ${escapeHtml(provider)}
                                ${store.country ? ` | Country: ${escapeHtml(store.country)}` : ''}
                            </p>
                            ${store.url_finding_error ? `<p style="margin: 5px 0 0 0; font-size: 12px; color: #e74c3c;">Reason: ${escapeHtml(store.url_finding_error)}</p>` : ''}
                        </div>
                        <span style="background: #ff9800; color: white; padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: 600;">NEEDS REVIEW</span>
                    </div>

                    <div style="margin-top: 15px;">
                        <label style="display: block; margin-bottom: 8px; font-size: 13px; font-weight: 600;">Select URL:</label>
                        ${candidateUrls.length > 0 ? `
                            <div style="display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px;">
                                ${candidateUrls.map((url, idx) => `
                                    <button data-url="${escapeHtml(url)}" data-store="${store.id}"
                                            class="review-url-btn"
                                            style="text-align: left; padding: 10px; border: 1px solid #ddd; border-radius: 4px; background: ${idx === 0 ? '#e3f2fd' : 'white'}; cursor: pointer; transition: background 0.2s;">
                                        ${idx === 0 ? '⭐ ' : ''}${escapeHtml(url)}
                                    </button>
                                `).join('')}
                            </div>
                        ` : ''}
                        <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                            <input type="text" id="manual-url-${store.id}" placeholder="Or enter URL manually"
                                   style="flex: 1; min-width: 220px; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                            <button data-manual-store="${store.id}"
                                    style="padding: 8px 16px; background: #27ae60; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: 500;">
                                Confirm Manual URL
                            </button>
                        </div>
                    </div>
                </div>
            `;
        });
        html += '</div>';
        reviewList.innerHTML = html;

        document.querySelectorAll('.review-url-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                const url = e.currentTarget.getAttribute('data-url');
                const storeId = e.currentTarget.getAttribute('data-store');
                await selectReviewUrl(storeId, url);
            });
        });

        document.querySelectorAll('[data-manual-store]').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                const storeId = e.currentTarget.getAttribute('data-manual-store');
                const input = document.getElementById(`manual-url-${storeId}`);
                await selectReviewUrl(storeId, input ? input.value : '');
            });
        });
    } catch (error) {
        console.error('Error loading review stores:', error);
        reviewList.innerHTML = `<p style="color: #e74c3c;">Error loading review stores: ${error.message}</p>`;
    }
}

async function selectReviewUrl(storeId, url) {
    if (!url || !url.trim()) {
        showReviewStatus('Please enter a URL', 'error');
        return;
    }

    try {
        showReviewStatus('Saving selected URL...', 'info');
        const response = await fetch(`/api/stores/${storeId}/review/select-url`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: url.trim() })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to save URL');
        }

        const data = await response.json();
        showReviewStatus(`URL saved successfully: ${data.url}`, 'success');
        await loadReviewStores();
    } catch (error) {
        console.error('Error selecting review URL:', error);
        showReviewStatus(`Error: ${error.message}`, 'error');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadReviewStores();
});
