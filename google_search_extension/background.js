// Background service worker for Chrome extension
// Listens for search requests and coordinates scraping

console.log('[Extension Background] Service worker started');

// Listen for messages from bridge page or direct requests
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  console.log('[Extension Background] Received message:', request.action);
  
  if (request.action === 'search') {
    handleSearchRequest(request.query, request.search_id, sendResponse);
    return true; // Keep message channel open for async response
  }
  
  if (request.action === 'getResults') {
    chrome.storage.local.get(['lastSearchResults'], (result) => {
      sendResponse({
        urls: result.lastSearchResults || [],
        success: true
      });
    });
    return true;
  }
  
  if (request.action === 'checkStatus') {
    sendResponse({status: 'active'});
    return true;
  }
  
  if (request.action === 'testPoll') {
    // Manually trigger a poll for testing
    pollForPendingSearches();
    sendResponse({status: 'polling triggered'});
    return true;
  }
});

// Also listen for messages from content scripts on Flask page
// This allows Flask to trigger searches
chrome.runtime.onConnect.addListener((port) => {
  if (port.name === 'flask-connection') {
    port.onMessage.addListener((msg) => {
      if (msg.action === 'search') {
        handleSearchRequest(msg.query, (response) => {
          port.postMessage(response);
        });
      }
    });
  }
});

function handleSearchRequest(query, searchId, sendResponse) {
  const searchUrl = `https://www.google.com/search?q=${encodeURIComponent(query)}`;
  
  console.log('[Extension] Opening Google search for:', query, 'search_id:', searchId);
  
  // Get current active tab to return to it later
  chrome.tabs.query({ active: true, currentWindow: true }, (currentTabs) => {
    const currentTabId = currentTabs[0]?.id;
    
    // Open Google search in new tab (background - don't switch to it)
    chrome.tabs.create({ url: searchUrl, active: false }, (tab) => {
      console.log('[Extension] Google tab opened in background:', tab.id);
      
      // Aggressively keep focus on the current tab
      // Chrome sometimes switches even with active: false
      if (currentTabId) {
        // Switch back immediately
        chrome.tabs.update(currentTabId, { active: true });
        
        // Also switch back after a tiny delay (in case Chrome switches during creation)
        setTimeout(() => {
          chrome.tabs.update(currentTabId, { active: true });
        }, 50);
        
        // And again after a bit longer
        setTimeout(() => {
          chrome.tabs.update(currentTabId, { active: true });
        }, 200);
      }
      
      // Wait for page to load
      chrome.tabs.onUpdated.addListener(function listener(tabId, info) {
        if (tabId === tab.id && info.status === 'complete') {
          chrome.tabs.onUpdated.removeListener(listener);
          
          // Make sure we're still on the original tab
          if (currentTabId) {
            chrome.tabs.update(currentTabId, { active: true });
          }
          
          console.log('[Extension] Google page loaded, waiting 3 seconds before scraping...');
          
          // Give page time to fully render (especially if CAPTCHA appears)
          setTimeout(() => {
            // One more time - ensure we're on the Flask page
            if (currentTabId) {
              chrome.tabs.update(currentTabId, { active: true });
            }
            
            console.log('[Extension] Sending scrape message to content script...');
            chrome.tabs.sendMessage(tab.id, {
              action: 'scrapeResults',
              query: query
            }, (response) => {
            if (chrome.runtime.lastError) {
              console.error('[Extension] Error scraping:', chrome.runtime.lastError);
              const errorResponse = {
                success: false,
                error: chrome.runtime.lastError.message,
                urls: []
              };
              
              // Send error to Flask
              sendToFlask(query, [], searchId, tab.id, () => {
                // Reset state after sending error
                resetProcessingState();
              });
              
              if (sendResponse) {
                sendResponse(errorResponse);
              }
              return;
            }
            
            if (response && response.urls) {
              console.log('[Extension] Scraped', response.urls.length, 'URLs');
              // Send results to Flask server
              sendToFlask(query, response.urls, searchId, tab.id, () => {
                // Reset state after successfully sending results
                resetProcessingState();
              });
              
              // Also store for retrieval
              chrome.storage.local.set({
                lastSearchResults: response.urls,
                lastSearchQuery: query,
                lastSearchTime: Date.now()
              }, () => {
                if (sendResponse) {
                  sendResponse({
                    success: true,
                    urls: response.urls,
                    query: query
                  });
                }
              });
            } else {
              console.warn('[Extension] No URLs found in response:', response);
              const errorResponse = {
                success: false,
                error: 'Failed to scrape results',
                urls: []
              };
              
              sendToFlask(query, [], searchId, tab.id, () => {
                // Reset state after sending empty results
                resetProcessingState();
              });
              
              if (sendResponse) {
                sendResponse(errorResponse);
              }
            }
          });
        }, 3000); // Wait 3 seconds for page to render
      }
    });
    });
  });
}

