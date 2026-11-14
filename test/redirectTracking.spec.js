var expect = require("chai").expect,
    Promise = require("bluebird"),
    Crawler = require("../lib/Crawler"),
    FifoUrlList = require("../lib/FifoUrlList"),
    Url = require("../lib/Url"),
    localServer = require("./utils/localServer");

/**
 * These tests exercise redirect tracking end-to-end against a local, self-made
 * HTTP server. They do not depend on the internet.
 */

var MAX_REDIRECTS = 10;

/**
 * Crawl a \FifoUrlList until it is exhausted, using a real Crawler and a very
 * short interval. Resolves once the crawler reports the list is complete.
 *
 * @param  {FifoUrlList} urlList  Queue to crawl.
 * @return {Promise}              Promise resolved when crawling is finished.
 */
var crawlToCompletion = function (urlList) {
  return new Promise(function (resolve, reject) {
    var crawler = new Crawler({
      urlList: urlList,
      interval: 1,
      concurrentRequestsLimit: 1,
      robotsEnabled: false
    });
    var timeout = setTimeout(function () {
      crawler.stop();
      reject(new Error("Crawl did not complete in time."));
    }, 5000);

    crawler.on("urllistcomplete", function () {
      clearTimeout(timeout);
      crawler.stop();
      resolve();
    });

    crawler.start();
  });
};

/**
 * Find the record for a URL in a \FifoUrlList.
 *
 * @param  {FifoUrlList} urlList  Queue to search.
 * @param  {string} url           URL to look for.
 * @return {Url|null}             Matching \Url, or null.
 */
var findRecord = function (urlList, url) {
  var found = null;

  urlList._list.forEach(function (candidate) {
    if (candidate.getUrl() === url) {
      found = candidate;
    }
  });

  return found;
};

/**
 * Build a route table that redirects /0 -> /1 -> ... -> /<chainLength>, and
 * makes the last route return a 200 instead of redirecting.
 *
 * @param  {number} chainLength   Number of redirect responses.
 * @return {Object}               Route table for \localServer.
 */
var makeChainRoutes = function (chainLength) {
  var routes = {},
      i;

  for (i = 0; i < chainLength; i++) {
    routes["/" + i] = (function (nextIndex) {
      return function (req, res) {
        res.statusCode = 301;
        res.setHeader("location", "/" + nextIndex);
        res.end();
      };
    })(i + 1);
  }

  routes["/" + chainLength] = {
    statusCode: 200,
    headers: {
      "content-type": "text/html"
    },
    body: "<html><body>end of chain</body></html>"
  };

  return routes;
};

