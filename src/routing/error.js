// @flow

/**
 * Underlying library errors (e.g. network errors) additionally carry a
 * machine-readable `code` such as `ECONNREFUSED`.
 */
type Cause = Error & { code?: string };

/**
 * Error type used for all crawler failures. It is a regular `Error` subclass
 * (therefore `instanceof Error` checks work) enriched with the target `uri`
 * and a `retriesExhausted` flag that distinguishes requests which kept failing
 * until all retry attempts were used up from ordinary, single-attempt
 * failures.
 */
export class SpiderError extends Error {
  /**
   * Absolute uri of the failed request.
   *
   * @type    {string}
   * @access  public
   */
  uri: string;

  /**
   * Whether all retry attempts have been exhausted (`true`) or the request
   * failed without being retried (`false`).
   *
   * @type    {boolean}
   * @access  public
   */
  retriesExhausted: boolean;

  /**
   * Original error thrown by the underlying request library, if any.
   *
   * @type    {Cause|undefined}
   * @access  public
   */
  cause: Cause | void;

  /**
   * Machine-readable error code from the underlying library, when available
   * (e.g. `ECONNREFUSED`, `ENOTFOUND`).
   *
   * @type    {string|undefined}
   * @access  public
   */
  code: string | void;

  /**
   * HTTP status code returned by the server, when a response was received.
   *
   * @type    {number|undefined}
   * @access  public
   */
  statusCode: number | void;

  /**
   * @param   {string}          message
   * @param   {Object}          properties
   * @param   {string}          properties.uri
   * @param   {boolean}         properties.retriesExhausted
   * @param   {Cause}           [properties.cause]
   * @param   {number}          [properties.statusCode]
   * @access  public
   */
  constructor(
    message: string,
    {
      uri,
      retriesExhausted,
      cause,
      statusCode
    }: {
      uri: string,
      retriesExhausted: boolean,
      cause?: Cause,
      statusCode?: number
    }
  ): void {
    super(message);

    this.name = "SpiderError";
    this.uri = uri;
    this.retriesExhausted = retriesExhausted;
    this.cause = cause;
    this.statusCode = statusCode;
    this.code = cause ? cause.code : undefined;
  }
}

/**
 * Wraps a request implementation in order to count how many physical HTTP
 * requests a single `retry-request` invocation performs. Each retry triggers
 * another call of the wrapped function, so more than one attempt means the
 * request was retried.
 *
 * @param   {Function}  request
 * @return  {Function}
 * @access  internal
 */
export function withAttemptCounter(request: Function): Function {
  function trackedRequest(...args: Array<*>): * {
    trackedRequest.attempts = (trackedRequest.attempts || 0) + 1;
    // `Reflect.apply` keeps the call working even if `request` is a wrapper
    // that does not expose its own `apply` method:
    return Reflect.apply(request, this, args);
  }

  return trackedRequest;
}

/**
 * Builds a readable `SpiderError` from a request failure.
 *
 * @param   {string}            uri                 Absolute target uri.
 * @param   {Cause|undefined}   error               Error thrown by the
 *                                                  underlying library.
 * @param   {Object|undefined}  response            Response received from the
 *                                                  server, if any.
 * @param   {number}            attempts            Amount of physical requests
 *                                                  performed.
 * @return  {SpiderError}
 * @access  internal
 */
export function createRequestError(
  uri: string,
  error: Cause | void,
  response: Object | void,
  attempts: number
): SpiderError {
  const retriesExhausted: boolean = attempts > 1;

  if (error) {
    return new SpiderError(`Request to ${uri} failed: ${error.message}`, {
      uri,
      retriesExhausted,
      cause: error,
      statusCode: response ? response.statusCode : undefined
    });
  }

  const statusCode: number = response ? response.statusCode : 0;

  return new SpiderError(
    `Request to ${uri} failed with status code ${statusCode}`,
    {
      uri,
      retriesExhausted,
      statusCode
    }
  );
}

/**
 * Normalizes any error thrown while handling an already fetched document so
 * that queue consumers always receive an `Error` instance carrying the target
 * `uri`. `SpiderError` instances are passed through untouched.
 *
 * @param   {string}  uri
 * @param   {*}       error
 * @return  {SpiderError}
 * @access  internal
 */
export function toSpiderError(uri: string, error: *): SpiderError {
  if (error instanceof SpiderError) {
    return error;
  }

  if (error instanceof Error) {
    return new SpiderError(`Failed to process ${uri}: ${error.message}`, {
      uri,
      retriesExhausted: false,
      cause: error
    });
  }

  return new SpiderError(`Failed to process ${uri}: ${String(error)}`, {
    uri,
    retriesExhausted: false
  });
}

export default SpiderError;
