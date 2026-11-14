const {
  SourceOutcomeEnum,
  SourceTypeEnum,
} = require('../../core/enums');
const { SourceResultModel } = require('../../core/models/application');
let puppeteerService = null;
const searchService = require('./search.service');
const {
  fileUtils,
  pathUtils,
  textUtils,
  validationUtils,
} = require('../../utils');

class SourceService {
  constructor() {
    this.isProductionMode = null;
    this.sourcePath = null;
    this.sourcesPath = null;
    this.distPath = null;
    this.countLimitDataModel = null;
    this.searchEngineSourcesList = null;
    this.pageSourcesList = null;
    // Holds the next index to pick from each development mode sources list, so each
    // call gets a predictable file instead of a random one.
    this.sourceIndexesMap = {};
  }

  async initiate(data) {
    const { applicationDataModel, pathDataModel, countLimitDataModel } = data;
    this.isProductionMode = applicationDataModel.isProductionMode;
    this.sourcePath = pathDataModel.sourcePath;
    this.sourcesPath = pathDataModel.sourcesPath;
    this.distPath = pathDataModel.distPath;
    this.countLimitDataModel = countLimitDataModel;

    if (this.isProductionMode) {
      // On production, load puppeteer service and initiate it.
      puppeteerService = require('./puppeteer.service');
      await puppeteerService.initiate(this.countLimitDataModel, false);
    } else {
      this.searchEngineSourcesList = [];
      this.pageSourcesList = [];
      this.sourceIndexesMap = {};
      await this.loadAllDevelopmentModeSources();
    }
  }

  async loadAllDevelopmentModeSources() {
    const searchEngines = searchService.getAllActiveSearchEngines();
    for (let i = 0, length = searchEngines.length; i < length; i++) {
      await this.loadDevelopmentModeSourcesByType({
        sourceType: SourceTypeEnum.ENGINE,
        searchEngineName: searchEngines[i].name,
      });
    }
    await this.loadDevelopmentModeSourcesByType({
      sourceType: SourceTypeEnum.PAGE,
    });
  }

  async loadDevelopmentModeSourcesByType(data) {
    const { sourceType, searchEngineName } = data;
    let sourcePath = sourceType;
    if (searchEngineName) {
      sourcePath = pathUtils.getJoinPath({
        targetPath: this.sourcesPath,
        targetName: sourceType,
      });
      sourcePath = pathUtils.getJoinPath({
        targetPath: sourcePath,
        targetName: searchEngineName,
      });
    } else {
      sourcePath = pathUtils.getJoinPath({
        targetPath: this.sourcesPath,
        targetName: sourceType,
      });
    }
    // Get all the files.
    let files = await fileUtils.getDirectoryFiles(sourcePath);
    // Validate that there is at least 1 file in the source directory.
    if (!validationUtils.isExists(files)) {
      throw new Error(`No any files exists in ${sourcePath} (1000027)`);
    }
    // Filter only the TXT files.
    files = files.filter((f) => {
      return pathUtils.isTypeFile({
        fileName: f,
        fileExtension: 'txt',
      });
    });
    // Validate that there is at least 1 TXT file in the source directory.
    if (!validationUtils.isExists(files)) {
      throw new Error(`No TXT files exists in ${sourcePath} (1000028)`);
    }
    // Keep the files order stable so the development mode results are reproducible.
    files = files.sort();
    for (let i = 0, length = files.length; i < length; i++) {
      const pageSource = await fileUtils.readFile(
        pathUtils.getJoinPath({
          targetPath: sourcePath,
          targetName: files[i],
        })
      );
      switch (sourceType) {
        case SourceTypeEnum.ENGINE: {
          this.searchEngineSourcesList.push({
            name: searchEngineName,
            fileName: files[i],
            pageSource: pageSource,
          });
          break;
        }
        case SourceTypeEnum.PAGE: {
          this.pageSourcesList.push({
            fileName: files[i],
            pageSource: pageSource,
          });
          break;
        }
      }
    }
  }

  async getPageSource(data) {
    return this.isProductionMode
      ? await this.getPageSourceProduction(data)
      : this.getPageSourceDevelopment(data);
  }