describe("Redirect tracking", function () {
  var server;

  afterEach(function () {
    if (server) {
      return server.close().then(function () {
        server = null;
      });
    }
  });

  describe("Crawler", function () {
    it("records the final URL and queues the target for an absolute Location", function () {
      var urlList,
          originalUrl,
          destinationUrl;

      return localServer({
        "/old": function (req, res, ctx) {
          res.statusCode = 301;
          res.setHeader("location", ctx.url + "/destination.html");
          res.end();
        },
        "/destination.html": {
          statusCode: 200,
          headers: {
            "content-type": "text/html"
          },
          body: "<html><body>destination</body></html>"
        }
      }).then(function (s) {
        server = s;
        originalUrl = server.url + "/old";
        destinationUrl = server.url + "/destination.html";
        urlList = new FifoUrlList();

        return urlList.insertIfNotExists(new Url({
          url: originalUrl
        }));
      }).then(function () {
        return crawlToCompletion(urlList);
      }).then(function () {
        var originalRecord = findRecord(urlList, originalUrl),
            destinationRecord = findRecord(urlList, destinationUrl);

        // Original URL points at the destination, and was successful (3xx is
        // not treated as an error).
        expect(originalRecord).to.not.equal(null);
        expect(originalRecord.getFinalUrl()).to.equal(destinationUrl);
        expect(originalRecord.getStatusCode()).to.equal(301);
        expect(originalRecord.getErrorCode()).to.equal(null);
        expect(originalRecord.getNumRedirects()).to.equal(0);

        // The destination was queued as a separate entry and then crawled.
        expect(destinationRecord).to.not.equal(null);
        expect(destinationRecord.getStatusCode()).to.equal(200);

        // The destination did not itself redirect, but was reached via a
        // redirect, so its record points at itself and shows the chain depth.
        expect(destinationRecord.getFinalUrl()).to.equal(destinationUrl);
        expect(destinationRecord.getNumRedirects()).to.equal(1);
      });
    });

    it("resolves a relative Location against the original URL", function () {
      var urlList,
          originalUrl,
          destinationUrl;

      return localServer({
        "/dir/old": {
          statusCode: 301,
          headers: {
            location: "/destination2.html"
          }
        },
        "/destination2.html": {
          statusCode: 200,
          headers: {
            "content-type": "text/html"
          },
          body: "<html><body>destination</body></html>"
        }
      }).then(function (s) {
        server = s;
        originalUrl = server.url + "/dir/old";
        destinationUrl = server.url + "/destination2.html";
        urlList = new FifoUrlList();

        return urlList.insertIfNotExists(new Url({
          url: originalUrl
        }));
      }).then(function () {
        return crawlToCompletion(urlList);
      }).then(function () {
        var originalRecord = findRecord(urlList, originalUrl),
            destinationRecord = findRecord(urlList, destinationUrl);

        expect(originalRecord.getFinalUrl()).to.equal(destinationUrl);
        expect(destinationRecord).to.not.equal(null);
        expect(destinationRecord.getFinalUrl()).to.equal(destinationUrl);
        expect(destinationRecord.getNumRedirects()).to.equal(1);
      });
    });

    it("accumulates redirect counts along a chain", function () {
      var urlList;

      return localServer(makeChainRoutes(3)).then(function (s) {
        server = s;
        urlList = new FifoUrlList();

        return urlList.insertIfNotExists(new Url({
          url: server.url + "/0"
        }));
      }).then(function () {
        return crawlToCompletion(urlList);
      }).then(function () {
        var expected = [
          {path: "/0", numRedirects: 0, finalPath: "/1"},
          {path: "/1", numRedirects: 1, finalPath: "/2"},
          {path: "/2", numRedirects: 2, finalPath: "/3"},
          {path: "/3", numRedirects: 3, finalPath: "/3"}
        ];

        expected.forEach(function (item) {
          var record = findRecord(urlList, server.url + item.path);

          expect(record).to.not.equal(null);
          expect(record.getNumRedirects()).to.equal(item.numRedirects);
          expect(record.getFinalUrl()).to.equal(server.url + item.finalPath);
        });

        // The final (non-redirecting) page was crawled successfully.
        expect(findRecord(urlList, server.url + "/3").getStatusCode()).to.equal(200);
        expect(findRecord(urlList, server.url + "/3").getErrorCode()).to.equal(null);
      });
    });

    it("stops queueing after 10 redirects and ends with a recognisable error", function () {
      var urlList,
          crawledUrls = [];

      // 12 consecutive redirects: /0 -> /1 -> ... -> /12. The crawler must
      // stop following after 10 hops, so /12 should never be requested.
      return localServer(makeChainRoutes(12)).then(function (s) {
        server = s;
        urlList = new FifoUrlList();

        return urlList.insertIfNotExists(new Url({
          url: server.url + "/0"
        }));
      }).then(function () {
        return crawlToCompletion(urlList);
      }).then(function () {
        urlList._list.forEach(function (record) {
          crawledUrls.push(record.getUrl());
        });

        // /0 through /10 were crawled: /10 is the 11th entry in the chain and
        // was reached after 10 redirects. It redirects once more, but the
        // destination (/11) must not be queued.
        expect(crawledUrls).to.include(server.url + "/10");
        expect(crawledUrls).to.not.include(server.url + "/11");
        expect(crawledUrls).to.not.include(server.url + "/12");
        expect(server.requests).to.not.include("/11");
        expect(server.requests).to.not.include("/12");

        // The 11th redirecting URL ended with a recognisable error code, and
        // the crawler kept running (no unhandled exception / rejected crawl).
        var lastRecord = findRecord(urlList, server.url + "/10");
        expect(lastRecord.getErrorCode()).to.equal("TOO_MANY_REDIRECTS");
        expect(lastRecord.getStatusCode()).to.equal(301);
        expect(lastRecord.getNumRedirects()).to.equal(MAX_REDIRECTS);
        expect(lastRecord.getFinalUrl()).to.equal(server.url + "/11");
      });
    });

    it("emits the standard events and redirect events as before", function () {
      var urlList,
          crawler,
          crawled = [],
          redirects = [];

      return localServer({
        "/old": {
          statusCode: 301,
          headers: {
            location: "/new"
          }
        },
        "/new": {
          statusCode: 200,
          headers: {
            "content-type": "text/html"
          },
          body: "<html><body>new</body></html>"
        }
      }).then(function (s) {
        server = s;
        urlList = new FifoUrlList();

        return urlList.insertIfNotExists(new Url({
          url: server.url + "/old"
        }));
      }).then(function () {
        return new Promise(function (resolve, reject) {
          crawler = new Crawler({
            urlList: urlList,
            interval: 1,
            concurrentRequestsLimit: 1,
            robotsEnabled: false
          });

          crawler.on("redirect", function (url, location) {
            redirects.push([url, location]);
          });

          crawler.on("crawledurl", function (url, errorCode, statusCode, errorMessage) {
            crawled.push([url, errorCode, statusCode, errorMessage]);
          });

          crawler.on("urllistcomplete", function () {
            crawler.stop();
            resolve();
          });

          setTimeout(function () {
            crawler.stop();
            reject(new Error("Crawl did not complete in time."));
          }, 5000);

          crawler.start();
        });
      }).then(function () {
        var oldCrawl;

        expect(redirects).to.deep.equal([
          [server.url + "/old", "/new"]
        ]);

        oldCrawl = crawled.filter(function (item) {
          return item[0] === server.url + "/old";
        })[0];

        // `crawledurl` keeps its 4-argument signature.
        expect(oldCrawl[1]).to.equal(null);
        expect(oldCrawl[2]).to.equal(301);
        expect(oldCrawl[3]).to.equal(null);
      });
    });

    it("does not treat links discovered by handlers as redirect successors", function () {
      var urlList,
          crawler;

      return localServer({
        "/page.html": {
          statusCode: 200,
          headers: {
            "content-type": "text/html"
          },
          body: "<html><body><a href=\"/linked.html\">link</a></body></html>"
        },
        "/linked.html": {
          statusCode: 200,
          headers: {
            "content-type": "text/html"
          },
          body: "<html><body>linked</body></html>"
        }
      }).then(function (s) {
        server = s;
        urlList = new FifoUrlList();

        return urlList.insertIfNotExists(new Url({
          url: server.url + "/page.html"
        }));
      }).then(function () {
        return new Promise(function (resolve, reject) {
          crawler = new Crawler({
            urlList: urlList,
            interval: 1,
            concurrentRequestsLimit: 1,
            robotsEnabled: false
          });

          crawler.addHandler("text/html", function (context) {
            var match = /href="([^"]+)"/.exec(context.body.toString());

            return match ? [context.url.replace("/page.html", match[1])] : [];
          });

          crawler.on("urllistcomplete", function () {
            crawler.stop();
            resolve();
          });

          setTimeout(function () {
            crawler.stop();
            reject(new Error("Crawl did not complete in time."));
          }, 5000);

          crawler.start();
        });
      }).then(function () {
        var linkedRecord = findRecord(urlList, server.url + "/linked.html");

        expect(linkedRecord).to.not.equal(null);
        expect(linkedRecord.getNumRedirects()).to.equal(0);
        expect(linkedRecord.getFinalUrl()).to.equal(null);
      });
    });
  });
});
