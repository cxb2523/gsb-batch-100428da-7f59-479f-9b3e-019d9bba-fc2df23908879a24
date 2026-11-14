var http = require("http"),
    Promise = require("bluebird");

/**
 * Start a local HTTP server serving a fixed set of routes. Each route maps a
 * path (e.g. "/page.html") to either an object of the form
 * `{statusCode, headers, body}` or a function `(req, res, ctx)` that handles
 * the request itself. The third argument `ctx` exposes the server's own
 * `url` so that a handler can construct an absolute `Location` header
 * pointing back at this server.
 *
 * This lets tests exercise redirects, errors and content handlers entirely on
 * localhost, without depending on the internet.
 *
 * @param  {Object} routes Path-to-response mapping.
 * @return {Promise} Promise resolving with {url, requests, close}.
 */
module.exports = function (routes) {
  var requests = [];

  return new Promise(function (resolve, reject) {
    var ctx = {
      url: null,
      requests: requests
    };
    var server = http.createServer(function (req, res) {
      var route,
          response;

      requests.push(req.url);
      route = routes[req.url];

      if (typeof route === "function") {
        return route(req, res, ctx);
      }

      if (typeof route === "undefined") {
        res.statusCode = 404;
        res.setHeader("content-type", "text/plain");
        res.end("Not found");

        return;
      }

      response = {
        statusCode: route.statusCode || 200,
        headers: route.headers || {},
        body: (typeof route.body === "undefined") ? "" : route.body
      };

      res.statusCode = response.statusCode;
      Object.keys(response.headers).forEach(function (name) {
        res.setHeader(name, response.headers[name]);
      });
      res.end(response.body);
    });

    server.on("error", reject);
    server.listen(0, "127.0.0.1", function () {
      var address = server.address();

      ctx.url = "http://127.0.0.1:" + address.port;

      resolve({
        url: ctx.url,
        requests: requests,
        close: function () {
          return new Promise(function (resolveClose) {
            server.close(function () {
              resolveClose();
            });
          });
        }
      });
    });
  });
};
