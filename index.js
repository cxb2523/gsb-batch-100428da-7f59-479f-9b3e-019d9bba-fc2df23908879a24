const _ = require('lodash')
const jQuery = require('jquery')
const { JSDOM } = require('jsdom')

module.exports = async (content, setting) => {
  const { window } = new JSDOM((content || '').toString()) // Prevent if content is a buffer
  const result = await analysis(window, setting)

  window.close()
  return result
}

function analysis(window, setting) {
  const $ = jQuery(window)

  if(pageNotFound($, setting.pageNotFound))
    return Promise.reject('Page not found')

  const {
    container = 'html',
    type = 'content',
    listOption = [],
    crawl: crawlData = {},
  } = setting

  const result = (type !== 'list')
    ? crawlContent($, $(container), crawlData)
    : crawlList($, $(container), crawlData, listOption)

  return Promise.resolve(result)
}

function pageNotFound($, pageNF) { // Not completed tested yet
  let result = []

  if(pageNF && pageNF.length) {
    result = pageNF.map(json => {
      if(!$(json.elem).length)
        return ''

      const check = (json.check && json.check.length) ? json.check[0] : ''
      const value = grabValue($(json.elem), json)
      switch(check) {
        case 'equal': return _.isEqual(value, json.check[1])
        default: return value
      }
    })
  }

  return (_.compact(result).join('') !== '')  // If check is all pass, return value will be ''
}

function crawlList($, $listElems, crawlData, [ _option, ..._list ]) {
  const total = $listElems.length
  const crawlAt = i => crawlContent($, $listElems.eq(i), crawlData)
  const wrap = (option, items, dropped = 0, truncated = 0) =>
    ({ items, summary: { option, count: items.length, dropped, truncated } })

  // Convert negative indices to count-from-end, then drop out-of-range and duplicated ones
  const normalize = list => {
    const seen = new Set()
    const indices = []
    let dropped = 0

    for(const raw of list) {
      const i = (raw < 0) ? (raw + total) : raw
      if(i < 0 || i >= total || seen.has(i)) {
        dropped++
        continue
      }
      seen.add(i)
      indices.push(i)
    }

    return { indices: indices.sort((a, b) => a - b), dropped }
  }

  const option = _list.length ? _option : 'all'

  switch(option) {
    case 'ignore': {
      const { indices, dropped } = normalize(_list)
      const items = _.difference(_.range(total), indices).map(crawlAt)
      return wrap(option, items, dropped)
    }
    case 'focus': {
      const { indices, dropped } = normalize(_list)
      return wrap(option, indices.map(crawlAt), dropped)
    }
    case 'range': {
      const [ start = 0, _end = total ] = _list
      const end = Math.min(_end, total)
      const truncated = Math.max(0, _end - total)
      const items = (start >= end) ? [] : _.times(end - start, i => crawlAt(start + i))
      return wrap(option, items, 0, truncated)
    }
    case 'limit': {
      const n = _list[0]
      const end = Math.min(n, total)
      const truncated = Math.max(0, n - total)
      return wrap(option, _.times(end, crawlAt), 0, truncated)
    }
    default:
      return wrap('all', _.times(total, crawlAt))
  }
}

function crawlContent($, $content, crawlData) {
  return _.mapValues(crawlData, options => {
    let $elem = options.outOfContainer ? $('html') : $content
    if(options.elem)
      $elem = $elem.find(options.elem)

    if(!$elem.length)
      return ('default' in options) ? options.default : null

    if(options.noChild)
      $elem = $elem.children().remove().end()

    const collectOptions = options.collect
    if(!collectOptions)
      return grabValue($elem, options)

    const dataList = (({ elems = [], loop = false }) => {
      switch(true) {
        case (elems.length > 0):
          return elems.map(tmp => {
            const $tmpElem = tmp.elem ? $elem.find(tmp.elem) : $elem
            return $tmpElem.length ? grabValue($tmpElem, tmp) : ''
          })
        case loop:
          return $elem.map((i, e) => grabValue($(e), collectOptions)).get()
        default:
          return []
      }
    })(collectOptions)

    return ('combineWith' in collectOptions && collectOptions.combineWith !== null)
      ? dataList.join(collectOptions.combineWith)
      : dataList
  })
}

function grabValue($elem, json) {
  const result = format($elem, json.get)

  switch(true) {
    case ['', null, undefined].includes(result):
      return ('default' in json) ? json.default : result
    case 'default' in json && result === json.default:
    case !json.process:
      return result
    case Array.isArray(json.process):
      return process(result, json.process)
    case (typeof json.process === 'function'):
      return json.process(result, $elem)
    default:
      return result
  }
}

function format($elem, returnType = 'text') {
  switch(returnType.split('-')[0]) {
    case 'num':
      const num = parseFloat($elem.text().replace(/[^\d\.]/g, '')) // Prevent like $1,000,0.001
      return !isNaN(num) ? num : 0
    case 'text':
    case 'html':
      return $elem[returnType]().trim()
    case 'length':
      return $elem.length
    case 'data':
      const tmp = returnType.split(/-|:/)
      const dataValue = $elem.data(tmp[1])

      return tmp[2] ? dataValue[tmp[2]] : dataValue
    default:
      return $elem.attr(returnType)
  }
}

_.mixin({
  match(data, regex, address) {
    const tmp = data.match(regex)
    const pointer = (typeof address !== 'undefined') ? parseFloat(address) : NaN

    return (tmp && !isNaN(pointer)) ? (tmp[pointer] || '') : tmp
  },
  split(data, keyword, address) {
    const tmp = data.split(keyword)
    const pointer = (typeof address !== 'undefined') ? parseFloat(address) : NaN

    return !isNaN(pointer) ? (tmp[pointer] || '') : tmp
  },
  replace(data, from, to) {
    return data.replace(from, to)
  },
  substring(data, start = 0, end) {
    end = end || data.length
    return data.substring(start, end)
  },
  prepend(data, arg) {
    return arg + data
  },
  append(data, arg) {
    return data + arg
  },
  encodeURI,
  encodeURIComponent,
  decodeURI,
  decodeURIComponent,
})

function process(data, processList) {
  for(let job of processList) {
    if(typeof data !== 'string')
      break

    data = _[job[0]](data, job[1], job[2])
  }

  return data
}
