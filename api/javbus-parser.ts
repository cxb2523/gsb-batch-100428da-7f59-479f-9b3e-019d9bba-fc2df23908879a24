import bytes from 'bytes'
import { HTTPError } from 'got'
import { type HTMLElement, parse } from 'node-html-parser'
import probe from 'probe-image-size'

import client, { agent } from './client.js'
import { JAVBUS } from './constants.js'
import type {
  FilterType,
  GetMoviesQuery,
  ImageSize,
  Magnet,
  MagnetType,
  Movie,
  MovieDetail,
  MovieType,
  MoviesPage,
  Property,
  Sample,
  SearchMoviesPage,
  SimilarMovie,
  SortBy,
  SortOrder,
  StarInfo,
} from './types.js'
import { PAGE_REG, formatImageUrl } from './utils.js'

type StarInfoRequiredKey = 'avatar' | 'id' | 'name'

type StarInfoOptionalKey = Exclude<keyof StarInfo, StarInfoRequiredKey>

const starInfoMap: Record<StarInfoOptionalKey, string> = {
  birthday: '生日: ',
  age: '年齡: ',
  height: '身高: ',
  bust: '胸圍: ',
  waistline: '腰圍: ',
  hipline: '臀圍: ',
  birthplace: '出生地: ',
  hobby: '愛好: ',
}

interface MoviesPageParseSuccess {
  outcome: 'success'
  moviesPage: MoviesPage
}

interface MoviesPageParseFailure {
  outcome: 'parse_failed'
}

type MoviesPageParseResult = MoviesPageParseSuccess | MoviesPageParseFailure

interface MoviesPageFilter {
  name: string
  type: FilterType
  value: string
}

type MoviesPageWithFilter = MoviesPage & { filter?: MoviesPageFilter }

function parseMoviesPage(
  pageHTML: string,
  filter?: (movie: Movie) => boolean,
): MoviesPageParseResult {
  const doc = parse(pageHTML)

  const movieListContainer = doc.querySelector('#waterfall')
  const paginationContainer = doc.querySelector('.pagination')

  // 页面结构不符合预期时视为解析失败，避免返回看似正常却缺少分页信息的列表
  if (!movieListContainer || !paginationContainer) {
    return { outcome: 'parse_failed' }
  }

  const movies = doc
    .querySelectorAll('#waterfall .item')
    .map((item) => {
      const img =
        formatImageUrl(item.querySelector('.photo-frame img')?.getAttribute('src')) ?? null
      const title = item.querySelector('.photo-frame img')?.getAttribute('title') ?? ''
      const info = item.querySelectorAll('.photo-info date')
      const id = info[0]?.textContent
      const date = info[1]?.textContent ?? null
      const tags = item.querySelectorAll('.item-tag button').map((item) => item.textContent)

      return { date, id, img, title, tags }
    })
    .filter((movie): movie is Movie => Boolean(movie.id))
    .filter((movie) => filter?.(movie) ?? true)

  const currentPage = Number(doc.querySelector('.pagination .active a')?.textContent ?? '1')
  const pages = doc
    .querySelectorAll('.pagination li a')
    .map((item) => item.textContent)
    .filter((n) => PAGE_REG.test(n))
    .map(Number)

  const hasNextPage = doc.querySelector('.pagination li #next') !== null
  const nextPage = hasNextPage ? currentPage + 1 : null

  return {
    outcome: 'success',
    moviesPage: { movies, pagination: { currentPage, hasNextPage, nextPage, pages } },
  }
}

/* 解析失败时返回的分页占位结构，页码取本次请求页码 */
function createEmptyMoviesPage(requestedPage?: string): MoviesPage {
  const pageNumber = Number(requestedPage ?? '1')

  return {
    movies: [],
    pagination: {
      currentPage: Number.isFinite(pageNumber) ? pageNumber : 1,
      hasNextPage: false,
      nextPage: null,
      pages: [],
    },
  }
}

