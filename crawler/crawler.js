/* 
  This code is taken from the older version of a npm package that is linked below
  https://www.npmjs.com/package/crawler
  https://github.com/bda-research/node-crawler

  Copyright (c) 2010 Sylvain Zimmer 

  Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

*/
const url = require('url')
const fs = require('fs')

function noop () {}

function contains (listOrObj, value) {
  if (Array.isArray(listOrObj)) return listOrObj.includes(value)
  return Object.prototype.hasOwnProperty.call(listOrObj, value)
}

async function fetchRequest (options, callback) {
  let responseLike
  try {
    const res = await fetch(options.url, {
      redirect: 'follow',
      headers: options.headers || {}
    })
    const arrayBuffer = await res.arrayBuffer()
    const body = Buffer.from(arrayBuffer)
    responseLike = {
      statusCode: res.status,
      headers: Object.fromEntries(res.headers.entries()),
      body,
      request: { uri: { href: res.url } },
      _redirect: { redirects: [{ redirectUri: res.url }] }
    }
  } catch (err) {
    const emptyResponse = { _redirect: { redirects: [] } }
    callback.call(emptyResponse, err, null, null)
    return
  }
  // Invoked outside the try block so that an exception thrown by a consumer
  // callback is not reported back as a request failure and does not invoke the
  // callback a second time. It is re-thrown asynchronously, after the crawler has
  // updated its bookkeeping in the callback's finally block.
  try {
    callback.call(responseLike, null, responseLike, responseLike.body)
  } catch (callbackError) {
    setTimeout(function () { throw callbackError }, 0)
  }
}
const path = require('path')
let config
try {
  config = JSON.parse(fs.readFileSync(path.join(process.cwd(), 'config.json'), 'utf8'))
} catch (_err) {
  config = { depth: 3, maxConcurrentRequests: 5000, maxRequestsPerSecond: 5000 }
}
let DEFAULT_DEPTH = config.depth
let DEFAULT_MAX_CONCURRENT_REQUESTS = config.maxConcurrentRequests
let DEFAULT_MAX_REQUESTS_PER_SECOND = config.maxRequestsPerSecond
const DEFAULT_USERAGENT = 'crawler/js-crawler'
const DEFAULT_HEADERS = {}

/*
 * Executor that handles throttling and task processing rate.
 */
function Executor (opts) {
  this.maxRatePerSecond = opts.maxRatePerSecond
  this.onFinished = opts.finished || function () {}
  this.canProceed = opts.canProceed || function () { return true }
  this.queue = []
  this.isStopped = false
  this.timeoutMs = (1 / this.maxRatePerSecond) * 1000
}

Executor.prototype.submit = function (func, context, args, shouldSkip) {
  this.queue.push({
    func: func,
    context: context,
    args: args,
    shouldSkip: shouldSkip
  })
}

Executor.prototype.start = function () {
  this._processQueueItem()
}

Executor.prototype.stop = function () {
  this.isStopped = true
}

Executor.prototype._processQueueItem = function () {
  const self = this

  if (this.canProceed()) {
    if (this.queue.length !== 0) {
      const nextExecution = this.queue.shift()
      const shouldSkipNext = (nextExecution.shouldSkip && nextExecution.shouldSkip.call(nextExecution.context))

      if (shouldSkipNext) {
        setTimeout(function () {
          self._processQueueItem()
        })
        return
      } else {
        nextExecution.func.apply(nextExecution.context, nextExecution.args)
      }
    }
  }
  if (this.isStopped) {
    return
  }
  setTimeout(function () {
    self._processQueueItem()
  }, this.timeoutMs)
}

/*
 * Main crawler functionality.
 */
