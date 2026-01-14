# Find Store URL - Chrome Extension

A Chrome extension to help find and save store URLs from Shopify reviews data.

## Features

- **Load JSON Data**: Import your `shopify_reviews.json` file
- **Automated Search**: Opens Google search with store name + country
- **Manual Verification**: Browse and verify the correct website
- **URL Saving**: Save verified URLs back to your data
- **Progress Tracking**: Shows current store and progress
- **Export Results**: Download updated JSON with verified URLs

## Installation

1. Open Chrome and go to `chrome://extensions/`
2. Enable "Developer mode" (toggle in top right)
3. Click "Load unpacked"
4. Select the `find_store_url` folder
5. The extension will appear in your toolbar

## Usage

1. **Load Data**: Click the extension icon, then "Load JSON Data" to import your `shopify_reviews.json`
2. **Search**: Click "Search Google" to open search results
3. **Verify**: Browse the results and find the correct store website
4. **Save**: Paste the correct URL and click "Save URL"
5. **Export**: When done, click "Export Progress" to download updated JSON

## Workflow

1. Extension shows: "Store: Maalhaz, Country: [country]"
2. Click "Search Google" → opens new tab with search
3. Browse results, verify the correct website
4. Copy the correct URL and paste it in the extension
5. Click "Save URL" → moves to next store
6. Repeat until all stores are processed

## File Structure

```
find_store_url/
├── manifest.json      # Extension configuration
├── popup.html         # Extension popup interface
├── popup.css          # Styling
├── popup.js           # Main functionality
├── background.js      # Background service worker
└── README.md          # This file
```

## Notes

- The extension works with your existing `shopify_reviews.json` structure
- Only processes stores that don't have a `base_url` field
- Progress is saved automatically in browser storage
- Export creates a new JSON file with verified URLs
