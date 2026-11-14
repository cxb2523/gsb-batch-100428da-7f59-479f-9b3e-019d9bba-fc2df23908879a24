import asyncio
import logging

class Plugin:
    async def should_run(self, url: str, content: str) -> bool:
        """
        Determine if the plugin should run for the given URL and content.
        Defaults to True; override to add conditional logic.
        """
        return True

    async def process(self, url: str, content: str) -> str:
        """
        Process the content asynchronously and return the (optionally modified) content.
        Must be implemented by subclasses.
        """
        raise NotImplementedError("Plugins must implement the async process method.")

class PluginManager:
    def __init__(self):
        self.plugins = []

    def register(self, plugin: Plugin) -> None:
        """
        Registers a plugin to be used by the crawler.
        """
        self.plugins.append(plugin)

    async def run_plugins(self, url: str, content: str) -> str:
        """
        Iterates through all registered plugins and runs those whose should_run() method returns True.
        The plugins are executed asynchronously, and their output (if any) is passed along.

        If a plugin raises, the error is logged together with the plugin name and the URL
        (it is never silently skipped) and the remaining plugins still run so that their
        side effects (e.g. persistence) are not lost. However, the content returned to the
        caller is the *original*, unprocessed content, so a partially processed chain can
        never replace the raw page that is saved and used for link discovery.
        """
        original_content = content
        failed = False
        for plugin in self.plugins:
            plugin_name = plugin.__class__.__name__
            try:
                if await plugin.should_run(url, content):
                    content = await plugin.process(url, content)
            except Exception as e:
                failed = True
                logging.error(
                    f"Plugin {plugin_name} failed for URL {url}: {e}",
                    exc_info=True,
                )
        return original_content if failed else content
