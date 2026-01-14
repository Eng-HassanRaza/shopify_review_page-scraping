// Content script that runs on web pages
class StoreURLCapture {
  constructor() {
    this.currentStore = null;
    this.init();
  }

  init() {
    // Listen for messages from popup
    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
      if (request.action === 'showCaptureButton') {
        this.currentStore = request.store;
        this.showCaptureButton();
        sendResponse({ success: true });
      } else if (request.action === 'hideCaptureButton') {
        this.hideCaptureButton();
        sendResponse({ success: true });
      }
    });

    // Check if there's a pending store to show button for
    this.checkForPendingStore();
  }

  async checkForPendingStore() {
    try {
      const result = await chrome.storage.local.get(['currentStore', 'showCaptureButton', 'buttonActive']);
      if ((result.showCaptureButton || result.buttonActive) && result.currentStore) {
        console.log('Found pending store:', result.currentStore);
        this.currentStore = result.currentStore;
        this.showCaptureButton();
        
        // Keep the button active across page navigations
        chrome.storage.local.set({ 
          showCaptureButton: false,
          buttonActive: true 
        });
      }
    } catch (error) {
      console.error('Error checking for pending store:', error);
    }
  }

  showCaptureButton() {
    console.log('Showing capture button for store:', this.currentStore);
    
    // Remove existing button if any
    this.hideCaptureButton();

    // Create floating button
    const button = document.createElement('div');
    button.id = 'store-url-capture-btn';
    button.innerHTML = `
      <div class="capture-button">
        <div class="store-info">
          <strong>${this.currentStore.store_name}</strong>
          <span>${this.currentStore.country || ''}</span>
        </div>
        <button class="capture-btn">✓ This is the correct store</button>
        <button class="close-btn">×</button>
      </div>
    `;

    // Add styles
    const style = document.createElement('style');
    style.textContent = `
      #store-url-capture-btn {
        position: fixed;
        bottom: 20px;
        left: 20px;
        z-index: 10000;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        animation: slideIn 0.3s ease-out;
      }
      
      .capture-button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 16px 20px;
        border-radius: 12px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        display: flex;
        align-items: center;
        gap: 12px;
        max-width: 400px;
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255,255,255,0.2);
      }
      
      .store-info {
        display: flex;
        flex-direction: column;
        gap: 2px;
        flex: 1;
      }
      
      .store-info strong {
        font-size: 14px;
        font-weight: 600;
        line-height: 1.2;
      }
      
      .store-info span {
        font-size: 12px;
        opacity: 0.9;
      }
      
      .capture-btn {
        background: #28a745;
        color: white;
        border: none;
        padding: 10px 16px;
        border-radius: 8px;
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.2s ease;
        white-space: nowrap;
      }
      
      .capture-btn:hover {
        background: #1e7e34;
        transform: translateY(-1px);
      }
      
      .close-btn {
        background: rgba(255,255,255,0.2);
        color: white;
        border: none;
        width: 24px;
        height: 24px;
        border-radius: 50%;
        font-size: 16px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.2s ease;
      }
      
      .close-btn:hover {
        background: rgba(255,255,255,0.3);
      }
      
      @keyframes slideIn {
        from {
          transform: translateX(-100%);
          opacity: 0;
        }
        to {
          transform: translateX(0);
          opacity: 1;
        }
      }
      
      @media (max-width: 768px) {
        #store-url-capture-btn {
          bottom: 10px;
          left: 10px;
          right: 10px;
        }
        
        .capture-button {
          flex-direction: column;
          text-align: center;
          gap: 8px;
        }
        
        .store-info {
          text-align: center;
        }
      }
    `;

    document.head.appendChild(style);
    document.body.appendChild(button);

    // Add event listeners
    button.querySelector('.capture-btn').addEventListener('click', () => {
      this.captureCurrentURL();
    });

    button.querySelector('.close-btn').addEventListener('click', () => {
      this.hideCaptureButton();
    });

    // Auto-hide after 30 seconds if no interaction
    setTimeout(() => {
      if (document.getElementById('store-url-capture-btn')) {
        this.hideCaptureButton();
      }
    }, 30000);
  }

  hideCaptureButton() {
    const button = document.getElementById('store-url-capture-btn');
    if (button) {
      button.remove();
    }
  }

  async captureCurrentURL() {
    try {
      const currentURL = window.location.href;
      
      console.log('Content: Sending URL to background script:', currentURL);
      console.log('Content: Store:', this.currentStore);
      
      // Send URL to background script
      chrome.runtime.sendMessage({
        action: 'captureURL',
        url: currentURL,
        store: this.currentStore
      }, (response) => {
        if (chrome.runtime.lastError) {
          console.error('Content: Error sending message:', chrome.runtime.lastError);
        } else {
          console.log('Content: Message sent successfully:', response);
        }
      });

      // Show success message
      this.showSuccessMessage();
      
      // Clear the button active state
      chrome.storage.local.set({ buttonActive: false });
      
      // Hide button after a short delay
      setTimeout(() => {
        this.hideCaptureButton();
      }, 2000);

    } catch (error) {
      console.error('Error capturing URL:', error);
      this.showErrorMessage();
    }
  }

  showSuccessMessage() {
    const button = document.getElementById('store-url-capture-btn');
    if (button) {
      const captureBtn = button.querySelector('.capture-btn');
      const originalText = captureBtn.textContent;
      captureBtn.textContent = '✓ Saved!';
      captureBtn.style.background = '#28a745';
      captureBtn.disabled = true;
      
      setTimeout(() => {
        this.hideCaptureButton();
      }, 1500);
    }
  }

  showErrorMessage() {
    const button = document.getElementById('store-url-capture-btn');
    if (button) {
      const captureBtn = button.querySelector('.capture-btn');
      captureBtn.textContent = '✗ Error';
      captureBtn.style.background = '#dc3545';
    }
  }
}

// Initialize when script loads
new StoreURLCapture();
