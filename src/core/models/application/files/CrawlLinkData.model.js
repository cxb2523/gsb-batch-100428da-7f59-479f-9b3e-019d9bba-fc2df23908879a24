const { SourceOutcomeEnum } = require('../../../enums');

class CrawlLinkDataModel {
  constructor() {
    this.crawlCount = 0;
    this.totalCount = 0;
    this.filterCount = 0;
    this.errorCount = 0;
    // Fetch outcomes of the search engine pages (the pages the links are taken from).
    this.sourceSuccessCount = 0;
    this.sourceTimeoutCount = 0;
    this.sourceInvalidPageCount = 0;
    this.sourceEmptySourceCount = 0;
    // The fetch outcome of the most recent search engine page.
    this.sourceLastOutcome = null;
  }

  updateLinksData(searchEngineResults) {
    // Update all the data.
    const { isValidPage, crawlCount, totalCount, filterCount } =
      searchEngineResults;
    this.crawlCount += crawlCount;
    this.totalCount += totalCount;
    this.filterCount += filterCount;
    this.updateErrorLink(isValidPage);
    this.updateSourceOutcomeFromResult(searchEngineResults);
  }

  updateErrorLink(isValidPage) {
    this.errorCount = isValidPage ? this.errorCount : this.errorCount + 1;
  }

  // Adds the fetch outcome of a single result model, but only once per result.
  updateSourceOutcomeFromResult(searchEngineResults) {
    if (
      !searchEngineResults ||
      searchEngineResults.isSourceOutcomeCounted ||
      !searchEngineResults.sourceOutcome
    ) {
      return this;
    }
    this.updateSourceOutcome(searchEngineResults.sourceOutcome);
    searchEngineResults.isSourceOutcomeCounted = true;
    return this;
  }

  // Adds a single search engine page fetch outcome to this crawl statistics.
  updateSourceOutcome(outcome) {
    this.sourceLastOutcome = outcome || null;
    switch (outcome) {
      case SourceOutcomeEnum.SUCCESS: {
        this.sourceSuccessCount++;
        break;
      }
      case SourceOutcomeEnum.TIMEOUT: {
        this.sourceTimeoutCount++;
        break;
      }
      case SourceOutcomeEnum.INVALID_PAGE: {
        this.sourceInvalidPageCount++;
        break;
      }
      case SourceOutcomeEnum.EMPTY_SOURCE: {
        this.sourceEmptySourceCount++;
        break;
      }
    }
    return this;
  }
}

module.exports = CrawlLinkDataModel;
