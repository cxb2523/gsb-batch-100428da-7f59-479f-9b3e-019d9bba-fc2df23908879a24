export type MovieType = 'normal' | 'uncensored'

export type MagnetType = 'all' | 'exist'

export type FilterType = 'star' | 'genre' | 'director' | 'studio' | 'label' | 'series'

export type SortBy = 'date' | 'size'

export type SortOrder = 'asc' | 'desc'

export interface Property {
  id: string
  name: string
}

export interface Movie {
  date: string | null
  title: string
  id: string
  img: string | null
  tags: string[]
}

export interface SimilarMovie {
  id: string
  title: string
  img: string | null
}

export interface Magnet {
  id: string
  link: string
  isHD: boolean
  title: string
  size: string | null
  numberSize: number | null
  shareDate: string | null
  hasSubtitle: boolean
}

export interface ImageSize {
  width: number
  height: number
}

export interface Sample {
  alt: string | null
  id: string
  thumbnail: string
  src: string | null
}

export interface MovieDetail {
  id: string
  title: string
  img: string | null
  date: string | null
  videoLength: number | null
  director: Property | null
  producer: Property | null
  publisher: Property | null
  series: Property | null
  genres: Property[]
  stars: Property[]
  imageSize: ImageSize | null
  samples: Sample[]
  similarMovies: SimilarMovie[]
  gid: string | null
  uc: string | null
}

export interface Pagination {
  currentPage: number
  hasNextPage: boolean
  nextPage: number | null
  pages: number[]
}

/**
 * 列表抓取结局
 * - `success`: 页面抓取与列表解析均成功
 * - `fetch_failed`: 目标页抓取失败（如上游返回 404、5xx）
 * - `parse_failed`: 页面已抓取，但缺少影片列表容器或分页容器，无法解析
 */
export type MoviesPageOutcome = 'success' | 'fetch_failed' | 'parse_failed'

export interface MoviesPage {
  movies: Movie[]
  pagination: Pagination
  /* 抓取结局；成功时可缺省，以兼容旧调用方 */
  outcome?: MoviesPageOutcome
}

export interface StarInfo {
  avatar: string | null
  id: string
  name: string
  birthday: string | null
  age: number | null
  height: number | null
  /* 胸围 */
  bust: string | null
  /* 腰围 */
  waistline: string | null
  /* 臀围 */
  hipline: string | null
  birthplace: string | null
  hobby: string | null
}

export interface SearchMoviesPage extends MoviesPage {
  keyword: string
}

export interface GetMoviesQuery {
  page?: string
  type?: MovieType
  magnet?: MagnetType
  filterType?: FilterType
  filterValue?: string
}
