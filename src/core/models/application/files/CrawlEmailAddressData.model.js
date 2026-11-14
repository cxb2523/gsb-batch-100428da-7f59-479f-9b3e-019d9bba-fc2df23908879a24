const { SourceOutcomeEnum } = require('../../../enums');

class CrawlEmailAddressDataModel {
  constructor(activeSearchEngineNames) {
    this.saveCount = 0;
    this.totalCount = 0;
    this.databaseCount = 0;
    this.existsCount = 0;
    this.invalidCount = 0;
    this.validFixCount = 0;
    this.invalidFixCount = 0;
    this.unsaveCount = 0;
    this.filterCount = 0;
    this.skipCount = 0;
    this.gibberishCount = 0;
    // Fetch outcomes of the target pages (the pages the email addresses are taken from).
    this.sourceSuccessCount = 0;
    this.sourceTimeoutCount = 0;
    this.sourceInvalidPageCount = 0;
    this.sourceEmptySourceCount = 0;
    // The fetch outcome of the most recent target page.
    this.sourceLastOutcome = null;
    this.statistics = activeSearchEngineNames.reduce(
      (acc, cur) => ({ ...acc, [cur]: 0 }),
      {}
    );
  }

  updateEmailAddressData(emailAddressesResultModel, searchEngineName) {
    const {
      saveCount,
      totalCount,
      existsCount,
      invalidCount,
      validFixCount,
      invalidFixCount,
      unsaveCount,
      filterCount,
      skipCount,
      gibberishCount,
    } = emailAddressesResultModel;
    // Update all the data.
    this.saveCount += saveCount;
    this.totalCount += totalCount;
    this.databaseCount += saveCount;
    this.existsCount += existsCount;
    this.invalidCount += invalidCount;
    this.validFixCount += validFixCount;
    this.invalidFixCount += invalidFixCount;
    this.unsaveCount += unsaveCount;
    this.filterCount += filterCount;
    this.skipCount += skipCount;
    this.gibberishCount += gibberishCount;
    this.statistics[searchEngineName] += saveCount;
    this.updateSourceOutcomeFromResult(emailAddressesResultModel);
  }

  // Adds the fetch outcome of a single result model, but only once per result.
  updateSourceOutcomeFromResult(emailAddressesResultModel) {
    if (
      !emailAddressesResultModel ||
      emailAddressesResultModel.isSourceOutcomeCounted ||
      !emailAddressesResultModel.sourceOutcome
    ) {
      return this;
    }
    this.updateSourceOutcome(emailAddressesResultModel.sourceOutcome);
    emailAddressesResultModel.isSourceOutcomeCounted = true;
    return this;
  }

  // Adds a single target page fetch outcome to this crawl statistics.
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

module.exports = CrawlEmailAddressDataModel;
