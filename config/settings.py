"""
Configuration settings and validation for ExploiterX.

Handles loading configuration from YAML files and environment variables,
with sensible defaults and validation.
"""

import os
import re
from typing import Optional, Dict, Any


# ============================================================================
# DEFAULT SETTINGS
# ============================================================================

DEFAULT_TIMEOUT: int = 10
DEFAULT_THREADS: int = 5
DEFAULT_RETRIES: int = 3
DEFAULT_LOG_LEVEL: str = "INFO"
DEFAULT_CONFIG_FILE: str = "config/config.yaml"
DEFAULT_MAX_DEPTH: int = 10
DEFAULT_MAX_LINKS: int = 0
DEFAULT_DELAY: float = 0.0
DEFAULT_TIME_BASED_THRESHOLD: float = 3.0


class ConfigurationError(Exception):
    """Raised when configuration validation fails."""
    pass


class URLValidator:
    """Validates and normalizes URLs."""
    
    URL_PATTERN: str = r'^https?://[^\s/$.?#].[^\s]*$'
    
    @staticmethod
    def validate(url: str) -> bool:
        """
        Validate URL format.
        
        Args:
            url: URL to validate
            
        Returns:
            bool: True if valid URL, False otherwise
        """
        if not url or not isinstance(url, str):
            return False
        
        url = url.strip()
        
        # Must start with http:// or https://
        if not url.startswith(('http://', 'https://')):
            return False
        
        # Must match URL pattern
        if not re.match(URLValidator.URL_PATTERN, url):
            return False
        
        return True
    
    @staticmethod
    def normalize(url: str) -> str:
        """
        Normalize a URL.
        
        Args:
            url: URL to normalize
            
        Returns:
            str: Normalized URL
        """
        url = url.strip()
        
        # Add http:// if no scheme provided
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url
        
        # Remove trailing slash
        url = url.rstrip('/')
        
        return url


