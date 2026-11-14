// @flow

import mixin from "merge-descriptors";
import request from "request";
import EventEmitter from "events";
import SpiderQueue from "./queue/promise";
import SpiderRouter from "./routing/router";
import SpiderRequest from "./routing/request";
import SpiderResponse from "./routing/response";

/**
 * @param  {string}     base
 * @param  {Object}     options
 * @return {Object}
 */
function createCrawler(base: string, options: Object = {}): Object {
  if (typeof base !== "string") {
    throw new Error(`Base must be a string, not ${typeof base}`);
  }

  const config: Object = {
    interval: 250,
    concurrent: 10,
    ...options
  };

  // Will be used by retry-request:
  const requestJar = request.jar();
  const requestObj = request.defaults({ jar: requestJar, ...config });

  // Expose the cookie jar on the request wrapper without replacing its
  // prototype: `setprototypeof` used to swap the wrapper's prototype for the
  // jar's, which removed stream/function methods (e.g. `destroy`) and broke
  // modern Node.js stream internals:
  Object.assign(requestObj, requestJar);

  // Crawler base:
  const crawler: Object = {
    base: base,
    opts: config,
    req: SpiderRequest,
    res: SpiderResponse,
    request: requestObj
  };

  // Glues all the components together:
  mixin(crawler, SpiderQueue, false);
  mixin(crawler, SpiderRouter, false);
  mixin(crawler, EventEmitter.prototype);

  return crawler;
}

module.exports = createCrawler;
module.exports.request = SpiderRequest;
module.exports.response = SpiderResponse;