  async getPageSourceProduction(data) {
    const { linkData } = data;
    const { link, userAgent } = linkData;
    if (!link) {
      return new SourceResultModel({
        isValidPage: false,
        pageSource: null,
        outcome: SourceOutcomeEnum.INVALID_PAGE,
      });
    }
    const crawlResults = await puppeteerService.crawl(link, userAgent);
    // The production timeout path may resolve with no result at all.
    if (!crawlResults) {
      return new SourceResultModel({
        isValidPage: false,
        pageSource: null,
        outcome: SourceOutcomeEnum.TIMEOUT,
      });
    }
    return new SourceResultModel(crawlResults);
  }

  // Returns the sources list that matches the requested source type.
  getDevelopmentModeSourcesList(data) {
    const { sourceType, searchEngineName } = data;
    switch (sourceType) {
      case SourceTypeEnum.ENGINE: {
        return this.searchEngineSourcesList
          ? this.searchEngineSourcesList.filter(
              (se) => se.name === searchEngineName
            )
          : null;
      }
      case SourceTypeEnum.PAGE: {
        return this.pageSourcesList;
      }
    }
    return null;
  }

  // Returns a stable, readable key for the requested sources list.
  getDevelopmentModeSourcesKey(data) {
    const { sourceType, searchEngineName } = data;
    return sourceType === SourceTypeEnum.ENGINE
      ? `${sourceType}_${searchEngineName}`
      : sourceType;
  }

  // Picks the next source file in a round-robin order, so every sample file is used
  // and the outcome of each call is reproducible.
  getNextDevelopmentModeSource(sourcesList, sourceKey) {
    const index = this.sourceIndexesMap[sourceKey] || 0;
    this.sourceIndexesMap[sourceKey] = (index + 1) % sourcesList.length;
    return sourcesList[index];
  }

  // Derives a deterministic outcome from the sample file, without any random value.
  // A file name that contains 'timeout' simulates a request timeout, and a file name
  // that contains 'invalid' simulates a blocked / invalid page. An empty file
  // simulates an empty page source. Any other file simulates a successful fetch.
  getDevelopmentModeOutcome(source) {
    const fileName = textUtils.toLowerCase(source.fileName || '');
    if (fileName.indexOf('timeout') > -1) {
      return SourceOutcomeEnum.TIMEOUT;
    }
    if (fileName.indexOf('invalid') > -1) {
      return SourceOutcomeEnum.INVALID_PAGE;
    }
    if (!source.pageSource || !source.pageSource.trim()) {
      return SourceOutcomeEnum.EMPTY_SOURCE;
    }
    return SourceOutcomeEnum.SUCCESS;
  }

  getPageSourceDevelopment(data) {
    const sourcesList = this.getDevelopmentModeSourcesList(data);
    if (!validationUtils.isExists(sourcesList)) {
      return new SourceResultModel({
        isValidPage: false,
        pageSource: null,
        outcome: SourceOutcomeEnum.INVALID_PAGE,
      });
    }
    const sourceKey = this.getDevelopmentModeSourcesKey(data);
    const source = this.getNextDevelopmentModeSource(sourcesList, sourceKey);
    const outcome = this.getDevelopmentModeOutcome(source);
    switch (outcome) {
      case SourceOutcomeEnum.SUCCESS: {
        return new SourceResultModel({
          isValidPage: true,
          pageSource: source.pageSource,
          outcome: outcome,
        });
      }
      case SourceOutcomeEnum.EMPTY_SOURCE: {
        return new SourceResultModel({
          isValidPage: true,
          pageSource: '',
          outcome: outcome,
        });
      }
      case SourceOutcomeEnum.TIMEOUT:
      case SourceOutcomeEnum.INVALID_PAGE:
      default: {
        return new SourceResultModel({
          isValidPage: false,
          pageSource: null,
          outcome: outcome,
        });
      }
    }
  }

  async close() {
    if (this.isProductionMode && puppeteerService) {
      await puppeteerService.close();
    }
  }
}

module.exports = new SourceService();
