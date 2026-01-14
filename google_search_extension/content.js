// Content script that runs on Google search pages
// Scrapes search result URLs and sends them back to background script

console.log('[Content Script] Loaded on Google search page');

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'scrapeResults') {
    console.log('[Content Script] Received scrape request for query:', request.query);
    const urls = scrapeGoogleResults(request.query);
    console.log('[Content Script] Scraped', urls.length, 'URLs');
    sendResponse({urls: urls});
    return true;
  }
});

function scrapeGoogleResults(query) {
  const results = [];
  const seenUrls = new Set();
  
  // Clean query for matching
  const queryWords = query.toLowerCase().split(/\s+/).filter(w => w.length > 2);
  
  // Try multiple selectors for Google search results
  const selectors = [
    'div.g a[href*="/url?q="]',
    'div.tF2Cxc a[href*="/url?q="]',
    'div.yuRUbf a',
    'a[data-ved][href*="/url?q="]',
    'div.g a[href^="http"]'
  ];
  
  let links = [];
  for (const selector of selectors) {
    links = document.querySelectorAll(selector);
    if (links.length > 0) break;
  }
  
  if (links.length === 0) {
    console.log('No search results found');
    return [];
  }
  
  links.forEach((link, index) => {
    if (index >= 30) return; // Limit to first 30 links
    
    try {
      let href = link.getAttribute('href');
      
      if (!href) return;
      
      // Extract actual URL from Google redirect
      let actualUrl = href;
      if (href.includes('/url?q=')) {
        const urlParams = new URLSearchParams(href.split('?')[1] || '');
        actualUrl = urlParams.get('q') || href;
        actualUrl = decodeURIComponent(actualUrl);
      }
      
      // Skip Google internal URLs
      if (actualUrl.includes('google.com') || 
          actualUrl.includes('googleusercontent.com') ||
          actualUrl.includes('youtube.com/watch')) {
        return;
      }
      
      // Skip if we've seen this domain
      try {
        const domain = new URL(actualUrl).hostname;
        if (seenUrls.has(domain)) return;
        seenUrls.add(domain);
      } catch (e) {
        return;
      }
      
      // Get title
      let title = 'No title';
      const titleSelectors = ['h3', 'span[role="heading"]', '.LC20lb', '.DKV0Md'];
      for (const sel of titleSelectors) {
        const titleElem = link.querySelector(sel);
        if (titleElem && titleElem.textContent.trim()) {
          title = titleElem.textContent.trim();
          break;
        }
      }
      
      // Get snippet
      let snippet = '';
      const parent = link.closest('div.g, div.tF2Cxc');
      if (parent) {
        const snippetSelectors = ['.VwiC3b', '.s', '.IsZvec', '.st'];
        for (const sel of snippetSelectors) {
          const snippetElem = parent.querySelector(sel);
          if (snippetElem && snippetElem.textContent.trim()) {
            snippet = snippetElem.textContent.trim().substring(0, 150);
            break;
          }
        }
      }
      
      // Calculate relevance score
      let relevanceScore = 0;
      const urlLower = actualUrl.toLowerCase();
      const titleLower = title.toLowerCase();
      const snippetLower = snippet.toLowerCase();
      
      // Check for query words in URL
      queryWords.forEach(word => {
        if (word.length > 3 && urlLower.includes(word)) {
          relevanceScore += 10;
        }
      });
      
      // Check for query words in title
      queryWords.forEach(word => {
        if (word.length > 3 && titleLower.includes(word)) {
          relevanceScore += 5;
        }
      });
      
      // Check for exact query match
      const queryLower = query.toLowerCase();
      if (urlLower.includes(queryLower) || titleLower.includes(queryLower)) {
        relevanceScore += 20;
      }
      
      // Detect Shopify store
      let isShopify = false;
      if (urlLower.includes('.myshopify.com')) {
        isShopify = true;
        relevanceScore += 15;
      } else if (urlLower.includes('shopify') || snippetLower.includes('shopify')) {
        isShopify = true;
        relevanceScore += 10;
      }
      
      // Clean URL (remove tracking parameters)
      const cleanUrl = cleanUrlParams(actualUrl);
      
      results.push({
        url: cleanUrl,
        title: title,
        snippet: snippet,
        is_shopify: isShopify,
        relevance_score: relevanceScore
      });
    } catch (e) {
      console.error('Error processing link:', e);
    }
  });
  
  // Sort by relevance score
  results.sort((a, b) => b.relevance_score - a.relevance_score);
  
  // Return top 10
  return results.slice(0, 10);
}

function cleanUrlParams(url) {
  try {
    const urlObj = new URL(url);
    const trackingParams = [
      'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
      'gclid', 'fbclid', 'srsltid', 'ref', 'source', 'campaign',
      'affiliate', 'partner', 'promo', 'discount', 'coupon'
    ];
    
    trackingParams.forEach(param => {
      urlObj.searchParams.delete(param);
    });
    
    let cleanUrl = urlObj.toString();
    if (cleanUrl.endsWith('/') && cleanUrl.length > 1) {
      cleanUrl = cleanUrl.slice(0, -1);
    }
    
    return cleanUrl;
  } catch (e) {
    return url;
  }
}

