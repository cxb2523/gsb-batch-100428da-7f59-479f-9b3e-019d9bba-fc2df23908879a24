const { SourceOutcomeEnum } = require('../../../enums');

// This model describes the outcome of a single page fetch (search engine page or
// target page). It keeps the original 'isValidPage' and 'pageSource' semantics and
// adds an explicit 'outcome', so the caller can tell a success from a timeout, an
// invalid page and an empty source.
class SourceResultModel {
  constructor(data) {
    const { isValidPage, pageSource, outcome } = data || {};
    this.pageSource = pageSource === undefined ? null : pageSource;
    this.isValidPage =
      typeof isValidPage === 'boolean'
        ? isValidPage
        : outcome !== SourceOutcomeEnum.TIMEOUT &&
          outcome !== SourceOutcomeEnum.INVALID_PAGE;
    this.outcome = this.getResolvedOutcome(outcome);
  }

  // A page that was marked as a success but has no source is an empty source.
  getResolvedOutcome(outcome) {
    if (
      outcome === SourceOutcomeEnum.SUCCESS &&
      (!this.pageSource || !this.pageSource.trim())
    ) {
      return SourceOutcomeEnum.EMPTY_SOURCE;
    }
    if (outcome) {
      return outcome;
    }
    if (!this.isValidPage) {
      return SourceOutcomeEnum.INVALID_PAGE;
    }
    if (!this.pageSource || !this.pageSource.trim()) {
      return SourceOutcomeEnum.EMPTY_SOURCE;
    }
    return SourceOutcomeEnum.SUCCESS;
  }
}

module.exports = SourceResultModel;