function Crawler () {
  /*
     * Urls that the Crawler has visited, as some pages may be in the middle of a redirect chain, not all the knownUrls will be actually
     * reported in the onSuccess or onFailure callbacks, only the final urls in the corresponding redirect chains
     */
  this.knownUrls = {}

  /*
     * Urls that were reported in the onSuccess or onFailure callbacks. this.crawledUrls is a subset of this.knownUrls, and matches it
     * iff there were no redirects while crawling.
     */
  this.crawledUrls = []
  this.depth = DEFAULT_DEPTH
  this.ignoreRelative = false
  this.userAgent = DEFAULT_USERAGENT
  this.headers = DEFAULT_HEADERS
  this.maxConcurrentRequests = DEFAULT_MAX_CONCURRENT_REQUESTS
  this.maxRequestsPerSecond = DEFAULT_MAX_REQUESTS_PER_SECOND

  this.shouldCrawl = function (url) {
    let returnValue = null
    if (!(typeof config.whiteList === 'undefined') && !(config.whiteList === null) && Array.isArray(config.whiteList) && config.whiteList.length !== 0) {
      config.whiteList.forEach(element => {
        if (url.includes(element)) {
          returnValue = true
        }
      })
      if (returnValue === null) {
        returnValue = false
      }
    }
    if (!(typeof config.blackList === 'undefined') && !(config.blackList === null) && Array.isArray(config.blackList) && config.blackList.length !== 0) {
      config.blackList.forEach(element => {
        if (url.includes(element)) {
          returnValue = false
        }
      })
      if (returnValue === null) {
        returnValue = true
      }
    }
    if ((returnValue == null)) {
      returnValue = true
    }
    return returnValue
  }
  this.shouldCrawlLinksFrom = function (_url) {
    return true
  }
  // Urls that are queued for crawling, for some of them HTTP requests may not yet have been issued
  this._currentUrlsToCrawl = []
  this._concurrentRequestNumber = 0

  // Host of the crawl root; links are restricted to it and its subdomains
  this._rootHostname = null

  // Urls reported through onSuccess/onFailure, used to report each page only once
  this._crawledUrlSet = new Set()

  // Guards against calling onAllFinished more than once per crawl
  this._finished = false

  this.request = fetchRequest
}

Crawler.prototype._setRootUrl = function (rootUrl) {
  this._rootHostname = null
  if (typeof rootUrl === 'string') {
    try {
      this._rootHostname = new URL(rootUrl).hostname.toLowerCase()
    } catch {
      // Not an absolute URL: keep the historical behaviour and don't restrict by host.
      this._rootHostname = null
    }
  }
}

/*
 * A link is in scope when it is an http/https URL on the same host as the crawl root,
 * or on one of its subdomains. Links that cannot be parsed are rejected once the root
 * host is known.
 */
Crawler.prototype._isUrlInScope = function (link) {
  let parsed
  try {
    parsed = new URL(link)
  } catch {
    return this._rootHostname === null
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return false
  }
  if (!this._rootHostname) {
    return true
  }
  const hostname = parsed.hostname.toLowerCase()
  return hostname === this._rootHostname || hostname.endsWith('.' + this._rootHostname)
}

Crawler.prototype.configure = function (options) {
  this.depth = (options && options.depth) || this.depth
  this.depth = Math.max(this.depth, 0)
  this.ignoreRelative = (options && options.ignoreRelative) || this.ignoreRelative
  this.userAgent = (options && options.userAgent) || this.userAgent
  this.headers = (options && options.headers) || this.headers //  accept custom headers
  this.maxConcurrentRequests = (options && options.maxConcurrentRequests) || this.maxConcurrentRequests
  this.maxRequestsPerSecond = (options && options.maxRequestsPerSecond) || this.maxRequestsPerSecond
  this.shouldCrawl = (options && options.shouldCrawl) || this.shouldCrawl
  this.shouldCrawlLinksFrom = (options && options.shouldCrawlLinksFrom) || this.shouldCrawlLinksFrom
  this.onSuccess = noop
  this.onFailure = noop
  this.onAllFinished = noop
  return this
}

Crawler.prototype._createExecutor = function () {
  const self = this

  return new Executor({
    maxRatePerSecond: this.maxRequestsPerSecond,
    canProceed: function () {
      return self._concurrentRequestNumber < self.maxConcurrentRequests
    }
  })
}

