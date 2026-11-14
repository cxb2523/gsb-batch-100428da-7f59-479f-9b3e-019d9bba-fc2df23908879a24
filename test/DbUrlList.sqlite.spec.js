var expect = require("chai").expect,
    path = require("path"),
    os = require("os"),
    fs = require("fs"),
    Sequelize = require("sequelize"),
    DbUrlList = require("../lib/DbUrlList"),
    Url = require("../lib/Url");

/**
 * These tests exercise DbUrlList against a real SQLite database, including
 * migrating a table that was created before the redirect tracking columns
 * (`finalUrl` and `numRedirects`) existed.
 *
 * The `sqlite3` driver is a native module. If it can't be loaded on this
 * machine, the whole suite is skipped rather than failing, since the rest of
 * the package (and its other tests) do not depend on a working database.
 */

var sqlite3Available = false;

try {
  require("sqlite3");
  sqlite3Available = true;
} catch (err) {
  sqlite3Available = false;
}

var TEMP_DIR = path.join(os.tmpdir(), "supercrawler-redirect-tests-" + process.pid);

var makeDbFile = function (name) {
  if (!fs.existsSync(TEMP_DIR)) {
    fs.mkdirSync(TEMP_DIR, {recursive: true});
  }

  return path.join(TEMP_DIR, name + "-" + Date.now() + "-" +
    Math.floor(Math.random() * 100000) + ".sqlite");
};

var dbOpts = function (storage) {
  return {
    db: {
      database: "crawler",
      username: "crawleruser",
      password: "crawlerpassword",
      sequelizeOpts: {
        dialect: "sqlite",
        storage: storage
      }
    }
  };
};

/**
 * Create a table using the schema that predates redirect tracking, and insert
 * one row into it, so we can verify automatic column migration afterwards.
 *
 * @param  {string} storage  SQLite file path.
 * @return {Promise}         Promise resolved once the legacy table exists.
 */
var createLegacyTable = function (storage) {
  var db = new Sequelize("crawler", "crawleruser", "crawlerpassword", {
    dialect: "sqlite",
    storage: storage,
    logging: false
  });
  var legacyTable = db.define("url", {
    urlHash: {
      type: Sequelize.STRING(40),
      allowNull: false
    },
    url: {
      type: Sequelize.STRING(10000),
      allowNull: false
    },
    statusCode: {
      type: Sequelize.STRING,
      allowNull: true
    },
    errorCode: {
      type: Sequelize.STRING,
      allowNull: true
    },
    errorMessage: {
      type: Sequelize.STRING(1000),
      allowNull: true
    },
    numErrors: {
      type: Sequelize.INTEGER(10),
      allowNull: false
    },
    nextRetryDate: {
      type: Sequelize.DATE,
      allowNull: false
    }
  }, {
    indexes: [{
      unique: false,
      fields: ["nextRetryDate"]
    }, {
      unique: true,
      fields: ["urlHash"]
    }]
  });

  return legacyTable.sync().then(function () {
    return legacyTable.create({
      urlHash: "legacy-hash",
      url: "https://example.com/legacy",
      statusCode: "200",
      errorCode: null,
      errorMessage: null,
      numErrors: 0,
      nextRetryDate: new Date(2000, 0, 1)
    });
  }).then(function () {
    return db.close();
  });
};

describe("DbUrlList with SQLite", function () {
  var dbFile,
      dbUrlList;

  before(function () {
    if (!sqlite3Available) {
      this.skip();
    }
  });

  afterEach(function () {
    var list = dbUrlList,
        closeProm;

    dbUrlList = null;
    closeProm = list ? list._db.close() : Promise.resolve();

    return closeProm.catch(function () {
      return true;
    }).then(function () {
      if (dbFile && fs.existsSync(dbFile)) {
        try {
          fs.unlinkSync(dbFile);
        } catch (err) {
          // best effort cleanup
        }
      }

      dbFile = null;
    });
  });

  it("adds the redirect columns to an existing legacy table", function () {
    var queryInterface;

    dbFile = makeDbFile("legacy");

    return createLegacyTable(dbFile).then(function () {
      dbUrlList = new DbUrlList(dbOpts(dbFile));
      return dbUrlList.insertIfNotExists(new Url({
        url: "https://example.com/new"
      }));
    }).then(function () {
      queryInterface = dbUrlList._db.getQueryInterface();
      return queryInterface.describeTable(dbUrlList._urlTable.getTableName());
    }).then(function (columns) {
      expect(columns).to.have.property("finalUrl");
      expect(columns).to.have.property("numRedirects");
    });
  });

  it("reads back the final URL and number of redirects", function () {
    var createdUrl;

    dbFile = makeDbFile("readwrite");
    dbUrlList = new DbUrlList(dbOpts(dbFile));
    createdUrl = new Url({
      url: "https://example.com/redirected",
      statusCode: 301,
      finalUrl: "https://example.com/destination",
      numRedirects: 4
    });

    return dbUrlList.insertIfNotExists(createdUrl).then(function () {
      // The record is ready to crawl immediately; fetch it back through the
      // queue's public API.
      return dbUrlList.getNextUrl();
    }).then(function (url) {
      expect(url.getUrl()).to.equal("https://example.com/redirected");
      expect(url.getFinalUrl()).to.equal("https://example.com/destination");
      expect(url.getNumRedirects()).to.equal(4);
    });
  });

  it("stores null/0 for a URL that did not redirect", function () {
    dbFile = makeDbFile("plain");
    dbUrlList = new DbUrlList(dbOpts(dbFile));

    return dbUrlList.insertIfNotExists(new Url({
      url: "https://example.com/plain",
      statusCode: 200
    })).then(function () {
      return dbUrlList.getNextUrl();
    }).then(function (url) {
      expect(url.getFinalUrl()).to.equal(null);
      expect(url.getNumRedirects()).to.equal(0);
    });
  });

  it("leaves legacy rows with a null final URL and 0 redirects", function () {
    dbFile = makeDbFile("legacyrows");

    return createLegacyTable(dbFile).then(function () {
      dbUrlList = new DbUrlList(dbOpts(dbFile));
      return dbUrlList.getNextUrl();
    }).then(function (url) {
      expect(url.getUrl()).to.equal("https://example.com/legacy");
      expect(url.getFinalUrl()).to.equal(null);
      expect(url.getNumRedirects()).to.equal(0);
    });
  });
});
