// Popup script for extension
document.addEventListener('DOMContentLoaded', () => {
  const testBtn = document.getElementById('test-btn');
  const status = document.getElementById('status');
  const pollBtn = document.createElement('button');
  pollBtn.textContent = 'Test Poll Flask';
  pollBtn.style.cssText = 'width: 100%; padding: 10px; margin: 5px 0; background: #4CAF50; color: white; border: none; border-radius: 4px; cursor: pointer;';
  testBtn.parentNode.insertBefore(pollBtn, testBtn.nextSibling);
  
  testBtn.addEventListener('click', () => {
    status.textContent = 'Testing...';
    status.className = 'status active';
    
    // Test by sending a message to background
    chrome.runtime.sendMessage({action: 'getResults'}, (response) => {
      if (chrome.runtime.lastError) {
        status.textContent = 'Error: ' + chrome.runtime.lastError.message;
        status.className = 'status error';
      } else {
        status.textContent = 'Extension is working!';
        status.className = 'status active';
      }
    });
  });
  
  pollBtn.addEventListener('click', () => {
    status.textContent = 'Polling Flask...';
    status.className = 'status active';
    
    // Manually trigger a poll
    chrome.runtime.sendMessage({action: 'testPoll'}, (response) => {
      if (chrome.runtime.lastError) {
        status.textContent = 'Error: ' + chrome.runtime.lastError.message;
        status.className = 'status error';
      } else {
        status.textContent = 'Poll triggered! Check background console.';
        status.className = 'status active';
      }
    });
    
    // Also test direct fetch
    fetch('http://localhost:5001/api/search/extension/pending')
      .then(res => res.json())
      .then(data => {
        if (data.query) {
          status.textContent = `Found: ${data.query}`;
        } else {
          status.textContent = 'No pending searches';
        }
      })
      .catch(err => {
        status.textContent = 'Flask error: ' + err.message;
        status.className = 'status error';
      });
  });
});

