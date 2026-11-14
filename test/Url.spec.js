var Url = require("../lib/Url"),
    expect = require("chai").expect;

describe("Url", function () {
  it("returns an instance when called as a function", function () {
    expect(Url({
      url: "https://example.com"
    })).to.be.an.instanceOf(Url);
  });

  it("uses URL as the unique ID", function () {
    expect(new Url({
      url: "https://example.com"
    }).getUniqueId()).to.equal("https://example.com");
  });

  it("accepts a string URL as the only argument", function () {
    expect(new Url("https://example.com").getUniqueId()).to.equal("https://example.com");
  });

  describe("#getStatusCode", function () {
    it("returns the statusCode if specified", function () {
      expect(new Url({
        url: "https://example.com",
        statusCode: 201
      }).getStatusCode()).to.equal(201);
    });

    it("defaults to null if statusCode not specified", function () {
      expect(new Url({
        url: "https://example.com"
      }).getStatusCode()).to.equal(null);
    });
  });

  describe("#getUrl", function () {
    it("returns the URL", function () {
      expect(new Url({
        url: "https://example.com"
      }).getUrl()).to.equal("https://example.com");
    });
  });

  describe("#getErrorCode", function () {
    it("returns the error code", function () {
      expect(new Url({
        url: "https://example.com",
        errorCode: "ROBOTS_NOT_ALLOWED"
      }).getErrorCode()).to.equal("ROBOTS_NOT_ALLOWED");
    });
  });

  describe("#getErrorMessage", function () {
    it("returns the error message", function () {
      expect(new Url({
        url: "https://example.com",
        errorCode: "ROBOTS_NOT_ALLOWED",
        errorMessage: "A robots.txt error"
      }).getErrorMessage()).to.equal("A robots.txt error");
    });

    it("returns null if no rror message", function () {
      expect(new Url({
        url: "https://example.com",
        errorCode: "ROBOTS_NOT_ALLOWED"
      }).getErrorMessage()).to.equal(null);
    });
  });

  describe("#getFinalUrl", function () {
    it("returns the final URL if specified", function () {
      expect(new Url({
        url: "https://example.com/old",
        finalUrl: "https://example.com/new"
      }).getFinalUrl()).to.equal("https://example.com/new");
    });

    it("defaults to null if final URL not specified", function () {
      expect(new Url({
        url: "https://example.com"
      }).getFinalUrl()).to.equal(null);
    });
  });

  describe("#getNumRedirects", function () {
    it("returns the number of redirects if specified", function () {
      expect(new Url({
        url: "https://example.com",
        numRedirects: 3
      }).getNumRedirects()).to.equal(3);
    });

    it("defaults to 0 if number of redirects not specified", function () {
      expect(new Url({
        url: "https://example.com"
      }).getNumRedirects()).to.equal(0);
    });

    it("accepts redirectCount as an alias option", function () {
      expect(new Url({
        url: "https://example.com",
        redirectCount: 2
      }).getNumRedirects()).to.equal(2);
    });

    it("exposes getRedirectCount as an alias", function () {
      expect(new Url({
        url: "https://example.com",
        numRedirects: 2
      }).getRedirectCount()).to.equal(2);
    });
  });
});
