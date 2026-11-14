class EmailAddressesResultModel {
  constructor() {
    this.isGoalComplete = false;
    this.saveCount = 0;
    this.totalCount = 0;
    this.existsCount = 0;
    this.invalidCount = 0;
    this.validFixCount = 0;
    this.invalidFixCount = 0;
    this.unsaveCount = 0;
    this.filterCount = 0;
    this.skipCount = 0;
    this.gibberishCount = 0;
    this.trendingSaveList = [];
    this.isValidPage = true;
    // The fetch outcome of the target page of this crawl
    // (success / timeout / invalidPage / emptySource).
    this.sourceOutcome = null;
    // Prevents counting the same outcome twice when the statistics are updated
    // from more than one place.
    this.isSourceOutcomeCounted = false;
  }
}

module.exports = EmailAddressesResultModel;