function sendToFlask(query, urls, searchId, tabId, onComplete) {
  console.log('[Extension] Sending results to Flask:', {
    query: query,
    urlCount: urls.length,
    searchId: searchId
  });
  
  // Send results to Flask server
  fetch('http://localhost:5001/api/search/extension/submit', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      query: query,
      urls: urls,
      search_id: searchId
    })
  }).then(res => {
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    }
    return res.json();
  }).then(data => {
    console.log('[Extension] Results sent to Flask successfully:', data);
    
    // Close the Google search tab after successfully sending results
    if (tabId) {
      chrome.tabs.remove(tabId, () => {
        if (chrome.runtime.lastError) {
          console.warn('[Extension] Error closing tab:', chrome.runtime.lastError.message);
        } else {
          console.log('[Extension] Google search tab closed successfully');
        }
        // Call completion callback after tab is closed
        if (onComplete) onComplete();
      });
    } else {
      // No tab to close, call completion callback immediately
      if (onComplete) onComplete();
    }
  }).catch(err => {
    console.error('[Extension] Error sending to Flask:', err);
    
    // Close the tab even on error to prevent tab buildup
    if (tabId) {
      chrome.tabs.remove(tabId, () => {
        if (chrome.runtime.lastError) {
          console.warn('[Extension] Error closing tab:', chrome.runtime.lastError.message);
        } else {
          console.log('[Extension] Google search tab closed after error');
        }
        // Call completion callback even on error
        if (onComplete) onComplete();
      });
    } else {
      // No tab to close, call completion callback immediately
      if (onComplete) onComplete();
    }
  });
}

// State management for polling and processing
const pollState = {
  isProcessing: false,
  currentSearch: null,
  pollCount: 0,
  lastPollTime: null,
  consecutiveErrors: 0,
  maxConsecutiveErrors: 5
};

// Function to poll Flask for pending searches
function pollForPendingSearches() {
  pollState.pollCount++;
  pollState.lastPollTime = Date.now();
  
  // If already processing, skip this poll (this is expected behavior)
  if (pollState.isProcessing) {
    if (pollState.pollCount % 10 === 0) { // Log every 10th skipped poll to reduce noise
      console.log(`[Extension Background] Poll #${pollState.pollCount}: Skipping (currently processing search ${pollState.currentSearch?.search_id || 'unknown'})`);
    }
    return;
  }
  
  // Log polling attempt (but not every single one to reduce noise)
  if (pollState.pollCount % 5 === 0 || pollState.pollCount <= 3) {
    console.log(`[Extension Background] Polling Flask (attempt ${pollState.pollCount})...`);
  }
  
  fetch('http://localhost:5001/api/search/extension/pending')
    .then(res => {
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      pollState.consecutiveErrors = 0; // Reset error count on success
      return res.json();
    })
    .then(data => {
      if (data.query && data.search_id) {
        console.log(`[Extension Background] Poll #${pollState.pollCount}: Found pending search - query: "${data.query}", search_id: ${data.search_id}`);
        startSearchProcessing(data.query, data.search_id);
      } else {
        // Only log "no pending searches" occasionally to reduce noise
        if (pollState.pollCount % 20 === 0) {
          console.log(`[Extension Background] Poll #${pollState.pollCount}: No pending searches`);
        }
      }
    })
    .catch(err => {
      pollState.consecutiveErrors++;
      console.error(`[Extension Background] Poll #${pollState.pollCount}: Error polling Flask - ${err.message} (consecutive errors: ${pollState.consecutiveErrors})`);
      
      // If too many consecutive errors, log a warning
      if (pollState.consecutiveErrors >= pollState.maxConsecutiveErrors) {
        console.warn(`[Extension Background] WARNING: ${pollState.consecutiveErrors} consecutive polling errors. Check if Flask server is running.`);
      }
    });
}

// Start processing a search with proper state management
function startSearchProcessing(query, searchId) {
  if (pollState.isProcessing) {
    console.warn(`[Extension Background] Attempted to start search ${searchId} while already processing ${pollState.currentSearch?.search_id}. This should not happen.`);
    return;
  }
  
  pollState.isProcessing = true;
  pollState.currentSearch = {
    query: query,
    search_id: searchId,
    startTime: Date.now()
  };
  
  console.log(`[Extension Background] Starting search processing: query="${query}", search_id=${searchId}`);
  
  // Set a timeout to prevent stuck state (30 seconds should be more than enough)
  const timeoutId = setTimeout(() => {
    if (pollState.isProcessing && pollState.currentSearch?.search_id === searchId) {
      console.error(`[Extension Background] TIMEOUT: Search ${searchId} took longer than 30 seconds. Resetting state.`);
      resetProcessingState();
    }
  }, 30000);
  
  // Store timeout ID so we can clear it when done
  pollState.currentSearch.timeoutId = timeoutId;
  
  // Start the search request
  handleSearchRequest(query, searchId, null);
}

// Reset processing state (called when search completes or errors)
function resetProcessingState() {
  if (pollState.currentSearch?.timeoutId) {
    clearTimeout(pollState.currentSearch.timeoutId);
  }
  
  const searchId = pollState.currentSearch?.search_id;
  const duration = pollState.currentSearch ? Date.now() - pollState.currentSearch.startTime : 0;
  
  pollState.isProcessing = false;
  pollState.currentSearch = null;
  
  if (searchId) {
    console.log(`[Extension Background] Search ${searchId} completed in ${(duration / 1000).toFixed(1)}s. State reset. Ready for next search.`);
  }
}

// Poll Flask for pending searches
console.log('[Extension Background] Starting polling interval (every 2 seconds)');
setInterval(() => {
  pollForPendingSearches();
}, 2000); // Poll every 2 seconds

// Also poll immediately on startup
pollForPendingSearches();

