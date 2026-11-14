# SPDX-License-Identifier: MIT

import os
import json
from dataclasses import dataclass, field
from typing import Optional, Union, Dict, List, Any
from urllib.parse import urlsplit

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def parse_proxy(proxy: Optional[Union[str, Dict[str, Any]]] = None) -> Optional[Dict[str, str]]:
    """
    Normalizes a proxy URL string, dictionary, or environment variable into Playwright format.
    
    Accepts:
      - "http://user:pass@host:port" or "http://host:port" or "socks5://host:port"
      - {"server": "http://host:port", "username": "...", "password": "..."}
      - Environment variable LLM_READER_PROXY / HTTPS_PROXY / HTTP_PROXY
    """
    if not proxy:
        proxy = os.getenv("LLM_READER_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")

    if not proxy:
        return None

    if isinstance(proxy, dict):
        # Already a dictionary
        config: Dict[str, str] = {}
        server = proxy.get("server") or proxy.get("url")
        if server:
            config["server"] = str(server)
        if "username" in proxy and proxy["username"]:
            config["username"] = str(proxy["username"])
        if "password" in proxy and proxy["password"]:
            config["password"] = str(proxy["password"])
        if "bypass" in proxy and proxy["bypass"]:
            config["bypass"] = str(proxy["bypass"])
        return config if "server" in config else None

    if isinstance(proxy, str):
        parsed = urlsplit(proxy.strip())
        if not parsed.hostname:
            return None
        
        server_netloc = parsed.hostname
        if parsed.port:
            server_netloc += f":{parsed.port}"
        
        server_scheme = parsed.scheme or "http"
        config = {"server": f"{server_scheme}://{server_netloc}"}

        if parsed.username:
            config["username"] = parsed.username
        if parsed.password:
            config["password"] = parsed.password

        return config

    return None


@dataclass
class ScraperConfig:
    """Configuration for browser navigation, proxying, bandwidth saving, and text extraction."""

    # Proxy Configuration
    proxy: Optional[Union[str, Dict[str, Any]]] = None

    # Browser & Anti-Bot Fingerprint
    headless: bool = True
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    viewport: Optional[Dict[str, int]] = field(default_factory=lambda: {"width": 1920, "height": 1080})
    locale: Optional[str] = None
    timezone_id: Optional[str] = None

    # Performance & Bandwidth Saving
    block_images: bool = False       # Default False so full image rendering is preserved; inspects data-src if True
    block_media: bool = True         # Blocks heavy video/audio streams (.mp4, .webm, .mp3)
    block_fonts: bool = True         # Blocks web fonts (.woff, .woff2) to save bandwidth
    block_trackers: bool = True      # Blocks ads and analytics scripts (GTM, FB Pixel, etc.)

    # Navigation Strategy
    timeout: int = 30000             # Page timeout in milliseconds
    wait_until: str = "load"         # 'load', 'domcontentloaded', or 'networkidle'
    post_load_wait: float = 1.5      # Extra pause in seconds after load
    scroll_down: bool = True         # Scroll down to trigger lazy loading
    scroll_times: int = 1            # How many PageDown scrolls to perform

    # Content & HTML Cleaning (Token Reduction)
    html_parser: str = "lxml"
    keep_images: bool = True
    remove_svg_image: bool = True
    remove_gif_image: bool = True
    remove_image_types: List[str] = field(default_factory=list)
    keep_webpage_links: bool = True
    remove_script_tag: bool = True
    remove_style_tag: bool = True
    remove_header_footer: bool = False  # Set True to strip <header>, <footer>, <nav>, <aside>
    remove_tags: List[str] = field(default_factory=list)
    extract: bool = False

    # Context window truncation (default None / 0: no truncation)
    max_characters: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScraperConfig":
        """Build ScraperConfig from a flattened or nested dictionary."""
        merged: Dict[str, Any] = {}

        # Allow nested structure (proxy.*, browser.*, performance.*, navigation.*, content.*)
        for section in ["proxy", "browser", "performance", "navigation", "content"]:
            if section in data and isinstance(data[section], dict):
                merged.update(data[section])

        # Overwrite with top-level keys if any
        for k, v in data.items():
            if k not in ["browser", "performance", "navigation", "content"]:
                merged[k] = v

        valid_fields = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in merged.items() if k in valid_fields}
        return cls(**filtered)


def load_config(config_source: Optional[Union[ScraperConfig, Dict[str, Any], str]] = None) -> ScraperConfig:
    """
    Loads ScraperConfig with fallback priority:
      1. Provided ScraperConfig instance or dictionary
      2. File path passed directly (or in LLM_READER_CONFIG env var)
      3. Default files in working directory: 'llm_reader.yaml', 'llm_reader.yml', 'llm_reader.json'
      4. Default ScraperConfig()
    """
    if isinstance(config_source, ScraperConfig):
        return config_source

    if isinstance(config_source, dict):
        return ScraperConfig.from_dict(config_source)

    target_file = None
    if isinstance(config_source, str) and os.path.isfile(config_source):
        target_file = config_source
    else:
        env_config = os.getenv("LLM_READER_CONFIG")
        if env_config and os.path.isfile(env_config):
            target_file = env_config
        else:
            candidates = ["llm_reader.yaml", "llm_reader.yml", "llm_reader.json", ".llm_reader.json"]
            for cand in candidates:
                if os.path.isfile(cand):
                    target_file = cand
                    break

    if target_file:
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                if (target_file.endswith(".yaml") or target_file.endswith(".yml")) and HAS_YAML:
                    data = yaml.safe_load(f) or {}
                else:
                    data = json.load(f) or {}
                return ScraperConfig.from_dict(data)
        except Exception as e:
            print(f"Warning: Failed to load config from {target_file}: {e}")

    # Fallback to default
    return ScraperConfig()
