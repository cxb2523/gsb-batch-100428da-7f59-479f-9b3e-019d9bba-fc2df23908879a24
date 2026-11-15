# frozen_string_literal: true

require File.expand_path('test_helper', __dir__)

# rubocop:disable Metrics/BlockLength
describe ValidateWebsite::Crawl do
  before do
    WebMock.reset!
    stub_request(:get, /#{TEST_DOMAIN}/).to_return(status: 200)
    _out, _err = capture_io do
      @validate_website = ValidateWebsite::Crawl.new(color: false)
    end
  end

  def validator
    ValidateWebsite::Validator
  end

  describe 'options' do
    it 'can change user-agent' do
      ua = %{Linux / Firefox 29: Mozilla/5.0 (X11; Linux x86_64; rv:29.0) \
             Gecko/20100101 Firefox/29.0}
      _out, _err = capture_io do
        v = ValidateWebsite::Crawl.new(site: TEST_DOMAIN, user_agent: ua)
        v.crawl
        _(v.crawler.user_agent).must_equal ua
      end
    end

    it 'can change html5 validator service url' do
      original = validator.html5_validator_service_url
      new = 'http://localhost:8888/'
      _out, _err = capture_io do
        ValidateWebsite::Crawl.new(site: TEST_DOMAIN,
                                   html5_validator_service_url: new)
        _(validator.html5_validator_service_url).must_equal new
        validator.html5_validator_service_url = original
      end
    end
  end

  describe('cookies') do
    it 'can set cookies' do
      cookies = 'tz=Europe%2FBerlin; guid=ZcpBshbtStgl9VjwTofq'
      _out, _err = capture_io do
        v = ValidateWebsite::Crawl.new(site: TEST_DOMAIN, cookies: cookies)
        v.crawl
        _(v.crawler.cookies.cookies_for_host(v.host))
          .must_equal v.default_cookies
      end
    end
  end

  describe('html') do
    it 'extract url' do
      name = 'xhtml1-strict'
      file = File.join('test', 'data', "#{name}.html")
      page = FakePage.new(name,
                          body: File.open(file).read,
                          content_type: 'text/html')
      @validate_website.site = page.url
      _out, _err = capture_io do
        @validate_website.crawl
      end
      _(@validate_website.history_count).must_equal 5
    end

    it 'extract link' do
      name = 'html4-strict'
      file = File.join('test', 'data', "#{name}.html")
      page = FakePage.new(name,
                          body: File.open(file).read,
                          content_type: 'text/html')
      @validate_website.site = page.url
      _out, _err = capture_io do
        @validate_website.crawl
      end
      _(@validate_website.history_count).must_equal 98
    end

    it 'can change validator' do
      name = 'html5-fail'
      file = File.join('test', 'data', "#{name}.html")
      page = FakePage.new(name,
                          body: File.open(file).read,
                          content_type: 'text/html')
      validator_res = File.join('test', 'data', 'validator.nu-failure.json')
      stub_request(:any, /#{validator.html5_validator_service_url}/)
        .to_return(body: File.open(validator_res).read)
      @validate_website.site = page.url
      _out, _err = capture_io do
        @validate_website.crawl(html5_validator: :nu, ignore: /Warning/)
      end
      _(@validate_website.errors_count).must_equal 1
    end

    it 'crawl when URLs are not ascii only' do
      name = 'cozy-community'
      file = File.join('test', 'data', "#{name}.html")
      page = FakePage.new(name,
                          body: File.open(file).read,
                          content_type: 'text/html')
      validator_res = File.join('test', 'data', 'validator.nu-failure.json')
      stub_request(:any, /#{validator.html5_validator_service_url}/)
        .to_return(body: File.open(validator_res).read)
      @validate_website.site = page.url
      _out, _err = capture_io do
        @validate_website.crawl
      end
    end

    it 'dont try to extract imgs for redirect' do
      url = 'https://wordpress.org/support/bb-login.php'
      stub_request(:get, url).to_return(
        status: 302,
        headers: {
          'Location' => 'https://login.wordpress.org/',
          'Content-Type' => 'text/html; charset=UTF-8'
        }
      )
      @validate_website.site = url
      _out, _err = capture_io do
        @validate_website.crawl
      end
    end
  end

  describe('css') do
    describe 'extract urls' do
      it 'crawl css and extract url' do
        page = FakePage.new('test.css',
                            body: '.t {background-image: url(pouet);}
                                 .t {background-image: url(/image/pouet.png)}
                                 .t {background-image: url(/image/pouet_42.png)}
                                 .t {background-image: url(/image/pouet)}',
                            content_type: 'text/css')
        @validate_website.site = page.url
        _out, _err = capture_io do
          @validate_website.crawl
        end
        _(@validate_website.history_count).must_equal 5
      end

      it 'should extract url with single quote' do
        page = FakePage.new('test.css',
                            body: ".test {background-image: url('pouet');}",
                            content_type: 'text/css')
        @validate_website.site = page.url
        _out, _err = capture_io do
          @validate_website.crawl
        end
        _(@validate_website.history_count).must_equal 2
      end

      it 'should extract url with double quote' do
        page = FakePage.new('test.css',
                            body: '.test {background-image: url("pouet");}',
                            content_type: 'text/css')
        @validate_website.site = page.url
        _out, _err = capture_io do
          @validate_website.crawl
        end
        _(@validate_website.history_count).must_equal 2
      end

      it 'should extract url with params' do
        page = FakePage.new('test.css',
                            body: '.test {background-image: url(/t?size=s);}',
                            content_type: 'text/css')
        @validate_website.site = page.url
        _out, _err = capture_io do
          @validate_website.crawl
        end
        _(@validate_website.history_count).must_equal 2
      end

      it 'should not extract invalid urls' do
        page = FakePage.new('test.css',
                            body: '.test {background-image: url(/test.png");}',
                            content_type: 'text/css')
        @validate_website.site = page.url
        _out, _err = capture_io do
          @validate_website.crawl
        end
        _(@validate_website.history_count).must_equal 1
      end
    end

    describe 'validate css syntax' do
      before do
        _out, _err = capture_io do
          @validate_website = ValidateWebsite::Crawl.new(color: false,
                                                         css_syntax: true)
        end
      end
      it 'should be invalid with bad urls' do
        page = FakePage.new('test.css',
                            body: '.test {background-image: url(/test.png");}',
                            content_type: 'text/css')
        @validate_website.site = page.url
        _out, _err = capture_io do
          @validate_website.crawl
        end
        _(@validate_website.errors_count).must_equal 1
      end

      it 'should be invalid with syntax error' do
        page = FakePage.new('test.css',
                            body: ' /**/ .foo {} #{bar {}',
                            content_type: 'text/css')
        @validate_website.site = page.url
        _out, _err = capture_io do
          @validate_website.crawl
        end
        _(@validate_website.errors_count).must_equal 1
      end
    end
  end

  describe 'exclude' do
    def stub_page(path, body, content_type = 'text/html')
      stub_request(:get, "#{TEST_DOMAIN.sub(%r{/$}, '')}#{path}").to_return(
        status: 200,
        headers: { 'Content-Type' => content_type },
        body: body
      )
    end

    it 'applies exclude to img and css urls before enqueuing' do
      stub_page('/', '<html><body>
                 <a href="/keep.html">keep</a>
                 <img src="/secret/image.png">
                 <link rel="stylesheet" href="/style.css">
               </body></html>')
      stub_page('/keep.html', '<html><body>keep</body></html>')
      stub_page('/style.css',
                '.a {background: url(/secret/bg.gif);}',
                'text/css')
      @validate_website.site = TEST_DOMAIN
      out, _err = capture_io do
        @validate_website.crawl(markup: false, exclude: /secret/)
      end
      _(@validate_website.crawler.history.map(&:to_s))
        .wont_include "#{TEST_DOMAIN}secret/image.png"
      _(@validate_website.crawler.history.map(&:to_s))
        .wont_include "#{TEST_DOMAIN}secret/bg.gif"
      _(@validate_website.crawler.queue).must_be :empty?
      _(@validate_website.history_count).must_equal 3
      _(out).must_match %r{Unvisited 2 url\(s\):}
    end

    it 'honors exclude given at construction time' do
      stub_page('/', '<html><body>
                 <img src="/secret/image.png">
                 <a href="/keep.html">keep</a>
               </body></html>')
      stub_page('/keep.html', '<html><body>keep</body></html>')
      v = nil
      _out, _err = capture_io do
        v = ValidateWebsite::Crawl.new(site: TEST_DOMAIN, color: false,
                                       exclude: /secret/)
        v.crawl(markup: false)
      end
      _(v.crawler.history.map(&:to_s))
        .wont_include "#{TEST_DOMAIN}secret/image.png"
      _(v.unvisited.size).must_equal 1
      _(v.unvisited.first.sources.to_a).must_equal ["#{TEST_DOMAIN}"]
    end

    it 'does not enqueue the same excluded url twice' do
      stub_page('/', '<html><body>
                 <img src="/secret/a.png">
                 <img src="/secret/a.png">
                 <a href="/secret/a.png">dup</a>
               </body></html>')
      @validate_website.site = TEST_DOMAIN
      _out, _err = capture_io do
        @validate_website.crawl(markup: false, exclude: /secret/)
      end
      _(@validate_website.crawler.queue).must_be :empty?
      _(@validate_website.unvisited.size).must_equal 1
    end

    it 'lists every source for a url referenced by two pages' do
      stub_page('/', '<html><body><a href="/a.html">a</a>
                 <a href="/b.html">b</a></body></html>')
      stub_page('/a.html', '<html><body><img src="/shared.png"></body></html>')
      stub_page('/b.html', '<html><body><img src="/shared.png"></body></html>')
      @validate_website.site = TEST_DOMAIN
      _out, _err = capture_io do
        @validate_website.crawl(markup: false, exclude: /shared/)
      end
      entry = @validate_website.unvisited.first
      _(entry.url).must_equal "#{TEST_DOMAIN}shared.png"
      _(entry.reason).must_equal :exclude
      _(entry.sources.to_a.sort).must_equal ["#{TEST_DOMAIN}a.html",
                                             "#{TEST_DOMAIN}b.html"]
      _(@validate_website.unvisited.size).must_equal 1
    end

    it 'filters html links coming from spidr' do
      stub_page('/', '<html><body>
                 <a href="/news/index.html">news</a>
                 <a href="/keep.html">keep</a>
               </body></html>')
      stub_page('/keep.html', '<html><body>keep</body></html>')
      @validate_website.site = TEST_DOMAIN
      _out, _err = capture_io do
        @validate_website.crawl(markup: false, exclude: /news/)
      end
      _(@validate_website.crawler.failures).must_be :empty?
      _(@validate_website.crawler.history.map(&:to_s))
        .wont_include "#{TEST_DOMAIN}news/index.html"
      _(@validate_website.unvisited.first.sources.to_a)
        .must_equal ["#{TEST_DOMAIN}"]
    end
  end

  describe 'not found' do
    it 'reports failed pages in unvisited with the original error' do
      stub_request(:get, "#{TEST_DOMAIN}dead.html")
        .to_raise(Errno::ECONNREFUSED.new('connection refused'))
      stub_request(:get, "#{TEST_DOMAIN}keep.html")
        .to_return(status: 200, headers: { 'Content-Type' => 'text/html' },
                   body: '<html><body>keep</body></html>')
      stub_request(:get, TEST_DOMAIN).to_return(
        status: 200,
        headers: { 'Content-Type' => 'text/html' },
        body: '<html><body><a href="/dead.html">d</a>
               <a href="/keep.html">k</a></body></html>'
      )
      @validate_website.site = TEST_DOMAIN
      _out, _err = capture_io do
        @validate_website.crawl(markup: false, not_found: true)
      end
      _(@validate_website.not_founds_count).must_equal 1
      _(@validate_website.exit_status)
        .must_equal ValidateWebsite::Core::EXIT_FAILURE_NOT_FOUND
      entry = @validate_website.unvisited.first
      _(entry.url).must_equal "#{TEST_DOMAIN}dead.html"
      _(entry.reason).must_equal :failure
      _(entry.error).must_be_instance_of Errno::ECONNREFUSED
      _(entry.error.message).must_include 'connection refused'
      _(entry.sources.to_a).must_equal ["#{TEST_DOMAIN}"]
    end

    it 'keeps exit code and counts unchanged when not_found is off' do
      stub_request(:get, "#{TEST_DOMAIN}dead.html")
        .to_raise(Errno::ECONNREFUSED.new('connection refused'))
      stub_request(:get, TEST_DOMAIN).to_return(
        status: 200,
        headers: { 'Content-Type' => 'text/html' },
        body: '<html><body><a href="/dead.html">d</a></body></html>'
      )
      @validate_website.site = TEST_DOMAIN
      out, _err = capture_io do
        @validate_website.crawl(markup: false, not_found: false)
      end
      _(@validate_website.not_founds_count).must_equal 0
      _(@validate_website.exit_status)
        .must_equal ValidateWebsite::Core::EXIT_SUCCESS
      _(out).wont_match(/linked but not exist/)
      # the failure is still explained, but does not change the exit code
      entry = @validate_website.unvisited.first
      _(entry.url).must_equal "#{TEST_DOMAIN}dead.html"
      _(entry.reason).must_equal :failure
      _(entry.error.message).must_include 'connection refused'
      _(out).must_match %r{Unvisited 1 url\(s\):}
    end

    it 'keeps 404 counting and exit code untouched' do
      stub_request(:get, "#{TEST_DOMAIN}missing.html").to_return(
        status: 404,
        headers: { 'Content-Type' => 'text/html' },
        body: 'not found'
      )
      stub_request(:get, TEST_DOMAIN).to_return(
        status: 200,
        headers: { 'Content-Type' => 'text/html' },
        body: '<html><body><a href="/missing.html">m</a></body></html>'
      )
      @validate_website.site = TEST_DOMAIN
      out, _err = capture_io do
        @validate_website.crawl(markup: false, not_found: true)
      end
      # 404 is a visited page, not a failure: unchanged behaviour
      _(@validate_website.not_founds_count).must_equal 0
      _(@validate_website.exit_status)
        .must_equal ValidateWebsite::Core::EXIT_SUCCESS
      _(@validate_website.unvisited).must_be :empty?
      _(out).wont_match(/Unvisited/)
    end

    it 'keeps markup errors at exit 64 alongside unvisited entries' do
      file = File.join('test', 'data', 'html5-fail.html')
      validator_res = File.join('test', 'data', 'validator.nu-failure.json')
      stub_request(:any, /#{validator.html5_validator_service_url}/)
        .to_return(body: File.open(validator_res).read)
      body = File.open(file).read.sub('</body>',
                                      '<img src="/secret/a.png"></body>')
      stub_request(:get, TEST_DOMAIN).to_return(
        status: 200,
        headers: { 'Content-Type' => 'text/html' },
        body: body
      )
      @validate_website.site = TEST_DOMAIN
      _out, _err = capture_io do
        @validate_website.crawl(html5_validator: :nu, ignore: /Warning/,
                                exclude: /secret/)
      end
      _(@validate_website.errors_count).must_equal 1
      _(@validate_website.unvisited.size).must_equal 1
      _(@validate_website.exit_status)
        .must_equal ValidateWebsite::Core::EXIT_FAILURE_MARKUP
    end
  end
end
