const assert = require('node:assert')
const crawl = require('../index')

const HTML = `
  <ul id="list">
    <li class="item">A0</li>
    <li class="item">A1</li>
    <li class="item">A2</li>
    <li class="item">A3</li>
    <li class="item">A4</li>
    <li class="item">A5</li>
  </ul>
`

const listSetting = listOption => ({
  type: 'list',
  container: '#list .item',
  ...(listOption ? { listOption } : {}),
  crawl: { name: { get: 'text' } },
})

const names = result => result.items.map(item => item.name)

const tests = {
  async 'default (no listOption) crawls everything'() {
    const result = await crawl(HTML, listSetting())
    assert.deepStrictEqual(names(result), ['A0', 'A1', 'A2', 'A3', 'A4', 'A5'])
    assert.deepStrictEqual(result.summary, { option: 'all', count: 6, dropped: 0, truncated: 0 })
  },

  async 'focus picks specific indices in original order'() {
    const result = await crawl(HTML, listSetting(['focus', 3, 1]))
    assert.deepStrictEqual(names(result), ['A1', 'A3'])
    assert.deepStrictEqual(result.summary, { option: 'focus', count: 2, dropped: 0, truncated: 0 })
  },

  async 'range end beyond total is truncated'() {
    const result = await crawl(HTML, listSetting(['range', 2, 10]))
    assert.deepStrictEqual(names(result), ['A2', 'A3', 'A4', 'A5'])
    assert.deepStrictEqual(result.summary, { option: 'range', count: 4, dropped: 0, truncated: 4 })
  },

  async 'range without end crawls to the last one'() {
    const result = await crawl(HTML, listSetting(['range', 4]))
    assert.deepStrictEqual(names(result), ['A4', 'A5'])
    assert.deepStrictEqual(result.summary, { option: 'range', count: 2, dropped: 0, truncated: 0 })
  },

  async 'range with start >= end returns empty array'() {
    for(const option of [ ['range', 4, 2], ['range', 3, 3], ['range', 6, 10] ]) {
      const result = await crawl(HTML, listSetting(option))
      assert.deepStrictEqual(result.items, [])
      assert.strictEqual(result.summary.option, 'range')
      assert.strictEqual(result.summary.count, 0)
    }
  },

  async 'focus/ignore convert negative, out-of-range and duplicated indices'() {
    // total = 6: -1 -> 5, -6 -> 0 (dup of 0), 100 and -100 are dropped
    const focused = await crawl(HTML, listSetting(['focus', -1, 0, 0, -6, 100, -100]))
    assert.deepStrictEqual(names(focused), ['A0', 'A5'])
    assert.deepStrictEqual(focused.summary, { option: 'focus', count: 2, dropped: 4, truncated: 0 })

    const ignored = await crawl(HTML, listSetting(['ignore', -1, 1]))
    assert.deepStrictEqual(names(ignored), ['A0', 'A2', 'A3', 'A4'])
    assert.deepStrictEqual(ignored.summary, { option: 'ignore', count: 4, dropped: 0, truncated: 0 })
  },

  async 'ignore excluding everything returns empty array, not an error'() {
    const result = await crawl(HTML, listSetting(['ignore', 0, 1, 2, 3, 4, 5, -1]))
    assert.deepStrictEqual(result.items, [])
    assert.deepStrictEqual(result.summary, { option: 'ignore', count: 0, dropped: 1, truncated: 0 })
  },

  async 'limit 0 returns empty array'() {
    const result = await crawl(HTML, listSetting(['limit', 0]))
    assert.deepStrictEqual(result.items, [])
    assert.deepStrictEqual(result.summary, { option: 'limit', count: 0, dropped: 0, truncated: 0 })
  },

  async 'limit beyond total returns all elements'() {
    const result = await crawl(HTML, listSetting(['limit', 10]))
    assert.deepStrictEqual(names(result), ['A0', 'A1', 'A2', 'A3', 'A4', 'A5'])
    assert.deepStrictEqual(result.summary, { option: 'limit', count: 6, dropped: 0, truncated: 4 })
  },

  async 'limit within total takes first N from 0'() {
    const result = await crawl(HTML, listSetting(['limit', 2]))
    assert.deepStrictEqual(names(result), ['A0', 'A1'])
    assert.deepStrictEqual(result.summary, { option: 'limit', count: 2, dropped: 0, truncated: 0 })
  },

  async 'content type still returns the plain object'() {
    const result = await crawl(HTML, {
      type: 'content',
      container: '#list',
      crawl: { first: { elem: '.item', get: 'text' }, amount: { elem: '.item', get: 'length' } },
    })
    assert.deepStrictEqual(result, { first: 'A0A1A2A3A4A5', amount: 6 })
  },
}

;(async () => {
  let failed = 0
  for(const [name, test] of Object.entries(tests)) {
    try {
      await test()
      console.log(`ok - ${name}`)
    } catch (err) {
      failed++
      console.error(`not ok - ${name}`)
      console.error(err)
    }
  }

  if(failed) {
    console.error(`${failed} test(s) failed`)
    process.exit(1)
  }
  console.log(`${Object.keys(tests).length} tests passed`)
})()
