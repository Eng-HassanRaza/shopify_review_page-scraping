// Content script that runs on Flask app page
// Listens for search requests and triggers extension

(function() {
  console.log('[Flask Content Script] Loaded');
  
  // Listen for messages from Flask page
  window.addEventListener('message', (event) => {
    // Only accept messages from same origin
    if (event.origin !== 'http://localhost:5001' && event.origin !== 'http://127.0.0.1:5001') return;
    
    if (event.data && event.data.action === 'triggerSearch') {
      const query = event.data.query;
      console.log('[Flask Content Script] Received search request:', query);
      
      // Send to extension background
      chrome.runtime.sendMessage({
        action: 'search',
        query: query
      }, (response) => {
        if (chrome.runtime.lastError) {
          console.error('[Flask Content Script] Error:', chrome.runtime.lastError);
          window.postMessage({
            action: 'searchResponse',
            success: false,
            urls: [],
            error: chrome.runtime.lastError.message
          }, event.origin);
          return;
        }
        
        console.log('[Flask Content Script] Got response from extension:', response);
        // Send response back to Flask page
        window.postMessage({
          action: 'searchResponse',
          success: response ? response.success : false,
          urls: response ? (response.urls || []) : [],
          error: response ? response.error : 'No response from extension'
        }, event.origin);
      });
    }
  });
  
  // Expose API to page using a safer method (no inline script)
  // Create a custom event-based API instead
  Object.defineProperty(window, 'extensionSearch', {
    value: function(query) {
      return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
          reject(new Error('Extension search timeout'));
        }, 60000); // 60 second timeout
        
        const handler = (event) => {
          if (event.data && event.data.action === 'searchResponse') {
            clearTimeout(timeout);
            window.removeEventListener('message', handler);
            if (event.data.success) {
              resolve(event.data);
            } else {
              reject(new Error(event.data.error || 'Search failed'));
            }
          }
        };
        
        window.addEventListener('message', handler);
        window.postMessage({action: 'triggerSearch', query: query}, '*');
      });
    },
    writable: false,
    configurable: false
  });
  
  console.log('[Flask Content Script] API exposed, ready to receive requests');
})();

