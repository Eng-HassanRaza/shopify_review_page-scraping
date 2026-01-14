// Extension popup functionality
class StoreURLFinder {
  constructor() {
    this.currentStore = null;
    this.stores = [];
    this.currentIndex = 0;
    this.init();
    this.setupMessageListener();
  }

  async init() {
    this.setupEventListeners();
    await this.loadStores();
    await this.loadSettings();
    this.updateDisplay();
  }

  setupEventListeners() {
    document.getElementById('search-btn').addEventListener('click', () => this.searchGoogle());
    document.getElementById('skip-btn').addEventListener('click', () => this.skipStore());
    document.getElementById('save-btn').addEventListener('click', () => this.saveURL());
    document.getElementById('cancel-btn').addEventListener('click', () => this.cancelURL());
    document.getElementById('capture-current-btn').addEventListener('click', () => this.captureCurrentURL());
    document.getElementById('load-data-btn').addEventListener('click', () => this.loadJSONData());
    document.getElementById('test-button-btn').addEventListener('click', () => this.testButton());
    document.getElementById('clear-button-btn').addEventListener('click', () => this.clearButton());
    document.getElementById('export-btn').addEventListener('click', () => this.exportProgress());
    document.getElementById('auto-search-toggle').addEventListener('change', (e) => this.saveAutoSearchPreference(e.target.checked));
  }

