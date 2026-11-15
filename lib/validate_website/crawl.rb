# frozen_string_literal: true

require 'English'

require 'validate_website/core'
require 'validate_website/utils'
require 'validate_website/unvisited'

module ValidateWebsite
  # Class for http website validation
  class Crawl < Core
    attr_reader :crawler

    def initialize(options = {}, validation_type = :crawl)
      @unvisited_tracker = Unvisited.new
      super
      start_message(@site)
    end

    def history_count
      crawler.history.size
    end

    # URLs skipped by the exclude rule or left unvisited after a failure,
    # deduplicated by url.
    #
    # @return [Array<Unvisited::Entry>]
    def unvisited
      @unvisited_tracker.entries
    end

    # @param [Hash] options
    #   :color [Boolean] color output (true, false)
    #   :exclude [String] a String used by Regexp.new
    #   :markup [Boolean] Check the markup validity
    #   :not_found [Boolean] Check for not found page (404)
    #
    def crawl(options = {})
      @options = @options.merge(options)
      patterns = [@options[:exclude], @options[:ignore_links]]
      @unvisited_tracker = Unvisited.new(patterns)
      @crawler = spidr_crawler(@site, @options)
      print_status_line(@crawler.history.size,
                        @crawler.failures.size,
                        @not_founds_count,
                        @errors_count)
      print_unvisited
    end

    private

    # Normalize a url the same way Spidr will before enqueuing it.
    #
    # @param [URI::HTTP, String] url
    # @return [URI::HTTP, nil]
    def normalized_url(url)
      @agent.sanitize_url(url.to_s)
    rescue URI::Error, TypeError, ArgumentError
      nil
    end

    def spidr_crawler(site, options)
      @host = URI(site).host
      Spidr.site(site, **options.slice(:user_agent)) do |crawler|
        @agent = crawler
        crawler.cookies[@host] = default_cookies if options[:cookies]
        on_every_url(crawler)
        on_every_link(crawler)
        on_every_css_page(crawler)
        on_every_html_page(crawler)
        on_every_failed_url(crawler)
      end
    end

    # Single entry point for every url enqueued, whether it comes from an
    # html link, an img src or a css url(): the exclusion rule is applied
    # before the url is added to the queue.
    def on_every_url(crawler)
      crawler.every_url do |url|
        next unless @unvisited_tracker.excluded?(url)

        @unvisited_tracker.skip(url)
        crawler.skip_link!
      end
    end

    def on_every_link(crawler)
      crawler.every_link do |source, url|
        absolute = normalized_url(url)
        next if absolute.nil? || crawler.visited?(absolute)

        @unvisited_tracker.reference(absolute, source)
      end
    end

    # Enqueue a url discovered outside of Spidr's html link extraction
    # (img src, css url()).
    def enqueue_url(url, source)
      absolute = normalized_url(url)
      return if absolute.nil?

      unless @agent.visited?(absolute)
        @unvisited_tracker.reference(absolute, source)
      end
      @agent.enqueue(absolute)
    end

    def on_every_css_page(crawler)
      crawler.every_css_page do |page|
        check_css_syntax(page) if options[:css_syntax]
        ValidateWebsite::Utils.extract_urls_from_css(page).each do |u|
          enqueue_url(u, page.url)
        end
      end
    end

    def validate?(page)
      options[:markup] && page.html? && !page.is_redirect?
    end

    def on_every_html_page(crawler)
      crawler.every_html_page do |page|
        ValidateWebsite::Utils.extract_imgs_from_page(page).each do |i|
          enqueue_url(i, page.url)
        end

        if validate?(page)
          keys = %i[ignore html5_validator]
          validate(page.doc, page.body, page.url, options.slice(keys))
        end
      end
    end

    # Failures are always explained in the unvisited report, but only the
    # not_found option turns them into a count and an exit code, so the
    # existing exit status semantics are preserved.
    def on_every_failed_url(crawler)
      crawler.every_failed_url do |url|
        @unvisited_tracker.fail(url, $ERROR_INFO)
        not_found_error(url) if options[:not_found]
      end
    end

    # Output the urls which were never visited, after the status line.
    def print_unvisited
      lines = @unvisited_tracker.lines
      return if lines.empty?

      puts "\n"
      lines.each { |line| puts color(:warning, line, options[:color]) }
    end
  end
end
