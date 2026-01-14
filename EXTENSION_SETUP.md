# Chrome Extension Setup Guide

## Installation

1. **Open Chrome Extensions Page**
   - Navigate to `chrome://extensions/` in Chrome
   - Or: Chrome Menu → More Tools → Extensions

2. **Enable Developer Mode**
   - Toggle "Developer mode" switch in top-right corner

3. **Load Extension**
   - Click "Load unpacked" button
   - Select the `google_search_extension` folder
   - Extension should now appear in your extensions list

4. **Verify Installation**
   - You should see "Shopify Store URL Finder" extension
   - Make sure it's enabled (toggle should be ON)

## How It Works

### Communication Flow

1. **User clicks "Find URL"** in Flask web app
2. **Flask requests search** via `/api/search/request` endpoint
3. **Extension polls Flask** every 2 seconds for pending searches
4. **Extension opens Google search** in new tab
5. **Extension scrapes URLs** from search results
6. **Extension sends results** to Flask via `/api/search/extension/submit`
7. **Flask displays URLs** as clickable buttons
8. **User selects correct URL**

### Alternative: Direct Communication

The extension also injects a script into the Flask page that allows direct communication:
- Flask page can call `window.extensionSearch(query)` directly
- Faster than polling method
- Falls back to polling if direct call fails

## Troubleshooting

### Extension Not Working

1. **Check Extension Status**
   - Go to `chrome://extensions/`
   - Make sure extension is enabled
   - Check for any error messages

2. **Check Permissions**
   - Extension needs:
     - Access to Google.com
     - Access to localhost:5000 (Flask server)
   - These are set in `manifest.json`

3. **Check Console**
   - Open Chrome DevTools (F12)
   - Check Console tab for errors
   - Check Background page console (chrome://extensions → Extension details → Service worker)

### No Results Extracted

1. **Google CAPTCHA**
   - If CAPTCHA appears, solve it manually
   - Extension will wait 3 seconds after page loads
   - Results should still be extracted

2. **Page Not Loading**
   - Check if Google search page loaded completely
   - Extension waits for `complete` status
   - May need to wait longer if page is slow

3. **Content Script Not Running**
   - Check if content script is injected
   - Look for console logs in Google search page
   - Verify `content.js` is in manifest.json

### Flask Connection Issues

1. **Flask Server Not Running**
   - Make sure Flask is running on `http://localhost:5000`
   - Extension polls this endpoint

2. **CORS Issues**
   - Flask should have CORS enabled (already configured)
   - Check Flask console for errors

3. **Port Mismatch**
   - Default Flask port is 5000
   - If different, update extension's fetch URLs in `background.js`

## Testing

1. **Test Extension Directly**
   - Open extension popup
   - Click "Test Connection" button
   - Should show "Extension is working!"

2. **Test from Flask**
   - Start Flask server
   - Open Flask web app
   - Click "Find URL" on any store
   - Should see "Extension is searching Google..." message
   - Results should appear within 5-10 seconds

## Files

- `manifest.json` - Extension configuration
- `background.js` - Service worker (handles search requests)
- `content.js` - Scrapes Google search results
- `flask_content.js` - Injected into Flask page for direct communication
- `popup.html/js` - Extension popup UI
- `bridge.html` - Bridge page (alternative communication method)

## Next Steps

After installation, the extension should work automatically. When you click "Find URL" in the Flask app, the extension will:
1. Open Google search
2. Scrape results
3. Send URLs back to Flask
4. Display them as clickable buttons

No additional configuration needed!





