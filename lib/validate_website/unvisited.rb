# frozen_string_literal: true

require 'set'

module ValidateWebsite
  # Registry of URLs referenced during a crawl but never visited, keeping
  # the page(s) which referenced them and the reason they were skipped.
  class Unvisited
    # A single unvisited URL.
    #
    # @!attribute url
    #   @return [String] normalized absolute url
    # @!attribute reason
    #   @return [Symbol] `:exclude` or `:failure`
    # @!attribute sources
    #   @return [Set<String>] pages which referenced this url
    # @!attribute error
    #   @return [Exception, nil] original error for a failure
    Entry = Struct.new(:url, :reason, :sources, :error) do
      def to_s
        line = +"  #{url} [#{reason_label}]"
        return line if sources.empty?

        line << " referenced by #{sources.to_a.sort.join(', ')}"
      end

      def reason_label
        return reason.to_s unless error

        "#{reason}: #{error.class}: #{error.message}"
      end
    end

    # @param [Array, nil] patterns exclusion rules: a single Regexp, Proc or
    #   String, or an Array of them. Strings are compiled to Regexp.
    def initialize(patterns = [])
      @patterns = Array(patterns).compact.map do |pattern|
        pattern.is_a?(String) ? Regexp.new(pattern) : pattern
      end
      @entries = {}
      @references = {}
    end

    # @return [Array<Entry>] entries sorted by url
    def entries
      @entries.values.sort_by(&:url)
    end

    # Report lines for the whole registry: a header then one line per entry.
    #
    # @return [Array<String>]
    def lines
      return [] if empty?

      ["Unvisited #{size} url(s):"] + entries.map(&:to_s)
    end

    def size
      @entries.size
    end

    def empty?
      @entries.empty?
    end

    # Apply the exclusion patterns to any url, whichever source found it.
    #
    # @param [URI::HTTP, String] url
    # @return [Boolean]
    def excluded?(url)
      link = url.to_s

      @patterns.any? do |pattern|
        if pattern.is_a?(Regexp)
          pattern.match?(link)
        elsif pattern.respond_to?(:call)
          pattern.call(link) == true
        else
          false
        end
      end
    end

    # Remember which page referenced a url, even if it is later skipped.
    def reference(url, source)
      key = url.to_s
      page = source.to_s
      return if page == key

      (@references[key] ||= Set[]) << page
      @entries[key].sources << page if @entries[key]
    end

    # Record a url skipped by the exclusion patterns.
    def skip(url)
      record(url, :exclude)
    end

    # Record a url which could not be fetched, keeping the original error.
    def fail(url, error)
      record(url, :failure) { |entry| entry.error = error }
    end

    private

    def record(url, reason)
      key = url.to_s
      entry = (@entries[key] ||= Entry.new(key, reason, Set[], nil))
      entry.sources.merge(@references[key]) if @references[key]
      yield entry if block_given?
      entry
    end
  end
end
