import R from 'ramda'
import { isIterable } from './isIterable'
import { iteratorToArray } from './iteratorToArray'
import { isSkipIterable } from './open'



export function pipe(...funcs: any[]): (...args: any[]) => Promise<any>|any {
    if (!funcs.length) {
        return (...args: any[]) => R.head(args)
    }
    const f = R.head(funcs)
    return async (...args: any[]) => {
        const arg = R.head(args)

        if (args.length == 1 && R.isNil(arg)) {
            return null
        }

        // A skipped page (noRevisit hit) has no DOM and no remaining stage can
        // meaningfully process it. Propagate its marked record untouched until the
        // output layer unwraps it, so $/$$/text/... never receive a fake Page.
        if (args.length == 1 && isSkipIterable(arg)) {
            return arg
        }

        if (isIterable(arg)) {
            return applyIterable(arg, funcs)
        }

        if (typeof arg === 'function') {
            return await arg(pipe(...funcs))
        }

        if (typeof f === 'function') {
            return await pipe(...R.tail(funcs))(await f(...args))
        }
        if (R.type(f) === 'Object') {
            return await pipe(...R.tail(funcs))(await parseObject(f, ...args))
        }
        if (R.type(f) === 'Array') {
            return await pipe(...R.tail(funcs))(await parseArray(f, ...args))
        }
        return f
    }
}

async function parseObject(object: Record<string, any>, ...args: any[]): Promise<Record<string, any>> {
    const res = {}
    for (const prop in object) {
        res[prop] = await iteratorToArray( await pipe(object[prop])(...args) )
    }
    return res
}

async function parseArray(array: any[], ...args: any[]) {
    const res = []
    for (let i=0; i<array.length; ++i) {
        res[i] = await pipe(array[i])(...args)
    }
    return res
}

function applyIterable(iterable: any, funcs: any[]): {} {
    return {
        async *[Symbol.asyncIterator] () {
            for (const item of iterable) {
                yield await pipe(...funcs)(item)
            }
        }
    }
}
