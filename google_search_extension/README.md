# Shopify Store URL Finder - Chrome Extension

Chrome extension that helps the Shopify Review Processor find store URLs from Google search results.

## Installation

1. Open Chrome and navigate to `chrome://extensions/`
2. Enable "Developer mode" (toggle in top right)
3. Click "Load unpacked"
4. Select the `google_search_extension` folder
5. The extension is now installed!

## How It Works

1. Flask app requests a search via `/api/search/request`
2. Extension receives the search query
3. Extension opens Google search in a new tab
4. Extension scrapes URLs from search results
5. Extension sends results back to Flask via `/api/search/extension/submit`
6. Flask displays URLs as clickable buttons

## Permissions

- **tabs**: To open Google search in new tabs
- **activeTab**: To access the current tab
- **scripting**: To inject content scripts
- **storage**: To temporarily store search results
- **host_permissions**: Access to Google and localhost Flask server

## Usage

1. Make sure the Flask server is running on `http://localhost:5001`
2. Click "Find URL" in the Flask web app
3. Extension will automatically:
   - Open Google search
   - Scrape results
   - Send URLs back to Flask
4. Select the correct URL from the list

## Troubleshooting

- **Extension not working**: Check that it's enabled in `chrome://extensions/`
- **No results**: Make sure Google search page loaded completely (may need to solve CAPTCHA)
- **Connection error**: Ensure Flask server is running on port 5001

## Files

- `manifest.json`: Extension configuration
- `background.js`: Service worker that handles search requests
- `content.js`: Script that runs on Google search pages to scrape URLs
- `popup.html/js`: Extension popup UI (optional)
- `bridge.html`: Bridge page for communication (if needed)

