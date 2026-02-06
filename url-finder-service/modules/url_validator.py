"""URL validation module - checks DNS resolution and HTTP status before saving URLs"""
import logging
import socket
import requests
from typing import Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class URLValidator:
    """Validates URLs by checking DNS resolution and HTTP status"""
    
    def __init__(self, timeout: int = 10, follow_redirects: bool = True, max_redirects: int = 3):
        """
        Initialize URL validator.
        
        Args:
            timeout: Request timeout in seconds
            follow_redirects: Whether to follow HTTP redirects (requests library handles this automatically)
            max_redirects: Maximum redirects to follow (not used by requests library, kept for API compatibility)
        """
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        # Note: requests library doesn't support max_redirects parameter directly
        # It follows redirects automatically when allow_redirects=True
        self.max_redirects = max_redirects  # Kept for API compatibility but not used
    
    def validate_url(self, url: str) -> Dict[str, any]:
        """
        Validate a URL by checking DNS resolution and HTTP status.
        
        Args:
            url: URL to validate
            
        Returns:
            {
                'is_valid': bool,
                'status_code': int or None,
                'final_url': str or None,
                'error': str or None,
                'error_type': str or None  # 'dns', 'timeout', 'connection', 'http_error'
            }
        """
        if not url:
            return {
                'is_valid': False,
                'status_code': None,
                'final_url': None,
                'error': 'URL is empty',
                'error_type': 'connection'
            }
        
        # Normalize URL
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
            
            if not hostname:
                return {
                    'is_valid': False,
                    'status_code': None,
                    'final_url': None,
                    'error': 'Invalid URL format - no hostname',
                    'error_type': 'connection'
                }
            
            # Step 1: Check DNS resolution
            try:
                socket.gethostbyname(hostname)
            except socket.gaierror as e:
                logger.warning(f"DNS resolution failed for {url}: {e}")
                return {
                    'is_valid': False,
                    'status_code': None,
                    'final_url': None,
                    'error': f'DNS resolution failed: {str(e)}',
                    'error_type': 'dns'
                }
            except Exception as e:
                logger.warning(f"DNS check error for {url}: {e}")
                return {
                    'is_valid': False,
                    'status_code': None,
                    'final_url': None,
                    'error': f'DNS check error: {str(e)}',
                    'error_type': 'dns'
                }
            
            # Step 2: Check HTTP status
            try:
                # Use HEAD request first (faster), fallback to GET if HEAD not supported
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                }
                
                # Try HEAD first (faster)
                try:
                    response = requests.head(
                        url,
                        headers=headers,
                        timeout=self.timeout,
                        allow_redirects=self.follow_redirects
                    )
                except requests.exceptions.RequestException:
                    # If HEAD fails, try GET
                    response = requests.get(
                        url,
                        headers=headers,
                        timeout=self.timeout,
                        allow_redirects=self.follow_redirects,
                        stream=True  # Don't download full content
                    )
                    # Close the connection immediately
                    response.close()
                
                status_code = response.status_code
                final_url = response.url
                
                # Consider 2xx and 3xx as valid (3xx are redirects, which we followed)
                if 200 <= status_code < 400:
                    logger.info(f"URL validation successful: {url} -> {final_url} (status: {status_code})")
                    return {
                        'is_valid': True,
                        'status_code': status_code,
                        'final_url': final_url,
                        'error': None,
                        'error_type': None
                    }
                else:
                    # 4xx or 5xx - URL exists but returns error
                    logger.warning(f"URL validation failed: {url} -> {final_url} (status: {status_code})")
                    return {
                        'is_valid': False,
                        'status_code': status_code,
                        'final_url': final_url,
                        'error': f'HTTP {status_code}',
                        'error_type': 'http_error'
                    }
                    
            except requests.exceptions.Timeout:
                logger.warning(f"URL validation timeout for {url}")
                return {
                    'is_valid': False,
                    'status_code': None,
                    'final_url': None,
                    'error': f'Request timeout after {self.timeout}s',
                    'error_type': 'timeout'
                }
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"URL validation connection error for {url}: {e}")
                return {
                    'is_valid': False,
                    'status_code': None,
                    'final_url': None,
                    'error': f'Connection error: {str(e)}',
                    'error_type': 'connection'
                }
            except requests.exceptions.RequestException as e:
                logger.warning(f"URL validation request error for {url}: {e}")
                return {
                    'is_valid': False,
                    'status_code': None,
                    'final_url': None,
                    'error': f'Request error: {str(e)}',
                    'error_type': 'connection'
                }
                
        except Exception as e:
            logger.error(f"Unexpected error validating URL {url}: {e}", exc_info=True)
            return {
                'is_valid': False,
                'status_code': None,
                'final_url': None,
                'error': f'Unexpected error: {str(e)}',
                'error_type': 'connection'
            }
