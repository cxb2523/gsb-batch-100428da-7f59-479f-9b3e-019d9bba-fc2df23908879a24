# crawl-rag

Web crawler for generating RAG knowledge base files

## install

```
npm install crawl-rag
```

## usage

```typescript
import { Crawler } from 'crawl-rag';

const crawler = new Crawler({
  maxDepth: 3,
  maxPages: 100,
  allowedDomains: ['docs.example.com']
});

// Crawl and generate knowledge base
const result = await crawler.crawl('https://docs.example.com');

// Save as JSON for vector database ingestion
await result.save('./output/knowledge-base.json');

// Or use individual documents
result.documents.forEach(doc => {
  console.log(doc.url, doc.content, doc.metadata);
});
```

## license

MIT
<!-- fix fixme later -->
