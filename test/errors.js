import chai from "chai";
import http from "http";
import crawlerr from "../src/";

describe("crawlerr failure handling", function() {
  const expect = chai.expect;

  // Retries introduce exponential delays (up to several seconds per attempt),
  // and jsdom is a heavy dependency:
  this.timeout(30000);

  // `get-link` only recognizes dotted hostnames and silently drops non-default
  // ports. A dotted hostname mapped locally to 127.0.0.1 lets us test anchor
  // expansion offline with exact URL assertions — no real DNS is involved:
  const HOST = "crawlerr.test";
  const localLookup = (hostname, options, callback) => {
    if (typeof options === "function") {
      callback = options;
      options = {};
    }

    if (hostname !== HOST) {
      return callback(new Error(`unexpected lookup for ${hostname}`));
    }

    const address = { address: "127.0.0.1", family: 4 };

    // Node's http client calls lookup with `{ all: true }`, expecting an
    // array of `{ address, family }` records:
    if (options && options.all) {
      return callback(null, [address]);
    }

    return callback(null, address.address, address.family);
  };

  let server;
  let port;
  let base;

  before(function(done) {
    server = http.createServer((req, res) => {
      if (req.url === "/") {
        res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        // The relative link is resolved by `get-link` during anchor parsing
        // (it drops the non-default port, which is a limitation of that
        // library) and then enqueued, handled and requested like any other
        // discovered page:
        res.end(
          '<html><body><a href="/not-found">failing</a>' +
            '<a href="https://example.com/off-site">off-site</a></body></html>'
        );
        return;
      }

      if (req.url === "/boom") {
        // 500 is retried by retry-request (its default shouldRetryFn):
        res.writeHead(500, { "Content-Type": "text/plain" });
        res.end("boom");
        return;
      }

      if (req.url === "/not-found") {
        // 404 is a client error and is never retried:
        res.writeHead(404, { "Content-Type": "text/plain" });
        res.end("not found");
        return;
      }

      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end("<html><body></body></html>");
    });

    server.listen(0, "127.0.0.1", () => {
      port = server.address().port;
      base = `http://${HOST}:${port}/`;
      done();
    });
  });

  after(function(done) {
    server.close(done);
  });

  describe("successful crawl", function() {
    it("resolves with the same { req, res, uri } structure", function() {
      const spider = crawlerr(base, { lookup: localLookup });

      return spider.get("/").then(result => {
        expect(result).to.have.property("req");
        expect(result).to.have.property("res");
        expect(result).to.have.property("uri");
        expect(result.req).to.be.an("object");
        expect(result.res).to.be.an("object");
        expect(result.uri).to.equal(base);
        expect(result.res.statusCode).to.equal(200);
      });
    });

    it("runs when() callbacks and expands <a> links", function(done) {
      const spider = crawlerr(base, { lookup: localLookup });
      const seen = [];

      spider.when("/", ({ req, res, uri }) => {
        expect(req).to.be.an("object");
        expect(res).to.be.an("object");
        expect(uri).to.equal(base);
        seen.push(uri);
      });

      spider.on("error", error => {
        // The anchor discovered on the successful page is enqueued by
        // `parseAnchors`, handled by the queue and fails when requested. The
        // queue re-emits its rejection as an "error" event carrying the full
        // SpiderError instead of a bare uri string:
        try {
          expect(error).to.be.an.instanceof(Error);
          expect(error.name).to.equal("SpiderError");
          expect(error.uri).to.contain("crawlerr.test");
          expect(error.uri).to.contain("/not-found");
          expect(error.retriesExhausted).to.equal(false);
          // The successful crawl (and its when() callback) ran first:
          expect(seen).to.include(base);
          spider.stop();
          done();
        } catch (assertionError) {
          done(assertionError);
        }
      });

      spider.start();
    });
  });

  describe("ordinary failure (no retry was made)", function() {
    it("rejects with an Error carrying the absolute uri and details", function() {
      const spider = crawlerr(base, { lookup: localLookup });

      return spider.get("/not-found").then(
        () => Promise.reject(new Error("Expected spider.get to reject")),
        error => {
          expect(error).to.be.an.instanceof(Error);
          expect(error.name).to.equal("SpiderError");
          expect(error.message).to.be.a("string");
          expect(error.message).to.contain("/not-found");
          expect(error.message).to.contain("404");
          expect(error.uri).to.equal(`${base}not-found`);
          expect(error.retriesExhausted).to.equal(false);
          expect(error.statusCode).to.equal(404);
        }
      );
    });

    it("reports network failures against an unreachable local address", function() {
      // Grab a free port, close it and then connect to it: the OS keeps the
      // port closed, giving ECONNREFUSED without touching the network.
      const probe = http.createServer();

      const freePort = new Promise(resolve =>
        probe.listen(0, "127.0.0.1", () => resolve(probe.address().port))
      ).then(
        free =>
          new Promise(resolve => {
            probe.close(() => resolve(free));
          })
      );

      return freePort.then(closedPort => {
        const unreachable = `http://127.0.0.1:${closedPort}/`;
        const spider = crawlerr(unreachable);

        // A single failed connection, no retries are performed:
        return spider
          .get("/", { retries: 1, noResponseRetries: 0 })
          .then(
            () =>
              Promise.reject(new Error("Expected spider.get to reject")),
            error => {
              expect(error).to.be.an.instanceof(Error);
              expect(error.message).to.be.a("string");
              expect(error.message).to.contain("ECONNREFUSED");
              expect(error.uri).to.equal(unreachable);
              expect(error.retriesExhausted).to.equal(false);
              expect(error.code).to.equal("ECONNREFUSED");
            }
          );
      });
    });
  });

  describe("retry exhaustion", function() {
    it("marks the error as retriesExhausted on a persistent 500", function() {
      const spider = crawlerr(base, { lookup: localLookup });

      return spider
        .get("/boom", { retries: 2, noResponseRetries: 0 })
        .then(
          () => Promise.reject(new Error("Expected spider.get to reject")),
          error => {
            expect(error).to.be.an.instanceof(Error);
            expect(error.uri).to.equal(`${base}boom`);
            expect(error.retriesExhausted).to.equal(true);
            expect(error.statusCode).to.equal(500);
            expect(error.message).to.contain("/boom");
            expect(error.message).to.contain("500");
          }
        );
    });

    it("emits the same structured error object on the spider error event", function(done) {
      // A crawler whose base itself keeps failing: `start()` requests it via
      // `handle` and re-emits its rejection as an "error" event.
      const spider = crawlerr(`${base}boom`, {
        lookup: localLookup,
        retries: 2,
        noResponseRetries: 0
      });

      spider.on("error", error => {
        try {
          expect(error).to.be.an.instanceof(Error);
          expect(error.name).to.equal("SpiderError");
          expect(error.uri).to.equal(`${base}boom`);
          expect(error.retriesExhausted).to.equal(true);
          expect(error.statusCode).to.equal(500);
          spider.stop();
          done();
        } catch (assertionError) {
          done(assertionError);
        }
      });

      spider.start();
    });
  });
});