export function parseStarInfo(pageHTML: string, starId: string): StarInfo {
  const doc = parse(pageHTML).querySelector('#waterfall .item .avatar-box')

  const avatar = formatImageUrl(doc?.querySelector('.photo-frame img')?.getAttribute('src')) ?? null
  const name = doc?.querySelector('.photo-info .pb10')?.textContent ?? ''

  const infos = doc?.querySelectorAll('.photo-info p')

  const mapKeys = Object.keys(starInfoMap) as StarInfoOptionalKey[]

  const rest = mapKeys.reduce<Record<string, string | null>>((info, key) => {
    const mapValue = starInfoMap[key]
    const value =
      infos?.find((p) => p.textContent.includes(mapValue))?.textContent.replace(mapValue, '') ??
      null

    info[key] = value

    return info
  }, {}) as Omit<StarInfo, StarInfoRequiredKey>

  return {
    avatar,
    id: starId,
    name,
    ...rest,
  }
}

function parseFilterInfo(pageHTML: string, type: FilterType, value: string): MoviesPageFilter {
  const doc = parse(pageHTML)
  const name = doc.querySelector('title')?.textContent.match(/^(?:第\d+?頁 - )?(.+?) - /)?.[1] ?? ''

  return { name, type, value }
}

/* 解析阶段不向外抛异常，任何结构异常都收敛为解析失败结局 */
function safeParseMoviesPage(pageHTML: string): MoviesPageParseResult {
  try {
    return parseMoviesPage(pageHTML)
  } catch {
    return { outcome: 'parse_failed' }
  }
}

function safeParseFilterInfo(pageHTML: string, type: FilterType, value: string): MoviesPageFilter {
  try {
    return parseFilterInfo(pageHTML, type, value)
  } catch {
    return { name: '', type, value }
  }
}

export async function getMoviesByPage({
  page = '1',
  magnet = 'exist',
  type,
  filterType,
  filterValue,
}: GetMoviesQuery): Promise<MoviesPageWithFilter> {
  let prefix = !type || type == 'normal' ? JAVBUS : `${JAVBUS}/${type}`
  prefix = filterType ? `${prefix}/${filterType}` : prefix
  const url =
    page === '1'
      ? filterType && filterValue
        ? `${prefix}/${filterValue}`
        : prefix
      : filterType && filterValue
        ? `${prefix}/${filterValue}/${page}`
        : `${prefix}/page/${page}`

  /* ----------------- 阶段一：抓取页面 ------------------ */
  let pageHTML: string
  try {
    pageHTML = await client(url, {
      headers: { Cookie: `existmag=${magnet === 'exist' ? 'mag' : 'all'}` },
    }).text()
  } catch (error) {
    // 上游返回 HTTP 错误（如 404、5xx）说明目标页抓取失败，以结局标记返回；
    // 连接被拒绝、超时等真正的网络错误仍按现有错误链路抛出
    if (error instanceof HTTPError) {
      const filterInfo =
        filterType && filterValue ? { name: '', type: filterType, value: filterValue } : undefined

      return { ...createEmptyMoviesPage(page), outcome: 'fetch_failed', filter: filterInfo }
    }
    throw error
  }

  const filterInfo =
    filterType && filterValue ? safeParseFilterInfo(pageHTML, filterType, filterValue) : undefined

  /* ----------------- 阶段二：解析列表 ------------------ */
  const parseResult = safeParseMoviesPage(pageHTML)

  if (parseResult.outcome === 'parse_failed') {
    return { ...createEmptyMoviesPage(page), outcome: 'parse_failed', filter: filterInfo }
  }

  return { ...parseResult.moviesPage, outcome: 'success', filter: filterInfo }
}

export async function getMoviesByKeywordAndPage(
  keyword: string,
  page = '1',
  magnet?: MagnetType,
  type?: MovieType,
): Promise<SearchMoviesPage> {
  const prefix = !type || type === 'normal' ? `${JAVBUS}/search` : `${JAVBUS}/${type}/search`
  const url = `${prefix}/${encodeURIComponent(keyword)}/${page}&type=1`

  const res = await client(url, {
    headers: { Cookie: `existmag=${magnet === 'exist' ? 'mag' : 'all'}` },
  }).text()

  const parseResult = safeParseMoviesPage(res)

  if (parseResult.outcome === 'parse_failed') {
    return { ...createEmptyMoviesPage(page), outcome: 'parse_failed', keyword }
  }

  return { ...parseResult.moviesPage, outcome: 'success', keyword }
}