Crawler.prototype.crawl = function (configParm, onSuccess, onFailure, onAllFinished) {
  this._finished = false
  this.workExecutor = this._createExecutor()
  this.workExecutor.start()
  let url = configParm.url
  config = configParm
  DEFAULT_DEPTH = config.depth
  DEFAULT_MAX_CONCURRENT_REQUESTS = config.maxConcurrentRequests
  DEFAULT_MAX_REQUESTS_PER_SECOND = config.maxRequestsPerSecond
  if (typeof url !== 'string') {
    const options = url

    onSuccess = options.success
    onFailure = options.failure
    onAllFinished = options.finished
    url = options.url
  }
  this._setRootUrl(url)
  this.onSuccess = onSuccess
  this.onFailure = onFailure
  this.onAllFinished = onAllFinished
  this._crawlUrl(url, null, this.depth)

  // The entry url may not be queueable at all (unsupported protocol, or depth 0).
  // In that case nothing will ever call _finishedCrawling, so finish explicitly.
  if (this._currentUrlsToCrawl.length === 0 && this._concurrentRequestNumber === 0) {
    this._allFinished()
  }

  return this
}

Crawler.prototype._startedCrawling = function (url) {
  if (this._currentUrlsToCrawl.indexOf(url) < 0) {
    this._currentUrlsToCrawl.push(url)
  }
}

Crawler.prototype._finishedCrawling = function (url) {
  const indexOfUrl = this._currentUrlsToCrawl.indexOf(url)

  if (indexOfUrl >= 0) {
    this._currentUrlsToCrawl.splice(indexOfUrl, 1)
  }
  if (this._currentUrlsToCrawl.length === 0 && this._concurrentRequestNumber === 0) {
    this._allFinished()
  }
}

Crawler.prototype._allFinished = function () {
  if (this._finished) {
    return
  }
  this._finished = true
  this.workExecutor && this.workExecutor.stop()
  this.onAllFinished && this.onAllFinished(this.crawledUrls)
}

Crawler.prototype._requestUrl = function (options, callback) {
  const self = this
  const url = options.url

  // Keep the crawl on the site it started from: only http(s) urls on the root host
  // (or one of its subdomains) may enter the queue.
  if (!self._isUrlInScope(url)) {
    return
  }

  // Do not request a url if it is already known, in flight, or already queued.
  // Checking the queue here (and not only when the executor picks the item up)
  // keeps duplicates out of the executor and prevents them from being counted.
  if (contains(self._currentUrlsToCrawl, url) || contains(self.knownUrls, url)) {
    return
  }

  self._startedCrawling(url)
  this.workExecutor.submit(function (options, callback) {
    self._concurrentRequestNumber++
    self.request(options, function (error, response, body) {
      self._redirects = (this && this._redirect && this._redirect.redirects) || []
      try {
        callback(error, response, body)
      } finally {
        self._concurrentRequestNumber--
        self._finishedCrawling(url)
      }
    })
  }, null, [options, callback], function shouldSkip () {
    const shouldCrawlUrl = self.shouldCrawl(url)
    if (!shouldCrawlUrl || contains(self.knownUrls, url)) {
      self._finishedCrawling(url)
      return true
    }
    return false
  })
}

