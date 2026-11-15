# frozen_string_literal: true

require File.expand_path('test_helper', __dir__)

# rubocop:disable Metrics/BlockLength
describe ValidateWebsite::Unvisited do
  def tracker(patterns = [])
    ValidateWebsite::Unvisited.new(patterns)
  end

  describe 'excluded?' do
    it 'matches strings with a regexp' do
      pattern = [/secret/]
      _(tracker(pattern).excluded?('http://x.test/secret/a.png'))
        .must_equal true
      _(tracker(pattern).excluded?('http://x.test/ok.png')).must_equal false
    end

    it 'accepts procs' do
      only_png = ->(link) { link.end_with?('.png') }
      _(tracker([only_png]).excluded?('http://x.test/a.png')).must_equal true
      _(tracker([only_png]).excluded?('http://x.test/a.gif')).must_equal false
    end

    it 'is false without patterns' do
      _(tracker.excluded?('http://x.test/a.png')).must_equal false
    end
  end

  describe 'entries' do
    it 'deduplicates by url and keeps every source' do
      u = tracker
      u.reference('http://x.test/shared.png', 'http://x.test/one.html')
      u.reference('http://x.test/shared.png', 'http://x.test/two.html')
      u.skip('http://x.test/shared.png')

      _(u.size).must_equal 1
      _(u.entries.first.url).must_equal 'http://x.test/shared.png'
      _(u.entries.first.reason).must_equal :exclude
      _(u.entries.first.sources.to_a.sort).must_equal(
        ['http://x.test/one.html', 'http://x.test/two.html']
      )
    end

    it 'keeps sources even when they arrive after the skip' do
      u = tracker
      u.skip('http://x.test/a.png')
      u.reference('http://x.test/a.png', 'http://x.test/page.html')
      _(u.entries.first.sources.to_a).must_equal ['http://x.test/page.html']
    end

    it 'keeps the original error for a failure' do
      u = tracker
      error = Errno::ECONNREFUSED.new('boom')
      u.reference('http://x.test/dead.html', 'http://x.test/')
      u.fail('http://x.test/dead.html', error)

      entry = u.entries.first
      _(entry.reason).must_equal :failure
      _(entry.error).must_be_same_as error
      _(entry.to_s).must_include 'failure: Errno::ECONNREFUSED:'
      _(entry.to_s).must_include 'boom'
      _(entry.to_s).must_include 'referenced by http://x.test/'
    end

    it 'sorts entries by url' do
      u = tracker
      u.skip('http://x.test/z.png')
      u.skip('http://x.test/a.png')
      _(u.entries.map(&:url)).must_equal ['http://x.test/a.png',
                                          'http://x.test/z.png']
    end

    it 'is empty by default' do
      _(tracker).must_be :empty?
      _(tracker.entries).must_equal []
    end
  end
end
