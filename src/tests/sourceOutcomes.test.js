require('../services/files/initiate.service').initiate('test');
const settings = require('../settings/settings');
const { activeSearchEngineNames } = require('../configurations');
const { SourceOutcomeEnum, SourceTypeEnum } = require('../core/enums');
const {
  ApplicationDataModel,
  CountLimitDataModel,
  PathDataModel,
} = require('../core/models/application');
const {
  crawlEmailAddressService,
  crawlLinkService,
} = require('../services');
const mongoDatabaseService = require('../services/files/mongoDatabase.service');
const sourceService = require('../services/files/source.service');
const { logUtils } = require('../utils');

// Development mode sample sources live under 'sources/page' and
// 'sources/engine/<engine>'. Each outcome is derived from the sample file name,
// so no real request is made.
const callsCount = 8;

// Keep this test self contained: no Mongo database is required.
mongoDatabaseService.saveEmailAddress = async () => 'save';

const logOutcome = (title, result) => {
  logUtils.log(
    `${title} | outcome: ${result.outcome} | isValidPage: ${result.isValidPage}`
  );
};

(async () => {
  const countLimitDataModel = new CountLimitDataModel(settings);
  const pathDataModel = new PathDataModel(settings);
  const applicationDataModel = new ApplicationDataModel({
    settings: settings,
    activeSearchEngineNames: activeSearchEngineNames,
    status: null,
    plan: null,
    restartsCount: 0,
  });
  applicationDataModel.isProductionMode = false;

  await sourceService.initiate({
    applicationDataModel: applicationDataModel,
    pathDataModel: pathDataModel,
    countLimitDataModel: countLimitDataModel,
  });

  // The direct return value of every fetch.
  for (let i = 0; i < callsCount; i++) {
    const engineResult = await sourceService.getPageSource({
      sourceType: SourceTypeEnum.ENGINE,
      searchEngineName: activeSearchEngineNames[0],
      linkData: { link: 'engine-page', userAgent: null },
    });
    const pageResult = await sourceService.getPageSource({
      sourceType: SourceTypeEnum.PAGE,
      searchEngineName: null,
      linkData: { link: `https://example.com/${i}`, userAgent: null },
    });
    logOutcome('Engine page', engineResult);
    logOutcome('Target page', pageResult);
  }

  // The crawl statistics of both services.
  crawlLinkService.initiate({
    applicationDataModel: applicationDataModel,
    countLimitDataModel: countLimitDataModel,
  });
  crawlEmailAddressService.initiate({
    applicationDataModel: applicationDataModel,
    countLimitDataModel: countLimitDataModel,
  });
  for (let i = 0; i < callsCount; i++) {
    await crawlLinkService.getSearchEnginePageLinks({
      searchProcessDataModel: {
        searchEngineModel: {
          name: activeSearchEngineNames[0],
          domainAddress: 'bing.com',
        },
        searchEngineLinkTemplate: 'https://www.bing.com/search?q=sample',
      },
      totalCrawlCount: 0,
    });
    await crawlEmailAddressService.getEmailAddressesFromPage({
      linkData: { link: `https://example.com/${i}`, userAgent: null },
      totalSaveCount: 0,
    });
  }

  const crawlLinkDataModel = applicationDataModel.crawlLinkDataModel;
  const crawlEmailAddressDataModel =
    applicationDataModel.crawlEmailAddressDataModel;
  const createStatisticsLine = (title, dataModel) => {
    return [
      title,
      `success: ${dataModel.sourceSuccessCount}`,
      `timeout: ${dataModel.sourceTimeoutCount}`,
      `invalidPage: ${dataModel.sourceInvalidPageCount}`,
      `emptySource: ${dataModel.sourceEmptySourceCount}`,
      `last: ${dataModel.sourceLastOutcome}`,
    ].join(' | ');
  };
  logUtils.log(createStatisticsLine('Search engine pages', crawlLinkDataModel));
  logUtils.log(
    createStatisticsLine('Target pages', crawlEmailAddressDataModel)
  );
  const outcomes = Object.values(SourceOutcomeEnum).join(', ');
  logUtils.log(`Outcomes | ${outcomes}`);
  logUtils.log(
    'A link that fails with a timeout is written to the error links file once.'
  );

  await sourceService.close();
})();