class Settings:
    """
    Application settings configuration.
    
    Handles loading, validation, and management of configuration parameters.
    """
    
    def __init__(self) -> None:
        """Initialize default settings."""
        self.timeout: int = DEFAULT_TIMEOUT
        self.threads: int = DEFAULT_THREADS
        self.retries: int = DEFAULT_RETRIES
        self.log_level: str = DEFAULT_LOG_LEVEL
        self.log_file: Optional[str] = "exploiterx.log"
        self.target_url: Optional[str] = None
        self.ignore_links: list = []
        self.auth_required: bool = False
        self.auth_url: Optional[str] = None
        self.auth_username: Optional[str] = None
        self.auth_password: Optional[str] = None
        self.max_depth: int = DEFAULT_MAX_DEPTH
        self.max_links: int = DEFAULT_MAX_LINKS
        self.delay: float = DEFAULT_DELAY
        self.time_based_threshold: float = DEFAULT_TIME_BASED_THRESHOLD
        self.output_format: list = ["console"]
        self.user_agent: Optional[str] = None
        self.verify_ssl: bool = True
        self.follow_redirects: bool = True
    
    def validate_timeout(self, timeout: int) -> None:
        """
        Validate timeout setting.
        
        Args:
            timeout: Timeout in seconds
            
        Raises:
            ConfigurationError: If timeout is invalid
        """
        if not isinstance(timeout, int):
            raise ConfigurationError("Timeout must be an integer")
        
        if timeout < 1 or timeout > 300:
            raise ConfigurationError("Timeout must be between 1 and 300 seconds")
        
        self.timeout = timeout
    
    def validate_threads(self, threads: int) -> None:
        """
        Validate thread count setting.
        
        Args:
            threads: Number of threads
            
        Raises:
            ConfigurationError: If threads value is invalid
        """
        if not isinstance(threads, int):
            raise ConfigurationError("Threads must be an integer")
        
        if threads < 1 or threads > 50:
            raise ConfigurationError("Threads must be between 1 and 50")
        
        self.threads = threads
    
    def validate_retries(self, retries: int) -> None:
        """
        Validate retries setting.
        
        Args:
            retries: Number of retries
            
        Raises:
            ConfigurationError: If retries value is invalid
        """
        if not isinstance(retries, int):
            raise ConfigurationError("Retries must be an integer")
        
        if retries < 0 or retries > 10:
            raise ConfigurationError("Retries must be between 0 and 10")
        
        self.retries = retries
    
    def validate_log_level(self, log_level: str) -> None:
        """
        Validate log level setting.
        
        Args:
            log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            
        Raises:
            ConfigurationError: If log level is invalid
        """
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        
        if log_level.upper() not in valid_levels:
            raise ConfigurationError(f"Log level must be one of {valid_levels}")
        
        self.log_level = log_level.upper()
    
    def validate_max_depth(self, max_depth: int) -> None:
        """
        Validate maximum crawl depth.
        
        Args:
            max_depth: Maximum crawl depth
            
        Raises:
            ConfigurationError: If max_depth is invalid
        """
        if not isinstance(max_depth, int) or isinstance(max_depth, bool):
            raise ConfigurationError("Max depth must be an integer")
        
        if max_depth < 0 or max_depth > 50:
            raise ConfigurationError("Max depth must be between 0 and 50")
        
        self.max_depth = max_depth
    
    def validate_max_links(self, max_links: int) -> None:
        """
        Validate maximum number of links to crawl.
        
        Args:
            max_links: Maximum link count (0 = unlimited)
            
        Raises:
            ConfigurationError: If max_links is invalid
        """
        if not isinstance(max_links, int) or isinstance(max_links, bool):
            raise ConfigurationError("Max links must be an integer")
        
        if max_links < 0 or max_links > 100000:
            raise ConfigurationError("Max links must be between 0 and 100000")
        
        self.max_links = max_links
    
    def validate_delay(self, delay: float) -> None:
        """
        Validate delay between requests.
        
        Args:
            delay: Delay in seconds
            
        Raises:
            ConfigurationError: If delay is invalid
        """
        if not isinstance(delay, (int, float)) or isinstance(delay, bool):
            raise ConfigurationError("Delay must be a number")
        
        if delay < 0 or delay > 60:
            raise ConfigurationError("Delay must be between 0 and 60 seconds")
        
        self.delay = float(delay)
    
    def validate_time_based_threshold(self, threshold: float) -> None:
        """
        Validate the time-based detection threshold.
        
        Args:
            threshold: Minimum seconds a time-based payload must delay the
                response before being flagged as vulnerable
            
        Raises:
            ConfigurationError: If threshold is invalid
        """
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            raise ConfigurationError("Time-based threshold must be a number")
        
        if threshold <= 0 or threshold > 60:
            raise ConfigurationError("Time-based threshold must be between 0 and 60 seconds")
        
        self.time_based_threshold = float(threshold)
    
    def set_output_format(self, output_format) -> None:
        """
        Set output report formats.
        
        Args:
            output_format: List of formats or comma-separated string
        """
        if isinstance(output_format, str):
            output_format = [
                fmt.strip().lower() for fmt in output_format.split(",") if fmt.strip()
            ]
        
        if not isinstance(output_format, list):
            raise ConfigurationError("Output format must be a list or comma-separated string")
        
        valid_formats = {"json", "csv", "html", "console"}
        for fmt in output_format:
            if fmt not in valid_formats:
                raise ConfigurationError(
                    f"Invalid output format '{fmt}'. Must be one of {sorted(valid_formats)}"
                )
        
        self.output_format = output_format
    
    def validate_target_url(self, url: str) -> None:
        """
        Validate target URL.
        
        Args:
            url: Target URL
            
        Raises:
            ConfigurationError: If URL is invalid
        """
        if not URLValidator.validate(url):
            raise ConfigurationError(f"Invalid URL: {url}")
        
        self.target_url = URLValidator.normalize(url)
    
    def set_ignore_links(self, links: list) -> None:
        """
        Set ignore links list.
        
        Args:
            links: List of URL patterns to ignore
        """
        if isinstance(links, str):
            # Convert comma-separated string to list
            links = [link.strip() for link in links.split(",")]
        
        if not isinstance(links, list):
            raise ConfigurationError("Ignore links must be a list or comma-separated string")
        
        self.ignore_links = links
    
    def set_authentication(
        self,
        auth_url: str,
        username: str,
        password: str
    ) -> None:
        """
        Set authentication parameters.
        
        Args:
            auth_url: URL for authentication
            username: Username
            password: Password
            
        Raises:
            ConfigurationError: If authentication parameters are invalid
        """
        if not URLValidator.validate(auth_url):
            raise ConfigurationError(f"Invalid authentication URL: {auth_url}")
        
        if not username or not password:
            raise ConfigurationError("Username and password cannot be empty")
        
        self.auth_required = True
        self.auth_url = URLValidator.normalize(auth_url)
        self.auth_username = username
        self.auth_password = password
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert settings to dictionary.
        
        Returns:
            Dictionary representation of settings
        """
        return {
            "timeout": self.timeout,
            "threads": self.threads,
            "retries": self.retries,
            "log_level": self.log_level,
            "log_file": self.log_file,
            "target_url": self.target_url,
            "ignore_links": self.ignore_links,
            "auth_required": self.auth_required,
            "auth_url": self.auth_url,
            "max_depth": self.max_depth,
            "max_links": self.max_links,
            "delay": self.delay,
            "time_based_threshold": self.time_based_threshold,
            "output_format": self.output_format,
            "verify_ssl": self.verify_ssl,
            "follow_redirects": self.follow_redirects,
        }


def load_config(config_file: Optional[str] = None) -> Settings:
    """
    Load configuration from file or use defaults.
    
    Args:
        config_file: Path to configuration file (YAML)
        
    Returns:
        Settings: Configured Settings instance
        
    Example:
        >>> settings = load_config("config/config.yaml")
        >>> print(settings.timeout)
        10
    """
    settings = Settings()
    
    # Try to load from YAML file if provided
    if config_file and os.path.exists(config_file):
        try:
            import yaml
            
            with open(config_file, 'r') as f:
                config_data = yaml.safe_load(f)
            
            if config_data is None:
                return settings
            
            # Apply settings from file
            if "timeout" in config_data:
                settings.validate_timeout(config_data["timeout"])
            
            if "threads" in config_data:
                settings.validate_threads(config_data["threads"])
            
            if "retries" in config_data:
                settings.validate_retries(config_data["retries"])
            
            if "log_level" in config_data:
                settings.validate_log_level(config_data["log_level"])
            
            if "log_file" in config_data:
                settings.log_file = config_data["log_file"]
            
            if "ignore_links" in config_data:
                settings.set_ignore_links(config_data["ignore_links"])
            
            if "target_url" in config_data:
                settings.validate_target_url(config_data["target_url"])
            
            if "max_depth" in config_data:
                settings.validate_max_depth(config_data["max_depth"])
            
            if "max_links" in config_data:
                settings.validate_max_links(config_data["max_links"])
            
            # Accept both delay_between_requests (config.yaml) and delay
            delay_key = (
                "delay" if "delay" in config_data else "delay_between_requests"
            )
            if delay_key in config_data:
                settings.validate_delay(config_data[delay_key])
            
            if "time_based_threshold" in config_data:
                settings.validate_time_based_threshold(config_data["time_based_threshold"])
            
            if "output_format" in config_data:
                settings.set_output_format(config_data["output_format"])
            
            if "user_agent" in config_data:
                settings.user_agent = config_data["user_agent"]
            
            if "verify_ssl" in config_data:
                settings.verify_ssl = bool(config_data["verify_ssl"])
            
            if "follow_redirects" in config_data:
                settings.follow_redirects = bool(config_data["follow_redirects"])
            
            # Authentication block
            if config_data.get("auth_required") or all(
                key in config_data
                for key in ("auth_url", "auth_username", "auth_password")
            ):
                if (
                    config_data.get("auth_url")
                    and config_data.get("auth_username")
                    and config_data.get("auth_password")
                ):
                    settings.set_authentication(
                        config_data["auth_url"],
                        config_data["auth_username"],
                        config_data["auth_password"],
                    )
            
        except ImportError:
            print("[!] Warning: PyYAML not installed, using default settings")
        except Exception as e:
            print(f"[!] Warning: Could not load config file: {e}")
    
    return settings
