# Debugging Chrome Extension

## Check if Extension is Working

### 1. Verify Extension is Installed
- Go to `chrome://extensions/`
- Find "Shopify Store URL Finder"
- Make sure it's **enabled** (toggle is ON)
- Check for any error messages (red text)

### 2. Check Extension Console
- In `chrome://extensions/`, click "Details" on the extension
- Click "Service worker" (or "Inspect views: background page")
- This opens the background script console
- You should see logs like:
  - `[Extension] Opening Google search for: ...`
  - `[Extension] Google tab opened: ...`
  - `[Extension] Scraped X URLs`

### 3. Check Flask Console
- Look at your Flask server terminal
- You should see logs like:
  - `Search requested: query='...', search_id=...`
  - `Extension requested search: ...`
  - `Extension submitting results: ...`

### 4. Check Google Search Page Console
- When Google search opens, press F12
- Go to Console tab
- You should see:
  - `[Content Script] Loaded on Google search page`
  - `[Content Script] Received scrape request for query: ...`
  - `[Content Script] Scraped X URLs`

## Common Issues

### Extension Not Polling
**Symptom**: No logs in extension console, Flask never receives requests

**Fix**:
1. Reload extension: `chrome://extensions/` → Click reload icon
2. Check if Flask is running on `http://localhost:5000`
3. Check extension permissions (should have access to localhost:5000)

### Extension Polling But Not Processing
**Symptom**: Extension console shows polling but no search happens

**Fix**:
1. Check if `isProcessing` flag is stuck (restart extension)
2. Check if pending searches exist in Flask (check Flask logs)

### No URLs Scraped
**Symptom**: Extension opens Google but returns 0 URLs

**Possible causes**:
1. CAPTCHA appeared - solve it manually, extension will retry
2. Google page structure changed - check content.js selectors
3. Page didn't load completely - increase timeout in background.js

### Flask Not Receiving Results
**Symptom**: Extension scrapes URLs but Flask doesn't get them

**Fix**:
1. Check Flask console for errors
2. Check extension console for fetch errors
3. Verify CORS is enabled in Flask (should be already)
4. Check network tab in extension console for failed requests

## Manual Testing

### Test Extension Directly
1. Open extension popup (click extension icon)
2. Click "Test Connection"
3. Should show "Extension is working!"

### Test Flask Endpoint
```bash
curl http://localhost:5000/api/search/extension/status
```
Should return: `{"status": "active", "message": "Extension can reach Flask server"}`

### Test Search Request
```bash
curl -X POST http://localhost:5000/api/search/request \
  -H "Content-Type: application/json" \
  -d '{"store_name": "test store"}'
```
Should return a `search_id`

### Check Pending Searches
```bash
curl http://localhost:5000/api/search/extension/pending
```
Should return the search query if extension hasn't picked it up yet

## Step-by-Step Debugging

1. **Click "Find URL" in Flask app**
   - Check Flask console: Should see "Search requested: ..."
   - Check extension console: Should see polling logs

2. **Extension picks up search**
   - Check extension console: Should see "Found pending search: ..."
   - Check Flask console: Should see "Extension requested search: ..."

3. **Google search opens**
   - Check extension console: Should see "Opening Google search for: ..."
   - Check extension console: Should see "Google tab opened: ..."

4. **Page loads**
   - Check Google page console (F12): Should see "[Content Script] Loaded..."
   - Check extension console: Should see "Google page loaded, waiting 3 seconds..."

5. **Scraping happens**
   - Check Google page console: Should see "[Content Script] Received scrape request..."
   - Check Google page console: Should see "[Content Script] Scraped X URLs"

6. **Results sent to Flask**
   - Check extension console: Should see "[Extension] Sending results to Flask..."
   - Check Flask console: Should see "Extension submitting results: ..."

7. **Frontend receives results**
   - Check browser console (F12 on Flask page): Should see polling success
   - URLs should appear as buttons

## Reset Everything

If nothing works:
1. Reload extension: `chrome://extensions/` → Reload
2. Restart Flask server
3. Clear browser cache
4. Try again





