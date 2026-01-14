// Background service worker for the extension
chrome.runtime.onInstalled.addListener(() => {
  console.log('Find Store URL extension installed');
});

// Handle messages from popup and content script
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'saveProgress') {
    // Handle saving progress to file
    saveProgressToFile(request.data)
      .then(() => sendResponse({ success: true }))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true; // Keep message channel open for async response
  }
  
  if (request.action === 'captureURL') {
    // Handle URL capture and auto-search
    handleURLCapture(request.url, request.store)
      .then(() => sendResponse({ success: true }))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true; // Keep message channel open for async response
  }
});

async function handleURLCapture(url, store) {
  try {
    console.log('Background: Handling URL capture for store:', store.store_name);
    
    // Get current data from storage
    const result = await chrome.storage.local.get(['stores', 'currentIndex', 'autoSearchEnabled']);
    let stores = result.stores || [];
    let currentIndex = result.currentIndex || 0;
    const autoSearchEnabled = result.autoSearchEnabled !== false; // default to true
    
    // Update the store with the captured URL
    const storeIndex = stores.findIndex(s => s.store_name === store.store_name);
    if (storeIndex !== -1) {
      stores[storeIndex].base_url = url;
      stores[storeIndex].url_verified = true;
      stores[storeIndex].verified_at = new Date().toISOString();
      
      // Move to next store
      currentIndex++;
      let nextStore = null;
      
      if (currentIndex < stores.length) {
        nextStore = stores[currentIndex];
      }
      
      // Save updated data
      await chrome.storage.local.set({
        stores: stores,
        currentIndex: currentIndex
      });
      
      console.log('Background: URL saved, moved to store index:', currentIndex);
      
      // Auto-search for next store if enabled and exists
      if (autoSearchEnabled && nextStore) {
        console.log('Background: Auto-searching for next store:', nextStore.store_name);
        await autoSearchNextStore(nextStore);
      } else if (!nextStore) {
        console.log('Background: No more stores to process');
      } else {
        console.log('Background: Auto-search disabled');
      }
    }
  } catch (error) {
    console.error('Background: Error handling URL capture:', error);
    throw error;
  }
}

async function autoSearchNextStore(store) {
  try {
    const storeName = store.store_name;
    const country = store.country || '';
    const searchQuery = `"${storeName}" ${country}`;
    const encodedQuery = encodeURIComponent(searchQuery);
    const googleURL = `https://www.google.com/search?q=${encodedQuery}`;
    
    console.log('Background: Auto-searching with query:', searchQuery);
    console.log('Background: Google URL:', googleURL);
    
    // Store the current store info for the content script
    await chrome.storage.local.set({ 
      currentStore: store,
      showCaptureButton: true,
      buttonActive: true 
    });
    
    // Open in new tab
    await chrome.tabs.create({ url: googleURL });
    
    console.log('Background: Auto-search completed for:', storeName);
  } catch (error) {
    console.error('Background: Error in auto-search:', error);
  }
}

async function saveProgressToFile(data) {
  try {
    // Create downloadable JSON file
    const jsonData = JSON.stringify(data, null, 2);
    const blob = new Blob([jsonData], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    
    // Trigger download
    await chrome.downloads.download({
      url: url,
      filename: 'shopify_reviews_updated.json',
      saveAs: true
    });
    
    // Clean up
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    console.error('Error saving file:', error);
    throw error;
  }
}
