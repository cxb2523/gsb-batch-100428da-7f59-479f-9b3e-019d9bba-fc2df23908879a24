from src.scrape_heading_task import scrape_heading_task

if __name__ == "__main__":
    scrape_heading_task({
        "links": [
            "https://www.omkar.cloud/",
            "https://www.omkar.cloud/blog/",
            "https://www.omkar.cloud/this-page-does-not-exist/",
        ]
    })