  setupMessageListener() {
    // Listen for messages from content script (if needed)
    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
      // Handle any popup-specific messages here
      sendResponse({ success: true });
    });
  }

  autoSearchNextStore() {
    console.log('autoSearchNextStore called, currentStore:', this.currentStore);
    
    if (!this.currentStore) {
      console.log('No current store, cannot auto-search');
      return;
    }
    
    const storeName = this.currentStore.store_name;
    const country = this.currentStore.country || '';
    const searchQuery = `"${storeName}" ${country}`;
    const encodedQuery = encodeURIComponent(searchQuery);
    const googleURL = `https://www.google.com/search?q=${encodedQuery}`;
    
    console.log('Auto-searching with query:', searchQuery);
    console.log('Google URL:', googleURL);
    
    // Store the current store info for the content script
    chrome.storage.local.set({ 
      currentStore: this.currentStore,
      showCaptureButton: true,
      buttonActive: true 
    });
    
    // Open in new tab
    chrome.tabs.create({ url: googleURL });
    
    this.showStatus(`Auto-searching for: ${storeName}`, 'success');
  }

  async loadStores() {
    try {
      // Try to load from storage first
      const result = await chrome.storage.local.get(['stores', 'currentIndex']);
      this.stores = result.stores || [];
      this.currentIndex = result.currentIndex || 0;
      
      if (this.stores.length === 0) {
        this.showStatus('No stores loaded. Click "Load JSON Data" to start.', 'error');
        return;
      }
      
      this.currentStore = this.stores[this.currentIndex];
    } catch (error) {
      console.error('Error loading stores:', error);
      this.showStatus('Error loading stores', 'error');
    }
  }

  async loadSettings() {
    try {
      const result = await chrome.storage.local.get(['autoSearchEnabled']);
      const autoSearchEnabled = result.autoSearchEnabled !== false; // default to true
      document.getElementById('auto-search-toggle').checked = autoSearchEnabled;
    } catch (error) {
      console.error('Error loading settings:', error);
    }
  }

  async saveAutoSearchPreference(enabled) {
    try {
      await chrome.storage.local.set({ autoSearchEnabled: enabled });
      console.log('Auto-search preference saved:', enabled);
    } catch (error) {
      console.error('Error saving auto-search preference:', error);
    }
  }

  async loadJSONData() {
    try {
      // Create file input for user to select JSON file
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = '.json';
      input.onchange = async (e) => {
        const file = e.target.files[0];
        if (file) {
          const text = await file.text();
          const data = JSON.parse(text);
          this.processJSONData(data);
        }
      };
      input.click();
    } catch (error) {
      console.error('Error loading JSON:', error);
      this.showStatus('Error loading JSON file', 'error');
    }
  }

  processJSONData(data) {
    // Extract stores that need URLs
    this.stores = data.filter(store => 
      store.store_name && 
      (!store.base_url || store.base_url === '') &&
      store.country
    );
    
    this.currentIndex = 0;
    this.currentStore = this.stores[this.currentIndex];
    
    // Save to storage
    chrome.storage.local.set({
      stores: this.stores,
      currentIndex: this.currentIndex
    });
    
    this.updateDisplay();
    this.showStatus(`Loaded ${this.stores.length} stores`, 'success');
  }

  updateDisplay() {
    if (!this.currentStore) {
      document.getElementById('store-name').textContent = 'No more stores';
      document.getElementById('store-country').textContent = '';
      document.getElementById('progress-text').textContent = 'Complete';
      document.getElementById('search-btn').disabled = true;
      return;
    }

    document.getElementById('store-name').textContent = this.currentStore.store_name;
    document.getElementById('store-country').textContent = this.currentStore.country || 'Unknown';
    document.getElementById('progress-text').textContent = 
      `Store ${this.currentIndex + 1} of ${this.stores.length}`;
    
    // Hide URL section
    document.getElementById('url-section').style.display = 'none';
    document.getElementById('search-btn').disabled = false;
  }

  searchGoogle() {
    if (!this.currentStore) return;
    
    const storeName = this.currentStore.store_name;
    const country = this.currentStore.country || '';
    const searchQuery = `"${storeName}" ${country}`;
    const encodedQuery = encodeURIComponent(searchQuery);
    const googleURL = `https://www.google.com/search?q=${encodedQuery}`;
    
    // Store the current store info for the content script
    chrome.storage.local.set({ 
      currentStore: this.currentStore,
      showCaptureButton: true,
      buttonActive: true 
    });
    
    // Open in new tab
    chrome.tabs.create({ url: googleURL });
    
    this.showStatus('Search opened. Navigate to a store website to see the floating button!', 'success');
  }

  skipStore() {
    this.nextStore();
    this.showStatus('Store skipped', 'success');
  }

  async saveURL() {
    const url = document.getElementById('found-url').value.trim();
    
    if (!url) {
      this.showStatus('Please enter a URL', 'error');
      return;
    }
    
    if (!this.isValidURL(url)) {
      this.showStatus('Please enter a valid URL', 'error');
      return;
    }
    
    try {
      // Update the store with the URL
      this.currentStore.base_url = url;
      this.currentStore.url_verified = true;
      this.currentStore.verified_at = new Date().toISOString();
      
      // Save to storage
      await chrome.storage.local.set({
        stores: this.stores,
        currentIndex: this.currentIndex
      });
      
      this.showStatus('URL saved successfully!', 'success');
      
      // Clear URL input
      document.getElementById('found-url').value = '';
      document.getElementById('url-section').style.display = 'none';
      
      // Move to next store after a short delay
      setTimeout(() => this.nextStore(), 1000);
      
    } catch (error) {
      console.error('Error saving URL:', error);
      this.showStatus('Error saving URL', 'error');
    }
  }

  cancelURL() {
    document.getElementById('found-url').value = '';
    document.getElementById('url-section').style.display = 'none';
    this.showStatus('Cancelled', 'success');
  }

  async captureCurrentURL() {
    try {
      // Get the current active tab
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      
      if (!tab || !tab.url) {
        this.showStatus('No active tab found', 'error');
        return;
      }
      
      // Check if it's a valid URL (not chrome:// or extension pages)
      if (tab.url.startsWith('chrome://') || tab.url.startsWith('chrome-extension://')) {
        this.showStatus('Cannot capture URL from this page', 'error');
        return;
      }
      
      // Set the URL in the input field
      document.getElementById('found-url').value = tab.url;
      this.showStatus('Current page URL captured!', 'success');
      
    } catch (error) {
      console.error('Error capturing URL:', error);
      this.showStatus('Error capturing URL', 'error');
    }
  }

  nextStore() {
    this.currentIndex++;
    
    if (this.currentIndex >= this.stores.length) {
      this.currentStore = null;
      this.showStatus('All stores processed!', 'success');
    } else {
      this.currentStore = this.stores[this.currentIndex];
    }
    
    // Save current index
    chrome.storage.local.set({ currentIndex: this.currentIndex });
    
    this.updateDisplay();
  }

  isValidURL(string) {
    try {
      new URL(string);
      return true;
    } catch (_) {
      return false;
    }
  }

  showStatus(message, type = '') {
    const statusEl = document.getElementById('status');
    statusEl.textContent = message;
    statusEl.className = `status ${type}`;
    
    // Clear status after 3 seconds
    setTimeout(() => {
      statusEl.textContent = '';
      statusEl.className = 'status';
    }, 3000);
  }

  testButton() {
    // Force show the capture button on current page
    chrome.storage.local.set({ 
      currentStore: this.currentStore || { store_name: 'Test Store', country: 'Test Country' },
      showCaptureButton: true,
      buttonActive: true 
    });
    
    // Send message to current tab
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, {
          action: 'showCaptureButton',
          store: this.currentStore || { store_name: 'Test Store', country: 'Test Country' }
        });
      }
    });
    
    this.showStatus('Test button triggered! Check the current page.', 'success');
  }

  clearButton() {
    // Clear the button from all tabs
    chrome.storage.local.set({ 
      buttonActive: false,
      showCaptureButton: false 
    });
    
    // Send message to all tabs to hide button
    chrome.tabs.query({}, (tabs) => {
      tabs.forEach(tab => {
        chrome.tabs.sendMessage(tab.id, {
          action: 'hideCaptureButton'
        }, () => {
          // Ignore errors for tabs that don't have content script
        });
      });
    });
    
    this.showStatus('Floating button cleared from all pages', 'success');
  }

  async exportProgress() {
    try {
      const dataStr = JSON.stringify(this.stores, null, 2);
      const dataBlob = new Blob([dataStr], { type: 'application/json' });
      const url = URL.createObjectURL(dataBlob);
      
      const a = document.createElement('a');
      a.href = url;
      a.download = 'shopify_reviews_updated.json';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      
      this.showStatus('Progress exported!', 'success');
    } catch (error) {
      console.error('Error exporting:', error);
      this.showStatus('Error exporting data', 'error');
    }
  }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
  new StoreURLFinder();
});
