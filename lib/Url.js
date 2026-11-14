var Url;

/**
 * Represents a URL, that is either waiting to be crawled or has already
 * been crawled. It also contains some state information, i.e. whether or not
 * the page was crawled, status code, etc.
 *
 * @param {Object|string} opts Options about this URL. Can also be string URL.
 */
Url = function (opts) {
  if (!(this instanceof Url)) {
    return new Url(opts);
  }

  if (typeof opts === "string") {
    opts = {
      url: opts
    };
  }

  this._url = opts.url;
  this._statusCode = opts.statusCode ? opts.statusCode : null;
  this._errorCode = opts.errorCode ? opts.errorCode : null;
  this._errorMessage = opts.errorMessage ? opts.errorMessage : null;

  // The final URL that this URL resolves to. For a URL that redirects, this
  // is the (absolute) destination given by the Location header. For a URL
  // that was reached via a redirect but does not itself redirect, this is the
  // URL itself. Null if no redirect was involved.
  this._finalUrl = opts.finalUrl ? opts.finalUrl : null;

  // Number of redirects that were followed (without actually following them;
  // the targets are added to the queue as separate entries) to reach this
  // URL. 0 for URLs that were not reached via a redirect.
  this._numRedirects = typeof opts.numRedirects === "number" ?
    opts.numRedirects : (typeof opts.redirectCount === "number" ? opts.redirectCount : 0);
};

/**
 * Get the string that uniquely identifies this record; typically the URL.
 * This will ensure that the object is replaced when added to a \UrlList.
 *
 * @return {string} Unique identifier
 */
Url.prototype.getUniqueId = function () {
  return this._url;
};

/**
 * Get the string URL that is to be requested.
 *
 * @return {string} URL.
 */
Url.prototype.getUrl = function () {
  return this._url;
};

/**
 * Get the error code of the the crawl.
 *
 * @return {string|null} String error code, or null if no error.
 */
Url.prototype.getErrorCode = function () {
  return this._errorCode;
};

/**
 * Return the status code of the crawl.
 *
 * @return {number|null} Status code, or null if crawl hasn't completed.
 */
Url.prototype.getStatusCode = function () {
  return this._statusCode;
};

/**
 * Return the error message of the URL.
 *
 * @return {string} Error message string.
 */
Url.prototype.getErrorMessage = function () {
  if (typeof this._errorMessage === "string") {
    return this._errorMessage.substr(0, 1000);
  }

  return null;
};

/**
 * Return the final URL that this URL resolves to. If this URL redirects, this
 * is the absolute destination from the Location header. If this URL was the
 * destination of a redirect (and does not itself redirect), this is the URL
 * itself. Null if this URL was not involved in a redirect.
 *
 * @return {string|null} Final URL, or null if no redirect was involved.
 */
Url.prototype.getFinalUrl = function () {
  return this._finalUrl;
};

/**
 * Return the number of redirects that were followed in the chain that led to
 * this URL. Redirect targets are added to the crawl queue as separate entries
 * rather than being followed automatically, so this counter is what allows a
 * chain of redirects to be traced. 0 if this URL was not reached via a
 * redirect.
 *
 * @return {number} Number of redirects in the chain.
 */
Url.prototype.getNumRedirects = function () {
  return this._numRedirects;
};

/**
 * Alias of \Url#getNumRedirects.
 *
 * @return {number} Number of redirects in the chain.
 */
Url.prototype.getRedirectCount = function () {
  return this.getNumRedirects();
};

module.exports = Url;
