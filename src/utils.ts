import constants from "./constants"
import fs from "fs"
import path, { dirname } from "path"
import request from "request"
import { rejects } from "assert"

export function delay(ms: number = constants.defaultDelayMillis) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

var deleteFolderRecursive = function(path: string) {
  if (fs.existsSync(path)) {
    fs.readdirSync(path).forEach(function(file) {
      var curPath = path + "/" + file
      if (fs.lstatSync(curPath).isDirectory()) {
        // recurse
        deleteFolderRecursive(curPath)
      } else {
        // delete file
        fs.unlinkSync(curPath)
      }
    })
    fs.rmdirSync(path)
  }
}

export function createNewDir(dir: string) {
  if (fs.existsSync(dir)) {
    deleteFolderRecursive(dir)
  }
  fs.mkdirSync(dir)
}

/**
 * Downloads uri to file. The bytes are written to a temporary file first and
 * only renamed to `file` once the download has completed, so a failed or
 * interrupted download can never leave a half-written png behind.
 */
export function downloadImage(uri: string, file: string): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const tempFile = `${file}.tmp`
    let settled = false
    let writeStream: fs.WriteStream | null = null
    let writeStreamClosed = false
    let requestStream: any = null
    let response: any = null
    let finishedWriting = false
    let expectedBytes = -1

    function removeTempFile() {
      try {
        if (fs.existsSync(tempFile)) {
          fs.unlinkSync(tempFile)
        }
      } catch (cleanupError) {
        console.error("error removing partial download: ", cleanupError)
      }
    }

    function fail(error: Error) {
      if (settled) return
      settled = true
      try {
        if (requestStream) requestStream.abort()
      } catch (abortError) {
        // request already finished/aborted
      }
      try {
        if (response) response.destroy()
      } catch (destroyError) {
        // response already destroyed
      }
      if (writeStream && !writeStreamClosed) {
        //unlink only once the stream has released the file handle (windows)
        writeStream.once("close", () => {
          removeTempFile()
          reject(error)
        })
        writeStream.destroy()
      } else {
        removeTempFile()
        reject(error)
      }
    }

    function succeed() {
      if (settled) return
      settled = true
      try {
        if (fs.existsSync(file)) {
          fs.unlinkSync(file) //windows won't let rename overwrite
        }
        fs.renameSync(tempFile, file)
        resolve()
      } catch (renameError) {
        removeTempFile()
        reject(renameError)
      }
    }

    try {
      writeStream = fs.createWriteStream(tempFile)
    } catch (error) {
      fail(error)
      return
    }
    writeStream.on("close", () => {
      writeStreamClosed = true
    })
    writeStream.on("error", fail)

    try {
      requestStream = request(uri)
    } catch (error) {
      fail(error)
      return
    }
    requestStream.on("error", fail)
    requestStream.on("response", (res: any) => {
      response = res
      const statusCode: number = res.statusCode || 0
      if (statusCode < 200 || statusCode >= 300) {
        res.resume() //drain so the socket can be released
        fail(new Error(`Image download failed with HTTP status ${statusCode}`))
        return
      }

      //content-length counts the bytes on the wire, so it can only be compared
      //against the bytes we wrote if the response wasn't content-encoded.
      const isEncoded = res.headers["content-encoding"] !== undefined
      const contentLength = Number(res.headers["content-length"])
      if (!isEncoded && !isNaN(contentLength)) {
        expectedBytes = contentLength
      }

      res.on("error", fail)
      res.on("aborted", () =>
        fail(new Error("Image download was aborted before it completed"))
      )

      requestStream.pipe(writeStream!)
      writeStream!.on("finish", () => {
        finishedWriting = true
      })
      writeStream!.on("close", () => {
        if (settled) return
        if (!finishedWriting || !res.complete) {
          fail(new Error("Image download ended before the file was complete"))
          return
        }
        const receivedBytes = writeStream!.bytesWritten
        if (expectedBytes >= 0 && receivedBytes !== expectedBytes) {
          fail(
            new Error(
              `Incomplete image download: expected ${expectedBytes} bytes but received ${receivedBytes}`
            )
          )
          return
        }
        succeed()
      })
    })
  })
}

export function hashIntFromString(str: string): number {
  var hash = 0,
    i,
    chr
  if (str.length === 0) return hash
  for (i = 0; i < str.length; i++) {
    chr = str.charCodeAt(i)
    hash = (hash << 5) - hash + chr
    hash |= 0 // Convert to 32bit integer
  }
  return hash
}