Crawler.prototype._crawlUrl = function (url, referer, depth) {
  if ((depth === 0) || this.knownUrls[url]) {
    return
  }

  const self = this

  // custom headers:
  const tempHeaders = {
    'User-Agent': this.userAgent,
    Referer: referer
  }
  this.headers = Object.assign(this.headers, tempHeaders)
  //

  this._requestUrl({
    url: url,
    encoding: null, // Added by @tibetty so as to avoid request treating body as a string by default
    rejectUnauthorized: false,
    followRedirect: true,
    followAllRedirects: true,
    headers: this.headers
  }, function (error, response) {
    if (self.knownUrls[url]) {
      // Was already crawled while the request has been processed, no need to call callbacks
      return
    }
    self.knownUrls[url] = true
    self._redirects.forEach(function (redirect) {
      self.knownUrls[redirect.redirectUri] = true
    })
    const isTextContent = self._isTextContent(response)
    const body = isTextContent ? self._getDecodedBody(response) : '<<...binary content (omitted by js-crawler)...>>'

    if (!error && (response.statusCode === 200)) {
      // If no redirects, then response.request.uri.href === url, otherwise last url
      const lastUrlInRedirectChain = response.request.uri.href
      // A same-site url may redirect to another domain, or several urls may resolve to
      // the same page: such pages are not part of this site's results.
      if (self.shouldCrawl(lastUrlInRedirectChain) &&
          self._isUrlInScope(lastUrlInRedirectChain) &&
          !self._crawledUrlSet.has(lastUrlInRedirectChain)) {
        self.onSuccess({
          url: lastUrlInRedirectChain,
          status: response.statusCode,
          content: body,
          error: error,
          response: response,
          body: body,
          referer: referer || ''
        })
        self.knownUrls[lastUrlInRedirectChain] = true
        self._recordCrawledUrl(lastUrlInRedirectChain)
        if (self.shouldCrawlLinksFrom(lastUrlInRedirectChain) && depth > 1 && isTextContent) {
          self._crawlUrls(self._getAllUrls(lastUrlInRedirectChain, body), lastUrlInRedirectChain, depth - 1)
        }
      }
    } else if (self.onFailure) {
      self.onFailure({
        url: url,
        status: response ? response.statusCode : undefined,
        content: body,
        error: error,
        response: response,
        body: body,
        referer: referer || ''
      })
      self._recordCrawledUrl(url)
    }
  })
}

Crawler.prototype._recordCrawledUrl = function (url) {
  if (!this._crawledUrlSet.has(url)) {
    this._crawledUrlSet.add(url)
    this.crawledUrls.push(url)
  }
}

Crawler.prototype._isTextContent = function (response) {
  return Boolean(response && response.headers && response.headers['content-type'] &&
        response.headers['content-type'].match(/^text\/html.*$/))
}

Crawler.prototype._getDecodedBody = function (response) {
  const defaultEncoding = 'utf8'
  let encoding = defaultEncoding

  if (response.headers['content-encoding']) {
    encoding = response.headers['content-encoding']
  }
  let decodedBody
  try {
    decodedBody = response.body.toString(encoding)
  } catch (_decodingError) {
    decodedBody = response.body.toString(defaultEncoding)
  }
  return decodedBody
}

Crawler.prototype._stripComments = function (str) {
  return str.replace(/<!--.*?-->/g, '')
}

Crawler.prototype._getBaseUrl = function (defaultBaseUrl, body) {
  /*
     * Resolving the base url following
     * the algorithm from https://www.w3.org/TR/html5/document-metadata.html#the-base-element
     */
  const baseUrlRegex = /<base href="(.*?)">/
  const baseUrlInPage = body.match(baseUrlRegex)
  if (!baseUrlInPage) {
    return defaultBaseUrl
  }

  return url.resolve(defaultBaseUrl, baseUrlInPage[1])
}

Crawler.prototype._isLinkProtocolSupported = function (link) {
  return (link.indexOf('://') < 0 && link.indexOf('mailto:') < 0) || link.indexOf('http://') >= 0 || link.indexOf('https://') >= 0
}

Crawler.prototype._getAllUrls = function (defaultBaseUrl, body) {
  const self = this
  body = this._stripComments(body)
  const baseUrl = this._getBaseUrl(defaultBaseUrl, body)
  const linksRegex = self.ignoreRelative ? /<a[^>]+?href=["'].*?:\/\/.*?["']/gmi : /<a[^>]+?href=["'].*?["']/gmi
  const links = body.match(linksRegex) || []

  // console.log('body = ', body);
  const mapped = links.map(function (link) {
    const match = /href=["'](.*?)[#"']/i.exec(link)
    link = match[1]
    link = url.resolve(baseUrl, link)
    return link
  })
  const uniq = [...new Set(mapped)]
  return uniq.filter(function (link) {
    return self._isUrlInScope(link) && self._isLinkProtocolSupported(link) && self.shouldCrawl(link)
  })
}

Crawler.prototype._crawlUrls = function (urls, referer, depth) {
  const self = this
  urls.forEach(function (url) {
    self._crawlUrl(url, referer, depth)
  })
}

module.exports = Crawler
