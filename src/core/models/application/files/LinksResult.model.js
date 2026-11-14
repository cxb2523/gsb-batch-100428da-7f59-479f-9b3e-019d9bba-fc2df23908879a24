class LinksResultModel {
  constructor() {
    this.isValidPage = true;
    this.crawlCount = 0;
    this.totalCount = 0;
    this.filterCount = 0;
    this.crawlLinksList = [];
    // The fetch outcome of the search engine page of this crawl
    // (success / timeout / invalidPage / emptySource).
    this.sourceOutcome = null;
    // Prevents counting the same outcome twice when the statistics are updated
    // from more than one place.
    this.isSourceOutcomeCounted = false;
  }
}

module.exports = LinksResultModel;