export function convertMagnetsHTML(html: string) {
  const doc = parse(html)

  const magnets = doc
    .querySelectorAll('tr')
    .map<Magnet>((tr) => {
      const firstAnchor = tr.querySelector('td a')
      const tagAnchors = firstAnchor?.querySelectorAll('a')

      const link = firstAnchor?.getAttribute('href') ?? ''
      const id = /magnet:\?xt=urn:btih:(\w+)/.exec(link)?.[1] ?? ''

      const isHD = Boolean(tagAnchors?.find((a) => a.textContent.includes('高清')))
      const hasSubtitle = Boolean(tagAnchors?.find((a) => a.textContent.includes('字幕')))

      if (tagAnchors?.length) {
        tagAnchors.forEach((a) => {
          a.remove()
        })
      }

      const title = firstAnchor?.textContent.trim() ?? ''
      const size = tr.querySelector('td:nth-child(2) a')?.textContent.trim() ?? null
      const numberSize = size ? bytes(size) : null
      const shareDate = tr.querySelector('td:nth-child(3) a')?.textContent.trim() ?? null

      return { id, link, isHD, title, size, numberSize, shareDate, hasSubtitle }
    })
    .filter(({ id, link, title }) => id && link && title)
    .toSorted((a, b) => (a.numberSize && b.numberSize ? b.numberSize - a.numberSize : 0))

  return magnets
}

export async function getMovieMagnets(params: {
  movieId: string
  gid: string
  uc: string
  sortBy?: SortBy
  sortOrder?: SortOrder
}): Promise<Magnet[]> {
  const { movieId, gid, uc } = params

  const magnetsRes = await client(`${JAVBUS}/ajax/uncledatoolsbyajax.php`, {
    searchParams: {
      lang: 'zh',
      gid,
      uc,
    },
    headers: {
      referer: `${JAVBUS}/${movieId}`,
    },
  }).text()

  const magnets = convertMagnetsHTML(magnetsRes)

  if (params.sortBy && params.sortOrder) {
    const { sortBy, sortOrder } = params

    magnets.sort((a, b) => {
      if (sortBy === 'date') {
        if (a.shareDate && b.shareDate) {
          const aDate = new Date(a.shareDate).getTime()
          const bDate = new Date(b.shareDate).getTime()

          return sortOrder === 'asc' ? aDate - bDate : bDate - aDate
        }
      } else {
        if (a.numberSize && b.numberSize) {
          return sortOrder === 'asc' ? a.numberSize - b.numberSize : b.numberSize - a.numberSize
        }
      }
      return 0
    })
  }

  return magnets
}

function textInfoFinder(infos: HTMLElement[], text: string, excludeText?: string): string | null {
  const info =
    infos
      .find((info) => info.querySelector('.header')?.textContent.includes(text))
      ?.lastChild?.textContent.trim() ?? null
  if (excludeText) {
    return info?.replace(excludeText, '') ?? null
  }
  return info
}

function linkInfoFinder(
  infos: HTMLElement[],
  text: string,
  prefix: string,
): { id: string; name: string } | null {
  const link = infos
    .find((info) => info.querySelector('.header')?.textContent.includes(text))
    ?.querySelector('a')

  if (!link) {
    return null
  }

  const href = link.getAttribute('href')
  const isUncensored = href?.includes('uncensored') ?? false
  const computedPrefix = isUncensored ? `uncensored/${prefix}` : prefix
  let id = link.getAttribute('href')?.replace(`${JAVBUS}/${computedPrefix}/`, '') ?? ''
  id = id && isUncensored ? `uncensored/${id}` : id
  const name = link.textContent.trim()

  if (!id || !name) {
    return null
  }

  return { id, name }
}

function multipleInfoFinder<T>(
  infoNodes: HTMLElement[],
  type: string,
  genreFilter: (genre: HTMLElement) => boolean,
  infoNodeGetter: (genre: HTMLElement) => HTMLElement | null,
  mapper?: (info: { id: string; name: string }) => T,
) {
  const infos =
    infoNodes
      .find((info) => info.querySelectorAll('.genre').filter(genreFilter).length > 0)
      ?.querySelectorAll('.genre')
      .map<Property>((genre) => {
        const node = infoNodeGetter(genre)
        const href = node?.getAttribute('href')
        const isUncensored = href?.includes('uncensored') ?? false
        const computedType = isUncensored ? `uncensored/${type}` : type
        let id = node?.getAttribute('href')?.replace(`${JAVBUS}/${computedType}/`, '') ?? ''
        id = id && isUncensored ? `uncensored/${id}` : id
        const name = node?.textContent ?? ''
        return { id, name }
      })
      .filter(({ id, name }) => Boolean(id) && Boolean(name)) ?? []
  return mapper ? infos.map<T>(mapper) : infos
}

