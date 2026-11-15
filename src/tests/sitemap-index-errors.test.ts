import 'async';
import 'assert';
import 'should';

import Sitemapper from '../../lib/assets/sitemapper.js';

/**
 * Builds a stub for sitemapper.parse that serves locally constructed
 * sitemapindex / urlset / error responses keyed by URL, so no real
 * network requests are made.
 */
function stubParse(sitemapper: Sitemapper, responses: { [url: string]: any }) {
  (sitemapper as any).parse = async (url: string) => {
    const response = responses[url];
    if (!response) {
      return {
        error: `Error occurred: UnexpectedUrl`,
        data: { name: 'UnexpectedUrlError' },
      };
    }
    return response;
  };
}

function urlset(...locs: string[]) {
  return {
    error: null,
    data: {
      urlset: {
        url: locs.map((loc) => ({ loc })),
      },
    },
  };
}

function sitemapindex(...locs: string[]) {
  return {
    error: null,
    data: {
      sitemapindex: {
        sitemap: locs.map((loc) => ({ loc })),
      },
    },
  };
}

function failure(type: string, message: string) {
  return {
    error: message,
    data: { name: type },
  };
}

describe('Sitemapper sitemapindex error handling', function () {
  let sitemapper: Sitemapper;

  beforeEach(() => {
    sitemapper = new Sitemapper();
  });

  describe('when all child sitemaps succeed', function () {
    it('should merge all sites in the original order with no errors', async function () {
      stubParse(sitemapper, {
        'https://example.com/sitemap.xml': sitemapindex(
          'https://example.com/sitemap-1.xml',
          'https://example.com/sitemap-2.xml',
          'https://example.com/sitemap-3.xml'
        ),
        'https://example.com/sitemap-1.xml': urlset(
          'https://example.com/page-1',
          'https://example.com/page-2'
        ),
        'https://example.com/sitemap-2.xml': urlset(
          'https://example.com/page-3'
        ),
        'https://example.com/sitemap-3.xml': urlset(
          'https://example.com/page-4',
          'https://example.com/page-5'
        ),
      });

      const result = await sitemapper.fetch('https://example.com/sitemap.xml');

      result.errors.should.be.an.Array().and.have.length(0);
      result.sites.should.deepEqual([
        'https://example.com/page-1',
        'https://example.com/page-2',
        'https://example.com/page-3',
        'https://example.com/page-4',
        'https://example.com/page-5',
      ]);
    });
  });

  describe('when some child sitemaps fail', function () {
    it('should return the successful sites and one readable error per failed sitemap', async function () {
      stubParse(sitemapper, {
        'https://example.com/sitemap.xml': sitemapindex(
          'https://example.com/sitemap-ok.xml',
          'https://example.com/sitemap-http-error.xml',
          'https://example.com/sitemap-timeout.xml'
        ),
        'https://example.com/sitemap-ok.xml': urlset(
          'https://example.com/page-1',
          'https://example.com/page-2'
        ),
        'https://example.com/sitemap-http-error.xml': failure(
          'HTTPError',
          'HTTP Error occurred: Response code 500 (Internal Server Error)'
        ),
        'https://example.com/sitemap-timeout.xml': failure(
          'CancelError',
          "Request timed out after 15000 milliseconds for url: 'https://example.com/sitemap-timeout.xml'"
        ),
      });

      const result = await sitemapper.fetch('https://example.com/sitemap.xml');

      // successful child sitemap pages are still returned
      result.sites.should.deepEqual([
        'https://example.com/page-1',
        'https://example.com/page-2',
      ]);

      // each failed child sitemap produces exactly one error record
      result.errors.should.be.an.Array().and.have.length(2);

      const byUrl: { [url: string]: any } = {};
      for (const error of result.errors as any[]) {
        byUrl[error.url] = error;
        // every record carries url, type, message and retries
        error.should.have.property('url').which.is.a.String();
        error.should.have.property('type').which.is.a.String();
        error.should.have.property('message').which.is.a.String();
        error.message.length.should.be.above(0);
        error.should.have.property('retries').which.is.a.Number();
      }

      byUrl.should.have.property('https://example.com/sitemap-http-error.xml');
      byUrl.should.have.property('https://example.com/sitemap-timeout.xml');
      byUrl['https://example.com/sitemap-http-error.xml'].type.should.equal(
        'HTTPError'
      );
      byUrl[
        'https://example.com/sitemap-http-error.xml'
      ].message.should.containEql('500');
      byUrl['https://example.com/sitemap-timeout.xml'].type.should.equal(
        'CancelError'
      );
      byUrl[
        'https://example.com/sitemap-timeout.xml'
      ].message.should.containEql('timed out');
    });

    it('should report a duplicated failing child sitemap only once', async function () {
      stubParse(sitemapper, {
        'https://example.com/sitemap.xml': sitemapindex(
          'https://example.com/sitemap-ok.xml',
          'https://example.com/sitemap-bad.xml',
          'https://example.com/sitemap-bad.xml'
        ),
        'https://example.com/sitemap-ok.xml': urlset(
          'https://example.com/page-1'
        ),
        'https://example.com/sitemap-bad.xml': failure(
          'HTTPError',
          'HTTP Error occurred: Response code 404 (Not Found)'
        ),
      });

      const result = await sitemapper.fetch('https://example.com/sitemap.xml');

      result.sites.should.deepEqual(['https://example.com/page-1']);
      result.errors.should.have.length(1);
      result.errors[0].url.should.equal('https://example.com/sitemap-bad.xml');
      result.errors[0].should.have.property('message').which.is.a.String();
    });

    it('should keep the sites of successful sitemaps in their original order', async function () {
      stubParse(sitemapper, {
        'https://example.com/sitemap.xml': sitemapindex(
          'https://example.com/sitemap-1.xml',
          'https://example.com/sitemap-bad.xml',
          'https://example.com/sitemap-2.xml'
        ),
        'https://example.com/sitemap-1.xml': urlset(
          'https://example.com/a',
          'https://example.com/b'
        ),
        'https://example.com/sitemap-bad.xml': failure(
          'ParseError',
          'Error occurred: ParseError'
        ),
        'https://example.com/sitemap-2.xml': urlset(
          'https://example.com/c',
          'https://example.com/d'
        ),
      });

      const result = await sitemapper.fetch('https://example.com/sitemap.xml');

      result.sites.should.deepEqual([
        'https://example.com/a',
        'https://example.com/b',
        'https://example.com/c',
        'https://example.com/d',
      ]);
      result.errors.should.have.length(1);
    });
  });

  describe('when all child sitemaps fail', function () {
    it('should return empty sites and one error per child sitemap', async function () {
      stubParse(sitemapper, {
        'https://example.com/sitemap.xml': sitemapindex(
          'https://example.com/sitemap-1.xml',
          'https://example.com/sitemap-2.xml'
        ),
        'https://example.com/sitemap-1.xml': failure(
          'HTTPError',
          'HTTP Error occurred: Response code 503 (Service Unavailable)'
        ),
        'https://example.com/sitemap-2.xml': failure(
          'CancelError',
          "Request timed out after 15000 milliseconds for url: 'https://example.com/sitemap-2.xml'"
        ),
      });

      const result = await sitemapper.fetch('https://example.com/sitemap.xml');

      result.sites.should.be.an.Array().and.have.length(0);
      result.errors.should.be.an.Array().and.have.length(2);

      const urls = (result.errors as any[]).map((error) => error.url);
      urls.should.containEql('https://example.com/sitemap-1.xml');
      urls.should.containEql('https://example.com/sitemap-2.xml');

      for (const error of result.errors as any[]) {
        error.should.have.property('type').which.is.a.String();
        error.should.have.property('message').which.is.a.String();
        error.message.length.should.be.above(0);
        error.should.have.property('retries').which.is.a.Number();
      }
    });
  });

  describe('when the root sitemap index itself fails', function () {
    it('should return empty sites and a single error', async function () {
      stubParse(sitemapper, {
        'https://example.com/sitemap.xml': failure(
          'HTTPError',
          'HTTP Error occurred: Response code 404 (Not Found)'
        ),
      });

      const result = await sitemapper.fetch('https://example.com/sitemap.xml');

      result.sites.should.be.an.Array().and.have.length(0);
      result.errors.should.be.an.Array().and.have.length(1);
      result.errors[0].url.should.equal('https://example.com/sitemap.xml');
      result.errors[0].type.should.equal('HTTPError');
      result.errors[0].should.have.property('message').which.is.a.String();
      result.errors[0].message.should.containEql('404');
      result.errors[0].should.have.property('retries').which.is.a.Number();
    });
  });
});
