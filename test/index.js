import chai from "chai";
import http from "http";
import crawlerr from "../src/";

describe("crawlerr", function() {
  const expect = chai.expect;

  // These tests run fully offline: a temporary local HTTP server serves the
  // success cases, and an unresolvable hostname serves the failure case.
  this.timeout(30000);

  let server;
  let base;

  before(function(done) {
    server = http.createServer((req, res) => {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end("<html><body></body></html>");
    });

    server.listen(0, "127.0.0.1", () => {
      base = `http://127.0.0.1:${server.address().port}/`;
      done();
    });
  });

  after(function(done) {
    server.close(done);
  });

  describe("spider", function() {
    describe(".get(uri)", function() {
      it("should handle promises", function() {
        const spider = crawlerr(base);

        expect(spider.get("/")).to.have.property("then");
        expect(spider.get("/")).to.have.property("catch");
      });

      it("should handle a valid request", function() {
        const spider = crawlerr(base);

        return spider.get("/").then(({ req, res, uri }) => {
          expect(req).to.be.an("object");
          expect(res).to.be.an("object");
          expect(uri).to.equal(base);
        });
      });

      it("should throw on not valid request", function() {
        // `.invalid` is a reserved TLD that never resolves, so this fails
        // locally without contacting any real site:
        const invalidHost = "nonexistent-crawlerr-host.invalid";
        const spider = crawlerr(`https://${invalidHost}/`);

        return spider
          .get("/", { retries: 0, noResponseRetries: 0 })
          .then(
            () =>
              Promise.reject(new Error("Expected spider.get to reject")),
            error => {
              expect(error).to.be.an.instanceof(Error);
              expect(error.message).to.be.a("string");
              expect(error.message.includes(`ENOTFOUND ${invalidHost}`)).to.equal(
                true
              );
              // New failure contract:
              expect(error.uri).to.equal(`https://${invalidHost}/`);
              expect(error.retriesExhausted).to.equal(false);
            }
          );
      });
    });

    describe(".when(pattern)", function() {
      it("should handle a valid request", function(done) {
        const spider = crawlerr(base);

        spider.when("/", ({ req, res, uri }) => {
          spider.stop();

          expect(req).to.be.an("object");
          expect(res).to.be.an("object");
          expect(uri).to.equal(base);
          done();
        });

        spider.start();
      });
    });
  });
});
