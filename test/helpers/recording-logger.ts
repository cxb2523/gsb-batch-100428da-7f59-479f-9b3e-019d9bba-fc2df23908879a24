import { DetailParseLogger } from '../../src/routes/detail-parser.js';

export type LogEntry = {
    level: 'info' | 'warning' | 'error' | 'debug';
    message: string;
    data?: Record<string, unknown>;
};

/** Captures log calls instead of printing them, so tests can assert on skipped-block warnings */
export const createRecordingLogger = (): DetailParseLogger & { entries: LogEntry[] } => {
    const entries: LogEntry[] = [];

    return {
        entries,
        info: (message, data) => entries.push({ level: 'info', message, data }),
        warning: (message, data) => entries.push({ level: 'warning', message, data }),
        error: (message, data) => entries.push({ level: 'error', message, data }),
        debug: (message, data) => entries.push({ level: 'debug', message, data }),
    };
};