export async function getMovieDetail(id: string): Promise<MovieDetail> {
  const res = await client(`${JAVBUS}/${id}`).text()
  const doc = parse(res)

  /* ----------------- 标题、图片 ------------------ */
  const title = doc.querySelector('.container h3')?.textContent ?? ''
  const img =
    formatImageUrl(doc.querySelector('.container .movie .bigImage img')?.getAttribute('src')) ??
    null

  let imageSize: ImageSize | null = null

  if (img) {
    try {
      const { width, height } = await probe(img, {
        agent,
        headers: {
          Referer: 'https://www.javbus.com/',
        },
      })
      imageSize = { width, height }
    } catch {
      //
    }
  }

  /* ----------------- 基本信息 ------------------ */
  const infoNodes = doc.querySelectorAll('.container .movie .info p')

  const date = textInfoFinder(infoNodes, '發行日期:')
  const videoLength = textInfoFinder(infoNodes, '長度:', '分鐘')
  const numberVideoLength = videoLength ? Number(videoLength) : null
  const director = linkInfoFinder(infoNodes, '導演:', 'director')
  const producer = linkInfoFinder(infoNodes, '製作商:', 'studio')
  const publisher = linkInfoFinder(infoNodes, '發行商:', 'label')
  const series = linkInfoFinder(infoNodes, '系列:', 'series')
  const genres = multipleInfoFinder<Property>(
    infoNodes,
    'genre',
    (genre) => !genre.hasAttribute('onmouseover'),
    (genre) => genre.querySelector('label a'),
  )
  const stars = multipleInfoFinder<Property>(
    infoNodes,
    'star',
    (genre) => genre.hasAttribute('onmouseover'),
    (genre) => genre.querySelector('a'),
  )

  /* ----------------- 磁力链接 ------------------ */
  const gidReg = /var gid = (\d+);/
  const ucReg = /var uc = (\d+);/

  const gid = gidReg.exec(res)?.[1] ?? null
  const uc = ucReg.exec(res)?.[1] ?? null

  /* ----------------- 样品图片 ------------------ */
  const samples = doc
    .querySelectorAll('#sample-waterfall .sample-box')
    .map<Sample>((box) => {
      const img = box.querySelector('.photo-frame img')
      const thumbnail = formatImageUrl(img?.getAttribute('src')) ?? ''
      const filename = thumbnail.split('/').pop()
      const id = filename?.match(/(\S+)\.(jpe?g|png|webp|gif)$/i)?.[1] ?? ''
      const alt = img?.getAttribute('title') ?? null
      const src = formatImageUrl(box.getAttribute('href')) ?? null
      return {
        alt,
        id,
        src,
        thumbnail,
      }
    })
    .filter(({ id, thumbnail }) => Boolean(id) && Boolean(thumbnail))

  /* ----------------- 同类影片 ------------------ */
  const similarMovies =
    doc
      .querySelector('#related-waterfall')
      ?.querySelectorAll('a')
      .map((link) => {
        const href = link.getAttribute('href')

        const id = href?.split('/').pop()
        const title = link.getAttribute('title')
        const img = formatImageUrl(link.querySelector('img')?.getAttribute('src')) ?? null

        return { id, title, img }
      })
      .filter((movie): movie is SimilarMovie => Boolean(movie.id) && Boolean(movie.title)) ?? []

  return {
    id,
    title,
    img,
    imageSize,
    date,
    videoLength: numberVideoLength,
    director,
    producer,
    publisher,
    series,
    genres,
    stars,
    samples,
    similarMovies,
    gid,
    uc,
  }
}

export async function getStarInfo(starId: string, type?: MovieType): Promise<StarInfo> {
  const prefix = !type || type === 'normal' ? JAVBUS : `${JAVBUS}/${type}`
  const url = `${prefix}/star/${starId}`

  const res = await client(url).text()

  const starInfo = parseStarInfo(res, starId)

  return starInfo
}
